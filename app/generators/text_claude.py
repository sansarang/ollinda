"""
Claude 기반 텍스트 생성기 — 인스타 캡션, 네이버 블로그 SEO 초안.
모델: claude-opus-4-8 (기본). 키: ANTHROPIC_API_KEY.
업종 페르소나(prof.persona)·메모(asset.note: 목적/타겟/추가정보 포함)를 강하게 반영.
"""
from __future__ import annotations

import re
import uuid

from app.domain.models import Asset, Channel, ContentKind, ContentPiece, ContentStatus, Tenant
from app.generators.base import Generator
from app.industries import resolve_industry, industry_brief
from app.strategies import resolve_strategy, buy_block
from app import seo

MODEL = "claude-opus-4-8"


def _pick_title(cands: list[str], kw0: str, body: str = "") -> tuple[str, str]:
    """제목 3안 중 1개 내부 자동 선택(CTR 최적화 4-1) — 유저에게 3안 비노출, 사유는 payload 로그.
    선택 기준(순서대로):
    ① 게이트 선통과: 타깃 키워드 원형 포함 필수(제목 1회 규칙과 정합) — 미포함 후보는 탈락
       (전부 탈락이면 원본 후보로 폴백해 기존 동작 유지)
    ② 낚시성 배제(정직): 제목의 숫자·'비용/가격' 약속이 본문에 없으면 감점 -4
       (본문이 답 못 주는 제목 금지)
    ③ 키워드 앞부분(첫 12자 안) +4 / 포함 +2 — '맨 앞 강제' 가중 폐지(기계 조합형 제목 방지, 제목 개선 ②)
    ④ 구체성: 숫자·차종 등 구체 토큰 +2, 검색의도 단어(후기·방법·비용…) +1
    ⑤ 길이: 22~35자 +3 (30자 내외 최적)"""
    import re
    pool = [c.strip() for c in cands if c.strip() and len(c.strip()) >= 8]
    gated = [c for c in pool if (not kw0 or kw0 in c)]           # ① 게이트 선통과
    pool2 = gated or pool
    best, best_score, why = "", -999, ""
    for c in pool2:
        s, notes = 0, []
        _pos = c.find(kw0) if kw0 else -1
        if 0 <= _pos <= 12:
            s += 4; notes.append("키워드 앞부분")
        elif _pos > 12:
            s += 2; notes.append("키워드 포함")
        s += 3 if 22 <= len(c) <= 35 else (1 if 18 <= len(c) <= 40 else 0)
        _nums = re.findall(r"[0-9]+", c)
        if _nums:
            if body and not all(n in body for n in _nums):
                s -= 4; notes.append("숫자 근거 없음(-)")       # ② 낚시 배제
            else:
                s += 2; notes.append("구체 숫자")
        if re.search(r"비용|가격", c) and body and not re.search(r"비용|가격|견적", body):
            s -= 4; notes.append("가격 약속 근거 없음(-)")
        if re.search(r"추천|후기|방법|비교|가격|정리|총정리|BEST|베스트", c):
            s += 1; notes.append("의도 단어")
        # ★ 화자 게이트(2026-08-12 사장님 지적, 2026-08-01 '화자 뒤집힘' 재발):
        #   우리는 파는 쪽이다. 손님이 산 뒤 쓰는 말투 제목은 강하게 눌러 뒤로 보낸다.
        #   ('후기'는 검색어라 허용 — 문제는 '내가 사서 써봤다'로 읽히는 표현이다)
        if re.search(r"내돈내산|직접\s*써보|써보니|사용기|구매기|솔직후기|직접\s*검수|"
                     r"내가\s*(사|타|써)|체험기|겪어보니", c):
            s -= 8; notes.append("손님 화자(-)")
        if re.search(r"^[^,]{2,12},\s", c) or c.count(",") >= 2:   # '추천, 부산 기장…' 쉼표 나열형 — 자연 문장형 우선
            s -= 3; notes.append("쉼표 나열(-)")
        if s > best_score:
            best, best_score, why = c, s, ", ".join(notes) or "기본"
    return best, f"{why} (점수 {best_score}, 후보 {len(pool)}·게이트 통과 {len(gated)})"


def _recent_openers(tenant_id: str, limit: int = 5) -> list[str]:
    """이 가게 최근 블로그 글의 첫 문장들 — '같은 훅 반복'(기계 티·유사문서 신호) 방지용 금지 목록.
    실측: '이거 안 보면/모르면 호구' 훅이 연속 글에서 반복됐음(2026-07-27)."""
    import re as _re
    out: list[str] = []
    try:
        from app import db as _db
        from app.models import ContentKind as _CK
        for s in _db.list_sets(tenant_id=tenant_id, limit=limit + 2):
            for p in _db.get_set_pieces(s["asset_id"]):
                if p.kind != _CK.BLOG:
                    continue
                body = (p.payload.get("body") or "").strip()
                first_line = next((ln.strip() for ln in body.split("\n")
                                   if ln.strip() and not ln.strip().startswith(("[", "#"))), "")
                first = _re.split(r"(?<=[다요죠])[.!?]?\s", first_line)[0][:70].strip()
                if first and first not in out:
                    out.append(first)
                break
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def _hook_style(asset_id: str) -> tuple:
    """asset 시드로 도입 스타일 1개 배정(결정적) — 같은 세트 재생성은 같은 스타일, 세트마다 회전."""
    import hashlib
    _h = int(hashlib.md5((asset_id or "").encode()).hexdigest()[:8], 16)
    return seo.HOOK_STYLES[_h % len(seo.HOOK_STYLES)]


_DWELL_PROMISE = ("보여드", "알려드", "공개", "정리해", "가져가", "확인하는 법", "말씀드", "펼쳐",
                  "소개", "드릴게요", "나눠드", "짚어드")
_DWELL_BRIDGE = ("아래에서", "바로 아래", "이제 ", "지금부터", "궁금하실", "다음은", "이어서",
                 "그렇다면", "여기서 ", "하나 더", "이 다음", "넘어가", "살펴보", "마지막에",
                 "될까요", "아닐까요", "다 믿어도", "그런데 왜", "그런데 계기판")


def _audit_dwell_devices(body: str) -> list[str]:
    """체류 3장치 기계 검사(발현률 게이트 — LLM 지시는 확률, 검사는 보장) → 누락 목록.
    ①first_promise: 첫 문단에 답 예고 ②itemized_preview: 서두 ①②③ 예고 ③bridge: 중간 전환 이정표."""
    import re
    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "")
             if p.strip() and not p.strip().startswith(("[", "#", "|", "-", "📍"))]
    missing = []
    head = " ".join(paras[:2])[:400]
    if not any(w in head for w in _DWELL_PROMISE):
        missing.append("first_promise")
    if "①" not in " ".join(paras[:3]):
        missing.append("itemized_preview")
    mid = " ".join(paras[2:-1]) if len(paras) > 4 else ""
    if not any(w in mid for w in _DWELL_BRIDGE):
        missing.append("bridge")
    return missing


