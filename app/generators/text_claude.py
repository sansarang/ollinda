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
    ③ 키워드 앞배치 +5 / 포함 +2 (네이버 제목 가중치)
    ④ 구체성: 숫자·차종 등 구체 토큰 +2, 검색의도 단어(후기·방법·비용…) +1
    ⑤ 길이: 22~35자 +3 (30자 내외 최적)"""
    import re
    pool = [c.strip() for c in cands if c.strip() and len(c.strip()) >= 8]
    gated = [c for c in pool if (not kw0 or kw0 in c)]           # ① 게이트 선통과
    pool2 = gated or pool
    best, best_score, why = "", -999, ""
    for c in pool2:
        s, notes = 0, []
        if kw0 and c.startswith(kw0):
            s += 5; notes.append("키워드 맨앞")
        elif kw0 and kw0 in c:
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
        if re.search(r"^[^,]{2,12},\s", c) or c.count(",") >= 2:   # '추천, 부산 기장…' 쉼표 나열형 — 자연 문장형 우선
            s -= 3; notes.append("쉼표 나열(-)")
        if s > best_score:
            best, best_score, why = c, s, ", ".join(notes) or "기본"
    return best, f"{why} (점수 {best_score}, 후보 {len(pool)}·게이트 통과 {len(gated)})"


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
            f"[입력 정보] {asset.note}{carousel}\n[CTA] {strat.cta}{buy_line}\n"
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
        kws = seo.target_keywords(prof.name, tenant.region, asset.note,
                                  axis=strat.keyword_axis, brand=tenant.brand_name)
        from app import llm as _llm
        text = _llm.call_task("caption", self._prompt(tenant, asset, len(imgs), kws), 1200,
                              default_model=self.model)   # 인스타 캡션(이원화)
        _cap_route = dict(_llm.LAST_ROUTE.get("caption") or {})
        # 저장·공유 CTA 자동 삽입(영상강화 PHASE 5) — 저장·공유가 좋아요보다 3~5배 가중치.
        # LLM이 이미 넣었으면 중복 삽입하지 않음. 해시태그 앞에 배치.
        if text and "저장" not in text:
            cta = seo.save_share_line("instagram")
            m = __import__("re").search(r"\n\s*#", text)
            text = (text[:m.start()] + "\n\n" + cta + text[m.start():]) if m else (text.rstrip() + "\n\n" + cta)
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.INSTAGRAM, kind=self.kind,
            payload={"text": text, "image_path": imgs[0], "image_paths": imgs,
                     "target_keywords": kws, "llm_route": _cap_route},
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
        kws = seo.target_keywords(prof.name, tenant.region, asset.note,
                                  axis=strat.keyword_axis, brand=tenant.brand_name)
        kplan = seo.keyword_plan(prof.name, tenant.region, asset.note,
                                 axis=strat.keyword_axis, brand=tenant.brand_name)   # 대표+롱테일(PHASE 6)
        buy = buy_block(tenant)
        kw0 = kplan.get("headline") or (kws[0] if kws else prof.name)
        # 🎯 진단→생성 연결(상위노출 PHASE 1): 진단에서 고른 미노출 키워드가 있으면 그 키워드가 대표
        tkw = (getattr(asset, "target_kw", "") or "").strip()
        if tkw:
            kw0 = tkw
            kws = list(dict.fromkeys([tkw] + kws))[:10]
            kplan["longtail"] = []      # 1글 1키워드(자동 글감 큐): 타깃 외 키워드 소제목 헤딩화 금지
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
            closing = ("[마무리] 글 끝은 방문 유도 한두 문장으로만 마쳐라. 주소·전화·영업시간·지도 링크는 "
                       "시스템이 자동 삽입하니 본문에 쓰지 마라(중복 금지). "
                       f"'네이버에서 \"{tenant.name}\" 검색 → 플레이스 저장·방문자리뷰·예약' 행동 유도는 좋다"
                       "(저장·리뷰·예약은 플레이스 순위의 핵심 신호). "
                       f"본문에서 업체명은 반드시 '{tenant.name}', 지역은 '{tenant.region}'으로 일관 표기"
                       "(플레이스 등록정보와 일치 = 상호 신뢰 신호).")
        prompt = (
            f"[가게] {tenant.name} (업종: {prof.name}, 지역: {tenant.region})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n[업종 톤] {prof.tone}\n"
            f"{industry_brief(prof)}"
            f"[입력 정보(실제 사진 분석 포함)] {asset.note}\n[사진 {len(imgs)}장]\n"
            f"{seo.speaker_frame(strat.key)}\n"
            f"{seo.keywords_line(kws)}\n{closing}\n\n"
            f"{_tpl_sequence(tenant)}\n"
            f"{seo.BLOG_DIRECTIVES}\n{seo.BLOG_SELL_STRUCT}\n{seo.COPY_PSYCH}\n{seo.FACTS_RULE}\n{seo.HUMAN_TOUCH}\n"
            + seo.geo_directive(getattr(tenant, "biz_type", "local") or "local", tenant.name, prof.name,
                                tenant.region, getattr(tenant, "brand_name", "") or "",
                                seo.geo_questions(prof.name, tenant.region, getattr(prof, "pain_points", "")))
            + (seo.blog_angle_directive(getattr(asset, "angle", "")) + "\n"
               if getattr(asset, "angle", "") else "")
            + "[실경험 강화 · D.I.A.+ 핵심] 위 '사진 분석'의 구체 사실(색·질감·전후 변화·차종/제품·수치)을 "
            "1인칭 경험담('직접 해보니','만져보니','시공하고 나니')으로 녹여라. 추상적 미사여구·일반론 금지, 손에 잡히듯 구체적으로.\n"
            "[필수 섹션] ① '## 자주 묻는 질문'(Q&A 정확히 3쌍) ② 가격대/영업시간/찾아오는길을 마크다운 표(| 항목 | 내용 |) 1개 "
            "③ '## 한눈 요약'(핵심 3줄 목록 — GEO).\n"
            + _kw_natural_directive(kw0, tenant.region)
            + "[입력 원문 노출 금지] 업종/키워드 입력이 '썬팅,광택'처럼 쉼표 나열형이면 제목·본문에 원문 그대로 "
            "박지 말고 자연어로 풀어 써라(예: '썬팅과 광택', '썬팅·광택 시공').\n"
            + (f"[연관 표현] '{', '.join(kplan['longtail'])}' 는 본문 문장 속에 자연스럽게 1회씩만 스치게 써라 — "
               "소제목(##)으로 만들지 마라(1글 1키워드 원칙).\n" if kplan.get("longtail") else "")
            + f"[1글 1키워드] 이 글의 소제목(##)은 오직 '{kw0}'의 검색 의도만 다룬다. "
            "다른 추적 키워드를 소제목으로 세우지 마라.\n"
            + f"사진 {min(len(imgs), SLOT_RECOMMENDED)}장 → 본문 문단 사이에 [사진1]..[사진{min(len(imgs), SLOT_RECOMMENDED)}]를 순서대로 한 번씩(한 줄 단독) 배치.\n\n"
            "아래 형식 그대로(대괄호 머리표 유지) 출력:\n"
            f"[제목후보]\n(3줄. 각 줄 '{kw0}'를 맨 앞에 + 서로 다른 각도(후기형/정보형/혜택형), 22~35자 롱테일, 숫자·혜택으로 클릭 유도)\n"
            "[메타설명]\n(150자 내외, 클릭 유도)\n"
            f"[본문]\n(첫 문장에 '{seo._kw_shorten(kw0)}' 같은 자연 변형 포함(원형 금지), ## 소제목 3~5개 + 마크다운 표 1개 + '## 자주 묻는 질문'(Q&A 3쌍), "
            "1500~2200자, [사진N] 마커 배치)\n"
            "[이미지배치]\n(- 각 사진을 어디에 왜)\n"
            "[키워드]\n(쉼표로 5~8개, 타겟 키워드 우선)"
        )
        raw = _call_llm(prompt, self.model, 5000)
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
        body = _ensure_photo_markers(d.get("본문") or raw, min(len(imgs), SLOT_RECOMMENDED))
        # 셀러: 본문 끝에 구매 블록 보강(누락 대비)
        if strat.closing in ("buy", "both") and buy and buy not in body:
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
                _t, _u = (_pub.get("post_title") or "").strip(), (_pub.get("published_url") or "").strip()
                if _t and _u and any(w in _t for w in _kw_toks):
                    _rel.append((_t, _u))
                if len(_rel) >= 2:
                    break
            if _rel:
                body = body.rstrip() + "\n\n## 함께 보면 좋은 글\n" + "\n".join(
                    f"- {t} : {u}" for t, u in _rel)
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
                               self.model, 400)
                request_check = "ok" if "YES" in (_v or "").upper() else "miss"
            except Exception:
                request_check = ""
        markers = [{"marker": f"[사진{i+1}]", "image_index": i, "image_path": p}
                   for i, p in enumerate(imgs[:SLOT_RECOMMENDED])]
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.NAVER_BLOG, kind=self.kind,
            payload={"title": title,
                     "title_options": title_cands,
                     "meta_description": d.get("메타설명", ""),
                     "body": body, "photo_markers": markers,
                     "recommended_image_placement": d.get("이미지배치", ""),
                     "tags": tags, "seo_keywords": tags, "target_keywords": kws,
                     "keyword_density": kdens,
                     "biz_type": strat.key, "closing": strat.closing, "buy_block": buy,
                     "angle": getattr(asset, "angle", "") or "",
                     "target_kw": tkw,
                     "business_name": tenant.name,      # 게이트 업체명 정합 검사용(재검증 STEP 1-2a)
                     "brand_name": getattr(tenant, "brand_name", "") or "",
                     "gen_finish": _last_finish(),      # stop_reason 기록(절단 검증 V1)
                     "title_pick": {"candidates": title_cands[:3], "picked": title,
                                    "why": _pick_why},          # 제목 3안 내부 선택 로그(CTR 4-2 — 유저 비노출)
                     "gen_source": (asset.note or "")[:4000],   # 날조 대조용 입력 스냅샷(게이트 경로 폴백)
                     "request_check": request_check,            # '꼭 반영할 요청' 셀프체크(1-3d)
                     "fixed_info_block": fixed_block,      # 발행 화면 컴포넌트 가이드용(템플릿 PHASE 2·3)
                     "raw": raw, "image_path": imgs[0], "image_paths": imgs},
            status=ContentStatus.DRAFT)


def _last_finish() -> str:
    """직전 LLM 호출의 stop_reason(절단 검증 V1) — 무키 더미 등은 빈 문자열."""
    try:
        from app import llm
        return llm.last_finish_reason
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
    KEY = _r.compile(r"세척|재단|성형|시공|부착|제거|검수|전후|완성|마감|코팅|건조")
    scored = []
    for i, p in enumerate(imgs):
        m = _r.search(rf"\[사진{i + 1}\]\s*([^\n]+)", analysis or "")
        has_process = bool(m and KEY.search(m.group(1)))
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
    step = max(1, slots // max(remaining, 1)) if remaining else slots + 1
    mi = 2
    for idx, p in enumerate(paras):
        out.append(p)
        if mi <= n and (idx + 1) % step == 0:
            out.append(f"[사진{mi}]")
            mi += 1
    while mi <= n:                          # 남으면 끝에
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


def _call_llm(prompt: str, model: str = MODEL, max_tokens: int = 1200) -> str:
    """공용 Claude 호출 — app.llm.call로 위임(리팩토링 #2, 동작 불변).
    9개 모듈이 이 이름을 역수입하므로 시그니처·이름은 유지한다."""
    from app import llm
    return llm.call(prompt, model, max_tokens)


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
            f"[정보(사진 분석 포함)] {asset.note}\n"
            f"[{mk} 최적화 규칙] {rules}\n\n"
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
        raw = _call_llm(prompt, self.model, 3000)
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
