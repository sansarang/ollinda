"""
SEO/성과 엔진 — 플랫폼별 '잘 팔리고 잘 노출되는' 콘텐츠 설계 규칙.
- target_keywords: 지역+업종+검색의도 기반 타겟 키워드(LLM 없이도 결정적 생성).
- *_DIRECTIVES: 각 플랫폼 성과/SEO 베스트프랙티스(프롬프트에 주입).
이 규칙이 곧 제품의 '성과 차별화'. 키워드는 검색량 있는 롱테일을 노린다.
"""
from __future__ import annotations

import re

# 검색 의도 수식어(구매 직전 키워드 = 전환율 높음). 3어절 롱테일 = 경쟁↓·전환↑(검색량 500~5,000 구간 노림).
_INTENTS = ["추천", "후기", "가격", "비용", "잘하는곳", "예약", "위치", "실력"]


# 온라인 셀러용 구매 직전 검색 의도(상품축)
_PRODUCT_INTENTS = ["추천", "후기", "내돈내산", "사용기", "단점", "비교", "가성비"]


import time as _time

_vol_cache: dict = {}
_VOL_TTL = 24 * 3600   # 검색량은 월간 지표 → 24h TTL(프로세스 수명 lru_cache 대체, PHASE 6)


def _volume_boost_cached(hints_key: str) -> tuple:
    """네이버 검색광고 API로 실검색량 스윗스팟(500~5,000) 키워드(24h TTL 캐시). 무키/실패 시 빈 튜플."""
    now = _time.time()
    ent = _vol_cache.get(hints_key)
    if ent and (now - ent[0]) < _VOL_TTL:
        return ent[1]
    try:
        from app.services import searchad
        res = (tuple(searchad.sweet_spot_keywords([h for h in hints_key.split("|") if h]))
               if searchad.configured() else tuple())
    except Exception:
        res = tuple()
    _vol_cache[hints_key] = (now, res)
    return res


def _apply_volume(kws: list[str], limit: int, hints: list[str] | None = None) -> list[str]:
    """검색광고 API 있으면 실검색량 스윗스팟 키워드 2개를 보강(내 지역 키워드는 앞에 유지)."""
    seeds = [h for h in (hints or kws)[:3] if h]
    vol = _volume_boost_cached("|".join(seeds))
    if not vol:
        return kws[:limit]
    extra = [v for v in vol if v and v not in kws][:2]          # 실검색량 신규 키워드 2개
    keep = kws[:max(1, limit - len(extra))]                     # 내 키워드 우선(첫 키워드=지역 유지)
    return list(dict.fromkeys(keep + extra))[:limit]


def product_keywords(note: str = "", brand: str = "", limit: int = 10, industry: str = "",
                     region: str = "") -> list[str]:
    """상품/후기축 키워드 — 온라인 셀러용(지역 대신 상품명+구매의도).
    note의 지시/라벨 라인('['·'-'로 시작: intake 블록·브리프)은 제외 — '사장님 제공 실제' 같은
    라벨이 타겟 키워드로 새어 제목에 박히던 버그 수정. 자유 텍스트 명사가 없으면 업종/브랜드 폴백."""
    kws: list[str] = []
    free_text = "\n".join(
        ln for ln in (note or "").splitlines()
        if ln.strip() and not ln.strip().startswith(("[", "-", "→", "Q.", "A.", "#", "|")))
    nouns = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", free_text)
             if w not in ("추천", "이벤트", "할인", "후기") and len(w) <= 12]
    # 단어를 쪼개지 말고 '제품 구'로 — 전체 구 + 뒤 2단어(종류어)
    phrase = " ".join(nouns[:3]) if nouns else (industry.strip() or brand.strip())  # 예: "무선 블루투스 이어폰"
    short = " ".join(nouns[-2:]) if len(nouns) >= 2 else phrase       # 예: "이어폰 노이즈캔슬링"
    heads = [h for h in dict.fromkeys([phrase, short]) if h] or ([brand.strip()] if brand.strip() else [])
    if industry.strip() and industry.strip() not in heads:
        heads.append(industry.strip())                                # 업종(상품명)은 항상 후보에
    # 체급 보정(셀러·병행): '업종+추천' 류 전국 대형 키워드보다 지역·차종 롱테일을 앞에 —
    # 신규 블로그(추적 이력 없음)가 이길 수 있는 좁은 판부터(승률 산식엔 체급 로직이 없어 순서로 반영).
    reg2 = " ".join((region or "").split()[:2])                       # 예: '부산광역시 기장군' → 다중 변형은 지역축이 담당
    reg2 = _kw_shorten(reg2) if reg2 else ""
    year = next(iter(re.findall(r"(?:19|20)\d{2}", free_text)), "")
    model = nouns[0] if nouns else ""
    if reg2 and industry.strip():
        kws.append(f"{reg2} {industry.strip()}")                      # 지역+업종: '부산 기장 중고차'
    if model and year:
        kws.append(f"{model} {year} 중고")                            # 차종+연식: '모닝 2019 중고'
    if reg2 and model and model != industry.strip():
        kws.append(f"{reg2} {model}")                                 # 지역+차종: '부산 기장 모닝'
    for n in heads:
        for it in _PRODUCT_INTENTS:
            kws.append(f"{n} {it}")
    if brand.strip() and phrase:
        kws.append(f"{brand.strip()} {phrase}")
    seen, out = set(), []
    for k in kws:
        if k and k not in seen:
            seen.add(k); out.append(k)
    return _apply_volume(out, limit, hints=heads)


# 스마트블록 의도별 앵글 3종 — 같은 키워드로 다른 블록 진입(성장 PHASE 7)
BLOG_ANGLES = {
    "review": "[앵글=후기형] 통합검색 '후기' 스마트블록을 노려라. 제목·본문을 1인칭 실제 후기 중심으로"
              "(직접 겪은 상황→과정→만족/아쉬움→추천). 별점·재방문 의사 등 경험 신호를 담아라.",
    "howto":  "[앵글=방법·과정형] '방법/과정' 스마트블록·지식스니펫을 노려라. 단계별(1·2·3) 과정·소요시간·"
              "주의점을 구체 수치로. Q&A 소제목으로 '어떻게'에 정확히 답하라.",
    "price":  "[앵글=가격·비용형] '가격/비용' 스마트블록을 노려라. 가격대·구성·비교 기준을 표로 정리"
              "(단, 입력에 없는 금액은 지어내지 말고 '문의/상담' 유도). 왜 이 가격이 합리적인지 근거 제시.",
}


def blog_angle_directive(angle: str) -> str:
    """의도별 앵글 지시문(후기/방법/가격) — 없으면 빈 문자열."""
    return BLOG_ANGLES.get(angle or "", "")


def posting_cadence_tip(days_since_last: int | None, weekly_target: int = 3) -> str:
    """C-Rank '활동 지속성' 코칭 — 주 N회 발행 권장. 발행 캘린더 안내(성장 PHASE 7)."""
    if days_since_last is None:
        return f"C-Rank는 '꾸준함'에 가점을 줘요. 같은 주제로 주 {weekly_target}회 발행을 목표로 시작해요."
    if days_since_last >= 3:
        return f"{days_since_last}일째 새 글이 없어요. 발행 간격이 벌어지면 C-Rank 신뢰가 식어요 — 오늘 한 편 올려요."
    return f"좋아요! 이 페이스(주 {weekly_target}회)를 유지하면 같은 주제 전문성이 쌓여 상위노출에 유리해져요."


# ── 타깃 키워드 단일 관문(경로 무관) — 오토큐·직접생성 모두 여기를 통과 ──────────
# 3번째 재발(기장군)의 뿌리 = 같은 규칙이 두 경로에 따로 살던 구조. 규칙을 여기 집결.
import re as _re_g