def _ensure_dwell_devices(body: str, kw0: str) -> tuple[str, dict]:
    """누락 장치만 소형 LLM 패치로 보충(원문 보존 — 전체 재작성 금지). 실패 시 원문 유지.
    반환 (body, {"missing": [...], "fixed": [...]})."""
    import re
    missing = _audit_dwell_devices(body)
    rep = {"missing": list(missing), "fixed": []}
    if not missing:
        return body, rep
    _need = []
    if "first_promise" in missing:
        _need.append("[첫문단]\n(첫 문장이 '이 글이 답을 직접 보여준다'는 예고로 시작하도록 기존 첫 문단을 "
                     "고쳐 쓴 교체본 — 내용·사실은 원문 그대로)")
    if "itemized_preview" in missing:
        _need.append("[예고]\n(①②③ 번호로 이 글이 줄 것 3가지를 예고하는 한 문장 — 본문에 실제 있는 내용만. "
                     "'끝까지 보시면 ~ 가져가실 수 있습니다' 정형구를 그대로 쓰지 말고 글의 문체에 맞게 변주)")
    if "bridge" in missing:
        _need.append("[이정표]\n(숫자 하나(몇 번째 문단 뒤에 넣을지) | 다음 섹션으로 넘어가는 자연스러운 "
                     "전환 문장 1개 — 실제 뒤에 나오는 내용만 예고)")
    try:
        raw = _call_llm(
            "아래 블로그 글에서 빠진 장치만 만들어라. 글을 다시 쓰지 마라 — 요청된 조각만 출력.\n"
            f"[핵심 키워드] {kw0}\n\n[본문]\n{body[:6000]}\n\n출력 형식(요청된 항목만, 머리표 유지):\n"
            + "\n".join(_need), model="claude-sonnet-5", max_tokens=700)
        d = _parse_sections(raw, ["첫문단", "예고", "이정표"])
        paras = re.split(r"(\n\s*\n)", body)               # 구분자 보존 분할(재조립 무손실)
        texts = [p for p in paras if p.strip()]

        def _para_index(n):
            cnt = -1
            for i, p in enumerate(paras):
                if p.strip():
                    cnt += 1
                    if cnt == n:
                        return i
            return None
        if "first_promise" in missing and (d.get("첫문단") or "").strip():
            _new = d["첫문단"].strip()
            i0 = _para_index(0)
            if i0 is not None and 20 <= len(_new) <= 600 and "[사진" not in paras[i0]:
                paras[i0] = _new
                rep["fixed"].append("first_promise")
        if "itemized_preview" in missing and "①" in (d.get("예고") or ""):
            i0 = _para_index(0)
            if i0 is not None:
                paras[i0] = paras[i0].rstrip() + "\n\n" + d["예고"].strip()
                rep["fixed"].append("itemized_preview")
        if "bridge" in missing and "|" in (d.get("이정표") or ""):
            _n_s, _sent = d["이정표"].split("|", 1)
            _sent = _sent.strip()
            try:
                _n = max(2, min(len(texts) - 2, int(re.sub(r"\D", "", _n_s) or 3)))
            except Exception:
                _n = 3
            ib = _para_index(_n)
            if ib is not None and 10 <= len(_sent) <= 200:
                paras[ib] = paras[ib].rstrip() + "\n\n" + _sent
                rep["fixed"].append("bridge")
        if rep["fixed"]:
            body = "".join(paras)
    except Exception:
        pass
    return body, rep


def _kw_density(body: str, kw: str) -> dict:
    """핵심키워드 밀도 검증 — 네이버 최적 1~2%, 3%+는 저품질 위험."""
    import re
    if not (body and kw):
        return {"count": 0, "pct": 0.0, "status": "none"}
    words = max(1, len(re.findall(r"[가-힣A-Za-z0-9]+", body)))
    count = body.count(kw)
    pct = round(count / words * 100, 2)
    status = ("low" if count < 2 else "over" if pct > 3.0 or count > 8 else "ok")
    return {"count": count, "pct": pct, "status": status}


class CaptionGenerator(Generator):
    """인스타 캡션 + 해시태그 (페르소나 강하게)."""
    kind = ContentKind.CAPTION

    def __init__(self, model: str = MODEL):
        self.model = model

    def _prompt(self, tenant: Tenant, asset: Asset, n_imgs: int, kws: list[str]) -> str:
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        seeds = " ".join(prof.hashtag_seeds)
        cautions = ("\n[주의] " + "; ".join(prof.cautions)) if prof.cautions else ""
        carousel = f"\n[사진 {n_imgs}장 — 캐러셀]" if n_imgs > 1 else ""
        buy = buy_block(tenant)
        buy_line = f"\n[구매 안내(마지막에 자연스럽게)] {buy}" if buy else ""
        tag_hint = "상품·후기 키워드" if strat.keyword_axis == "product" else "지역명·타겟키워드"
        return (
            f"[가게] {tenant.name} (업종: {prof.name}, 지역: {tenant.region})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n[업종 톤] {prof.tone}\n"
            f"{industry_brief(prof)}"
            f"{carousel}\n[CTA] {strat.cta}{buy_line}\n"   # 입력정보(asset.note)는 캐시 프리픽스로 전달
            f"{seo.speaker_frame(strat.key)}\n"
            f"[기본 해시태그] {seeds}{cautions}\n"
            f"{seo.keywords_line(kws)}\n\n"
            f"{seo.CAPTION_DIRECTIVES}\n{seo.HOOK_RULE}\n{seo.PLATFORM_REEL}\n{seo.COPY_PSYCH}\n{seo.FACTS_RULE}\n{seo.HUMAN_TOUCH}\n\n"
            "위 페르소나 말투를 강하게 적용해 인스타그램 캡션을 한국어로 작성하라. "
            f"과장 없이 솔직하게, 이모지는 적당히. 해시태그는 정확한 3~5개만({tag_hint} 포함, 2026엔 많으면 도달↓)."
        )

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        imgs = images or [asset.path]
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        _kw0c, kws = seo.resolve_target_keyword(   # 공유 관문(전 생성기 공통)
            industry=(getattr(tenant, "industry", "") or prof.name), region=tenant.region or "",
            note=asset.note or "", biz=(getattr(tenant, "biz_type", "local") or "local"),
            content_type=(getattr(asset, "content_type", "sell") or "sell"), brand=tenant.brand_name or "",
            keyword_axis=strat.keyword_axis, target_kw_override=(getattr(asset, "target_kw", "") or ""),
            tenant_id=tenant.id, prof_name=prof.name)
        from app import llm as _llm
        text = _llm.call_task("caption", self._prompt(tenant, asset, len(imgs), kws), 1200,
                              default_model=self.model,
                              cache_prefix=cache_prefix_for(asset))   # 인스타 캡션(공유 컨텍스트 캐싱)
        _cap_route = dict(_llm.LAST_ROUTE.get("caption") or {})
        # 저장·공유 CTA 자동 삽입(영상강화 PHASE 5) — 저장·공유가 좋아요보다 3~5배 가중치.
        # 소재 정합 게이트(캐스퍼/토레스 실사고 재발 방지) — 불일치면 소재 고정 재작성 1회
        _subj_state = ""
        _subj = seo.subject_match(text, asset.note or "", (kws[0] if kws else ""))
        if _subj is False:
            try:   # 빈 응답 예외로 '멀쩡한 초안'까지 폐기되지 않게(검토 지적)
                _re2 = _llm.call_task(
                    "caption", self._prompt(tenant, asset, len(imgs), kws)
                    + "\n[재작성 — 소재 고정] 직전 초안이 사진 분석에 없는 차종·제품을 실물처럼 서술해 폐기됐다. "
                      "이번 소재는 위 [사진N] 분석에서 확인되는 것만이다. 사진과 다른 차종·모델명은 언급 자체를 "
                      "하지 마라.", 1200, default_model=self.model, cache_prefix=cache_prefix_for(asset))
            except Exception:
                _re2 = ""
            if (_re2 or "").strip():
                text = _re2
            _subj = seo.subject_match(text, asset.note or "", (kws[0] if kws else ""))
            _subj_state = "retried_ok" if _subj is not False else "miss"
        elif _subj is True:
            _subj_state = "ok"
        # LLM이 이미 넣었으면 중복 삽입하지 않음. 해시태그 앞에 배치.
        if text and "저장" not in text:
            cta = seo.save_share_line("instagram")
            m = __import__("re").search(r"\n\s*#", text)
            text = (text[:m.start()] + "\n\n" + cta + text[m.start():]) if m else (text.rstrip() + "\n\n" + cta)
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.INSTAGRAM, kind=self.kind,
            payload={"text": text, "image_path": imgs[0], "image_paths": imgs,
                     "target_keywords": kws, "llm_route": _cap_route,
                     "subject_check": _subj_state},
            status=ContentStatus.DRAFT)


class BlogDraftGenerator(Generator):
    """네이버 블로그 SEO 구조화 초안(제목/메타/본문/이미지배치/키워드). 반자동(사람 발행)."""
    kind = ContentKind.BLOG

    def __init__(self, model: str = MODEL):
        self.model = model

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        imgs = images or [asset.path]
        imgs = _select_slot_photos(imgs, asset.note or "")   # 슬롯 선별(권장 초과분은 뒤로 — 그리드·ZIP 전용)
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        kplan = seo.keyword_plan(prof.name, tenant.region, asset.note,
                                 axis=strat.keyword_axis, brand=tenant.brand_name)   # 대표+롱테일(PHASE 6)
        buy = buy_block(tenant)
        _ctype = (getattr(asset, "content_type", "sell") or "sell")
        _biz_g = (getattr(tenant, "biz_type", "local") or "local")
        tkw = (getattr(asset, "target_kw", "") or "").strip()   # 진단 유래 미노출 키워드(있으면 대표)
        # ★ 키워드 결정 = 공유 관문 seo.resolve_target_keyword(전 생성기 공통) — phantom 필터·앵커 게이트·
        #   검색량·기초지역 배제 일괄. 생성기별 자체 결정 금지(3번째 재발 근본책 + SHORT/캐스퍼 계보 차단).
        kw0, kws = seo.resolve_target_keyword(
            industry=(getattr(tenant, "industry", "") or prof.name), region=tenant.region or "",
            note=asset.note or "", biz=_biz_g, content_type=_ctype, brand=tenant.brand_name or "",
            keyword_axis=strat.keyword_axis, target_kw_override=tkw, tenant_id=tenant.id, prof_name=prof.name)
        # 🏔 헤드 빌드업(계층 공략): '부산 기장 중고차'의 부모('부산 중고차') 정확 구문을 글에 심어
        #   헤드 키워드 형태소·스마트블록 진입 재료 확보. kws 편입 → 태그·순위 추적 자동 편승.
        _parent_kw = ""
        try:
            _parent_kw = seo.parent_keyword(kw0, tenant.region or "",
                                            getattr(tenant, "address", "") or "")
            if _parent_kw:                              # kw0 바로 뒤(뒤에 붙이면 10개 캡에 잘림 — 실측)
                _rest = [k for k in kws if k not in (kw0, _parent_kw)]
                kws = list(dict.fromkeys([kw0, _parent_kw] + _rest))[:10]
        except Exception:
            _parent_kw = ""
        # 📏 가변 분량(상위 블로거 실전 검증: 경쟁 키워드는 3천자급이 표준) — 검색량 실측으로 판정.
        #   롱테일(기본) 1,500~2,200자 / 월 3,000회+ 경쟁 키워드 2,500~3,500자. 무키·실패 시 기본.
        _target_len, _len_competitive = "1500~2200", False
        try:
            from app.services import searchad as _sa_len
            if _sa_len.configured():
                _v0 = next((int(r.get("total") or 0) for r in _sa_len.keyword_volumes([kw0])
                            if (r.get("keyword") or "").replace(" ", "") == kw0.replace(" ", "")), 0)
                if _v0 >= 3000:
                    _target_len, _len_competitive = "2500~3500", True
        except Exception:
            pass
        if tkw:
            kplan["longtail"] = []      # 1글 1키워드(자동 글감 큐): 타깃 외 키워드 소제목 헤딩화 금지
        # ★ canonical_region — 지역 토큰 단일 소스(검색량 실측 + 기초지역 배제).
        try:
            from app.services import indschema as _iscr
            _hookr = _iscr.get_schema(getattr(tenant, "industry", ""), _biz_g).get("allow_region_hook")
        except Exception:
            _hookr = None
        _creg = seo.canonical_region(tenant.region or "", _biz_g,
                                     (getattr(tenant, "industry", "") or prof.name), allow_region_hook=_hookr)
        _reg_txt = _creg or "전국"                        # 프롬프트 표기용(셀러=전국)
        _title_reg = (f"지역명은 '{_creg}'만 쓰고 구·군 등 기초지역 지명은 제목에 넣지 마라."
                      if _creg else "제목에 지역 지명을 넣지 마라(전국 대상).")
        if strat.closing == "buy":
            closing = ("[마무리] 글 끝은 '구매 유도'로. 상세페이지/스토어로 자연스럽게 연결하고 찜·후기를 권하라."
                       + (f" 구매 안내 문구: {buy}" if buy else ""))
        elif strat.closing == "both":
            place = (f" 네이버 지도: {tenant.map_url}" if getattr(tenant, "map_url", "") else "")
            closing = ("[마무리] 가까운 손님은 매장 방문(찾아오는길·연락처) + "
                       f"'네이버에서 \"{tenant.name}\" 검색 → 플레이스 찜·예약', 먼 손님은 온라인 구매로 안내."
                       + (f" 구매 안내: {buy}" if buy else "") + place)
        else:
            # 고정정보(주소·전화·영업시간·주차·지도)는 템플릿이 자동 삽입 — LLM은 행동 유도만(블로그템플릿 PHASE 2)
            _reg_line = (f"본문에서 업체명은 반드시 '{tenant.name}', 지역은 '{_creg}'으로 일관 표기(플레이스 등록정보 일치 = 신뢰 신호). 기초지역(구·군) 지명은 쓰지 마라."
                         if _creg else f"본문에서 업체명은 '{tenant.name}'으로 일관 표기하고, 지역 지명은 넣지 마라(전국 대상).")
            closing = ("[마무리] 글 끝은 방문 유도 한두 문장으로만 마쳐라. 주소·전화·영업시간·지도 링크는 "
                       "시스템이 자동 삽입하니 본문에 쓰지 마라(중복 금지). "
                       f"'네이버에서 \"{tenant.name}\" 검색 → 플레이스 저장·방문자리뷰·예약' 행동 유도는 좋다"
                       "(저장·리뷰·예약은 플레이스 순위의 핵심 신호). " + _reg_line)
        # 기계 티 방지(2026-07-27): 도입 스타일 시드 회전 + 최근 글 첫 문장 반복 금지(데이터 기반)
        _hook = _hook_style(asset.id)
        _recent_open = _recent_openers(tenant.id)
        # 🔬 상위 글 실측 기준선(캐시만 — 크롤 대기 0초, 없으면 백그라운드 예열) — 전 업종 공통
        _anat_line = ""
        _battle_meta: dict = {}
        try:
            from app.services import bloganatomy as _ba
            _anat_line = _ba.baseline_line(kw0)
            # 🗺 판 유형별 작전 지시서(2026-08-01) — 4신호를 글쓰기 작전으로(치열한 판=각도 전환,
            #   열린 판=속전속결·최신성, 상승 추세=시의성 톤). 신호 없으면 빈 문자열(기존 그대로).
            _bp_line, _battle_meta = _ba.battle_plan(kw0, tenant_id=getattr(tenant, 'id', '') or '')
            _anat_line = _anat_line + _bp_line
        except Exception:
            pass
        try:      # 🎯 질의별 답변 문단 규칙(2026-08-16 실측) — 게이트와 같은 모듈이 문장을 만든다.
            #   핵심(kw0)은 seo.resolve_target_keyword가 정한 canonical 값을 그대로 넘긴다.
            from app.services import answerblock as _abm
            _ab_plan = _abm.plan(kw0, kws)
            _ab_rule = _abm.prompt_rule(kws, core=kw0)
        except Exception:
            _ab_plan, _ab_rule = {}, ""
        prompt = (
            f"[가게] {tenant.name} (업종: {prof.name}, 지역: {_reg_txt})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n[업종 톤] {prof.tone}\n"
            f"{industry_brief(prof)}"
            f"[사진 {len(imgs)}장]\n"   # 입력정보(asset.note)는 캐시 프리픽스로 전달(track-A)"
            f"{seo.speaker_frame(strat.key)}\n"
            f"{seo.keywords_line(kws)}\n{closing}\n\n"
            f"{_tpl_sequence(tenant)}\n"
            f"{seo.BLOG_DIRECTIVES}\n{seo.BLOG_SELL_STRUCT}\n{seo.RETENTION_DENSITY}\n{seo.MOBILE_SPEC}\n{seo.COPY_PSYCH}\n{seo.FACTS_RULE}\n{seo.HUMAN_TOUCH}\n"
            + seo.geo_directive(getattr(tenant, "biz_type", "local") or "local", tenant.name, prof.name,
                                _creg, getattr(tenant, "brand_name", "") or "",
                                seo.geo_questions(prof.name, _creg, getattr(prof, "pain_points", "")))
            + (seo.blog_angle_directive(getattr(asset, "angle", "")) + "\n"
               if getattr(asset, "angle", "") else "")
            + "[실경험 강화 · D.I.A.+ 핵심] 위 '사진 분석'의 구체 사실(색·질감·전후 변화·차종/제품·수치)을 "
            "1인칭 경험담('직접 해보니','만져보니','시공하고 나니')으로 녹여라. 추상적 미사여구·일반론 금지, 손에 잡히듯 구체적으로.\n"
            # 체류 설계(2026 상위 유지 = 평균 체류 2.5~3분, 상위 블로거 실전 방법론 검증) — 업종 무관 공통 장치
            + "[체류 설계 — 3장치 필수] ① 첫 문장: 이 키워드를 검색한 사람이 가장 걱정·궁금해하는 것을 짚고 "
            "'이 글이 그 답을 직접 보여준다'는 예고로 시작(제목이 한 약속을 즉시 받아주기). "
            "② 서두(첫 문단 안이나 직후): '끝까지 보시면 ①… ②… ③…을 그대로 가져가실 수 있습니다'처럼 "
            "이 글에 실제로 있는 것만 3가지 예고. "
            "③ 본문 중간: '바로 아래에서 ○○을 직접 보여드립니다' 같은 다음 섹션 예고를 1~2회 — "
            "실제 뒤에 나오는 내용만(없는 것 예고 금지). "
            "★3장치 모두 정형 문구를 복사하지 말고 이 글의 소재·문체에 맞는 자연스러운 표현으로 변주하라.\n"
            + f"[이번 글 도입 스타일 — 필수] '{_hook[0]}': {_hook[1]} "
            "체류 설계 ①(답 예고)은 이 스타일 안에서 녹여라(스타일 따로 예고 따로 금지).\n"
            + (("[최근 글 첫 문장 — 반복 금지] 아래는 이 가게가 최근 쓴 글의 시작들이다. "
                "같은 훅·비슷한 문장 구조·같은 상투구로 시작하면 안 된다(유사문서·기계 티):\n"
                + "\n".join(f"- {s}" for s in _recent_open) + "\n") if _recent_open else "")
            + _anat_line
            # ★ 사실 기반 필수 요소(2026-08-01 실측): '상호 미표기' -12점이 88점 천장을 만들고 있었다.
            #   표면 수선은 '새 정보 추가 금지' 원칙 때문에 못 고친다 → 생성 단계가 책임진다.
            #   날조가 아니라 우리가 아는 사실(가게 이름)이므로 자연스럽게 1회 이상 쓰면 된다.
            + f"[상호 표기 — 필수] 이 글 어딘가에 가게 이름 '{tenant.name}'을 그대로 1회 이상 자연스럽게 "
              "써라(예: 작업 주체를 밝히는 문장, 마무리 안내). 억지 반복·도배 금지, 정확한 표기 유지.\n"
            + "[필수 섹션] ① '## 자주 묻는 질문'(Q&A 정확히 3쌍) ② 가격대/영업시간/찾아오는길을 마크다운 표(| 항목 | 내용 |) 1개 "
            "③ '## 한눈 요약'(핵심 3줄 목록 — GEO).\n"
            # 🎯 2026-08-16 실측(남의 상위글 339개): 네이버는 글이 아니라 '문단'을 뽑아 노출한다.
            #   같은 글이 검색어에 따라 다른 대목을 요약으로 받았다(3업종 84%).
            #   업종 무관한 글 구조 규칙이라 어떤 업종에도 그대로 적용된다.
            + _ab_rule
            # ★ 채점기가 기계적으로 세는 항목은 처음부터 맞춘다(2026-08-01 실측). 이 네 가지가
            #   매 글 반복해서 깎였고(-8·-6·-5·-3), 뒤에서 재작성으로 되돌리느라 8분을 썼다.
            #   전부 업종·가게 무관한 '글 구조' 규칙이라 어떤 업종에도 그대로 적용된다.
            # ★ 읽는 사람 시점(2026-08-01 사장님 지적) — '중고차판매 가격 걱정?'은 파는 쪽 시점이라
            #   사는 손님이 읽으면 주어가 뒤집혀 있다. 검색어는 그대로 쓰되 문장은 손님 행동으로 쓴다.
            #   전 업종 공통(사는 손님·맡기는 손님·의뢰하는 손님 — 가게 업무 용어가 아니라 손님 행동어).
            + "[화자와 청자 — 헷갈리지 마라]\n"
              "★ 이 글을 쓰는 사람은 '가게 사장'이다. 손님에게 우리 물건·서비스를 보여주고 팔려고 쓰는 글이다. "
              "말투는 끝까지 가게 주인의 것이다(예: 저희가 직접 확인했습니다 / 보여드립니다 / 오시면 열어드립니다). "
              "손님이 쓴 사용기·구매 체험담처럼 쓰지 마라 — 우리는 파는 쪽이다.\n"
              "★ 다만 '손님의 고민'을 부를 때는 손님이 쓰는 말로 불러라. 손님은 사는 쪽이라 "
              "가게 업무 용어로 자기 고민을 부르지 않는다.\n"
              "  나쁜 예: '중고차판매 가격 걱정?'(손님은 파는 게 아니다) → 좋은 예: '중고차 구매 가격 걱정?'\n"
              "★ '후기'는 손님이 쓴 경험담을 뜻한다. 가게가 자기 물건을 소개하는 글 제목에는 쓰지 마라.\n"
              "★ 사전에 없는 조어를 만들지 마라 — '검수기·시공기' 같은 말은 아무도 안 쓴다.\n"
            + "[감점 방지 — 반드시 지켜라]\n"
              "① 사진·표·소제목 없이 글 문단이 5개 연속되면 안 된다. 문단 4개마다 [사진N]이나 '##' 소제목을 넣어 끊어라.\n"
              "② 도입 3문장 안에 '끝까지 읽을 이유'를 예고하라(예: '아래에서 …까지 보여드릴게요', '끝에 …를 정리해 뒀어요').\n"
              "③ 이모지는 글 전체에서 0~1개만. 소제목·목록에 이모지를 붙이지 마라.\n"
              "④ 같은 내용을 두 문단에서 되풀이하지 마라 — 앞에서 쓴 문장은 뒤에서 다시 쓰지 말고 새 정보만 담아라.\n"
              "⑤ '알아보겠습니다·살펴보겠습니다·도움이 되셨길·포스팅을 시작' 같은 빈 문장 금지 — 바로 본론.\n"
            + _kw_natural_directive(kw0, _creg)
            + (f"[상위 확장 키워드] '{_parent_kw}' — 이 정확 구문(연속 그대로)을 글에 1회 이상 담되 "
               "**자연스러운 자리에만**: ①검색 인용형(\"'" + _parent_kw + "' 검색하고 들어오셨다면\") "
               f"②명사구 두괄(\"{_parent_kw}, 어디에 맡길지 고민이라면\") ③'## 한눈 요약' 줄 안 ④제목 후보 1개. "
               f"★키워드를 동사에 그대로 붙인 어색한 문장 금지(나쁜 예: '{_parent_kw} 맡기실 때') — 그런 자리엔 "
               "조사를 넣은 자연형(예: '부산에서 썬팅 맡기실 때')을 쓰고, 정확 구문은 위 ①~④ 자리에서 채워라. "
               "본문 1~2회 추가도 같은 원칙(도배 금지). 소제목(##)에는 쓰지 마라(1글 1키워드 유지).\n"
               if _parent_kw else "")
            + "[입력 원문 노출 금지] 업종/키워드 입력이 '썬팅,광택'처럼 쉼표 나열형이면 제목·본문에 원문 그대로 "
            "박지 말고 자연어로 풀어 써라(예: '썬팅과 광택', '썬팅·광택 시공').\n"
            + (f"[연관 표현] '{', '.join(kplan['longtail'])}' 는 본문 문장 속에 자연스럽게 1회씩만 스치게 써라 — "
               "소제목(##)으로 만들지 마라(1글 1키워드 원칙).\n" if kplan.get("longtail") else "")
            + f"[1글 1키워드] 이 글의 소제목(##)은 오직 '{kw0}'의 검색 의도만 다룬다. "
            "다른 추적 키워드를 소제목으로 세우지 마라.\n"
            + f"사진 {len(imgs)}장 → 각 [사진N]을 **그 사진이 보여주는 내용(위 입력정보의 [사진N] 분석)을 다루는 문단 옆에** "
            "배치하라 — 예: 작업/시공 문단엔 작업 사진, 서류·점검 문단엔 서류 사진, 완성품·메뉴 문단엔 그 실물 사진"
            "(업종 무관 — 사진 분석의 내용과 문단 주제를 맞추는 것이 원칙). "
            "★업로드 순서대로 기계적 나열 금지. 대표 외관(전면/전체) 컷은 글 앞부분에 먼저. "
            f"각 [사진1]..[사진{len(imgs)}]을 한 번씩(한 줄 단독), 위치는 내용에 맞게.\n"
            # ★ 2026-08-04 실물 사고: 본문이 "사진13은 짙은 회색 도어 패널을…"이라고 썼는데
            #   그 자리엔 다른 사진이 있었다(3건). 우리는 LLM의 마커 배치를 신뢰하지 않고
            #   어절 겹침으로 다시 옮긴다 — 그러면 산문에 박힌 번호는 반드시 어긋난다.
            #   확률이 아니라 구조다. 사진 한 장의 설명은 캡션의 일이고, 본문은 주제를 쓴다.
            + "[사진 지칭 금지] 본문 **문장 안에서** 사진을 번호로 부르지 마라 — "
              "'사진3은', '사진 13이' 모두 금지다. 번호는 [사진N] 마커에만 쓴다. "
              "문장에서 가리켜야 하면 '아래 사진', '이 장면'처럼 번호 없이 쓴다.\n"
            # ★ 이미지 검색 유입(2026-08-01 실측: 이미지탭 상위 50에 우리 사진 0건) — 네이버 이미지
            #   검색은 '사진 주변 문맥'을 크게 본다. 파일명 SEO만으로는 부족. 업종·업태 공통 원칙.
            # ★ 2026-08-04: '사진 옆 문장에 그 사진 설명을 써라'는 요구를 폐기했다.
            #   우리는 마커를 어절 겹침으로 다시 배치한다 — 옆 문장이 어떤 사진 옆에 남을지
            #   LLM은 알 수 없다. 지킬 수 없는 요구를 시키면 '위 사진이 …입니다'가 틀린 사진을
            #   가리킨다(실물 2건). 사진 한 장의 설명은 캡션이 맡고, 본문은 주제를 쓴다.
            + f"[이미지 검색 노출 — 전 업종 공통] 사진은 '검색되는 자산'이다. 사진이 놓일 문단은 "
              f"그 사진이 다루는 주제를 구체적으로 서술하고, 최소 2곳에서는 '{kw0}'의 "
              "자연 변형이 문장 안에 자연스럽게 들어가게 하라. "
              "★ 단, 특정 사진을 가리켜 설명하지 마라 — '위 사진은', '아래 장면이', '이 사진에서'는 "
              "모두 금지다. 사진 위치는 글이 완성된 뒤 내용에 맞춰 다시 배치되므로 가리키면 어긋난다. "
              "★키워드 나열·캡션 남발 금지 — 읽는 사람에게 필요한 설명이 우선이고, 검색어는 그 안에 "
              "자연스럽게 담길 때만 효과가 있다(억지 삽입은 저품질 신호).\n\n"
            "아래 형식 그대로(대괄호 머리표 유지) 출력:\n"
            f"[제목후보]\n(3줄. 각 줄 '{kw0}'를 제목 앞부분(첫 12자 안)에 자연스럽게 포함 — 키워드를 기계적으로 "
            "맨 앞에 박지 말고, 어색하면 어순·조사를 바꿔 자연스러운 한국어 문장을 우선하라. "
            # ★ 제목도 화자를 지킨다(2026-08-12 사장님 지적) — 본문엔 화자 규칙이 있는데 제목에 없어서
            #   '중고차 후기 … 직접 검수'처럼 손님(산 사람) 말투 제목이 나왔다. 우리는 파는 쪽이다.
            f"서로 다른 각도(정보형/근거형/혜택형), 22~35자 롱테일, 숫자·혜택으로 클릭 유도. "
            "★제목 화자 = 가게 사장(파는 쪽)이다. 손님이 산 뒤 쓰는 말투('내돈내산·사용기·"
            "직접 써보니·구매기·솔직후기')로 제목을 쓰지 마라 — 우리 물건을 소개·공개하는 말"
            "('공개·정리·따져보기·기록·안내')로 쓴다. 우리가 직접 한 시공·검수·점검을 보여주는 "
            f"표현은 허용. 검색어에 '후기'가 들어 있으면 키워드는 그대로 살리되, 문장은 "
            "'우리가 보여주는 기록'으로 읽히게 하라(손님이 쓴 체험담처럼 읽히면 실패). "
            f"{_title_reg})\n"
            "[메타설명]\n(150자 내외, 클릭 유도)\n"
            f"[본문]\n(첫 문장에 '{seo._kw_shorten(kw0)}' 같은 자연 변형 포함(원형 금지), "
            # 📷 사진 부수물 배제(2026-08-03 사장님 지적) — 사진에 우연히 담긴 것은 글감이 아니다.
            + ("[사진에서 쓰지 말 것] 시계에 찍힌 시각, 바닥·벽·조명 같은 배경, 촬영 환경은 "
               "본문에 쓰지 마라. 손님이 사는 것과 무관하다. 사진에서는 '상품·시공·상태'만 가져와라. ")
            # 🏷 입력 식별자 강제(2026-08-03 실사고) — 사장님이 준 모델명이 본문에서 사라졌다.
            + (f"[반드시 그대로 쓸 말] 입력에 있는 {', '.join(seo.input_anchors(asset.note or ''))} — "
               "본문에 최소 1회 그대로 써라. '신차 한 대'처럼 뭉개지 마라. 차종·등급명은 "
               "손님이 검색하는 말이자 믿을 근거다. 단, 입력에 없는 모델명은 절대 지어내지 마라, "
               if seo.input_anchors(asset.note or "") else "")
            + (f"첫 문단에 '{_parent_kw}' 정확 구문 1회 — 단 [상위 확장 키워드]의 자연 프레임(①~④)으로만"
               "(동사 직결 금지, 어색하면 조사 넣은 자연형 + 요약줄에서 정확 구문 충족), " if _parent_kw else "")
            + "## 소제목 3~5개 + 마크다운 표 1개 + '## 자주 묻는 질문'(Q&A 3쌍), "
            f"{_target_len}자, [사진N] 마커 배치)\n"
            "[이미지배치]\n(- 각 사진을 어디에 왜)\n"
            "[키워드]\n(쉼표로 5~8개, 타겟 키워드 우선)"
        )
        _exp = []
        if _ctype == "info":
            # 트랙 B — 정보성 글(GEO 구조 강제). 트랙 A 프롬프트를 대체(훅-후답 구조 미사용).
            # 사장 실경험 Q&A 주입 — 본문 핵심 단락에 반영 강제(G6 게이트가 검증). 경험은 asset 또는 DB에서.
            from app.services import geo_track as _geo
            from app import db as _dbe
            _exp = getattr(asset, "owner_experience", None) or _dbe.list_owner_experience(tenant.id)
            _trust = _geo._author_trust(tenant, asset.note or "")
            prompt = _geo.info_prompt(tenant, prof.name, tenant.region or "", kw0,
                                      getattr(asset, "angle", "howto") or "howto",
                                      asset.note or "", len(imgs),
                                      trust=_trust, experiences=_exp)
        raw = _call_llm(prompt, self.model,
                        7500 if _len_competitive else (5500 if _ctype == "info" else 5000),
                        cache_prefix=(cache_prefix_for(asset) if _ctype != "info" else ""))
        _body_finish = _last_finish()   # ★ 본문 호출 '직후' 절단 기록(2026-07-31 실사고: 뒤의 소형
        #   판단 호출 stop_reason이 덮어써 '본문 미완결' 오탐 → 채점 감점·재작성 루프 유발)
        d = _parse_sections(raw, ["제목후보", "제목", "메타설명", "본문", "이미지배치", "키워드"])
        # ① 제목 3안 → 상위노출 최적 1개 자동 선택 ([제목]으로 준 경우도 흡수)
        title_cands = [t.strip().lstrip("-*·0123456789.) ").strip()
                       for t in ((d.get("제목후보") or d.get("제목") or "")).split("\n") if t.strip()]
        _body_for_pick = d.get("본문") or raw
        title, _pick_why = _pick_title(title_cands, kw0, _body_for_pick)
        title = title or (title_cands[0] if title_cands else (d.get("제목") or "제목 [기입필요]"))
        parsed = [k.strip().lstrip("#") for k in (d.get("키워드", "")).replace("\n", ",").split(",") if k.strip()]
        # 파싱된 키워드 + 타겟 키워드 병합(중복 제거)
        tags = list(dict.fromkeys(parsed + kws))[:10]
        # 발현률 게이트: 체류 3장치(첫 문장 답 예고·①②③ 예고·이정표)를 기계 검사, 누락분만 보충
        # — 프롬프트 지시는 확률, 게이트는 보장(제목·FAQ 보강과 동일 패턴)
        _body_raw, _dwell_rep = _ensure_dwell_devices(d.get("본문") or raw, kw0)
        # 글-사진 의미 매칭: LLM 마커 배치 대신 사진 설명↔문단 어절 겹침으로 결정적 재배치(레이트리밋 무관)
        body = _semantic_photo_placement(_body_raw, asset.note or "", len(imgs))
        # 셀러: 본문 끝에 구매 블록 보강(누락 대비) — 트랙 B 정보성 글은 상업 블록 제외(정보 순수성)
        if _ctype != "info" and strat.closing in ("buy", "both") and buy and buy not in body:
            body = body.rstrip() + "\n\n" + buy
        # 매장(local/hybrid): 글 끝에 고정정보 블록 자동 삽입(블로그템플릿 PHASE 2)
        # 지도는 텍스트 URL 대신 [여기 네이버 지도 넣기] 마커 — 발행 화면에서 장소 컴포넌트 가이드(PHASE 3)
        fixed_block = ""
        if (getattr(tenant, "biz_type", "local") or "local") in ("local", "hybrid") and "찾아오는 길" not in body:
            from app.services import blogtpl
            fixed_block = blogtpl.fixed_info_block(tenant)
            body = body.rstrip() + "\n\n" + fixed_block
        # (자동화 2-3b) 내부링크 자동 삽입 — 같은 주제 축의 '발행 확인된' 내 글 1~2개를 본문 끝
        # 문단으로 포함(주제 응집도 = C-Rank 신호). 기존 발행 글 없는 가게는 문단 생략(날조 금지).
        try:
            from app import db as _dbl
            _kw_toks = {w for w in seo._kw_shorten(kw0).split() if len(w) >= 2}
            _rel = []
            for _pub in _dbl.list_blog_publishes(tenant.id, limit=15):
                _t = (_pub.get("post_title") or "").strip()
                _u = (_pub.get("published_url") or "").strip().split("?")[0]   # RSS 추적 파라미터 제거(복붙 청결)
                if _t and _u and any(w in _t for w in _kw_toks):
                    _rel.append((_t, _u))
                if len(_rel) >= 2:
                    break
            if _rel:
                # 블록은 한 곳에서만 만든다 — 클릭되는 형태(URL 줄 단독)를 두 경로가 공유한다
                from app.services.blogsync import related_links_block as _rlb
                _blk = _rlb([{"title": t, "url": u} for t, u in _rel])
                if _blk:
                    body = body.rstrip() + "\n\n" + _blk
        except Exception:
            pass
        # ③ FAQ 섹션 누락 대비 최소 보강(스마트블록·체류 신호)
        if "자주 묻는 질문" not in body and "자주묻는" not in body:
            body = body.rstrip() + (
                "\n\n## 자주 묻는 질문\n"
                f"Q. {kw0} 예약이나 문의는 어떻게 하나요?\n"
                f"A. 네이버에서 '{tenant.name}' 검색 후 플레이스에서 예약·문의하시면 가장 빠릅니다.\n"
                f"Q. {prof.name} 상담도 가능한가요?\n"
                "A. 네, 방문 전 연락 주시면 상황에 맞게 안내해 드립니다.")
        # ④ 키워드 밀도 검증
        kdens = _kw_density(body, kw0)
        # ⑤ '꼭 반영할 요청' 셀프체크 1회(폼사실 게이트 1-3d) — 미반영이면 게이트가 감점
        request_check = ""
        _rq = re.search(r"\[반드시 반영할 요청\]\s*([^\n]+)", asset.note or "")
        if _rq and __import__("os").environ.get("ANTHROPIC_API_KEY"):
            try:
                _v = _call_llm("사용자 요청이 아래 글에 반영됐는지만 판단해 YES 또는 NO 한 단어로 답하라.\n"
                               f"요청: {_rq.group(1).strip()}\n글 제목: {title}\n글 앞부분:\n{body[:900]}",
                               "claude-haiku-4-5-20251001", 400)
                request_check = "ok" if "YES" in (_v or "").upper() else "miss"
            except Exception:
                request_check = ""
        _subj_b = seo.subject_match(body[:1200], asset.note or "", kw0)   # 소재 정합 감사(블로그는 기록만)
        _win_rec = None                                # 🎲 승산 스코어(실패해도 생성 안 막음)
        try:
            from app.services import winscore as _wsc
            _win_rec = _wsc.score(tenant.id, kw0)
        except Exception:
            pass
        markers = [{"marker": f"[사진{i+1}]", "image_index": i, "image_path": p}
                   for i, p in enumerate(imgs)]
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.NAVER_BLOG, kind=self.kind,
            payload={"title": seo.natural_kr_number(title),
                     "title_options": [seo.natural_kr_number(t) for t in (title_cands or [])],
                     "meta_description": d.get("메타설명", ""),
                     # 🔢 '5.7만km' 류 비한국어 표기 교정(2026-08-02 사장님 지적) — 표면 단일 규칙
                     "body": seo.natural_kr_number(body), "photo_markers": markers,
                     "recommended_image_placement": d.get("이미지배치", ""),
                     "tags": tags, "seo_keywords": tags, "target_keywords": kws,
                     # 🎯 노린 질의 구조[핵심 1 + 속성 2~3] — 발행 후 queryscout 실측과 대조해
                     #   '노린 질의'와 '실제로 잡힌 질의'를 채점하기 위한 기록(2026-08-16)
                     "query_plan": _ab_plan,
                     "keyword_density": kdens,
                     "biz_type": strat.key, "closing": strat.closing, "buy_block": buy,
                     "angle": getattr(asset, "angle", "") or "",
                     "target_kw": tkw,
                     "content_type": _ctype,               # sell=트랙A / info=트랙B(GEO)
                     "canonical_region": _creg,             # ★ 지역 토큰 단일 소스(전 표면·오염게이트 참조)
                     "owner_experience": _exp,              # 트랙B 실경험 Q&A(G6 게이트 검증용)
                     "citation_count": None,                # 3층 성과: AI 브리핑 인용수(캡처 판독으로 채움 — 자리 예약)
                     "business_name": tenant.name,      # 게이트 업체명 정합 검사용(재검증 STEP 1-2a)
                     "brand_name": getattr(tenant, "brand_name", "") or "",
                     "gen_finish": _body_finish,        # 본문 호출의 stop_reason(절단 검증 V1 — 오탐 수정 2026-07-31)
                     "title_pick": {"candidates": title_cands[:3], "picked": title,
                                    "why": _pick_why},          # 제목 3안 내부 선택 로그(CTR 4-2 — 유저 비노출)
                     "gen_source": (asset.note or "")[:8000],   # 입력 스냅샷 — [사진N] 전수 보존(kit 캡션·매칭 재사용, 재분석 0)
                     "request_check": request_check,            # '꼭 반영할 요청' 셀프체크(1-3d)
                     "dwell_gate": _dwell_rep,                  # 체류 장치 발현률 게이트 감사 기록
                     "battle_plan": _battle_meta,               # 판 분석·작전(2026-08-01) 감사 기록
                     "win_score": _win_rec,                     # 🎲 쓰기 전 승산 실측(근거 포함)
                     "subject_check": ("" if _subj_b is None else ("ok" if _subj_b else "miss")),

                     "fixed_info_block": fixed_block,      # 발행 화면 컴포넌트 가이드용(템플릿 PHASE 2·3)
                     "raw": raw, "image_path": imgs[0], "image_paths": imgs},
            status=ContentStatus.DRAFT)