def basic_region_cores(region: str) -> list:
    """기초지역(구·군·읍·면) 어간 — '부산광역시 기장군' → ['기장']. 광역시(부산)는 제외 안 함."""
    out = []
    for tok in (region or "").split():
        if _re_g.search(r"(군|구|읍|면)$", tok):
            core = _re_g.sub(r"(특별자치시|특별자치도|자치도|군|구|읍|면)$", "", tok)
            if len(core) >= 2:
                out.append(core)
    return out


def is_basic_region_kw(kw: str, region: str, biz_type: str) -> bool:
    """셀러·병행 글 타깃 하드 배제 판정 — 기초지역(구·군) 포함이면 True. (광역시 허용.)"""
    if (biz_type or "local") not in ("seller", "hybrid"):
        return False
    kwf = (kw or "").replace(" ", "")
    return any(core in kwf for core in basic_region_cores(region))


_CAR_MODELS = ("모닝", "레이", "스파크", "캐스퍼", "아반떼", "쏘나타", "그랜저", "K3", "K5", "K7", "K8",
               "코나", "티볼리", "셀토스", "투싼", "쏘렌토", "싼타페", "카니발", "스포티지", "포터", "봉고",
               "제네시스", "G80", "GV70", "GV80", "말리부", "트랙스", "베뉴", "팰리세이드", "스타리아")
_CAR_CLASSES = ("경차", "소형", "준중형", "중형", "준대형", "대형", "SUV", "승합", "화물", "수입")


def _kw_rank_tier(kw: str, models: list, classes: list, wide: str, ind0: str) -> int:
    """매물 속성 서열 — 낮을수록 우선. 0:[차종+연식/중고] 1:[차급+중고] 2:[광역+차종] 3:[광역+업종] 4:기타.
    차종·차급은 저장 컨텍스트 + 화이트리스트 양쪽으로 인식(컨텍스트 없어도 '그랜저 중고' 인식)."""
    k = (kw or "").replace(" ", "")
    _mset = set(m.replace(" ", "") for m in models if m) | set(_CAR_MODELS)
    _cset = set(classes) | set(_CAR_CLASSES)
    has_model = any(m and m in k for m in _mset)
    has_year = bool(_re_g.search(r"(19|20)\d{2}", k))
    has_class = any(c and c in k for c in _cset)
    has_wide = bool(wide and wide in k)
    if has_model and (has_year or "중고" in k):
        return 0
    if has_class and "중고" in k:
        return 1
    if has_wide and has_model:
        return 2
    if has_wide and ind0 and ind0 in k:
        return 3
    return 4


def select_target_keyword(candidates: list, biz_type: str = "local", region: str = "",
                          industry: str = "", tenant_id: str = "", verify_volume: bool = True) -> str:
    """★ 타깃 키워드 최종 선택 단일 관문(오토큐·직접생성 공통).
    ① 기초지역(구·군) 하드 배제(셀러·병행) ② 매물 속성 서열 정렬 ③ 검색량 검증(월 100회+, 실패 시 스킵).
    후보 전부 탈락하면 광역+업종 폴백. 매장(local)은 지역 규칙 미적용(원 후보 유지)."""
    cands = [" ".join((c or "").split()) for c in (candidates or []) if c and c.strip()]
    cands = list(dict.fromkeys(cands))
    biz = (biz_type or "local")
    ind0 = ((industry or "").replace("/", ",").split(",")[0] or "").strip()
    if biz not in ("seller", "hybrid"):
        return cands[0] if cands else (f"{_kw_shorten(region)} {ind0}".strip() if ind0 else "")
    # 기초지역 배제
    cands = [c for c in cands if not is_basic_region_kw(c, region, biz)]
    # 매물 속성(차종·차급) — 컨텍스트 기반 서열
    models, classes = [], []
    if tenant_id:
        try:
            from app import db as _db
            for ctx in _db.recent_inventory_context(tenant_id, limit=6):
                if ctx.get("model"):
                    models.append(ctx["model"])
                if ctx.get("car_class"):
                    classes.append(ctx["car_class"])
        except Exception:
            pass
    wide = next((_re_g.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|도)$", "", tk)
                 for tk in (region or "").split()
                 if _re_g.search(r"(특별시|광역시|특별자치시|특별자치도|도)$", tk)), "")
    cands.sort(key=lambda c: _kw_rank_tier(c, models, classes, wide, ind0))
    # 검색량 검증 — 서열 순으로 첫 통과
    fallback = f"{wide} {ind0} 추천".strip() if wide else (f"{ind0} 추천" if ind0 else "")
    if verify_volume:
        try:
            from app.services import searchad as _sa
            if _sa.configured() and cands:
                vols = {}
                for vv in _sa.keyword_volumes(cands[:8], limit=80):
                    vols[(vv.get("keyword") or "").replace(" ", "")] = vv.get("total", 0)
                for c in cands:
                    v = vols.get(c.replace(" ", ""))
                    if v is None or v >= 100:          # 무측정은 통과(임의 숫자 금지), 측정은 100회+
                        return c
                return fallback or (cands[0] if cands else "")
        except Exception:
            pass
    return cands[0] if cands else fallback


def keyword_plan(industry_name: str, region: str, note: str = "", axis: str = "local", brand: str = "") -> dict:
    """대표키워드 1개(제목) + 롱테일 2~3개(본문 소제목) + 실검색량 여부('추정') — 성장 PHASE 5.
    지역+업종+의도 3요소 조합, 실검색량 500~5,000 롱테일 우선(searchad 주경로, 무키 시 규칙 폴백=추정)."""
    try:
        from app.services import searchad
        estimated = not searchad.configured()
    except Exception:
        estimated = True
    kws = target_keywords(industry_name, region, note, limit=10, axis=axis, brand=brand)
    headline = kws[0] if kws else (f"{region} {industry_name}").strip()
    longtail = [k for k in kws[1:] if k and k != headline][:3]
    return {"headline": headline, "longtail": longtail, "keywords": kws, "estimated": estimated}


def target_keywords(industry_name: str, region: str, note: str = "", limit: int = 10,
                    axis: str = "local", brand: str = "") -> list[str]:
    """키워드 세트. axis='product'면 상품/후기축(셀러), 'both'면 지역+상품 병합, 기본은 지역축."""
    if axis == "product":
        return product_keywords(note, brand, limit, industry=industry_name, region=region)
    if axis == "both":
        merged = (product_keywords(note, brand, limit, industry=industry_name, region=region)
                  + target_keywords(industry_name, region, note, limit))
        return list(dict.fromkeys(merged))[:limit]
    kws: list[str] = []
    reg = (region or "").strip()
    ind = (industry_name or "").strip()
    if reg and ind:
        # 지역 다중 granularity — 검색자마다 '동/구/시+구'로 다르게 검색하므로 변형별 키워드 생성
        toks = reg.split()
        variants = [reg]                                          # 부산 동구 초량동
        if len(toks) >= 2:
            variants.append(" ".join(toks[:2]))                  # 부산 동구
        dong = next((t for t in toks if t.endswith(("동", "읍", "면", "가", "리"))), "")
        if dong:
            variants.append(dong)                                # 초량동
        variants = list(dict.fromkeys(variants))
        for v in variants:
            kws.append(f"{v} {ind}")                              # 각 변형 기본
        for v in variants[:2]:                                    # 대표 변형에 의도 결합
            for it in _INTENTS[:4]:
                kws.append(f"{v} {ind} {it}")
    if ind:
        kws += [f"{ind} 추천", f"{ind} 가격"]
    # 메모에서 핵심 명사 추출(신메뉴/차종/시술명 등)
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or ""):
        if w not in ("추천", "이벤트", "할인") and len(w) <= 12:
            cand = f"{reg} {w}".strip()
            if cand and cand not in kws:
                kws.append(cand)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k); out.append(k)
    # 지역+업종 결합 힌트 우선 → 지역 롱테일 실검색량 반영(전국 키워드 혼입 방지, PHASE 6)
    _hints = [h for h in (f"{reg} {ind}".strip(), ind) if h] or None
    return _apply_volume(out, limit, hints=_hints)