def _last_finish() -> str:
    """직전 LLM 호출의 stop_reason(절단 검증 V1) — 무키 더미 등은 빈 문자열."""
    try:
        from app import llm
        # ★ 스레드별 값을 우선 읽는다(2026-08-01 검토 지적) — 채널 병렬 생성에서 전역 하나를
        #   공유하면 X·캡션의 절단이 본문 기록으로 새어 멀쩡한 글이 -15점을 먹는다.
        return llm.last_finish()
    except Exception:
        return ""


SLOT_RECOMMENDED = 15   # 본문 슬롯 권장 상단 — 근거: 공식 D.I.A.+는 멀티미디어 가점만 공표(수치 미공표),
                        # 상위글 본문 실측은 크롤링 금지로 불가 → 하한 6(기존 D.I.A.+ 운영 근거) ~ 상단 15
                        # (로딩·이탈 리스크 보수값). 초과분은 슬롯에서 제외하고 키트 그리드·ZIP에 전량 포함.


def _select_slot_photos(imgs: list, analysis: str, cap: int = SLOT_RECOMMENDED) -> list:
    """(사진 제한 해제 1-3) 슬롯 초과분 자동 선별 — 유저에게 선택 요구 없음.
    선별 기준: ① vision 분석에 과정·전후·구체 피사체 묘사([사진N] 라인에 과정 키워드)가 있는 사진 우선
              ② 그 외는 업로드 순서 보존(사장님이 정한 순서 존중)
    반환: 선별본이 앞으로 오도록 재정렬된 전체 목록(마커 [사진1..cap]=선별, 나머지는 그리드·ZIP 전용)."""
    import re as _r
    if len(imgs) <= cap:
        return list(imgs)
    # ★ 2026-08-04: 업종어 목록(세척·재단·성형·코팅…)을 언어 규칙으로 바꿨다.
    #   저건 시공업 어휘라 빵집·미용실에서는 '과정 사진'을 하나도 못 고른다(업종 중립 조항).
    #   과정이란 '무엇을 하고 있는 장면'이다 — 관형형·진행 어미가 그 신호다.
    KEY = _r.compile(r"[가-힣](는|던)\s|[가-힣](는|던)$|중인|중이|하며|하면서")
    # ★ 묘사 파싱은 단일 파서만 쓴다(첫 매치는 헤더·라벨을 집는다 — 캡션 10회 재발 계열).
    from app.services import photodesc as _pdsc
    scored = []
    for i, p in enumerate(imgs):
        _d = _pdsc.best_line(analysis or "", i + 1)
        has_process = bool(_d and KEY.search(_d))
        scored.append((0 if has_process else 1, i, p))    # 과정 묘사 우선, 동순위는 순서 보존
    ordered = [p for _, _, p in sorted(scored)]
    return ordered[:cap] + [p for p in imgs if p not in ordered[:cap]]