# ── 네이버 플레이스(지도) 노출 보조 ──
def place_keywords(industry_name: str, region: str, limit: int = 12) -> list[str]:
    """플레이스 업체명·소개·메뉴·리뷰에 녹일 키워드(지역+업종+의도). 검색 매칭↑."""
    reg, ind = (region or "").strip(), (industry_name or "").strip()
    out: list[str] = []
    if reg and ind:
        out += [f"{reg} {ind}", f"{reg} {ind} 추천", f"{reg} {ind} 잘하는곳",
                f"{reg} {ind} 예약", f"{reg} {ind} 후기", f"{reg} 가까운 {ind}"]
    if ind:
        out += [f"{ind} 추천", f"{ind} 잘하는곳", f"{ind} 가격"]
    seen, res = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k); res.append(k)
    return res[:limit]


def review_request(tenant_name: str, region: str = "", industry: str = "") -> str:
    """방문자 영수증 리뷰 요청 문구 — 플레이스 노출의 핵심 연료(리뷰 수·키워드)."""
    kw = (f"{region} {industry}").strip() or "후기"
    name = tenant_name or "저희 가게"
    return ("방문해 주셔서 감사합니다! 🙏\n"
            f"도움이 되셨다면 네이버에 '{name}' 검색 → 영수증 리뷰 한 줄만 남겨주세요.\n"
            f"리뷰에 '{kw}' 키워드를 자연스럽게 적어주시면 다른 분들도 더 쉽게 찾을 수 있어요. 💙")


# ── 플랫폼별 성과/SEO 디렉티브(프롬프트 주입) ──
BLOG_DIRECTIVES = (
    "[네이버 상위노출 알고리즘 분석 → 반영 필수]\n"
    "네이버는 C-Rank(주제전문성40%·활동지속성30%·사용자반응20%·품질10%) + "
    "D.I.A.(실제 '경험·후기'를 높게 평가)로 순위를 매긴다. 그래서:\n"
    "- **1인칭 실제 경험·후기체**로 써라('직접 가봤더니', '먹어보니', '시공해보니') → D.I.A 가점.\n"
    "- 한 주제를 깊게(전문성), 곁가지 잡담 금지 → C-Rank 전문성.\n"
    "- 댓글·질문을 부르는 문장 1개(예: '○○ 더 궁금하면 댓글 주세요') → 사용자반응.\n"
    "- 제목: **핵심 키워드를 맨 앞**에(예: '지역+업종+추천/후기'), 25~35자 롱테일. 숫자·연도·혜택 넣으면 클릭↑.\n"
    "- **첫 문장에 핵심 키워드 1회**(검색 의도 즉시 충족, 2~3문장 인트로).\n"
    "- **연관 검색어**(같은 의도의 변형어 2~3개)를 자연스럽게 본문에 녹여라 → D.I.A+ 가점.\n"
    "- **'## 자주 묻는 질문' 섹션 1개**(Q&A 2~3쌍, '저장각' 정보) → 네이버 Q&A·체류 가점.\n"
    "- 분량 1200~1800자, 소제목(##) 3~5개, 타겟 키워드 4~6회(남발 금지).\n"
    "- 신뢰·체류↑: 가격대·찾아오는길·영업시간·주차·예약을 표/목록으로.\n"
    "- [사진N] 마커를 본문 곳곳(체류↑). 마지막 방문/예약 CTA+연락 안내.\n"
    "[저품질·스팸 회피(어기면 3페이지로 추락)]\n"
    "- 과장·낚시·광고성 표현 금지: 최고/최저가/100%/무조건/보장/완벽/대박/강력추천/유일/1위/공짜.\n"
    "- 같은 키워드 7회+ 남발 금지, 복사·짜깁기 금지, 실시간 이슈 억지 삽입 금지, 느낌표 남발 금지."
)

# ── GEO(Generative Engine Optimization, B블록) — AI 검색(ChatGPT·Perplexity·클로바X)이
#    인용하기 쉬운 구조: 정의문·검색질문형 Q&A·한눈 요약·표기 일관(NAP/SPU). 인용 '보장'은 없다(정직).
def geo_questions(industry: str, region: str = "", pain_points: str = "") -> list[str]:
    """업종별 'AI가 받을 질문' 3개 — 프로필 pain_points 1개 + 검색질문 템플릿 2개."""
    industry = (industry or "").strip() or "이 업종"
    loc = (region or "").strip()
    qs = []
    pains = [s.strip() for s in re.split(r"[,·/]", pain_points or "") if s.strip()]
    if pains:
        qs.append(f"{pains[0]} — 어떻게 해결하나요?")
    qs.append(f"{(loc + ' ') if loc else ''}{industry} 고를 때 뭘 봐야 하나요?")
    qs.append(f"{industry} 가격(비용)은 어느 정도인가요?")
    return qs[:3]


def geo_directive(biz_type: str, name: str, industry: str, region: str = "",
                  brand: str = "", questions: list[str] | None = None) -> str:
    """블로그 프롬프트 주입용 GEO 구조 지시 — 매장(NAP)/셀러(SPU) 분기."""
    qline = " / ".join(questions or [])
    if (biz_type or "local") == "seller":
        pname = f"{brand} {name}".strip() if brand and brand not in (name or "") else (name or "")
        return (
            "[GEO — AI 검색(ChatGPT·Perplexity 등)이 인용하기 쉬운 구조로]\n"
            f"- 첫 문단에 상품 정의문 한 문장: \"{pname}은(는) ~한 {industry}다\" 꼴로 자연스럽게(무엇인지 한 문장으로 규정).\n"
            "- '## 한눈 요약' 소제목 1개: 핵심 3줄(- 목록) — 검색자가 답만 뽑아가게.\n"
            "- '## 솔직 장단점' 소제목 1개: 입력에 근거한 장점 2~3개 + 아쉬운 점 1개(솔직함이 AI 인용 신뢰를 높인다. 없는 단점 지어내기 금지).\n"
            f"- 비교 질문 Q&A 1개: \"{name} 비슷한 제품과 차이는?\" — 입력 정보로만 답하고 타사 비방·비교 우위 날조 금지.\n"
            + (f"- FAQ 질문은 실제 검색 질문형으로: {qline}\n" if qline else "")
            + "- 상품명·스토어명·구매링크(SPU) 표기는 본문 전체에서 한 글자도 다르지 않게 일관되게.\n")
    place = f"{region}의 {industry}".strip()
    return (
        "[GEO — AI 검색(ChatGPT·Perplexity 등)이 인용하기 쉬운 구조로]\n"
        f"- 첫 문단에 정의문 한 문장: \"{name}은(는) {place} 전문점이다\" 꼴로 자연스럽게(무엇을 하는 곳인지 한 문장으로 규정).\n"
        "- '## 한눈 요약' 소제목 1개: 핵심 3줄(- 목록) — 검색자·AI가 답만 뽑아가게.\n"
        + (f"- FAQ 질문은 실제 검색 질문형으로: {qline}\n" if qline else "")
        + f"- 상호는 항상 '{name}', 지역은 '{region}'으로 본문 전체 일관 표기(NAP 일관 = 인용 신뢰 신호).\n")