def _kw_natural_directive(kw0: str, region: str) -> str:
    """키워드 자연 변형 지시(재검증 STEP 1-2b) — 원형은 제목 1회, 본문은 구어형 변형."""
    short = seo._kw_shorten(kw0)
    toks = short.split()
    ex = [f"'{short}'"]
    if len(toks) >= 2:
        ex.append(f"'{toks[0]}에서 {' '.join(toks[1:])}'")
        ex.append(f"'{toks[-1]} 맡기실 때' 같은 문장형")
    rshort = seo._kw_shorten(region or "")
    full_warn = (f" 행정구역 풀네임 대신 '{rshort}'처럼 구어형으로 쓰고, 풀네임은 본문 2회 이하."
                 if rshort and rshort != (region or "") else "")
    return (f"[키워드 자연 변형] 타깃 키워드 '{kw0}' 원형은 제목에서만 정확히 1회. "
            f"본문·소제목에서는 원형을 그대로 반복하지 말고 자연 변형으로 풀어 써라(예: {', '.join(ex)}). "
            f"변형 포함 노출은 3~5회(남발=저품질 추락), 반복 대신 유의어·연관어로 확장.{full_warn}\n")


def _tpl_sequence(tenant) -> str:
    """업종별 블로그 템플릿 시퀀스(블로그템플릿 PHASE 2) — 매장형/셀러형 자동분기 재사용."""
    try:
        from app.services import blogtpl
        return blogtpl.sequence_directive(getattr(tenant, "biz_type", "local") or "local")
    except Exception:
        return ""