def geo_audit(kind: str, payload: dict, name: str = "", industry: str = "",
              region: str = "", biz_type: str = "local") -> dict:
    """GEO(AI검색 준비) 점수 — 구조 요소 기계 채점(LLM 0콜). blog만 의미 있음.
    항목: 정의문(첫 문단 상호+업종/지역) · 한눈 요약 · 검색질문형 Q&A · 표기 일관(NAP/SPU)
    + 셀러는 솔직 장단점. '인용 보장'이 아니라 '인용되기 유리한 구조' 점수다."""
    if kind != "blog":
        return {}
    text = payload.get("body") or ""
    if not text:
        return {}
    hits, misses = [], []
    head = text[:260]
    if name and name in head and (industry in head or (region and region.split()[0] in head)):
        hits.append("정의문(첫 문단에 상호+업종/지역)")
    else:
        misses.append("첫 문단 정의문 없음")
    if "한눈 요약" in text or "한 눈 요약" in text:
        hits.append("한눈 요약")
    else:
        misses.append("'## 한눈 요약' 없음")
    if any(s in text for s in ("자주 묻는", "Q&A", "Q.")):
        hits.append("Q&A")
    else:
        misses.append("Q&A 없음")
    if biz_type == "seller":
        if any(s in text for s in ("솔직 장단점", "아쉬운 점", "단점")):
            hits.append("솔직 장단점")
        else:
            misses.append("솔직 장단점 없음")
        consistent = bool(name) and text.count(name) >= 2
    else:
        consistent = bool(name) and text.count(name) >= 2 and (not region or region.split()[0] in text)
    if consistent:
        hits.append("표기 일관(NAP/SPU)")
    else:
        misses.append("상호/상품 표기 일관성 약함")
    total = len(hits) + len(misses)
    score = int(round(100 * len(hits) / total)) if total else 0
    return {"score": score, "hits": hits, "misses": misses}


SHORT_DIRECTIVES = (
    "[릴스/쇼츠 알고리즘 분석 → 반영 필수]\n"
    "배포 1위 신호는 '시청 유지(watch time)'. 3초 홀드율 60%+면 도달이 5~10배. "
    "DM 공유(sends)·저장(saves)은 좋아요보다 3~5배 중요. 그래서:\n"
    "- 0~3초 훅: 첫 프레임부터 질문/충격/공감으로 스크롤을 멈춰라('○○ 이거 모르면 손해').\n"
    "- 길이 30~45초(2026 스윗스팟). 15초 이하는 완주 절대량 미달로 도달 붕괴 → 30초 이상 채워라.\n"
    "- 완주율 목표: 30초↓ 65%+, 30~60초 50%+. 끝→처음 루프(재생=새 조회로 카운트).\n"
    "- 직접 만든 나레이션·BGM=원본 오디오(2026 가점, 소규모 계정은 트렌딩 사운드보다 유리).\n"
    "- '저장각' 정보(꿀팁) 1개 + '친구 태그/공유(DM)' 유도 → sends·saves↑.\n"
    "- 제목/설명에 검색 키워드(유튜브=검색엔진), 해시태그 3개.\n"
    "- 자막은 무음 시청 대비 큰 글씨. 마지막 1.5초 명확한 CTA."
)

CAPTION_DIRECTIVES = (
    "[인스타 알고리즘 분석 → 반영 필수]\n"
    "도달 핵심은 watch + sends(DM 공유)·saves. 그래서:\n"
    "- 첫 줄 훅('더보기' 전 노출)로 시선 잡기.\n"
    "- '저장각' 유용함(팁/정보) 1개 + 'DM/공유하고 싶은' 한 줄 → saves·sends↑.\n"
    "- 해시태그는 '분류 라벨'일 뿐(2026) — 정확한 3~5개만. 많으면 도달↓(무해시태그가 나을 때도).\n"
    "- 마지막에 '댓글 질문 1개'(발행 1시간 내 답글=대화신호↑) + 방문/문의 CTA. 과장·낚시 금지."
)

X_DIRECTIVES = (
    "[X 알고리즘] 초반 인게이지먼트 속도가 노출을 좌우. 첫 문장 훅, 한 가지 핵심 메시지, "
    "리트윗/답글 부르는 한 줄, 해시태그 1~2개, 방문/문의 유도. 280자 이내. 과장 금지.\n"
    "⚠️ 2026 핵심: 외부 링크(URL)는 도달 50~90% 감소 → 본문에 링크 넣지 말고 '검색/프로필' 유도. "
    "답글=좋아요의 150배 → 반드시 '질문 한 줄'로 끝내 답글을 유도하라(대화 유발)."
)

# 셀러(상품 판매) 전용 쇼츠/릴스 — '방문'이 아니라 '구매 전환'축
SHORT_DIRECTIVES_SELLER = (
    "[셀러 커머스 영상 — 구매 전환 최적화]\n"
    "이 영상의 목표는 방문이 아니라 '구매(스토어/상세페이지)'다. 판매자가 직접 상품을 보여주는 시연·언박싱 톤(고객 후기 사칭 금지). "
    "쇼츠/릴스 배포 신호(3초 홀드·완주·저장·공유)를 커머스로 노려라:\n"
    "- 0~3초 훅: 문제제기/가격충격/비교('이 가격에 이 퀄은 못 참죠', '○○ 이거 하나면 끝').\n"
    "- 각 장면 = 셀링포인트 1개 시연(소재·기능·사이즈감·활용법). '말'보다 '보여주듯' 묘사.\n"
    "- 사용 전/후(before-after)로 효과를 눈에 보이게.\n"
    "- '장바구니 각이면 저장 ❤️' 저장 유도 1회 → 커머스 저장 신호로 도달↑.\n"
    "- 마지막 CTA는 명확한 구매 안내(프로필 링크 / 스토어 검색어).\n"
    "- 허위 효능·과장 금지(단점 솔직히 한 줄이면 신뢰↑). 길이 30~45초, 자막 큰 글씨."
)

# 정직 원칙 — 모든 생성물(글·영상·상세페이지) 공통. 허위·날조는 '안 만드느니만 못함' + 표시광고법 위반.
FACTS_RULE = (
    "[⚠️ 정직 원칙 — 반드시 지켜라(위반하면 콘텐츠 폐기)]\n"
    "- 입력(메모·사진분석·상품정보)에 '없는' 가격·할인율·수치·스펙·모델명·성분·효능·용량·수상/인증·후기수를 절대 지어내지 마라.\n"
    "- 가격·할인은 입력에 명시됐을 때만 그 값 그대로 써라. 없으면 금액을 아예 언급하지 마라(임의 숫자 금지).\n"
    "- 소요 시간·기간(예: '2~3시간 걸린다', '30분이면 끝')도 입력에 없으면 숫자로 단정하지 마라 — "
    "'차종·상태에 따라 달라진다'로 쓰고 정확한 안내는 상담으로 돌려라.\n"
    "- 아래 [사실 정보]([✅ 사장님 제공 실제 정보]·[매장 정보]·[가게])에 있는 내용만 사실로 서술하라. "
    "비어 있는 항목(보증 기간·시공 시간·금액 등)은 그 주제의 문장 자체를 만들지 말고 자연스럽게 생략하라. "
    "업체명·주소·전화는 [가게]/[매장 정보]의 값만 그대로 써라.\n"
    "- 고객·손님에 관한 구체 일화(방문 시점, 직업, 나이, 대화 내용, 반응)는 [사장님 제공 실제 정보]의 "
    "경험담에 있는 것만 서술하라. 없으면 특정 일화를 지어내지 말고 일반 서술"
    "('이런 고민으로 오시는 분들이 많습니다')로만 써라 — 가짜 후기·가짜 사례는 절대 금지.\n"
    "- 상품 등급/성능(예: 노이즈캔슬링·방수)과 가격이 안 맞게 쓰지 마라 — 확실치 않은 사실은 쓰지 말고 비워둬라.\n"
    "- 모르는 정보는 '지어내기'보다 '생략'. 추측을 사실처럼 단정하지 마라.\n"
    "- 과장·낚시 금지: 최고/최저가/100%/무조건/보장/완벽/1위/유일/대박.\n"
    "- [🔒 개인정보 보호] 사진분석에 차량 번호판·전화번호·차대번호(VIN)·이름·주소·라벨 숫자가 보여도 "
    "콘텐츠(글·자막·해시태그)에 절대 그대로 쓰지 마라. 특정 개인·차량을 식별할 수 있는 값은 언급 자체를 생략하라."
)

# 훅 — 도달의 80%를 좌우(영상·캡션·X 공통). 3안 구상 후 최강으로 오픈.
HOOK_RULE = (
    "[훅(0~2초 / 첫 줄) — 도달을 좌우]\n"
    "쓰기 전 훅 3개를 속으로 구상해 '가장 강한 1개'로 열어라. 아래 검증 공식 중 택1:\n"
    "① 결과 먼저('3만원으로 이렇게 바뀝니다') ② 손실 회피('이거 모르고 사면 손해') "
    "③ 호기심 갭('판매자만 아는 고르는 법') ④ 구체 숫자('2번이면 끝나는 ○○').\n"
    "⚠️ 밋밋한 인사('안녕하세요 ○○입니다')로 시작 금지 — 첫 마디에 궁금·공감·충격을 넣어라."
)

# 파는 카피 심리 — 모든 글 공통(정직 원칙 위에서).
COPY_PSYCH = (
    "[파는 카피 심리]\n"
    "- 손실 회피 > 이득: 같은 말도 '놓치면 손해' 프레임으로(단, 없는 혜택 지어내기 금지).\n"
    "- 구체성=신뢰: '좋아요' 대신 숫자·디테일('3주 써보니 배터리 40% 남음').\n"
    "- '당신' 화법: 읽는 사람을 직접 지칭('사장님도 이런 적 있으시죠').\n"
    "- 단점 1줄 솔직히 → 신뢰↑('무겁긴 해요, 대신 튼튼')."
)

# 네이버 블로그 — 파는 글 구조.
BLOG_SELL_STRUCT = (
    "[파는 글 구조 — 반드시 적용]\n"
    "① 첫 3줄=PAS: 문제 제기→공감/증폭→'그래서 오늘 보여드릴게요'(검색 유입자 이탈 방지=체류=상위노출).\n"
    "② 스펙이 아니라 FAB: 기능→'그래서 당신에게 뭐가 좋은지'(혜택)로 번역해서 써라.\n"
    "③ 특정 손님 스토리(BAB): 한 사람 사례(전→과정→후)로 몰입시켜라.\n"
    "④ 반론 선제 해소: FAQ에 손님이 망설이는 것(가격/AS/효과/배송)을 미리 답하라.\n"
    "⑤ CTA 계단: 저장→댓글→검색·예약→방문·구매 순(바로 '사세요'는 저항).\n"
    "⑥ 스마트블록 대응: 그 키워드의 세부 검색의도(가격·후기·방법·비교·추천)를 각각 소제목(##)으로 다뤄라 "
    "— 스마트블록·AI 답변 인용에 잡히게(정확·전문적으로 써야 AI가 인용)."
)

# 체류시간·정보 밀도(상위노출 v2) — 블로그 본문 전용. 정직 원칙 위에서.
RETENTION_DENSITY = (
    "[체류시간·정보 밀도 — 상위노출 v2(반드시 적용)]\n"
    "① 도입 첫 3~4문장(모바일 첫 화면)에 세 가지를 담아라: (a) 검색자의 질문 재확인 "
    "(b) 이 글이 주는 답 예고 (c) '끝까지 읽을 이유' 예고 — 예: '마지막에 서류 보는 법까지 알려드릴게요'. "
    "이 셋이 스크롤 약속이 되어 초반 이탈을 막는다.\n"
    "② 글 중반(대략 절반 지점)에 '궁금증 재점화' 1회 — 새 질문을 던져 계속 읽게 하라 "
    "(예: 비용 글이면 '그런데 왜 견적이 업체마다 다를까요?'). 억지 말고 본문 주제에서 자연스럽게.\n"
    "③ 허사·패딩 금지: '~에 대해 알아보겠습니다', 같은 사실을 말만 바꿔 반복, 결론을 뒤로 미루는 채우기 문장 금지. "
    "각 문단은 '새 정보 1개 이상'을 담아라(정보 없는 문단 삭제).\n"
    "④ 경험 분산: 사장님 경험담·사진에서 확인된 사실을 도입·중반·결론에 최소 1회씩 나눠 배치하라 "
    "(한 문단에 몰아넣지 마라). 진짜 경험의 배치가 AI 판별을 이기는 정공법 — 없는 경험은 절대 만들지 마라.\n"
    "⑤ 분량은 '정보 단위' 기준: 폼 입력·사진 사실을 다 쓰면 끝내라. 글자수 채우려 늘리기 금지(늘린 허사가 오히려 감점).\n"
)

# 영상 스크립트 — 파는 글쓰기 + 리텐션.
VIDEO_SCRIPT_CRAFT = (
    "[영상 글쓰기 — 반드시]\n"
    "- 문어체 금지, 말하듯 한 문장 한 호흡('물에 빠뜨려도 멀쩡해요, 보실래요?').\n"
    "- 나레이션↔자막 역할 분리: 나레이션=대화체, 자막=핵심 키워드만 5~7자 큰 글씨('열차단 99%').\n"
    "- 감정 곡선: 궁금(훅)→공감(문제)→해결(시연)→만족(결과)→행동(CTA).\n"
    "- 한 장면=셀링포인트 1개를 '보여주듯' 묘사. 2~3초마다 새 장면/정보(죽은 구간 0).\n"
    "- 현장·과정 1컷 이상 포함(작업하는 손·before/after·제품 디테일) — 릴스 훅용이자 네이버 블로그 '경험 증명'용으로 둘 다 재사용.\n"
    "- 끝 프레임=첫 프레임과 연결(루프→자동 반복=시청시간↑). 마지막 1.5초 단일 CTA(행동 하나만)."
)

# 저장·공유 유도(영상강화 PHASE 5) — 저장·공유(DM)가 좋아요보다 3~5배 가중치.
SAVE_SHARE_RULE = (
    "[저장·공유 유도 — 도달 최강 신호] 콘텐츠를 '저장할 가치'가 있게 만들어라: "
    "정보/튜토리얼 포맷('OO하는 법 3단계', 'OO 고르는 기준 3가지')이 저장 점수가 가장 높다. "
    "가능하면 내레이션·구성을 단계형(1·2·3)으로. 마지막에 저장 유도 1회('저장해두고 필요할 때 보세요') — "
    "좋아요 구걸('좋아요 눌러주세요')은 금지."
)


def save_share_line(platform: str) -> str:
    """플랫폼별 저장·공유 CTA 한 줄(캡션/설명 자동 삽입용, 영상강화 PHASE 5)."""
    return {
        "instagram": "🔖 저장해두고 필요할 때 꺼내보세요 · 도움될 친구에게 DM으로 공유!",
        "youtube": "📌 저장해두면 필요할 때 바로 찾아요 · 도움됐다면 친구에게 공유해 주세요",
        "x": "🔖 북마크해두고 필요할 때 보세요",
    }.get(platform or "instagram", "저장해두고 필요할 때 보세요!")