# 대표 컷 신호 — 촬영 각도·범위를 뜻하는 말만(업종어 금지: '차량 전체'는 뺐다 2026-08-04)
_HERO_HINT = ("외관", "전면", "전체", "정면", "측면", "앞모습", "풀샷", "전경")


def _semantic_photo_placement(body: str, note: str, n: int) -> str:
    """글-사진 의미 매칭 — 각 사진을 '그 사진 내용과 가장 관련 깊은 문단' 뒤에 배치.
    LLM의 마커 배치(41% 신뢰)를 폐기하고, 사진 설명(analyze_all의 [사진N] 라인)의 핵심 어절이
    가장 많이 겹치는 문단에 결정적으로 배치한다 — API·레이트리밋 무관, 재현 가능.
    설명이 없으면 기존 순차 배치(_ensure_photo_markers)로 폴백."""
    import re
    if n <= 0:
        return re.sub(r"[ \t]*\[사진\d+\][ \t]*\n?", "", body)
    # 1) 사진별 설명 파싱([사진N] <설명> — 다음 [사진 또는 줄바꿈까지)
    # ★ 묘사 파싱은 단일 파서만 쓴다(2026-08-04) — setdefault는 '첫 매치'라 헤더를 집는다.
    #   같은 재료를 읽는 소비자가 둘 이상이면 파서를 하나로(조항).
    from app.services import photodesc as _pdsc
    descs: dict[int, str] = {k: v for k, v in _pdsc.desc_map(note or "", n).items() if v}
    if len(descs) < max(2, n // 2):          # 설명 태부족 → 기존 로직(날조 대신 순차)
        import logging
        logging.getLogger("shopcast.gen").warning(
            "[배치] 묘사 %d/%d — 의미 배치 포기, 순차 배치로 폴백", len(descs), n)
        return _ensure_photo_markers(body, n)
    # 2) 기존 마커 제거 + 문단 분할(빈줄 기준; 소제목도 개별 문단)
    clean = re.sub(r"[ \t]*\[사진\d+\][ \t]*\n?", "", body).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    if len(paras) < 2:
        return _ensure_photo_markers(body, n)

    def _toks(s: str) -> set:                # 길이2+ 한글/영숫자 토큰(핵심어)
        return {t for t in re.split(r"[^가-힣A-Za-z0-9]+", s or "") if len(t) >= 2}

    ptoks = {i: _toks(descs.get(i, "")) for i in range(1, n + 1)}
    jtoks = [_toks(p) for p in paras]
    used = [0] * len(paras)                   # 문단별 배정 수(과밀 방지)
    # ★ 금지 구역(실측 결함 수정: 요약·FAQ 사이에 사진이 꽂히고 서두에 6장 뭉텅이):
    #   소제목 단독·요약·FAQ·표·목록·고정정보·지도 마커 문단엔 사진 배정 금지 + 문단당 최대 2장.
    _FORBID = ("한눈 요약", "자주 묻는", "찾아오는 길", "함께 보면 좋은", "[여기 네이버")
    MAX_PER = 2                                # 허용 문단 수를 센 뒤 아래에서 다시 계산한다

    def _allowed(j: int) -> bool:
        p = paras[j]
        if p.startswith(("|", "- ", "**Q", "Q.", "A.", "📍")):
            return False
        if p.startswith("#") and "\n" not in p:        # 소제목 단독 문단 — 사진은 내용 문단 뒤로
            return False
        return not any(f in p for f in _FORBID)
    allowed_idx = [j for j in range(len(paras)) if _allowed(j)]
    if not allowed_idx:
        return _ensure_photo_markers(body, n)
    # ★ 2026-08-04 실물: 20장 중 9장이 도입부에 연달아 붙었다. 상한은 있었지만
    #   허용 문단이 사진 수보다 적으면 폴백이 상한을 무시하고(or allowed_idx) 앞으로 몰았다.
    #   상한은 고정값이 아니라 '사진 수 ÷ 담을 문단 수'다 — 그래야 어떤 글 길이에서도 고르게 퍼진다.
    MAX_PER = max(2, -(-n // len(allowed_idx)))
    assign: dict[int, int] = {}
    # 정보량 많은 사진부터 배정(강한 신호 우선 선점)
    order = sorted(range(1, n + 1), key=lambda i: -len(ptoks.get(i) or set()))
    for i in order:
        pt = ptoks.get(i) or set()
        is_hero = any(h in (descs.get(i, "")) for h in _HERO_HINT)
        best, best_score = None, -1e9
        for j in allowed_idx:
            if used[j] >= MAX_PER:                    # 하드 상한 — 뭉텅이 원천 차단
                continue
            overlap = len(pt & (jtoks[j]))
            score = overlap - used[j] * 0.6           # 과밀 문단 감점 → 고르게 분산
            if is_hero and j == allowed_idx[0]:
                score += 0.4                          # 대표(외관) 컷은 글 앞 문단 선호
            if score > best_score:
                best_score, best = score, j
        if best is None or best_score <= 0:           # 매칭 실패 → 가장 덜 찬 문단으로(앞쪽 몰림 금지)
            cand = [j for j in allowed_idx if used[j] < MAX_PER] or allowed_idx
            _tgt = int((i - 1) / max(1, n) * len(paras))
            best = min(cand, key=lambda j: (used[j], abs(j - _tgt)))
        assign[i] = best
        used[best] += 1
    # 3) 재조립 — 각 문단 뒤에 배정된 사진 마커(사진번호 오름차순)
    by_para: dict[int, list] = {}
    for i, j in assign.items():
        by_para.setdefault(j, []).append(i)
    out: list[str] = []
    for j, para in enumerate(paras):
        out.append(para)
        for i in sorted(by_para.get(j, [])):
            out.append(f"[사진{i}]")
    result = "\n\n".join(out)
    return _ensure_photo_markers(result, n)           # 최종 무결성(중복·누락) 보정


def _ensure_photo_markers(body: str, n: int) -> str:
    """본문에 [사진1]..[사진n] 마커가 정확히 있도록 보장. 부족=재배치, 초과=빈 슬롯이라 제거."""
    import re
    if n <= 0:
        return re.sub(r"[ \t]*\[사진\d+\][ \t]*", "", body)
    _seen: set = set()

    def _keep(m):
        i = int(m.group(1))
        if i > n or i in _seen:              # 사진 수 초과·중복 마커 = 빈 슬롯 → 제거
            return ""
        _seen.add(i)
        return m.group(0)

    body = re.sub(r"[ \t]*\[사진(\d+)\][ \t]*", _keep, body)
    present = [i for i in range(1, n + 1) if f"[사진{i}]" in body]
    if len(present) >= n:
        return body
    # 마커가 부족하면 기존 마커 제거 후 재배치(순서·중복 보장)
    clean = re.sub(r"\[사진\d+\]", "", body)
    paras = [p.strip() for p in clean.split("\n\n") if p.strip()]
    if not paras:
        return "\n\n".join(f"[사진{i+1}]" for i in range(n))
    out = [f"[사진1]"]                       # 첫 사진은 맨 위
    remaining = n - 1
    # 남은 마커를 문단들 사이에 고르게
    slots = len(paras)
    # ★ 2026-08-04: 옛 방식은 step으로 띄엄띄엄 놓고 '남으면 끝에' 몰았다.
    #   문단보다 사진이 많으면 그 나머지가 통째로 한 곳에 붙는다 — 그게 뭉침이다.
    #   문단 진행 비율에 맞춰 그때까지 놓여야 할 수만큼 놓는다(어느 곳도 몰리지 않는다).
    mi = 2
    for idx, p in enumerate(paras):
        out.append(p)
        want = 1 + round((idx + 1) * remaining / max(1, slots))
        while mi <= n and mi <= want:
            out.append(f"[사진{mi}]")
            mi += 1
    while mi <= n:                          # 반올림 오차분만 — 최대 1장
        out.append(f"[사진{mi}]")
        mi += 1
    return "\n\n".join(out)


def _parse_sections(raw: str, headers: list[str]) -> dict:
    """[머리표] 기준으로 섹션 분리. 머리표 없으면 빈 dict(상위에서 raw 폴백)."""
    import re
    out: dict[str, str] = {}
    # 각 [헤더] 위치 찾기
    positions = []
    for h in headers:
        m = re.search(rf"\[{re.escape(h)}\]", raw)
        if m:
            positions.append((m.start(), m.end(), h))
    positions.sort()
    for i, (s, e, h) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(raw)
        out[h] = raw[e:end].strip()
    return out


def _call_llm(prompt: str, model: str = MODEL, max_tokens: int = 1200, cache_prefix: str = "") -> str:
    """공용 Claude 호출 — app.llm.call로 위임(리팩토링 #2, 동작 불변).
    9개 모듈이 이 이름을 역수입하므로 시그니처·이름은 유지(cache_prefix 기본값이라 하위호환)."""
    from app import llm
    return llm.call(prompt, model, max_tokens, cache_prefix=cache_prefix)


def cache_prefix_for(asset) -> str:
    """채널 공통 캐시 프리픽스 — 전 텍스트 생성기가 '동일 문자열'로 이 컨텍스트를 보내야 캐시 히트.
    asset.note(브리프+사진분석+지시)를 표준 라벨로 감싼다. 짧으면 llm이 자동으로 캐싱 안 함(무해)."""
    return f"[입력 정보(브리프·사진 분석 포함)]\n{asset.note or ''}\n"


class MarketplaceGenerator(Generator):
    """셀러 판매 플랫폼 콘텐츠 — 마켓 상품명(3안) + 상세페이지 + 검색 태그. 셀러 전용."""
    kind = ContentKind.MARKETPLACE

    def __init__(self, model: str = MODEL):
        self.model = model

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        imgs = images or [asset.path]
        prof = resolve_industry(tenant.industry)
        market_map = {"coupang": "쿠팡", "smartstore": "스마트스토어", "11st": "11번가",
                      "gmarket": "지마켓", "self": "자사몰"}
        mk = market_map.get(getattr(tenant, "marketplace", "") or "", "스마트스토어")
        brand = getattr(tenant, "brand_name", "") or tenant.name
        rules = {
            "쿠팡": "쿠팡 규칙: 상품명 최대 100자·핵심 검색키워드 맨 앞·[브랜드]+상품+속성+용도 순, 특수문자 최소. '로켓배송/무료배송' 등 정책문구 넣지 말 것.",
            "스마트스토어": "스마트스토어 규칙: 상품명은 검색키워드 자연 조합(같은 단어 반복 금지)·태그 10개 필수·상세는 이미지 설명+구매포인트 위주.",
            "11번가": "11번가 규칙: 상품명 키워드 앞배치·간결. 카테고리 명확히.",
            "지마켓": "지마켓 규칙: 상품명 키워드 앞배치·간결. 옵션/용도 명시.",
        }.get(mk, "상품명은 검색키워드를 맨 앞에·간결하게.")
        prompt = (
            f"[상품] {tenant.name} (브랜드: {brand}, 판매 마켓: {mk}, 카테고리: {prof.name})\n"
            f"[{mk} 최적화 규칙] {rules}\n\n"   # 입력정보(asset.note)는 캐시 프리픽스로 전달
            f"너는 오픈마켓({mk}) 상품명·상세페이지 SEO 최적화 전문가다. 위 마켓 규칙을 지켜 만들어라.\n"
            f"{seo.COPY_PSYCH}\n{seo.FACTS_RULE}\n"
            "특히 상세페이지 스펙·가격은 입력에 있는 것만 써라. 없는 성능/치수/가격을 채워넣지 마라(빈칸 유지).\n\n"
            "아래 형식 그대로(대괄호 머리표 유지) 출력:\n"
            "[상품명]\n(3줄. 각 줄 서로 다른 조합 — [브랜드]+핵심키워드+특징+용도 순, "
            "검색 키워드를 앞쪽에, 40~50자, 특수문자·중복 남발 금지)\n"
            "[상세페이지]\n(구매를 부르는 상세설명. ## 핵심 셀링포인트 3가지 · ## 이런 분께 추천 · "
            "## 상세 스펙(가능하면 표) · ## 자주 묻는 질문(Q&A 2~3) · 마지막 구매 유도 한 줄. 900~1400자)\n"
            "[요약본]\n(상세페이지 요약 — 핵심 소구점 딱 5줄. 각 줄 한 문장, 구매 결정 포인트만. "
            "썸네일·목록·SNS 소개에 바로 쓰는 용도)\n"
            "[스펙표]\n(입력에 있는 스펙만 '항목: 값' 형식 한 줄씩. 입력에 스펙이 없으면 "
            "'입력된 스펙 없음' 한 줄만 — 지어내기 금지)\n"
            "[태그]\n(쉼표로 10개, 마켓 검색 노출용 키워드 — 상품종류·용도·타겟·시즌 등)"
        )
        raw = _call_llm(prompt, self.model, 3000, cache_prefix=cache_prefix_for(asset))
        d = _parse_sections(raw, ["상품명", "상세페이지", "요약본", "스펙표", "태그"])
        names = [n.strip().lstrip("-*·0123456789.) ").strip()
                 for n in (d.get("상품명", "")).split("\n") if n.strip()][:3]
        tags = [t.strip().lstrip("#") for t in (d.get("태그", "")).replace("\n", ",").split(",") if t.strip()][:10]
        # 목록 마커('- ', '1. ')만 제거 — lstrip 문자셋은 '60L'의 숫자까지 벗겨 오파싱
        summary = [re.sub(r"^[\s\-\*·]*(?:\d+[.)]\s*)?", "", s).strip()
                   for s in (d.get("요약본", "")).split("\n") if s.strip()][:5]
        spec = (d.get("스펙표", "") or "").strip()
        if "입력된 스펙 없음" in spec:
            spec = ""                                    # 스펙 미입력 = 표 자체를 안 보여줌(날조 방지)
        # 리뷰 유도 키트 — 결정적 템플릿(LLM 미사용: 대가성 제안·날조 위험 원천 차단). 정당한 요청만.
        review_kit = [
            f"{brand}입니다. 받아보신 상품, 써보시고 솔직한 후기를 남겨주시면 다음 상품을 만드는 데 큰 힘이 됩니다.",
            "혹시 불편한 점이 있었다면 후기보다 먼저 문의로 알려주세요 — 바로 도와드릴게요.",
            "사진과 함께 남겨주시는 솔직한 사용 후기는 다른 구매자분들께 큰 도움이 됩니다. 내용과 무관하게 감사드려요.",
        ]
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.MARKETPLACE, kind=self.kind,
            payload={"product_names": names or [tenant.name],
                     "detail_body": d.get("상세페이지", "") or raw,
                     "detail_summary": summary, "spec_table": spec, "review_kit": review_kit,
                     "tags": tags, "market": mk, "brand": brand,
                     "buy_url": getattr(tenant, "buy_url", "") or "",
                     "search_kw": getattr(tenant, "search_kw", "") or "",
                     "raw": raw, "image_path": imgs[0], "image_paths": imgs},
            status=ContentStatus.DRAFT)