# 자막 정보 밀도(영상강화 PHASE 2) — 반복재생 유도. 과하지 않게(한 씬 1정보+수치).
SUBTITLE_DENSITY = (
    "[자막 정보 밀도] 각 문장(씬)에 구체 정보 1개(수치·비교·팁)를 꼭 담아라 — 정보가 빽빽하면 "
    "한 번에 다 못 읽어 반복재생하게 된다. 단, 한 씬에 정보 2개 이상 욱여넣지 마라(피로)."
)

# 플랫폼별 최적화(같은 소재도 채널마다 다르게).
PLATFORM_YOUTUBE = "[유튜브 쇼츠=검색엔진] 제목·설명 첫 줄에 검색 키워드를 넣어라. 해시태그 3~5개."
PLATFORM_REEL = "[인스타 릴스] '저장각/공유각' 1회 유도(saves·sends 신호↑). 발행 시 트렌딩 사운드 권장."


def speaker_frame(strat_key: str) -> str:
    """업종/사업형태별 '화자와 목적' 프레이밍 — 정직하면서 효과적인 관점 고정(글·영상 공통)."""
    if strat_key == "seller":
        return ("[화자·목적] 너는 이 상품을 파는 '판매자 본인'이다. 판매자가 직접 상품을 보여주는 "
                "'상품 시연·언박싱·사용법' 관점으로 써라. 목표는 상세페이지로 데려가 '구매'시키는 것. "
                "⚠️ 고객인 척 '내돈내산 후기' 사칭 금지(가짜후기=저품질·불법). '제가 판매하며 직접 보여드릴게요' 톤으로 정직하게.")
    if strat_key == "hybrid":
        return ("[화자·목적] 너는 이 가게를 운영하며 직접 작업·판매하는 '사장 본인'이다. "
                "가까운 손님은 매장 방문, 먼 손님은 온라인 구매로 안내. 사장의 실제 경험·작업 관점으로 정직하게.")
    return ("[화자·목적] 너는 이 일을 직접 하는 '사장(작업자·운영자) 본인'이다. "
            "오늘 직접 한 시공·시술·조리 등을 '작업일지·현장 후기'처럼 써라(어떤 케이스→어떻게 작업→과정·팁·주의점→전/후 결과). "
            "목표는 '이런 고민 있으면 방문·예약하세요'로 방문 유도. ⚠️ 고객인 척 후기 사칭 금지 — 작업자 본인 관점으로 정직하게.")

# ── 저품질/스팸 위험 표현(휴리스틱, 공식 목록 아님) ──
RISKY_EXPRESSIONS = [
    "최고", "최저가", "100%", "무조건", "보장", "완벽", "대박", "강력추천",
    "절대", "유일", "1위", "공짜", "무료나눔", "지금당장", "한정특가", "폭탄세일",
    "초대박", "역대급", "클릭", "꼭 사세요",
]

# ── AI 클리셰(휴먼터치 A1) — 'AI가 쓴 티' 나는 정형 표현. 2026 AI 콘텐츠 피로 → 감점 대상 ──
AI_CLICHES = [
    "알아보겠습니다", "알아보도록 하겠습니다", "소개해드리겠습니다", "소개해 드리겠습니다",
    "추천드립니다", "추천드려요", "추천해 드립니다",
    "도움이 되셨길", "도움이 되었으면", "마무리하겠습니다", "마치겠습니다",
    "어떠셨나요", "포스팅을 마",
]

# 휴먼터치 지시 — blog/insta/X 공통 주입(A1). '사람이 쓴 것 같은' 리듬·구어가 차별화.
HUMAN_TOUCH = (
    "[휴먼터치 — AI 티 빼기(어기면 저품질·독자 이탈)]\n"
    "- 금지 클리셰: '안녕하세요~ 오늘은 ~알아보겠습니다' 류 도입, '~추천드립니다'·'~소개해드리겠습니다' 반복, "
    "'지금까지 ~였습니다'·'도움이 되셨길 바랍니다' 류 마무리, '어떠셨나요?' 상투 질문.\n"
    "- 문장 길이를 일부러 섞어라 — 아주 짧은 문장 뒤에 긴 문장. 문단 길이도 균일하게 맞추지 마라.\n"
    "- 자연스러운 구어 추임새를 가끔만: '근데', '사실', '솔직히' 같은 말(남발 금지).\n"
    "- 사장님(판매자) 1인칭 목소리 유지 — 설명문이 아니라 '내 가게(내 상품) 이야기'로.\n"
    "- 이모지: 네이버 블로그 0~1개, 인스타 1~2개까지만(남발=AI티).\n"
)

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF❤⭐✨]")

# 감점이 아니라 '발행 차단' 대상 — 의료광고법·자동차관리법 위반 소지가 큰 단정 표현(PHASE 7)
HARD_BLOCK_EXPRESSIONS = [
    "완치", "부작용 없", "부작용없", "무통", "100% 효과", "영구적", "재발 없",
    "완전무사고", "무사고 보장", "침수 아님 보장",
]


def hard_block_hits(text: str) -> list[str]:
    """발행 차단 대상 표현 탐지(감점 아님). 하나라도 걸리면 자동발행 보류(PHASE 7)."""
    t = text or ""
    return [w for w in HARD_BLOCK_EXPRESSIONS if w in t]


def _money_nums(s: str) -> set:
    """텍스트에서 '금액·%·수치+단위'를 정규화 추출(콤마·공백 제거). 날조 탐지용(PHASE 7)."""
    raw = re.findall(r"(\d[\d,]*)\s*(원|만원|%|퍼센트|만|천원|시간|분)", s or "")
    return {num.replace(",", "") + unit for num, unit in raw}


def keywords_line(kws: list[str]) -> str:
    return "[타겟 키워드(이 키워드로 검색 상위·전환을 노림)] " + ", ".join(kws) if kws else ""


# 경험/후기 신호(D.I.A 가점)
_EXPERIENCE_WORDS = ["후기", "직접", "경험", "먹어보", "써보", "방문", "가봤", "시공해", "느꼈"]

# 고객 일화(서사형 날조) 신호 — 특정 시점·특정 인물 표지가 있는 문장만(일반 서술 '오시는 분들'은 제외)
_ANECDOTE_RE = re.compile(
    r"지난\s?(번|주|달|해)|어제|엊그제|얼마 전|며칠 전|최근에 오|"
    r"[0-9]+대\s?(남성|여성|사장님|손님|고객|차주|어머니|아버지)|"
    r"한 분이 오|손님(이|께서) 오셨|고객님(이|께서) 오|차주분(이|께서) 오|오신 손님|오셨어요|오셨습니다")
# 일화 문장 대조 시 무시할 일반어(업종 공통) — 이것만 겹쳐선 근거로 안 침
_ANECDOTE_STOP = {"손님", "고객", "사장님", "차주", "여성", "남성", "어머니", "아버지",
                  "방문", "매장", "저희", "때문", "이야기", "그래서", "그런데", "하시", "하셨",
                  "오셨", "오시", "오셔", "지난", "어제", "엊그제", "그저께", "최근", "며칠", "얼마"}


def _ungrounded_anecdote(text: str, source: str) -> str:
    """입력(경험담·확인 사진)에 근거 없는 고객 일화 문장 탐지 → 해당 문장(없으면 '').
    경험담을 인용한 문장은 통과: 문장의 '구별 토큰'이 source에 하나라도 있으면 근거 있음."""
    src = (source or "")
    for sent in re.split(r"(?<=[.!?다요])\s+|\n", text or ""):
        sent = sent.strip()
        if not (10 <= len(sent) <= 200) or not _ANECDOTE_RE.search(sent):
            continue
        toks = [t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", sent)
                if not any(t.startswith(s) for s in _ANECDOTE_STOP)
                and not re.fullmatch(r"[0-9]+대?", t)]
        distinct = [t for t in toks if len(t) >= 2][:12]
        if not distinct:
            continue                                  # 구체 정보가 없는 일반 문장 — 날조로 안 봄
        # 근거 판정: 3자+ 토큰의 포함, 또는 짧은 토큰의 단어 단위 일치('분이'⊂'차주분이' 오매칭 방지)
        grounded = (any(t in src for t in distinct if len(t) >= 3)
                    or any(t in src.split() for t in distinct))
        if not grounded:
            return sent
    return ""


# 행정구역 풀네임(본문 반복 시 기계 삽입 티) — 키워드 자연 변형 게이트(재검증 STEP 1-2b)
_ADMIN_FULL_RE = re.compile(r"[가-힣]{2,}(?:광역시|특별시|특별자치시|특별자치도)")


def _kw_shorten(kw: str) -> str:
    """'부산광역시 썬팅 비용' → '부산 썬팅 비용' — 행정구역 풀네임을 구어형으로."""
    return re.sub(r"([가-힣]{2,})(광역시|특별시|특별자치시|특별자치도)", r"\1", kw or "").strip()


def _kw_variant_hits(text: str, kw: str) -> int:
    """타깃 키워드의 자연 변형 노출 수 — 핵심 토큰(축약형)이 한 문장에 모두 있으면 1회."""
    toks = [t for t in _kw_shorten(kw).split() if len(t) >= 2]
    if not toks:
        return 0
    return sum(1 for s in re.split(r"[\n.!?]", text) if all(t in s for t in toks))


def quality_audit(channel: str, kind: str, payload: dict, source: str = "") -> dict:
    """네이버 랭킹 신호(C-Rank·D.I.A.+·플레이스) 기준 채점(0~100) + 개선 경고.
    가점: 검색의도 정합·1차 경험·구체 수치·이미지 4+·Q&A·제목-본문 일치·롱테일.
    감점/차단: 키워드 도배·낚시(제목-본문 불일치)·빈약 문서·과장/날조(표시광고법)·PII.
    source(입력 메모+사진분석) 제공 시 입력에 없는 금액·수치 날조를 기계적으로 탐지(PHASE 7·9)."""
    text = (payload.get("body") or payload.get("text") or "")
    warnings: list[str] = []
    score = 100

    # 사실 검증: 출력의 금액·%·수치가 입력에 존재하는지 대조(LLM 0콜 날조 탐지)
    # source 미전달 호출(게이트 경로 등)은 생성 시 저장한 payload.gen_source로 폴백
    source = source or (payload.get("gen_source") or "")
    if source:
        fabricated = [n for n in _money_nums(text) if n not in _money_nums(source)]
        if fabricated:
            warnings.append(f"입력에 없는 수치/금액 {fabricated[:4]} → 날조 의심(제거 권장)")
            score -= min(20, 8 * len(fabricated))
        # 고객 일화 창작(서사형 날조): 입력 경험담에 없는 특정 일화 → 게이트 실패(-30, 자동 재생성)
        _anec = _ungrounded_anecdote(text, source)
        if _anec:
            warnings.append(f"입력에 없는 고객 일화 '{_anec[:40]}…' — 가짜 사례 창작(게이트 실패)")
            score -= 30
        # 보증 기간 날조(폼사실 게이트 1-3b): 폼에 없는 'N년/N개월 보증'은 게이트 실패급
        _src_flat = source.replace(" ", "").replace(",", "")
        for g in re.findall(r"(\d+)\s*(년|개월)\s*(?:무상|무료|하자)?\s*보증|보증\s*(?:기간)?\s*(\d+)\s*(년|개월)", text):
            _n, _u = (g[0] or g[2]), (g[1] or g[3])
            if _n and (_n + _u) not in _src_flat:
                warnings.append(f"입력에 없는 보증 기간 '{_n}{_u}' — 날조(게이트 실패)")
                score -= 30
                break
    # '꼭 반영할 요청' 미반영(폼사실 게이트 1-3d) — 생성기 셀프체크 결과
    if (payload.get("request_check") or "") == "miss":
        warnings.append("'꼭 반영할 요청'이 글에 반영되지 않음 — 재작성 필요")
        score -= 10

    # 공통: 저품질/과장 표현
    hits = [w for w in RISKY_EXPRESSIONS if w in text]
    if hits:
        warnings.append(f"과장·광고성 표현 {hits[:5]} → 저품질/스팸 위험")
        score -= min(25, 6 * len(hits))
    if text.count("!") >= 5 or "!!!" in text:
        warnings.append("느낌표 남발 → 스팸 신호")
        score -= 5
    # 휴먼터치(A1): AI 클리셰·균일 문단·이모지 남발 = 'AI가 쓴 티' 감점
    cliches = [w for w in AI_CLICHES if w in text]
    if cliches:
        warnings.append(f"AI 클리셰 {cliches[:3]} → AI티(사람 냄새 없는 글)")
        score -= min(15, 5 * len(cliches))
    paras = [p for p in text.split("\n\n") if len(p.strip()) >= 40 and not p.strip().startswith(("#", "|", "["))]
    if len(paras) >= 4:
        lens = [len(p) for p in paras]
        mean = sum(lens) / len(lens)
        cv = (sum((l - mean) ** 2 for l in lens) / len(lens)) ** 0.5 / mean if mean else 1
        if cv < 0.18:
            warnings.append("문단 길이가 너무 균일 → AI티(길이 변주 권장)")
            score -= 5
    emoji_n = len(_EMOJI_RE.findall(text))
    emoji_cap = {"blog": 1, "caption": 2, "x_post": 2}.get(kind)
    if emoji_cap is not None and emoji_n > emoji_cap:
        warnings.append(f"이모지 {emoji_n}개 > {emoji_cap} → AI티·과장 인상")
        score -= 4
    # 키워드 남발(스터핑)
    for kw in (payload.get("target_keywords") or [])[:3]:
        if kw and text.count(kw) > 6:
            warnings.append(f"'{kw}' {text.count(kw)}회 과다반복(남발)")
            score -= 10

    if kind == "blog":
        title = payload.get("title", "")
        main_kw = (payload.get("target_keywords") or [""])[0]
        _body = payload.get("body") or text
        _bparas = [p.strip() for p in re.split(r"\n{2,}", _body) if p.strip()]
        # (v2 1-5) 5문단 연속 텍스트 검사 — 시각요소(사진[사진N]·표|·소제목##) 없이 텍스트 문단 5+ 연속
        _txt_streak, _max_streak = 0, 0
        for p in _bparas:
            if p.startswith("#") or p.startswith("[사진") or p.startswith("|") or "[사진" in p[:8]:
                _txt_streak = 0
            else:
                _txt_streak += 1
                _max_streak = max(_max_streak, _txt_streak)
        if _max_streak >= 5:
            warnings.append(f"텍스트 {_max_streak}문단 연속(시각요소 없음) → 체류 이탈 위험(사진·표·소제목 삽입)")
            score -= 8
        # (v2 3-1) 허사·패딩 검사 — 결론 지연·무정보 클리셰 문장
        _PAD = ("에 대해 알아보겠습니다", "에 대해 알아보아요", "지금부터 알아보", "함께 알아보",
                "에 대해 살펴보겠습니다", "정리해보았습니다", "정리해 보았습니다", "도움이 되셨길", "포스팅을 시작")
        _pad_hits = [w for w in _PAD if w in _body]
        if _pad_hits:
            warnings.append(f"허사·패딩 표현 {_pad_hits[:3]} → 정보 밀도 저하(삭제)")
            score -= min(12, 4 * len(_pad_hits))
        # (v2 3-1) 동어반복 문단 — 정규화 후 60%+ 겹치는 문단쌍
        def _nrm(p):
            return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", p))
        _dup = 0
        _nts = [_nrm(p) for p in _bparas if len(p) >= 30]
        for _i in range(len(_nts)):
            for _j in range(_i + 1, len(_nts)):
                if _nts[_i] and _nts[_j]:
                    _ov = len(_nts[_i] & _nts[_j]) / len(_nts[_i] | _nts[_j])
                    if _ov > 0.6:
                        _dup += 1
        if _dup:
            warnings.append(f"동어반복 문단 {_dup}쌍 → 정보 밀도 저하(다른 정보로 교체)")
            score -= min(10, 5 * _dup)
        # (v2 1-5) 도입 훅 3요소 — 첫 3~4문장에 '읽을 이유 예고'(마지막·끝·아래·뒤에서) 신호
        _intro = " ".join(_bparas[:2])[:220]
        if _intro and not re.search(r"(마지막|끝까지|아래에서|뒤에서|끝에|글 후반|이 글에서.*알려|정리해 드릴|보여드릴게|확인하는 법)", _intro):
            warnings.append("도입에 '끝까지 읽을 이유' 예고 없음 → 초반 이탈 위험(v2 도입 훅 3요소)")
            score -= 6
        # 입력 원문 노출(생성품질 E2E #2): '썬팅,광택' 같은 쉼표 나열형이 제목/첫문단에 그대로 박히면 감점
        if re.search(r"[가-힣A-Za-z]{2,},[가-힣A-Za-z]{2,}", title + " " + text[:150]):
            warnings.append("쉼표 나열형 입력이 원문 그대로 노출 — 자연어로 풀어 쓰기('썬팅과 광택')")
            score -= 10
        # 1글 1키워드(생성품질 E2E #3): 타깃 외 추적 키워드가 소제목(##)으로 헤딩화되면 감점
        _heads = [ln.lstrip("#").strip() for ln in text.splitlines() if ln.strip().startswith("##")]
        for _ok in (payload.get("target_keywords") or [])[1:6]:
            if _ok and len(_ok) >= 4 and _ok != main_kw and any(_ok in h for h in _heads):
                warnings.append(f"타깃 외 키워드('{_ok}')가 소제목에 — 1글 1키워드 위반")
                score -= 8
                break
        # 절단 검증(V1): 재시도 후에도 max_tokens면 본문이 중간에서 끊긴 것 — 게이트 실패급
        if (payload.get("gen_finish") or "") == "max_tokens":
            warnings.append("생성이 토큰 한도로 절단됨(stop_reason=max_tokens) — 본문 미완결")
            score -= 15
        # ★ 제목 기초지역 이중 안전망(3번째 재발 방지): 셀러·병행 제목에 기초지역(구·군) 어간 → 게이트 실패
        _bizq = (payload.get("biz_type") or "").strip()
        _regq = (payload.get("region") or "")
        if _bizq in ("seller", "hybrid") and is_basic_region_kw(title, _regq, _bizq):
            _bad_reg = next((c for c in basic_region_cores(_regq) if c in title.replace(" ", "")), "")
            warnings.append(f"제목에 기초지역('{_bad_reg}') — 셀러·병행 글 타깃 부적합(차종·광역 롱테일로 재생성)")
            score -= 30
        # 업체명 정합(재검증 STEP 1-2a): 본문 업체명 ≠ 프로필 업체명 → 게이트 실패(-30)
        _bname = (payload.get("business_name") or "").strip()
        if _bname:
            if _bname not in (title + " " + text):
                warnings.append(f"프로필 업체명 '{_bname}' 미표기 — 상호 일관 신호 없음")
                score -= 12
            for _nm in re.findall(r"네이버(?:에서)?\s*['\"‘“]([^'\"’”]{2,25})['\"’”]\s*검색", text):
                _nm = _nm.strip()
                if _nm and _nm != _bname and _nm != (payload.get("brand_name") or "").strip():
                    warnings.append(f"본문 업체명 '{_nm}' ≠ 프로필 '{_bname}' — 업체명 불일치(게이트 실패)")
                    score -= 30
                    break
        if main_kw and main_kw not in title:
            warnings.append(f"제목에 핵심키워드 '{main_kw}' 없음 → 상위노출 크게 불리")
            score -= 12
        # 키워드 자연 변형(재검증 STEP 1-2b): 원형은 제목 1회만 — 본문은 자연 변형으로
        if main_kw:
            if main_kw != _kw_shorten(main_kw) and main_kw in text:
                warnings.append(f"본문에 키워드 원형 '{main_kw}' 그대로 — 자연 변형('{_kw_shorten(main_kw)}' 등)으로")
                score -= 8
            if _kw_variant_hits(text[:200], main_kw) == 0:
                warnings.append("첫 문단에 핵심키워드(자연 변형 포함) 없음 → 검색의도 매칭 약함")
                score -= 6
            if _kw_variant_hits(text, main_kw) < 2:
                warnings.append(f"핵심키워드 '{main_kw}'(자연 변형 포함) 본문 노출 부족(2회↓)")
                score -= 6
        _fulls = _ADMIN_FULL_RE.findall(text)
        if len(_fulls) >= 3:
            warnings.append(f"행정구역 풀네임 {len(_fulls)}회('{_fulls[0]}' 등) — 기계 삽입 티, 구어형으로")
            score -= 6
        if not any(s in text for s in ("자주 묻는", "Q&A", "Q.", "Q1")):
            warnings.append("FAQ(자주 묻는 질문) 없음 → Q&A·체류 가점 놓침")
            score -= 4
        if len(text) < 1000:
            warnings.append(f"본문 {len(text)}자 < 1000 (체류시간↓ → C-Rank 불리)")
            score -= 15
        if "##" not in text:
            warnings.append("소제목(##) 없음 → 구조/가독성 약함")
            score -= 5
        if "[사진1]" not in text:
            warnings.append("사진 마커 없음 → 체류시간↓")
            score -= 5
        if not any(w in text for w in _EXPERIENCE_WORDS):
            warnings.append("실제 경험·후기 표현 약함 → D.I.A 불리")
            score -= 12
        if len(re.findall(r"\d", text)) < 5:      # 구체 수치(소요시간·단계·전후) 부족(PHASE 9)
            warnings.append("구체 수치(시간·단계·전후) 부족 → D.I.A. 구체성↓")
            score -= 6
        if text.count("[사진") < 4:               # 이미지 4장 미만 → 정합·체류 신호 약함(PHASE 9)
            warnings.append(f"이미지 {text.count('[사진')}장 < 4 → 이미지 정합·체류 신호 약함")
            score -= 4
    elif kind in ("short",):
        if not payload.get("hook_strategy"):
            warnings.append("0~3초 훅 없음 → 시청유지↓")
            score -= 15
        d = payload.get("duration_sec", 0)
        if d and d > 60:
            warnings.append(f"{d}s > 60 → 완주율↓(30~45초 권장)")
            score -= 6
        elif d and d < 15:
            warnings.append(f"{d}s < 15 → 완주 절대량 미달로 도달↓(30~45초 권장)")
            score -= 6
    elif kind == "caption":
        n_tags = text.count("#")
        if n_tags < 3:
            warnings.append("해시태그 부족(<3)")
            score -= 5
        elif n_tags > 6:
            warnings.append(f"해시태그 과다({n_tags}개>6) → 2026 도달↓, 3~5개 권장")
            score -= 4
    elif kind == "x_post":
        if len(text) > 280:
            warnings.append("280자 초과")
            score -= 10

    score = max(0, min(100, score))
    grade = "우수" if score >= 85 else ("양호" if score >= 70 else "개선필요")
    return {"score": score, "grade": grade, "warnings": warnings}
