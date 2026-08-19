"""
SEO/성과 엔진 — 플랫폼별 '잘 팔리고 잘 노출되는' 콘텐츠 설계 규칙.
- target_keywords: 지역+업종+검색의도 기반 타겟 키워드(LLM 없이도 결정적 생성).
- *_DIRECTIVES: 각 플랫폼 성과/SEO 베스트프랙티스(프롬프트에 주입).
이 규칙이 곧 제품의 '성과 차별화'. 키워드는 검색량 있는 롱테일을 노린다.
"""
from __future__ import annotations

import re

# 검색 의도 수식어(구매 직전 키워드 = 전환율 높음). 3어절 롱테일 = 경쟁↓·전환↑(검색량 500~5,000 구간 노림).
# ★ 앞 4개만 **지역과 결합**된다(target_keywords의 `_INTENTS[:4]`).
#   여기에는 '지역+업종'과 붙여도 한국어가 되는 말만 둔다.
#   2026-08-16 실사고: 과정·시간을 여기 올렸더니 '부산 동구 썬팅업체 시간' 같은
#   **아무도 안 치는 조합**이 나왔고, 그게 그대로 소제목이 돼 AI 티가 났다.
#   헌법: 키워드는 손님이 치는 말로. 기계 조합은 키워드가 아니다.
_INTENTS = ["추천", "후기", "잘하는곳", "실력", "가격", "비용", "예약", "위치"]

#: 지역과 붙이면 어색한 축 — **업종 단독으로만** 쓴다('썬팅 과정'은 되고 '부산 동구 썬팅업체 과정'은 안 된다).
_SOLO_INTENTS = ["과정", "시간"]

#: 가격 의도 키워드를 타깃 후보에서 뺀다(2026-08-16 사장님 지시로 본문 금액 표기 중단).
#: 되돌릴 때는 이 값만 False로. 판정은 언어 규칙만 쓴다 — 업종어를 박지 않는다.
EXCLUDE_PRICE_KEYWORDS = True
_PRICE_KW = re.compile(r"가격|비용|얼마|요금|견적|시세|단가")


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


def _no_new_subject(cand: str, ctx_toks: set) -> bool:
    """주입 키워드 안전 판정(업종 중립) — 내 후보 어휘에 없는 '새 내용 명사'(다른 차종·제품·동네)를
    끌고 오는 키워드는 거부. 스키마 토큰 열거(phantom 필터)에 의존하지 않는 원천 차단.
    실사고(2026-07-27): 네이버 고검색량 연관어 '캐스퍼 중고'가 토레스 세트 후보에 주입 → 캡션 날조 연쇄."""
    import re as _r
    for t in _r.findall(r"[가-힣A-Za-z0-9]{2,}", cand or ""):
        if not any((t in c) or (c in t) for c in ctx_toks):
            return False
    return True


def _apply_volume(kws: list[str], limit: int, hints: list[str] | None = None) -> list[str]:
    """검색광고 API 있으면 실검색량 스윗스팟 키워드 2개를 보강(내 지역 키워드는 앞에 유지).
    ★ 보강 키워드는 내 후보 어휘의 재조합만 허용(_no_new_subject) — 세트와 무관한 인기 키워드 주입 차단."""
    import re as _r
    seeds = [h for h in (hints or kws)[:3] if h]
    vol = _volume_boost_cached("|".join(seeds))
    if not vol:
        return kws[:limit]
    ctx = {t for k in kws for t in _r.findall(r"[가-힣A-Za-z0-9]{2,}", k)}
    _rej = [v for v in vol if v and v not in kws and not _no_new_subject(v, ctx)]
    if _rej:
        import logging as _lg
        _lg.getLogger("shopcast.seo").warning("[volume-boost] 이질 소재 주입 거부(%d): %s",
                                              len(_rej), _rej[:4])
    extra = [v for v in vol if v and v not in kws and _no_new_subject(v, ctx)][:2]
    keep = kws[:max(1, limit - len(extra))]                     # 내 키워드 우선(첫 키워드=지역 유지)
    return list(dict.fromkeys(keep + extra))[:limit]


def drop_phantom_attr_kws(kws: list[str], industry: str, biz: str,
                          context_text: str = "", inventory_models: list | None = None) -> tuple:
    """★ Layer 1(2차 방어) — searchad 주입 등으로 섞인 '유령 속성 키워드' 제거(업종 중립).
    스키마 attribute_axes 속성 토큰(차종·향·메뉴 등)이 키워드에 있는데 그 토큰이 '현재 세트 컨텍스트
    (context_text) ∪ 재고(inventory_models)'에 없으면 = 이 가게가 안 파는 것 → 제거.
    (그랜저 딜러에 캐스퍼중고가격 / 라벤더 캔들집에 타향 / 카페에 타메뉴 키워드 차단.) 단어경계 동일.
    반환 (kept, dropped)."""
    import re as _r
    # ★ 재고 앵커(recent_inventory_context)로 '보유/미보유'를 판정하는 필터라 재고형(seller/hybrid) 전용.
    #   서비스업(local 방문형)은 재고가 없고 axis0 토큰(썬팅지·PPF 등)이 전부 '정당한 시공/메뉴 어휘'라
    #   재고 부재를 근거로 지우면 오탐(썬팅지 오제거 사고). local은 필터 미적용 — 전량 통과.
    if (biz or "local") not in ("seller", "hybrid"):
        return list(kws), []
    try:
        from app.services import indschema as _isc
        sch = _isc.get_schema(industry, biz)
        axis0 = [t for t in ((sch.get("attribute_axes") or [{}])[0].get("tokens") or []) if t]  # 1축=핵심 속성
    except Exception:
        axis0 = []
    if not axis0:
        return list(kws), []                                    # 속성 앵커 없는 업종 → 필터 무의미(통과)
    def _wb(tok, text):
        return bool(tok) and bool(_r.search(r"(?<![가-힣])" + _r.escape(tok), text or ""))
    allowed = {a for a in axis0 if _wb(a, context_text)}
    for m in (inventory_models or []):
        mn = " ".join((m or "").split())
        for a in axis0:
            if a and (a in mn or _wb(a, mn)):
                allowed.add(a)
    kept, dropped = [], []
    for k in kws:
        bad = [a for a in axis0 if _wb(a, k) and a not in allowed]
        (dropped if bad else kept).append(k if not bad else (k, ",".join(bad)))
    return kept, dropped


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
    # 트레이드 접미(중고·추천 등)는 업종 스키마 search_grammar 리터럴에서 파생(차량 하드코딩 0)
    _suf = ""
    try:
        from app.services import indschema as _isc
        _lits = []
        for _g in (_isc.get_schema(industry, "seller").get("search_grammar") or []):
            _lits += re.findall(r"[가-힣]{2,}", re.sub(r"\{[^}]*\}", " ", _g))
        _suf = next((w for w in _lits if w not in ("추천",)), _lits[0] if _lits else "")
    except Exception:
        pass
    if reg2 and industry.strip():
        kws.append(f"{reg2} {industry.strip()}")                      # 지역+업종: '부산 기장 중고차'
    if model and year:
        kws.append(f"{model} {year} {_suf}".strip())                  # 속성+연식+트레이드어: '모닝 2019 중고'
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


REGION_MIN_VOLUME = 100    # 기초지역 지명 표면 허용 최소 월검색량(기장 월20 류 차단 — 키워드 관문과 동일 기준)


def _do_abbrev(stem: str) -> str:
    """도 이름 구어 축약 — '경상남'→'경남', '충청북'→'충북', '전라남'→'전남'.
    ★ 2026-08-02 실측 결함: 접미사만 떼면 '경상남'처럼 아무도 쓰지 않는 말이 표면에 나간다.
    언어 규칙만 쓴다(어간 3글자 + 방위자 끝 → 1번째+3번째). 지명 하드코딩 0 —
    '경기'·'강원'·'제주'는 어간이 2글자라 그대로 통과한다."""
    return stem[0] + stem[2] if len(stem) == 3 and stem[2] in ("남", "북") else stem


def _region_wide(region: str) -> str:
    """광역 어간 — '부산광역시 기장군' → '부산', '경상남도 김해시' → '경남'."""
    return next((_do_abbrev(_re_g.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|도)$", "", tk))
                 for tk in (region or "").split()
                 if _re_g.search(r"(특별시|광역시|특별자치시|특별자치도|도)$", tk)), "")


def _dedup_tokens(s: str) -> str:
    """같은 지역 토큰이 반복되면 하나로(2026-08-02 실사고: 타깃 키워드에 '부산 부산').
    광역과 기초지역이 같은 값일 때 'wide + basic'이 그대로 이어붙던 경로를 막는다.
    언어 규칙만 — 지명 하드코딩 0."""
    out = []
    for w in (s or "").split():
        if w and w not in out:
            out.append(w)
    return " ".join(out)


def canonical_industry(category: str) -> str:
    """지도 카테고리 → 사람이 실제로 치는 업종어. 단일 관문(2026-08-14).

    사고: 상호로 가게를 찾아 카테고리를 그대로 업종으로 썼더니 '광택전문'이 됐고,
      진단이 '부산 광택전문'(월 20회)으로 돌았다. 실측 '부산 광택'은 250회 —
      12배 차이다. 카테고리는 업체 분류명이지 검색어가 아니다.
      (지명에서 '부산광역시 썬팅'을 잡았던 것과 같은 계열: 우리 말 vs 사람 말)

    언어 규칙만 쓴다 — 업종 목록 하드코딩 0.
      ① 여러 분류가 붙으면 첫 번째만('썬팅,광택' → '썬팅')
      ② 분류 접미사를 뗀다('광택전문' → '광택', '카페전문점' → '카페')
      ③ 떼고 남은 말이 너무 짧으면 원형 유지(과교정 방지)
    """
    c = re.split(r"[,·/|>]", (category or "").strip())[0].strip()
    if not c:
        return ""
    m = re.match(r"^(.*?)(전문점|전문|점포|매장)$", c)
    if m and len(m.group(1)) >= 2:
        return m.group(1).strip()
    return c


def canonical_region(region: str, biz_type: str = "local", industry: str = "",
                     allow_region_hook=None, verify_volume: bool = True) -> str:
    """★ 세트 지역 토큰 단일 소스(canonical) — 지역이 등장하는 전 표면(제목·훅·태그·해시태그·영상)이 참조.
    키워드 관문과 동일 기준: 기초지역(구·군)은 '검색량 관문 통과 시에만' 허용, 아니면 광역.
    셀러·hook=False는 '' (지역 토큰 미주입). 업종 중립(특정 지명 하드코딩 0).
    반환: 표면용 지역 문자열('부산' / '부산 기장' / '')."""
    biz = (biz_type or "local")
    if biz == "seller":
        return ""                                        # 전국 셀러 → 지역 토큰 없음(hook 규칙과 무관하게 biz가 권위)
    # 하이브리드·매장은 스키마 hook=False여도 지역 적용(부산 광역은 로컬 SEO 신호). allow_region_hook은 호환용.
    _ = allow_region_hook
    wide = _region_wide(region)
    cores = basic_region_cores(region)
    if not cores:
        return _dedup_tokens(wide or _kw_shorten(region))   # 기초지역 없음 → 광역
    basic = cores[0]
    ind0 = ((industry or "").replace("/", ",").split(",")[0] or "").strip()
    if verify_volume and ind0:
        try:
            from app.services import searchad as _sa
            if _sa.configured():
                vv = {(_v.get("keyword") or "").replace(" ", ""): (_v.get("total") or 0)
                      for _v in _sa.keyword_volumes([f"{basic} {ind0}", f"{wide} {ind0}"], limit=10)}
                if vv.get((basic + ind0).replace(" ", ""), 0) >= REGION_MIN_VOLUME:
                    return _dedup_tokens(f"{wide} {basic}".strip() if wide else basic)
                return _dedup_tokens(wide or basic)      # 미달 → 광역(기장 배제)
        except Exception:
            pass
    return _dedup_tokens(wide or basic)                  # 무키/미검증 → 광역(안전)


def _kw_rank_tier(kw: str, models: list, classes: list, wide: str, ind0: str) -> int:
    """매물 속성 서열 — 낮을수록 우선. 0:[속성+연식/거래어] 1:[분류+거래어] 2:[광역+속성] 3:[광역+업종] 4:기타.
    속성·분류는 세트 컨텍스트 + 업종 스키마 attribute_axes에서 공급(차량 하드코딩 제거·전 업종)."""
    k = (kw or "").replace(" ", "")
    _mset = set(m.replace(" ", "") for m in models if m)
    _cset = set(c.replace(" ", "") for c in classes if c)
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


def _gap_first(cands: list, tenant_id: str = "", note: str = "") -> list:
    """🕳 빈자리 키워드를 후보 앞으로(2026-08-02 사장님 지적).

    사고: 소나타 DN8 신차 종합시공 소재를 올렸는데 타깃이 '부산 동구 썬팅업체'로 잡혔다.
    같은 날 정찰이 '신차 썬팅'을 빈자리(자리 있음·우리 글 없음·검색량 통과)로 찾아뒀는데
    소재가 딱 맞는데도 안 쓰였다 — 정찰과 생성이 이어져 있지 않았다.

    ★ 지어내지 않는다. 두 조건을 모두 만족할 때만 앞으로 낸다:
      ① 판정이 '확실'이고 점수 > 0 (자리 있음 · 검색량 통과 · 사장님 영역)
      ② 이 소재(note)가 그 키워드를 실제로 뒷받침한다(의미 낱말 2개 이상 겹침)
    소재가 없는 키워드를 밀어 넣으면 사진에 없는 걸 쓰게 된다 — 날조 유도다.
    """
    if not (cands and tenant_id and note):
        return cands
    try:
        from app.services import gapscout as _gs
        gaps = [g for g in _gs.list_gaps(tenant_id, domain="확실", limit=20)
                if (g.get("score") or 0) > 0]
    except Exception:
        return cands
    if not gaps:
        return cands
    _nt = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or "")}
    hits = []
    for g in gaps:
        kt = {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", g["keyword"])}
        if len(kt & _nt) >= 2:                     # 소재가 뒷받침하는 것만
            hits.append((g["score"], g["keyword"]))
    if not hits:
        return cands
    hits.sort(reverse=True)
    front = [k for _s, k in hits]
    rest = [c for c in cands if c not in front]
    return front + rest


def _surface_first(cands: list, tenant_id: str = "") -> list:
    """🧱 통합검색 지면 신호 반영(2026-08-01 실측) — 블로그 글이 아무리 좋아도 그 키워드의
    통합검색 첫 화면에 '블로그 지면'이 없으면 노출로 이어지지 않는다(부산 썬팅·썬팅업체 등 실측 0건,
    반면 '부산 동구 썬팅'은 인기글 블록이 살아 있고 우리 글이 실제 노출 중).
    정찰 데이터가 있는 키워드만 재정렬 — 데이터 없으면 순서 유지(안전). 하드코딩 0."""
    if not (cands and tenant_id):
        return cands
    try:
        from app.services import blogreach as _brc
        scored = []
        for i, c in enumerate(cands):
            b = _brc.blocks_for(tenant_id, c) or {}
            surf = b.get("blog_surface")
            # 지면 있음=0(앞) / 미상=1(중립) / 지면 없음=2(뒤) — 원 순서는 tie-break로 보존
            rank = 0 if surf is True else (2 if surf is False else 1)
            scored.append((rank, i, c))
        scored.sort()
        return [c for _, _, c in scored]
    except Exception:
        return cands


#: 업종명 뒤에 붙는 **일반 업태어** — 업종이 아니라 '업소'를 뜻하는 말이다.
#: 2026-08-19 실측: 프로필 업종명이 '썬팅업체'라 지면 판정이 제목에서 '썬팅업체'를 찾았다.
#: 상위 글 제목은 '부산 썬팅 후기'처럼 쓰지 '썬팅업체'라고 쓰지 않는다 → 살아 있는 판까지 죽었다.
#: ★ 업종어가 아니라 업태어 목록이다(어느 업종에나 똑같이 붙는다) — 업종 중립 유지.
GENERIC_BIZ_SUFFIX = ("업체", "전문점", "전문", "매장", "가게", "센터", "샵")


def industry_core(industry: str) -> str:
    """'썬팅업체'→'썬팅', '인테리어 전문점'→'인테리어'. 판정·후보 생성의 공통 어간."""
    t = re.sub(r"\s+", "", (industry or "").split(",")[0])
    for suf in GENERIC_BIZ_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf) + 1:
            return t[: -len(suf)]
    return t


#: 이 미만이면 1위를 해도 손님이 오지 않는다. 월 100회 = 하루 3~4명.
#: 2026-08-19 실측 — 주력 키워드가 월 20회(하루 0.7명)였다.
MIN_MONTHLY_VOLUME = 100


#: 상위글이 이만큼 오래됐으면 '아무도 관리하지 않는 자리' — 올라가면 지킬 수 있다.
#: 반대로 최근 글이 계속 들어오는 자리는 1위를 해도 곧 밀린다.
STALE_TOP_DAYS = 180


def slot_score(keyword: str, volume: int, docs: int, top_age_days: "int | None") -> dict:
    """'치고 들어갈 자리'인가 — 세 축으로 판정한다(2026-08-19 사장님 지시).

    사장님: "이길 수 있는 자리에 글을 써야 1위를 뛰어넘고 1위를 지속할 수 있다."

    ① 수요   — 검색량. 이게 없으면 1위여도 손님이 안 온다(월 20회 = 하루 0.7명).
    ② 경쟁   — 기회지수(검색량÷문서수). 공급이 수요보다 많으면 뚫기 어렵다.
    ③ **지속** — 상위글 나이. 낡았으면 아무도 관리하지 않는 자리라 **지킬 수 있다.**
                 최근 글이 계속 들어오는 자리는 올라가도 밀린다.

    실측(루마썬팅, 2026-08-19):
      부산 썬팅      670회 · 26만건 · 494일  → 수요 있고 상위글 낡음 = **최선**
      썬팅 가격    4,280회 · 86만건 ·  37일  → 수요 크지만 새 글이 계속 들어옴
      차량 썬팅    1,770회 · 226만건 ·  51일  → 레드오션
      부산 동구 썬팅   30회 · 3,489건 · 2076일 → **기회지수 1위인데 수요가 없다**
    ★ 기회지수만 보면 아무도 안 찾는 말이 1등으로 뽑힌다 — 그게 지금까지의 함정이었다.
    """
    vol = int(volume or 0)
    opp = (vol / docs) if docs and docs > 0 else 0.0
    stale = bool(top_age_days and top_age_days >= STALE_TOP_DAYS)
    if vol < MIN_MONTHLY_VOLUME:
        return {"keyword": keyword, "ok": False, "why": f"수요 없음(월 {vol}회)",
                "vol": vol, "docs": docs, "opp": opp, "age": top_age_days, "rank": -1}
    # 점수 = 수요(로그) × 기회지수 × **지속성**
    #   ★ 지속성이 결정적이다(2026-08-19). 처음엔 stale에 ×1.6만 줬더니
    #     '썬팅 가격'(4,280회·86만건·상위글 37일)이 1등으로 뽑혔다.
    #     수요는 크지만 새 글이 계속 밀고 들어오는 자리라 **1위를 해도 지키지 못한다.**
    #     사장님: "이길 수 있는 자리에 글을 써야 1위를 뛰어넘고 1위를 지속할 수 있다."
    #     → 낡지 않은 자리는 **감점**한다(×0.5). 올라가는 것보다 지키는 것이 어렵다.
    #   ★ 수요를 로그로 누르는 이유 — 안 그러면 문서 226만 건짜리 거대 키워드가 독식한다.
    import math
    score = math.log10(max(vol, 10)) * (1 + opp * 200) * (1.6 if stale else 0.5)
    why = ("상위글이 낡아 지킬 수 있다" if stale else
           "수요는 있으나 상위가 최근 글로 계속 갱신된다(지키기 어렵다)")
    return {"keyword": keyword, "ok": True, "why": why, "vol": vol, "docs": docs,
            "opp": opp, "age": top_age_days, "stale": stale, "rank": score}


def _volume_first(cands: list, verify: bool = True, deep: bool = True,
                  industry: str = "") -> str:
    """후보 중 **가장 이길 만한 자리**를 고른다(매장·셀러 공통 관문).

    deep=True면 문서 수·상위글 나이까지 재서 slot_score로 정렬한다(무료 API, 후보 5개까지).
    deep=False면 검색량 하한만 본다(빠른 경로).

    ★ 무측정(None)은 통과시킨다 — 검색광고 API가 못 재는 말도 있고,
      임의 숫자로 채우면 그게 날조다(정직 게이트). 다만 **0으로 측정된 것은 버린다.**
    ★ 전부 미달이면 빈 문자열 — 부르는 쪽이 자기 폴백을 쓴다.
    """
    if not cands:
        return ""
    if not verify:
        return cands[0]
    import logging as _lgv
    _log = _lgv.getLogger("shopcast.seo")
    try:
        from app.services import searchad as _sa
        if not _sa.configured():
            return cands[0]
        vols = {(v.get("keyword") or "").replace(" ", ""): v.get("total")
                for v in _sa.keyword_volumes(cands[:8], limit=80)}
    except Exception:
        return cands[0]                      # 조회 실패로 생성을 막지 않는다

    passed, skipped = [], []
    for c in cands:
        v = vols.get(c.replace(" ", ""))
        if v is None:                        # 못 잰 말 — 통과시키되 순위는 뒤로
            passed.append((c, None))
        elif v >= MIN_MONTHLY_VOLUME:
            passed.append((c, v))
        else:
            skipped.append(f"{c}({v}회)")
    if not passed:
        _log.warning("[자리 판정] 후보 전부 월 %d회 미만 — %s", MIN_MONTHLY_VOLUME, skipped[:6])
        return ""
    if not deep:
        return passed[0][0]

    # 🎯 이길 자리 판정 — 문서 수·상위글 나이까지 실측(네이버 API 무료, 24h 캐시)
    try:
        import datetime as _dt
        from app.services import blogrank as _br
        if not _br.configured():
            return passed[0][0]
        now, scored, dead = _dt.datetime.utcnow(), [], []
        _ind = industry_core(industry)
        for c, v in passed[:5]:
            if v is None:
                continue
            try:
                docs = _br.doc_count(c) or 0
                items = _br._search_blog(c, 10)
                ages = []
                for it in items[:5]:
                    try:
                        ages.append((now - _dt.datetime.strptime(
                            it.get("postdate", ""), "%Y%m%d")).days)
                    except Exception:
                        pass
                age = int(sum(ages) / len(ages)) if ages else None
                # 🧱 **지면이 살아 있는가** — 상위 10개가 이 업종 글이 아니면 그 쿼리엔 판이 없다.
                #   실측(2026-08-19): '부산 썬팅업체'(월 100회+) 상위 10개가 '평택시 지역화폐',
                #   '국민내일배움카드' 같은 스팸이었다. 죽은 '부산 동구 썬팅업체'와 같은 모양이다.
                #   여기서 1위를 해도 잘해서가 아니라 아무도 없어서다 — 손님은 오지 않는다.
                #   ★ 업종어는 인자로만 온다(하드코딩 0). 못 재면(검색 실패) 판정하지 않는다.
                if _ind and items:
                    _hit = sum(1 for it in items
                               if _ind in re.sub(r"<[^>]+>|\s+", "",
                                                 (it.get("title") or "") + (it.get("description") or "")))
                    if _hit < 2:
                        dead.append(f"{c}(지면 무관 {_hit}/{len(items)})")
                        continue
                scored.append(slot_score(c, v, docs, age))
            except Exception:
                continue
        scored = [s for s in scored if s.get("ok")]
        if dead:
            _log.warning("[자리 판정] 지면 없음 제외 — %s", dead[:4])
            if not scored:
                # ★ 전부 죽은 판이면 **빈 문자열**로 돌려준다(헌법: 침묵 폴백 금지).
                #   여기서 passed[0]을 그냥 돌려주면 방금 '죽었다'고 판정한 자리를
                #   그대로 쓰게 된다 — 실제로 그래서 '부산 썬팅업체'가 다시 뽑혔다.
                _log.warning("[자리 판정] 살아 있는 판이 없다 — 폴백에 맡긴다")
                return ""
        if scored:
            scored.sort(key=lambda s: -s["rank"])
            best = scored[0]
            _log.info("[자리 판정] 선택 %r — 월 %s회·문서 %s건·상위글 %s일 (%s)%s",
                      best["keyword"], f"{best['vol']:,}", f"{best['docs']:,}",
                      best["age"], best["why"],
                      (" · 제외 " + ", ".join(skipped[:4])) if skipped else "")
            return best["keyword"]
    except Exception:
        pass
    if skipped:
        _log.info("[자리 판정] 수요 미달 제외 %s → 선택 %r", skipped[:5], passed[0][0])
    return passed[0][0]


def select_target_keyword(candidates: list, biz_type: str = "local", region: str = "",
                          industry: str = "", tenant_id: str = "", verify_volume: bool = True,
                          primary_model: str = "", allow_inventory_rank: bool = False,
                          note: str = "") -> str:
    """★ 타깃 키워드 최종 선택 단일 관문(오토큐·직접생성 공통).
    ① 기초지역(구·군) 하드 배제(셀러·병행) ② 매물 속성 서열 정렬 ③ 검색량 검증(월 100회+, 실패 시 스킵).
    후보 전부 탈락하면 광역+업종 폴백. 매장(local)은 지역 규칙 미적용(원 후보 유지)."""
    cands = [" ".join((c or "").split()) for c in (candidates or []) if c and c.strip()]
    cands = list(dict.fromkeys(cands))
    biz = (biz_type or "local")
    ind0 = ((industry or "").replace("/", ",").split(",")[0] or "").strip()
    cands = _surface_first(cands, tenant_id)             # ★ 통합검색에 블로그 지면이 있는 판을 앞으로
    cands = _gap_first(cands, tenant_id, note)           # ★ 소재가 뒷받침하는 빈자리를 그보다 앞으로
    if biz not in ("seller", "hybrid"):
        # 🔍 2026-08-19 — **매장(local)도 검색량 관문을 거친다.**
        #   실사고: 루마썬팅(local)의 주력 키워드가 '부산 동구 썬팅업체'였는데 **월 20회**였다.
        #   12편을 그 키워드로 썼고, 1위를 해도 하루 0.7명이라 손님이 오지 않는다.
        #   그 검색 결과 상위에는 썬팅 글이 하나도 없었다(지역화폐·직업훈련비·2008년 글) —
        #   네이버가 그 쿼리에 블로그를 제대로 안 뿌린다는 뜻이고, 우리가 1위였던 건
        #   잘해서가 아니라 **아무도 없어서**였다.
        #   원인은 이 한 줄이었다 — 셀러만 검증하고 매장은 `cands[0]`을 그대로 돌려줬다.
        #   함수 설명에는 '③ 검색량 검증(월 100회+)'이 있었는데 그 코드가 셀러 분기 안에만 있었다.
        #   헌법 금지선: '검색량 없는 키워드 욱여넣기'.
        _local_fb = (f"{_kw_shorten(region)} {ind0}".strip() if ind0 else "")
        # 🗺 매장은 **지역이 붙은 후보 안에서만** 자리를 고른다(2026-08-19 실측으로 추가).
        #   관문을 열자마자 '썬팅 추천'(전국 1,020회·문서 65만)이 뽑혔다. 상위글이 낡아
        #   점수는 높지만, 부산 동구 가게가 전국 키워드에서 1위를 지킬 수도 없고
        #   1위를 해도 검색자가 전국이라 가게에 손님이 오지 않는다 — 존재 이유(1항)에 어긋난다.
        #   지역 후보가 전부 수요 미달이면 빈 문자열로 두고 부르는 쪽 폴백에 맡긴다.
        _wide = _region_wide(region)
        # ★ 광역 자리를 후보에 넣어준다(2026-08-19 실측). 후보 생성기는 구·군 조합만 만든다 —
        #   '부산 동구 썬팅'(월 30회) 계열뿐이라, 관문을 통과시켜도 고를 것이 없었다.
        #   같은 판의 광역 자리 '부산 썬팅'은 월 670회다. 이게 실제로 이길 수 있는 자리다.
        _core = industry_core(ind0)
        for _w in ([f"{_wide} {_core}", f"{_wide} {ind0}"] if _wide and ind0 else []):
            #   ★ 어간 쪽('부산 썬팅')을 먼저 — 업태어가 붙은 말('부산 썬팅업체')은
            #     검색량이 있어도 지면이 죽어 있는 경우가 많다(2026-08-19 실측).
            if _w.strip() and _w not in cands:
                cands = cands + [_w]
        _wide_kw = f"{_wide} {_core}".strip() if (_wide and ind0) else ""
        if _wide_kw:                          # 폴백도 광역으로 — 구·군 폴백은 수요가 없다
            _local_fb = _wide_kw
        _in_region = [c for c in cands if _wide and _wide in c.replace(" ", "")]
        _picked = _volume_first(_in_region or cands, verify_volume, industry=ind0)
        if not _picked and _in_region:
            import logging as _lgl
            _lgl.getLogger("shopcast.seo").warning(
                "[자리 판정] 지역(%s) 후보 %d개 전부 수요 미달 — 제네릭 폴백", _wide, len(_in_region))
        return _picked or _local_fb
    # 기초지역 배제
    cands = [c for c in cands if not is_basic_region_kw(c, region, biz)]
    # 매물 속성(핵심 속성·분류) — 세트 컨텍스트 + 업종 스키마 attribute_axes에서 공급(전 업종)
    models, classes = [], []
    # ★ tenant 전체 인벤토리로 후보 랭킹 = 타세트 매물 유입 통로 → 기본 차단. '매물 목록형' 등 정당한 곳만
    #   allow_inventory_rank=True로 명시 허용. 일반 세트는 primary_model(현재 세트)로 이미 확정됨.
    if tenant_id and allow_inventory_rank:
        try:
            from app import db as _db
            for ctx in _db.recent_inventory_context(tenant_id, limit=6):
                if ctx.get("model"):
                    models.append(ctx["model"])
                if ctx.get("car_class"):
                    classes.append(ctx["car_class"])
        except Exception:
            pass
    try:
        from app.services import indschema as _isc
        _sch = _isc.get_schema(industry, biz)
        _axes = _sch.get("attribute_axes") or []
        for _t in (_axes[0].get("tokens") if _axes else []) or []:   # 1축=핵심 속성(차종·메뉴·향)
            models.append(_t)
        for _ax in _axes[1:]:                                        # 이후 축=분류(차급·용량 등)
            classes += [t for t in (_ax.get("tokens") or [])]
    except Exception:
        pass
    wide = _region_wide(region)          # 광역 어간 단일 소스(도 축약 포함)
    pm = (primary_model or "").strip()
    if pm:
        # 이번 업로드 매물 모델 = 반드시 타깃(허위·미끼 방지). 검색량 무관 즉시 확정(다른 차종으로 새는 것 원천 차단).
        pmf = pm.replace(" ", "")
        _pm_cand = next((c for c in cands if pmf in c.replace(" ", "")), "")
        return _pm_cand or f"{pm} 중고"
    def _tier(c):
        return _kw_rank_tier(c, models, classes, wide, ind0)
    cands.sort(key=_tier)
    # 검색량 검증 — 매장·셀러가 **같은 관문**을 쓴다(2026-08-19).
    #   전에는 이 로직이 셀러 분기 안에만 있어서 매장은 검증 없이 통과했고,
    #   그래서 월 20회짜리가 주력 키워드가 됐다. 같은 판정이 두 곳에 살면 한쪽만 낫는다.
    fallback = f"{wide} {ind0} 추천".strip() if wide else (f"{ind0} 추천" if ind0 else "")
    return _volume_first(cands, verify_volume, industry=ind0) or fallback or (cands[0] if cands else "")


def parent_keyword(kw: str, region: str = "", address: str = "") -> str:
    """계층 공략(헤드 빌드업, 실측 요구 2026-07-26) — '광역+기초+업종' 키워드에서 기초지역을 뺀
    상위 변형. 예: '부산 기장 중고차판매' → '부산 중고차판매' / 실검색량이 더 큰 자연 축약형
    ('부산 중고차')이 있으면 그쪽 선택(searchad 실측, 무키 시 원형 유지). 도출 불가면 ''.
    기초지역 어간은 region+address 양쪽에서 수집 — region이 '부산'처럼 짧아도 주소의 '기장군'으로 판별."""
    toks = (kw or "").split()
    if len(toks) < 3:
        return ""
    base_stems = set()
    _srcs = [t for t in (region or "").split()[1:]] + [t for t in (address or "").split()]
    for t in _srcs:                                        # region 둘째 토큰부터 + 주소 전 토큰
        if not re.search(r"(군|구|읍|면|동|리)$", t):       # 주소는 행정 접미 토큰만(도로명·번지 배제)
            continue
        core = re.sub(r"(특별자치시|특별자치도|광역시|자치도|군|구|읍|면|동|리|시)$", "", t)
        if len(core) >= 2:
            base_stems.add(core)
    drop_i = None
    for i, t in enumerate(toks[1:], 1):
        core = re.sub(r"(군|구|읍|면)$", "", t)
        if re.search(r"(군|구|읍|면)$", t) or core in base_stems:
            drop_i = i
            break
    if drop_i is None:
        return ""
    parent = " ".join(toks[:drop_i] + toks[drop_i + 1:]).strip()
    if not parent or parent == kw or len(parent.split()) < 2:
        return ""
    cands = [parent]
    tail = parent.split()[-1]                              # 업종어 자연 축약 변형(판매·매매 등 접미 제거)
    short_tail = re.sub(r"(판매|매매|업체|전문점|전문)$", "", tail)
    if short_tail and short_tail != tail and len(short_tail) >= 2:
        cands.append(" ".join(parent.split()[:-1] + [short_tail]))
    if len(cands) > 1:
        try:
            from app.services import searchad
            if searchad.configured():
                vols = {(v.get("keyword") or "").replace(" ", ""): (v.get("total") or 0)
                        for v in searchad.keyword_volumes(cands)}
                cands.sort(key=lambda c: -(vols.get(c.replace(" ", ""), 0)))
        except Exception:
            pass
    return cands[0]


def region_conflict(kw: str, region: str) -> bool:
    """키워드가 '가게 지역과 다른 지역'을 겨냥하는지 판정(지역 정합 게이트, 2026-08-01).
    지역 사전 하드코딩 없음 — LLM YES/NO, (키워드|지역) 7일 캐시. 무키·실패 False(막지 않음)."""
    kw = " ".join((kw or "").split())
    reg = " ".join((region or "").split())
    if not (kw and reg):
        return False
    try:
        from app import ratelimit as _rl
        _ck = f"kwregion:{kw}|{reg}"
        _hit = _rl.cache_get(_ck, 7 * 86400)
        if _hit is not None:
            return bool(_hit.get("conflict", False))
    except Exception:
        _rl = None
        _ck = ""
    try:
        from app import llm as _llm
        v = _llm.call_task("judge",
            f"키워드: '{kw}' / 가게 소재지: '{reg}'\n"
            "이 키워드 안에 한국의 지역명(시·군·구·동네)이 포함되어 있고, 그 지역이 가게 소재지와 "
            "명백히 다른 생활권이면 YES. 지역명이 없거나, 가게 소재지와 같은 지역(상위 광역 포함 — "
            "예: 가게가 '부산 동구'면 '부산'은 같은 지역)이면 NO. YES 또는 NO 한 단어만.",
            max_tokens=10)
        conflict = "YES" in (v or "").strip().upper()[:8]
    except Exception:
        conflict = False
    try:
        if _rl and _ck:
            _rl.cache_set(_ck, {"conflict": conflict})
    except Exception:
        pass
    return conflict


# 🗣 공급자 쪽 접미어 — 사업자등록증·업종분류에 쓰는 말이지 손님이 검색창에 치는 말이 아니다.
#   언어 단위 목록일 뿐 업종·가게 하드코딩이 아니다(어느 업종에나 같은 규칙으로 적용).
#   실측 2026-08-01: '중고차판매' 6,580회 vs '중고차' 271,600회(41배) — 기회지수도 거의 꼴찌.
_SUPPLIER_TAIL = ("판매", "매매", "시공", "제작", "제조", "가공", "설치", "수리", "정비",
                  "도매", "소매", "유통", "납품", "대행", "서비스업", "전문점", "전문", "업")


def searcher_term(industry: str) -> str:
    """업종명을 '손님이 실제로 검색하는 말'로 바꾼다(2026-08-01 사장님 지적).
    방법: 공급자 접미어를 떼어낸 변형들을 만들고 **실측 검색량·기회지수로 승부**를 붙인다.
    사전에 정답을 박아두지 않는다 — 이긴 쪽이 정답이다(업종 하드코딩 0). 30일 캐시.
    키 없음·조회 실패 시 원본 유지(기존 동작)."""
    base = " ".join((industry or "").split())
    if len(base) < 3:
        return base
    cands, cur = [base], base
    while True:                    # ★ '중고차판매업' → '중고차판매' → '중고차'까지 벗긴다
        nxt = next((cur[: -len(t)].strip() for t in _SUPPLIER_TAIL
                    if cur.endswith(t) and len(cur) - len(t) >= 2), "")
        if not nxt or nxt in cands:
            break
        cands.append(nxt)
        cur = nxt
    cands = list(dict.fromkeys([c for c in cands if len(c) >= 2]))
    if len(cands) < 2:
        return base
    try:
        from app import ratelimit as _rl
        _ck = "searcherterm:" + base
        _hit = _rl.cache_get(_ck, 30 * 86400)
        if _hit is not None:
            return _hit.get("term") or base
    except Exception:
        _rl = None
        _ck = ""
    best = base
    try:
        from app.services import searchad as _sa
        if not _sa.configured():
            return base
        vols = _sa.volume_map(cands) or {}       # ★ 키는 공백 제거형으로 돌아온다
        from app.services import blogrank as _br
        scored = []
        for c in cands:
            v = int(vols.get(c.replace(" ", "")) or 0)
            if v <= 0:
                continue
            try:
                d = int(_br.doc_count(c) or 0)
            except Exception:
                d = -1
            if d <= 0:
                continue          # ★ doc_count는 실패 시 -1 — '공급 0'으로 읽으면 기회지수가
                                  #   폭등해 오답이 30일 캐시된다(검토 지적). 실패 후보는 버린다.
            # 기회지수 = 검색량 / 문서수(공급) — 검색량만 크고 이미 포화된 말은 이기지 못한다
            scored.append((v / d, v, c))
        if scored:
            scored.sort(reverse=True)
            best = scored[0][2]
            if best != base:
                import logging as _lgs2
                _lgs2.getLogger("shopcast.seo").info(
                    "[손님말] 업종명 %r → 검색어 %r (검색량 %d)", base, best, scored[0][1])
    except Exception:
        return base
    try:
        if _rl and _ck:
            _rl.cache_set(_ck, {"term": best})
    except Exception:
        pass
    return best


def keyword_intent_ok(kw: str, industry: str, biz: str, content_type: str, note: str = "") -> bool:
    """키워드-소재 의도 정합 검증(제목 개선 ①) — 검색량이 커도 '이 글이 그 검색의 답이 되는' 키워드만.
    실측 결함: '자동차판매순위'(브랜드 판매량 통계 의도)가 중고 매물 글 타깃으로 선정 → 제목 어색 + 이탈 유발.
    LLM 1콜(YES/NO), (키워드|업종|글유형) 단위 7일 캐시. 무키·실패 시 True(막지 않음 — 기존 동작 유지)."""
    kw = " ".join((kw or "").split())
    if not kw:
        return True
    ind0 = ((industry or "").replace("/", ",").split(",")[0] or "").strip()
    try:
        from app import ratelimit as _rl
        _ck = f"kwintent:{kw}|{ind0}|{content_type}"
        _hit = _rl.cache_get(_ck, 7 * 86400)
        if _hit is not None:
            return bool(_hit.get("ok", True))
    except Exception:
        _rl = None
        _ck = ""
    try:
        from app import llm as _llm
        _bz = {"local": "동네 매장(방문 손님 유치)", "seller": "온라인 판매자",
               "hybrid": "매장+온라인"}.get(biz or "local", "매장")
        _snip = " ".join((note or "").split())[:200]
        v = _llm.call_task("judge",
            f"'{kw}'를 네이버에 검색하는 사람의 의도를 판단하라.\n"
            f"글 주인: {ind0} 업종의 {_bz}. 이번 글 소재: {_snip or ind0}\n"
            "이 검색자가 '이 가게의 실제 매물·시공·상품·서비스 소개 글'에서 원하는 답을 얻는가?\n"
            "브랜드 판매량 순위·시세 통계·뉴스·연예 등 '자료 조사' 의도라서 가게 글이 답이 못 되면 NO.\n"
            # ★ 거래 방향 검사(2026-08-01 사장님 지적) — 실측: 중고차 '판매' 가게에
            #   '중고차팔기'(자기 차를 팔려는 사람)가 확장 키워드로 뽑혔다. 정반대 손님이라
            #   글이 아무리 좋아도 문의로 이어지지 않는다. 전 업종 공통(사고파는 방향, 맡기고 받는 방향).
            "★거래 방향: 이 검색자는 가게와 '거래 방향'이 맞는가? 가게가 파는 쪽이면 검색자는 사려는 "
            "사람이어야 하고, 가게가 사들이는 쪽이면 검색자는 팔려는 사람이어야 한다. 방향이 반대라서 "
            "이 가게의 손님이 될 수 없으면 NO(예: 파는 가게인데 검색자는 자기 물건을 처분하려는 사람).\n"
            "동종업자·구직·창업 정보를 찾는 검색이라 손님이 될 수 없어도 NO.\n"
            "구매·방문·시공·비교 검토 의도라 가게 글이 답이 되면 YES. YES 또는 NO 한 단어만.",
            max_tokens=10)
        ok = "NO" not in (v or "").strip().upper()[:8]
    except Exception:
        return True
    try:
        if _rl and _ck:
            _rl.cache_set(_ck, {"ok": ok})
    except Exception:
        pass
    return ok


def resolve_target_keyword(industry: str, region: str, note: str, biz: str = "local",
                           content_type: str = "sell", brand: str = "", keyword_axis: str = "local",
                           target_kw_override: str = "", tenant_id: str = "", prof_name: str = "",
                           verify_volume: bool = True) -> tuple:
    """★ 전 생성기 공통 키워드 결정 단일 관문 — 키워드를 자체 결정하는 유일 경로.
    ① target_keywords 후보 생성 → ② Layer1 phantom 필터(현재 세트·재고 밖 속성 토큰 제거) →
    ③ primary_model(사진분석 note 우선) → ④ Layer2 앵커 게이트(앵커 부재 시 검색량 랭킹 보류·제네릭) →
    ⑤ select_target_keyword(검색량 관문 + 기초지역 배제). 반환 (kw0, kws).
    생성기가 이 함수를 안 거치고 seo.target_keywords로 직접 키워드를 정하면 phantom·기초지역 누수 재발."""
    import logging as _lgk
    _slog = _lgk.getLogger("shopcast.seo")
    prof_name = prof_name or ((industry or "").replace("/", ",").split(",")[0].strip())
    prof_name = searcher_term(prof_name) or prof_name     # 🗣 업종명 → 손님이 실제로 검색하는 말
    kws = target_keywords(prof_name, region, note, axis=keyword_axis, brand=brand)
    kplan = keyword_plan(prof_name, region, note, axis=keyword_axis, brand=brand)
    kw0 = kplan.get("headline") or (kws[0] if kws else prof_name)
    tkw = (target_kw_override or "").strip()
    # 🗺 지역 정합 게이트(2026-08-01 사장님 지시 — '김해썬팅' 실사고): 수동 지정 키워드가
    #   가게 지역과 다른 지역을 겨냥하면 무시하고 자동 선정으로 폴백(다른 지역 검색자에게 미끼 글 방지).
    #   지역 사전 하드코딩 없이 LLM YES/NO + 7일 캐시(의도 게이트와 동일 패턴). 무키·실패 = 통과(기존 동작).
    if tkw and (region or "").strip() and region_conflict(tkw, region):
        import logging as _lgrc
        _lgrc.getLogger("shopcast.seo").warning(
            "[지역게이트] 수동 키워드 %r ↛ 가게 지역 %r — 무시하고 자동 선정", tkw, region)
        tkw = ""
    if tkw:
        kw0 = tkw
        kws = list(dict.fromkeys([tkw] + kws))[:10]
    _biz = biz or "local"
    # ★ 매장(local)도 여기를 지난다(2026-08-19 실측으로 잡음).
    #   select_target_keyword 안의 검색량 관문만 고쳤더니 아무것도 안 바뀌었다 —
    #   **매장은 이 위 블록이 seller/hybrid 전용이라 그 함수까지 오지도 못했다.**
    #   루마썬팅이 12편을 월 20회짜리 '부산 동구 썬팅업체'로 쓴 진짜 경로가 여기다.
    #   phantom(재고 속성) 처리는 여전히 셀러 전용, **검색량 관문은 전 업태 공통**으로 가른다.
    _pm, _anchor_missing, _model_toks = "", False, []
    if content_type != "info" and _biz in ("seller", "hybrid"):
        import re as _rpm
        try:
            from app.services import indschema as _iscpm
            _axes = (_iscpm.get_schema(industry, _biz).get("attribute_axes") or [])
            _model_toks = (_axes[0].get("tokens") if _axes else []) or []
        except Exception:
            _model_toks = []
        _inv = []
        if tenant_id:
            try:
                from app import db as _db
                _inv = [c.get("model") for c in _db.recent_inventory_context(tenant_id, limit=6) if c.get("model")]
            except Exception:
                pass
        _kept, _drop = drop_phantom_attr_kws([kw0] + list(kws), industry, _biz,
                                             context_text=(note or ""), inventory_models=_inv)
        if _drop:
            _slog.warning("[resolve-kw] phantom 제거(%d): %s", len(_drop), _drop[:5])
        if _kept:
            kw0 = _kept[0]
            kws = list(dict.fromkeys(_kept))[:10]

        def _fm(src):
            return next((t for t in _model_toks
                         if t and _rpm.search(r"(?<![가-힣])" + _rpm.escape(t), src or "")), "")
        _pm = _fm(note) or _fm(kw0)
        _anchor_missing = bool(_model_toks and not _pm and not _fm(" ".join(kws)))
    if content_type != "info":
        if _anchor_missing:                        # 앵커 부재 → 검색량 랭킹 보류·제네릭(셀러 전용 조건)
            _gk = f"{_kw_shorten(region or '')} {prof_name}".strip() or prof_name
            _slog.warning("[resolve-kw] 앵커 부재 → 제네릭 확정: %r", _gk)
        else:
            _gk = select_target_keyword([kw0] + list(kws), _biz, region or "", prof_name,
                                        tenant_id=tenant_id, primary_model=_pm,
                                        verify_volume=verify_volume, note=note)
        if _gk:
            kw0 = _gk
            kws = list(dict.fromkeys([_gk] + [k for k in kws
                       if not is_basic_region_kw(k, region or "", _biz)]))[:10]
    # ⑥ 의도 정합 게이트(제목 개선 ①) — 진단·큐가 준 override 포함 전 경로 공통.
    #   기각 시 차순위 후보(최대 4개 검사) → 전부 기각이면 지역+업종 제네릭(글이 답 못 주는 키워드로 안 쓴다).
    if content_type != "info" and kw0:
        try:
            _ordered = list(dict.fromkeys([kw0] + list(kws)))
            for _cand in _ordered[:4]:
                if keyword_intent_ok(_cand, industry, _biz, content_type, note):
                    if _cand != kw0:
                        _slog.warning("[resolve-kw] 의도 불일치 → 교체: %r → %r", kw0, _cand)
                        kw0 = _cand
                        kws = list(dict.fromkeys([_cand] + kws))[:10]
                    break
            else:
                _gk2 = f"{_kw_shorten(region or '')} {prof_name}".strip() or prof_name
                _slog.warning("[resolve-kw] 후보 전부 의도 불일치 → 제네릭 폴백: %r", _gk2)
                kw0 = _gk2
                kws = list(dict.fromkeys([_gk2] + kws))[:10]
            # ★ 확장 키워드 목록도 같은 게이트를 통과해야 한다(2026-08-01 사장님 지적).
            #   지금까지는 대표 키워드만 검사해, '중고차팔기'(정반대 손님)가 태그·소제목·본문에
            #   그대로 실렸다. 캐시가 (키워드|업종|글유형) 단위라 같은 업종 가게끼리 재사용된다.
            _keep = [kw0]
            for _c in [k for k in kws if k != kw0][:5]:
                try:
                    if keyword_intent_ok(_c, industry, _biz, content_type, note):
                        _keep.append(_c)
                    else:
                        _slog.info("[resolve-kw] 확장 키워드 방향 불일치 제외: %r", _c)
                except Exception:
                    _keep.append(_c)
            _rest = [k for k in kws if k not in _keep][5:]   # 검사 범위 밖은 삭제하지 않고 보존
            kws = list(dict.fromkeys(_keep + _rest))[:10]
        except Exception:
            pass
    # 🕳 빈자리 최종 반영(2026-08-02 실측) — 앞의 select_target_keyword 안에서만 적용했더니
    #   '앵커 부재 → 제네릭 확정' 같은 다른 분기로 빠질 때 통째로 무시됐다.
    #   실측: 소나타 DN8 소재에서 후보 재정렬은 '신차 썬팅'을 1위로 올렸는데
    #   최종 kw0는 '부산 동구 썬팅,광택'이었다. 결정이 끝나는 자리에서 한 번 더 본다.
    #   조건은 그대로다 — 판정 '확실' + 점수>0 + 소재가 뒷받침(낱말 2개 이상). 지어내지 않는다.
    # ★ 승격은 자동 선정 경로 전용이다(2026-08-07 실측: 빈자리 큐가 지목한 '차량 썬팅 가격'을
    #   이 블록이 '썬팅 가격'으로 갈아치웠다 — 글이 큐의 질문에 답하지 않게 된다).
    #   지목 키워드(tkw)가 지역 게이트를 살아 넘었으면 그 지목이 곧 소재다. 세트=한 소재=한 키워드.
    if not tkw:
        try:
            _gf = _gap_first([kw0] + list(kws), tenant_id, note)
            if _gf and _gf[0] != kw0:
                _slog.warning("[resolve-kw] 빈자리 승격: %r → %r", kw0, _gf[0])
                kw0 = _gf[0]
                kws = list(dict.fromkeys([kw0] + [k for k in kws if k != kw0]))[:10]
        except Exception:
            pass
    # 🧱 지면 게이트(2026-08-13 사장님 승인) — 통합검색 첫 화면에 '블로그 지면'이 아예 없는
    #   판에는 글을 쏘지 않는다. 실측: 올린다가 쓴 '부산 기장 중고차' 글은 블로그탭 6위를
    #   찍었지만 그 키워드의 첫 화면에는 블로그 블록 자체가 없어 손님 눈에는 0이었다
    #   (블로그탭 순위는 착시 — 헌법 3장). _surface_first는 '뒤로 미루기'만 해서 후보가
    #   그것뿐이면 그대로 선택됐다. 결정이 끝나는 자리에서 한 번 더 본다(빈자리 승격과 같은 이유).
    #   ★ 판정 근거가 '최근 실측'일 때만 뺀다. 미측정(None)은 빼지 않는다 — 모른다고 버리면
    #     지도가 얇은 신규 가게가 아무 글도 못 쓴다.
    try:
        kw0, kws = _drop_dead_surfaces(kw0, kws, tenant_id)
    except Exception:
        _lgk.getLogger("shopcast.seo").exception("[resolve-kw] 지면 게이트 실패 — 원래 키워드 유지")
    # 💰 가격 키워드 최종 차단 — **결정이 끝나는 이 한 자리에서만** 막는다(2026-08-16).
    #   target_keywords에서 한 번 걸렀는데 gapscout '빈자리 승격'이 다른 목록에서 끌어와
    #   '부산 썬팅 가격'을 핵심으로 다시 밀어 올렸다(실측). 경로마다 막으면 다음 경로에서 또 뚫린다
    #   — 헌법: 같은 계열 2회째부터는 표면별 수정 금지, 전 표면 공통 규칙으로만.
    kw0, kws = _strip_price_keywords(kw0, kws)
    return kw0, kws


def _strip_price_keywords(kw0: str, kws: list) -> tuple:
    """가격 의도 키워드를 타깃에서 제거한다 — 본문에 금액을 쓰지 않기로 했기 때문.

    핵심(kw0)이 가격 키워드면 가격 아닌 첫 후보로 갈아탄다.
    후보가 전부 가격뿐이면 **바꾸지 않고 크게 로그를 남긴다** — 조용히 빈손을 만들지 않는다.
    """
    # ★ 로거는 지역 import로 못 박는다 — 상위 스코프의 `_lgk`를 빌려 쓰다 NameError를 낸 게
    #   오늘만 세 번째다(main._warm_botnets · 여기). 함수가 자기 것만 쓰게 한다.
    import logging as _lg
    if not EXCLUDE_PRICE_KEYWORDS:
        return kw0, kws
    clean = [k for k in (kws or []) if k and not _PRICE_KW.search(k)]
    if _PRICE_KW.search(kw0 or ""):
        if clean:
            _lg.getLogger("shopcast.seo").warning(
                "[resolve-kw] 가격 키워드 차단: %r → %r", kw0, clean[0])
            kw0 = clean[0]
        else:
            _lg.getLogger("shopcast.seo").warning(
                "[resolve-kw] 가격 키워드 %r뿐 — 대체 후보가 없어 유지한다(본문 금액 금지와 충돌)", kw0)
            return kw0, list(kws or [])
    return kw0, list(dict.fromkeys([kw0] + clean))[:10]


def _surface_verdict(tenant_id: str, kw: str) -> "bool | None":
    """이 키워드 첫 화면에 블로그 지면이 있는가 — 최근 실측만 신뢰. 모르면 None."""
    if not (tenant_id and kw):
        return None
    try:
        from datetime import datetime as _dt, timedelta as _td
        from app.services import blogreach as _brc
        from app.services.gapscout import MAP_TTL_DAYS as _ttl
        b = _brc.blocks_for(tenant_id, kw) or {}
        surf = b.get("blog_surface")
        if surf is None:
            return None
        ts = (b.get("checked_at") or "")[:19]
        if not ts:
            return None
        if _dt.fromisoformat(ts) < (_dt.utcnow() - _td(days=_ttl)):
            return None                     # 낡은 판정은 근거로 쓰지 않는다
        return bool(surf)
    except Exception:
        return None


def _drop_dead_surfaces(kw0: str, kws: list, tenant_id: str) -> tuple:
    """지면 없음이 실측된 키워드를 대표 자리에서 뺀다. 살아 있는 후보로 교체하되,
    전부 죽었으면 바꾸지 않고 사유만 남긴다(임의 키워드를 지어내지 않는다)."""
    if _surface_verdict(tenant_id, kw0) is not False:
        return kw0, kws
    alive = [k for k in kws if k != kw0 and _surface_verdict(tenant_id, k) is not False]
    import logging as _lgd
    _dlog = _lgd.getLogger("shopcast.seo")
    if not alive:
        _dlog.warning("[resolve-kw] 지면 없음(%r) — 대체할 살아 있는 판이 없어 그대로 진행", kw0)
        return kw0, kws
    _dlog.warning("[resolve-kw] 지면 없음 → 교체: %r → %r", kw0, alive[0])
    return alive[0], list(dict.fromkeys([alive[0]] + [k for k in kws if k != alive[0]]))[:10]


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
    # 행정 풀네임 축약(2026-07-28 실사고: '부산광역시 동구 썬팅업체' 키워드 → 모든 글이 풀네임 반복
    # → 감사 감점 자기모순). 검색자도 '부산 동구'로 검색 — 키워드는 축약형이 정답.
    reg = _kw_shorten((region or "").strip()) or (region or "").strip()
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
        # 업종 단독 축 — 가격은 뺀다(본문에 금액을 안 쓴다).
        #   과정·시간은 **지역과 붙이지 않는다**: '썬팅 과정'은 사람 말이지만
        #   '부산 동구 썬팅업체 과정'은 아무도 안 치는 기계 조합이다(2026-08-16 실사고).
        kws += [f"{ind} 추천"] + [f"{ind} {it}" for it in _SOLO_INTENTS]
    # 메모에서 핵심 명사 추출(신메뉴/차종/시술명 등)
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or ""):
        if w not in ("추천", "이벤트", "할인") and len(w) <= 12:
            cand = f"{reg} {w}".strip()
            if cand and cand not in kws:
                kws.append(cand)
    # 💰 가격 의도 키워드 배제(2026-08-16 사장님 지시) — 본문에 금액을 쓰지 않기로 했다.
    #   가격을 안 쓰면서 가격 키워드를 노리면 모순이다: 검색자는 금액을 보러 오는데 글에 없다.
    #   실물 사고: 본문 가격 금지 직후 핵심 키워드가 '부산 썬팅 가격'으로 승격돼
    #   제목이 '부산 썬팅 가격 궁금하다면…'이 됐다(약속하고 못 지키는 글).
    #   ★ 되돌리려면 이 상수만 False로. 언어 규칙만 쓴다(업종어 하드코딩 0).
    if EXCLUDE_PRICE_KEYWORDS:
        kws = [k for k in kws if not _PRICE_KW.search(k)]
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
    # 🦴 2026-08-19 — 질문 섹션 요구는 여기서 뺐다. 골격(services/blogshape.py)이 정한다.
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
                  brand: str = "", questions: list[str] | None = None,
                  shape_id: str = "", summary_head: str = "") -> str:
    """블로그 프롬프트 주입용 GEO 구조 지시 — 매장(NAP)/셀러(SPU) 분기.

    ★ 섹션 이름은 글마다 변형된다(services/sections.py). **이번 글의 이름을 인자로 받는다.**
      2026-08-19 실측 — 전에는 여기서 기준형('한눈 요약')을 쓰고 "호출부가 이번 글 이름을
      따로 지시하니 모델이 그쪽을 따른다"고 뒀는데, 한 프롬프트에 이름이 둘이 되자
      모델이 **요약 섹션을 두 개** 만들었다('한눈 요약'과 '요약하면'이 한 글에 같이 실렸다).
      지시가 둘이면 모델은 둘 다 따른다 — 이름을 정하는 곳은 하나여야 한다.

    ★ 2026-08-19 — 요약·FAQ 지시는 **골격이 요구할 때만** 넣는다(shape_id).
      실측: 골격을 넣고도 3편 연속 FAQ가 붙었다. FAQ를 요구하는 자리가 여덟 곳이었고
      그중 여기가 하나였다. 지시가 한 곳이라도 남으면 모델은 '쓰라'는 쪽을 따른다.
    """
    from app.services import blogshape as _shp
    from app.services import sections as _sec
    _sm = (summary_head or "").strip() or _sec.SUMMARY[0]
    _want_sum = _shp.needs_summary(shape_id)
    _want_faq = _shp.needs_faq(shape_id)
    qline = " / ".join(questions or []) if _want_faq else ""
    if (biz_type or "local") == "seller":
        pname = f"{brand} {name}".strip() if brand and brand not in (name or "") else (name or "")
        return (
            "[GEO — AI 검색(ChatGPT·Perplexity 등)이 인용하기 쉬운 구조로]\n"
            f"- 첫 문단에 상품 정의문 한 문장: \"{pname}은(는) ~한 {industry}다\" 꼴로 자연스럽게(무엇인지 한 문장으로 규정).\n"
            + (f"- '## {_sm}' 소제목 1개: 핵심 3줄(- 목록) — 검색자가 답만 뽑아가게.\n" if _want_sum else "")
            + "- '## 솔직 장단점' 소제목 1개: 입력에 근거한 장점 2~3개 + 아쉬운 점 1개(솔직함이 AI 인용 신뢰를 높인다. 없는 단점 지어내기 금지).\n"
            f"- 비교 질문 Q&A 1개: \"{name} 비슷한 제품과 차이는?\" — 입력 정보로만 답하고 타사 비방·비교 우위 날조 금지.\n"
            + (f"- FAQ 질문은 실제 검색 질문형으로: {qline}\n" if qline else "")
            + "- 상품명·스토어명·구매링크(SPU) 표기는 본문 전체에서 한 글자도 다르지 않게 일관되게.\n")
    place = f"{region}의 {industry}".strip()
    return (
        "[GEO — AI 검색(ChatGPT·Perplexity 등)이 인용하기 쉬운 구조로]\n"
        f"- 첫 문단에 정의문 한 문장: \"{name}은(는) {place} 전문점이다\" 꼴로 자연스럽게(무엇을 하는 곳인지 한 문장으로 규정).\n"
        + (f"- '## {_sm}' 소제목 1개: 핵심 3줄(- 목록) — 검색자·AI가 답만 뽑아가게.\n" if _want_sum else "")
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
    from app.services import blogshape as _shp
    from app.services import sections as _sec
    # 🦴 2026-08-19 — 골격이 요구하지 않는 섹션은 **채점하지 않는다**(있고 없고를 묻지 않는다).
    #   전에는 모든 글에 요약·Q&A를 요구해 점수를 줬고, 그래서 모든 글이 그 둘을 달았다.
    #   점수를 받으려고 같은 블록을 붙이는 것이 뼈대 획일화의 원인이었다.
    #   ★ 없는 것을 감점하지도, 있다고 가점하지도 않는다 — 그 글에 해당 없는 항목이다.
    _shape_id = (payload.get("blog_shape") or "")
    if _shp.needs_summary(_shape_id):
        if _sec.has_summary(text):
            hits.append("한눈 요약")
        else:
            misses.append("요약 섹션 없음")   # 이름은 글마다 다르다 — 특정 변형을 진단문에 박지 않는다
    if _shp.needs_faq(_shape_id):
        if _sec.has_faq(text):
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
def subject_match(text: str, note: str, kw: str) -> "bool | None":
    """소재 정합 게이트(생성 후 기계 검증) — 글이 '실물처럼' 서술하는 소재(차종·모델·제품)가
    사진 분석(note)에서 확인되는지 LLM YES/NO. True=일치, False=불일치, None=판정 불가(fail-open).
    실사고(2026-07-27): 캡션이 키워드 '캐스퍼'에 끌려 토레스 사진 세트에 '오늘 들여온 캐스퍼'·
    '휠 스크래치'를 날조 — 프롬프트 지시만으로는 재발을 못 막아 기계 게이트로 보증."""
    if not (text or "").strip() or "[사진" not in (note or ""):
        return None                                     # 사진 분석 없는 세트는 판정 불가(스킵)
    try:
        from app import llm
        # 🔄 2026-08-18 Solar 전환(사장님 승인) — 이 게이트가 **크레딧 때문에 죽어 있었다.**
        #   haiku를 부르는데 Anthropic 크레딧이 0이라 매번 400으로 실패했고(Sentry 79건),
        #   실패는 None을 돌려주므로 fail-open — 즉 어제부터 **날조 검사 없이 글이 나갔다.**
        #   Solar로 옮기면 크레딧과 무관해진다. 실측(10/10, 날조 놓침 0):
        #     인공 6건 + 실전 4건(프로덕션 글 2,765자에 차종·수치·없는차량 날조 주입)
        #     수치 변조(57,216km→23,450km)까지 잡았고 원본은 통과시켰다. 답도 흔들리지 않았다.
        #   ★ 표본 10건이다. 옮겼다고 끝이 아니라 실전 판정을 계속 재야 한다.
        v = llm.call_task(
            "judge",
            "너는 사실 검증자다. [글]이 '지금 여기 있는 실물'처럼 서술하는 소재(차종·메뉴·제품·시술 대상 등)가 "
            "[사진 분석]에서 확인되는지만 판단하라. 사진 분석에 없는 차종·모델·제품을 실물처럼(입고·검수·"
            "흠집·상태 묘사) 서술하면 NO. 분석이 일반적으로 묘사한 대상(빵·꽃·차량 등)을 글이 자연스러운 "
            "구체 명칭으로 부르는 정도는 일치(YES)로 본다. 지역·업종·일반 조언·비유 언급은 판단에서 제외. YES/NO 한 단어만.\n"
            f"[타깃 키워드] {kw}\n[사진 분석(발췌)]\n{(note or '')[:2500]}\n\n[글]\n{(text or '')[:1200]}",
            max_tokens=10)
        s = (v or "").strip().upper()
        if "NO" in s and "YES" not in s:
            return False
        return True if "YES" in s else None
    except Exception:
        return None


FACTS_RULE = (
    "[⚠️ 정직 원칙 — 반드시 지켜라(위반하면 콘텐츠 폐기)]\n"
    "- 입력(메모·사진분석·상품정보)에 '없는' 가격·할인율·수치·스펙·모델명·성분·효능·용량·수상/인증·후기수를 절대 지어내지 마라.\n"
    "- 이번 소재(차종·모델·제품)는 [사진N] 분석과 입력에서 확인된 것만이다. 타깃 키워드 속 차종·모델·상품명이 "
    "사진과 다르면 그건 검색 문맥일 뿐 — 그 차종/제품이 지금 여기 있는 것처럼 서술하지 마라"
    "(흠집·상태·'오늘 들여온' 같은 묘사를 키워드 쪽에 붙이는 것 절대 금지).\n"
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
    "- [사실 일관성] 입력에 명시된 긍정 상태 사실(예: 무사고·정품·신품·정상작동·침수 없음)을 의심·부정·번복하거나 "
    "'정말 ○○일까?'·'숨겨진 ○○'·'믿어도 될까' 식으로 흔드는 프레이밍 금지 — 판매자가 밝힌 사실은 확정으로 다루고, "
    "필요하면 입력에 있는 증빙(성능점검·인증·기록부 등)으로 뒷받침하라. 일반적 불안·리스크는 '이런 걱정 많으시죠'까지만 "
    "공감하되, 그 불안을 '이 상품/매물'에 씌워 상태 사실과 모순되게 쓰지 마라.\n"
    "- [서류·이력 정직 고지] 입력(사진분석·서류 판독)에서 확인된 '주요 사용·이력성 사실'(예: 렌트/리스/영업용 출신, "
    "용도변경 이력, 리퍼·전시품, 단순수리 등 — 업종 불문 서류·이력에서 드러난 사실)은 절대 감추지 말고 본문에 정직하게 "
    "고지하라. 방식: 장점으로 포장하지 말고 사실 그대로 서술 + '이런 이력의 상품 볼 때 확인점'을 안내(구매자가 스스로 "
    "검증하게). 확인된 이력을 언급조차 안 하고 침묵하면 표면 간 불일치·기만이다. (없는 이력을 지어내는 것도 금지 — 입력에 있는 것만.)\n"
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
    "④ 반론 선제 해소: 손님이 망설이는 것(가격/AS/효과/배송)을 본문에서 미리 답하라.\n"
    "⑤ CTA 계단: 저장→댓글→검색·예약→방문·구매 순(바로 '사세요'는 저항).\n"
    "⑥ 스마트블록 대응: 그 키워드의 세부 검색의도(가격·후기·방법·비교·추천)를 각각 소제목(##)으로 다뤄라 "
    "— 스마트블록·AI 답변 인용에 잡히게(정확·전문적으로 써야 AI가 인용)."
)

# 체류시간·정보 밀도(상위노출 v2) — 블로그 본문 전용. 정직 원칙 위에서.
RETENTION_DENSITY = (
    "[체류시간·정보 밀도 — 상위노출 v2(반드시 적용)]\n"
    # 🧹 통폐합 3-3(2026-08-16): 따로 서 있던 [체류 설계 — 3장치 필수]를 여기로 흡수했다.
    #   둘 다 "체류시간을 늘려라"를 말하면서 도입 예고·중간 이정표를 각각 지시해 겹쳤다.
    #   ★ 세 장치는 기계 검사 대상이다(_audit_dwell_devices: 답 예고·'①' 나열·중간 이정표).
    #     지시를 지우면 게이트가 매번 걸려 LLM 보충 콜이 돈다 — 겹친 서두만 덜고 장치는 남긴다.
    "① 도입 첫 3~4문장(모바일 첫 화면)에 세 가지를 담아라: (a) 검색자의 질문 재확인 "
    "(b) 이 글이 주는 답 예고(보여드릴게요·알려드릴게요 같은 말로) "
    "(c) '끝까지 보시면 ①… ②… ③…' 처럼 **이 글에 실제로 있는 것만** 세 가지를 번호로 예고. "
    "이 셋이 스크롤 약속이 되어 초반 이탈을 막는다. "
    "★정형 문구를 그대로 복사하지 말고 이 글의 소재·문체에 맞게 변주하라.\n"
    "② 글 중반(대략 절반 지점): 새 질문을 던져 궁금증을 되살리고"
    "(예: 비용 글이면 '그런데 왜 견적이 업체마다 다를까요?'), "
    "'바로 아래에서 ○○을 보여드립니다' 같은 다음 대목 이정표를 1~2회 놓아라 "
    "— 실제 뒤에 나오는 내용만(없는 것 예고 금지). 억지 말고 본문 주제에서 자연스럽게.\n"
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

# 🗣 겁주기·공포 마케팅 — ★ 영상 자막과 '같은 목록'을 쓴다(2026-08-02 실사고).
#   프롬프트(HUMAN_TOUCH)에는 '호구 낚시 훅' 금지가 있었는데 채점기가 그 목록을 안 봐서
#   본문에 "호구 잡힐까 불안한 분들"이 그대로 들어간 채 88점으로 통과했다.
#   생성 규칙과 검사 규칙이 두 곳에 따로 있으면 반드시 어긋난다(영상에서 겪은 것과 같은 사고).
def _fear_patterns():
    """단일 소스 — app.generators.video.FEAR_PATTERNS. 순환참조 회피 위해 지연 import."""
    try:
        from app.generators.video import FEAR_PATTERNS as _FP
        return _FP
    except Exception:
        return (r"호구", r"사기\s?당", r"모르면\s?(손해|당)", r"허위\s?매물\s?(걱정|불안)")

# 휴먼터치 지시 — blog/insta/X 공통 주입(A1). '사람이 쓴 것 같은' 리듬·구어가 차별화.
HUMAN_TOUCH = (
    "[휴먼터치 — AI 티 빼기(어기면 저품질·독자 이탈)]\n"
    "- 금지 클리셰: '안녕하세요~ 오늘은 ~알아보겠습니다' 류 도입, '~추천드립니다'·'~소개해드리겠습니다' 반복, "
    "'지금까지 ~였습니다'·'도움이 되셨길 바랍니다' 류 마무리, '어떠셨나요?' 류 상투 질문(단, 진짜 궁금증을 묻는 질문 문장은 권장한다), "
    "'이거 모르면/안 보면 호구' 류 낚시 훅.\n"
    "- 어미를 섞어라: 기본 해요체에 ~습니다/~거든요/~더라고요/~더군요 자연 혼용 — 같은 어미로 3문장 연속 금지.\n"
    "- 리듬을 일부러 어긋내라: 다섯 어절 이하 짧은 문장을 사이사이에, 한 문장짜리 문단 1~2회. "
    "모든 소제목 섹션이 같은 길이·같은 구성(2문단+사진)이면 기계 티 — 어떤 섹션은 길게, 어떤 섹션은 두 줄로.\n"
    "- 괄호 혼잣말 1~2회: '(이건 저도 다시 보고 놀랐네요)' 같은 — 글마다 다른 내용으로.\n"
    "- 접속사로 시작하는 문장 가끔 허용: '근데', '그리고', '아, 그리고'.\n"
    "- 구어 추임새는 소량, 매 글 다른 단어로 — 특정 단어('솔직히' 등)를 글마다 반복하면 그게 곧 AI 티다.\n"
    "- 사장님(판매자) 1인칭 목소리 유지 — 설명문이 아니라 '내 가게(내 상품) 이야기'로.\n"
    "- 이모지: 네이버 블로그 0~1개, 인스타 1~2개까지만(남발=AI티).\n"
)

# 도입 스타일 회전(기계 티 방지) — 글마다 asset 시드로 1개 배정 → 가게 글이 매번 다른 얼굴로 시작.
# 전 스타일 공통: 사진·입력에 근거한 사실만(날조 금지), 체류 설계 ①(답 예고)은 스타일 '안에서' 녹임.
HOOK_STYLES = [
    ("질문형", "검색자가 실제 던질 법한 질문 문장 그 자체로 시작하라(수사 의문 '~하셨죠?' 말고 진짜 질문문). ★ 이 제약은 **첫 문장**에만 적용된다 — 본문 중간에서 손님에게 묻는 것은 막지 않는다."),
    ("장면형", "그날 매장·작업 현장의 구체적 한 장면 묘사로 시작하라(사진에서 확인되는 사실 기반, 눈에 보이듯)."),
    ("결론형", "결론이나 핵심 숫자를 첫 문장에 먼저 던져라('결론부터 말씀드리면 ~입니다' 스타일)."),
    ("고백형", "판매자의 솔직한 속마음·관찰로 시작하라('이 매물 처음 입고됐을 때 저부터 꼼꼼히 봤습니다' 류 — 사실 기반만)."),
    ("정보형", "검색자가 가장 헷갈려하는 오해 하나를 바로잡으며 시작하라('~라고들 아시는데, 실제로는 다릅니다')."),
]

# 이모지 판정 단일 소스(2026-08-02) — 채점기와 수선기가 다른 목록을 쓰면 채점기가 잡은 것을
# 수선기가 못 지운다(실측: ⭐가 채점 목록에만 있어 '이모지 2개' 감점이 영원히 안 사라졌다).
# 겁주기 목록 이원화와 같은 사고 — 판정하는 쪽 목록을 유일한 기준으로 삼는다.
# 범위: 그림문자 본체 + 기타기호(2600-27BF) + 별·화살표기호(2B00-2BFF).
# 문장부호(— → ① 등)는 이모지가 아니므로 넣지 않는다(실측 본문 전수 확인).
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF]")

# 감점이 아니라 '발행 차단' 대상 — 의료광고법·자동차관리법 위반 소지가 큰 단정 표현(PHASE 7)
HARD_BLOCK_EXPRESSIONS = [
    "완치", "부작용 없", "부작용없", "무통", "100% 효과", "영구적", "재발 없",
    "완전무사고", "무사고 보장", "침수 아님 보장",
]


def hard_block_hits(text: str) -> list[str]:
    """발행 차단 대상 표현 탐지(감점 아님). 하나라도 걸리면 자동발행 보류(PHASE 7)."""
    t = text or ""
    return [w for w in HARD_BLOCK_EXPRESSIONS if w in t]


# 날조 탐지에서 제외할 '수사적 나열' 어미 — 실제 주장이 아니라 가정·비유를 열거하는 말.
#   실측 2026-08-01: "말로 3만이니 5만이니 하는 것보다 계기판 숫자가 정직합니다"에서
#   3만·5만이 날조로 잡혔다. 이 차의 수치를 주장한 게 아니라 관행을 지적한 문장이다.
#   언어 규칙일 뿐 업종·가게 하드코딩이 아니다.
#   ★ '이나/나'는 제외하지 않는다 — "보증금 3만원이나 5만원"처럼 실제 금액 범위를 잇는
#     '또는'으로 훨씬 자주 쓰인다. 놓치면 진짜 날조를 통과시킨다.
#   ★ '이니까' 같은 이유 어미는 나열이 아니다 → '이니' 뒤에 '까'가 오면 제외하지 않는다.
#     1글자 '든'도 뺀다 — "5만 든든하게" 같은 다음 어절을 잘못 삼켰다(검토 지적).
_RHETORIC_TAIL = re.compile(r"^(이니(?!까)|이라느니|라느니|이라던|라던|이든지|이든)")


_KR_DEC_UNIT = re.compile(r"(?<![\d.])(\d{1,4})\.(\d)(\d*)\s*(만|억)")


_ANCHOR_RE = re.compile(r"\b([A-Za-z]{1,6}\d{1,4}[A-Za-z]?|\d{1,4}[A-Za-z]{1,6})\b")


def input_anchors(note: str, limit: int = 4) -> list[str]:
    """입력에 있는 고유 식별자(모델명·등급명 등) — 본문에 반드시 살려야 할 말.

    ★ 2026-08-03 실사고: 사장님이 '기아 PV5'라고 주셨는데 본문에 PV5가 한 번도 안 나왔다.
      전부 '신차 한 대'로 뭉갰다. 차종·모델명은 손님이 검색하는 말이자 신뢰 근거인데
      통째로 사라진 것이다.
    판정은 언어 규칙만 — 영문+숫자가 붙은 토큰(PV5·DN8·GV80·버텍스500)을 식별자로 본다.
    업종어를 목록으로 갖지 않으므로 빵집·카페의 제품 코드에도 그대로 통한다.
    """
    out = []
    for m in _ANCHOR_RE.finditer(note or ""):
        w = m.group(1)
        # ★ 시각·파일명 꼬리는 식별자가 아니다(2026-08-04 실측: 사진 파일명의 '54PM'·'55PM'이
        #   모델명으로 잡혀 '본문에 없다'는 오탐 감점이 났다). '사진13'을 뺀 것과 같은 계열.
        if re.fullmatch(r"\d{1,4}(AM|PM|am|pm)", w):
            continue
        if w.lower() in ("mp4", "jpg", "png", "1080p", "4k") or w in out:
            continue
        out.append(w)
    # 한글+숫자 결합형(버텍스500)도 식별자다.
    #   ★ 우리 내부 표기는 뺀다(2026-08-03 실측: '사진13'·'사진14'가 실값으로 잡혀
    #     캡션에 그대로 쓰였다). 시스템이 만든 말은 사장님이 준 실값이 아니다.
    _SYS = ("사진", "표", "항목", "단계", "번호")
    for m in re.finditer(r"[가-힣]{2,}\d{2,4}", note or ""):
        w = m.group(0)
        if w not in out and not any(w.startswith(x) for x in _SYS):
            out.append(w)
    return out[:limit]


def natural_kr_number(text: str) -> str:
    """'5.7만km' → '5만 7천km' — 한국어에 없는 소수점+만/억 표기를 자연 표기로(2026-08-02 사장님 지적).

    왜: 한국어는 만·억을 소수로 쪼개 말하지 않는다. '5.7만'은 중국어·일본어식 표기이고,
    손님이 검색창에 치지도, 읽고 자연스럽지도 않다. 실측: 제목 '5.7만km 흰색 SUV'.
    프롬프트로 부탁하는 대신 기계로 고친다 — 부탁은 확률이고 이건 규칙이다.
    언어 규칙만(업종·지명 무관). 소수점 뒤 둘째 자리부터는 버린다(천 단위까지가 자연스럽다).
    """
    def _r(m):
        a, b, _rest, unit = m.group(1), m.group(2), m.group(3), m.group(4)
        if b == "0":
            return f"{a}{unit}"
        return f"{a}만 {b}천" if unit == "만" else f"{a}억 {b}천만"
    return _KR_DEC_UNIT.sub(_r, text or "")


_CLOCK_RE = re.compile(r"(\d{1,2})\s*:\s*(\d{2})")


def _clock_nums(s: str) -> set:
    """시계 표기(10:21)를 한국어 말투와 같은 토큰으로 — '10시', '21분'.

    ★ 2026-08-03 오탐: 사진에 찍힌 시계를 본문이 '10시 21분'으로 썼는데, 근거(사진 분석)에는
      '10:21'로 적혀 있어 날조로 잡혔다. 같은 사실을 표기만 달리 쓴 것이다.
      숫자를 느슨하게 봐주는 게 아니라, 같은 표기를 같은 것으로 읽게 만든다."""
    out = set()
    for m in _CLOCK_RE.finditer(s or ""):
        out.add(f"{int(m.group(1))}시")
        out.add(f"{int(m.group(2))}분")
    return out


def _money_nums(s: str) -> set:
    """텍스트에서 '금액·%·수치+단위'를 정규화 추출(콤마·공백 제거). 날조 탐지용(PHASE 7).
    ★ 오탐 2종 차단(2026-08-01 실측):
      ① 단위가 실제로 붙어 있어야 한다 — "연식 2022, 원동기형식"의 '원'을 금액으로 읽어
        '2022원'을 날조로 잡았다(쉼표·공백 건너뛰기가 원인) → 숫자 바로 뒤만 인정.
      ② 수사적 나열("3만이니 5만이니")은 주장이 아니다 → 제외."""
    out = set()
    for m in re.finditer(r"(\d+(?:,\d{3})*)(원|만원|%|퍼센트|만|천원|시간|분)", s or ""):
        tail = (s or "")[m.end():m.end() + 6]
        if _RHETORIC_TAIL.match(tail):
            continue
        out.add(m.group(1).replace(",", "") + m.group(2))
    return out


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
    """행정구역 풀네임 → 사람이 실제로 치는 구어형. 전 표면 공통 관문.

    '부산광역시 썬팅 비용' → '부산 썬팅 비용'
    '경기도 구리시 카센터' → '구리 카센터'     (2026-08-14 추가)

    ★ 도(道) 처리를 추가한 이유(실측): 구리 카센터 180회 / 구리시 카센터 30회 /
      경기도 구리시 자동차정비 20회. 도 이름을 붙이면 아무도 안 친다. 광역시는 붙여 쓰지만
      (부산 동구 썬팅=실검색) 도는 빼고 시 이름만 쓴다 — 사람들이 그렇게 부른다.
    ★ 지명 자리(맨 앞 두 어절)에만 적용한다. 문장 중간의 '…시'까지 건드리면
      '자동차정비시' 같은 말이 잘린다(과교정 방지).
    """
    s = re.sub(r"([가-힣]{2,})(광역시|특별시|특별자치시|특별자치도)", r"\1", kw or "").strip()
    # ★ 통합 행정구역(2026-08-14 실측) — '전남광주통합특별시'는 공식 명칭이지만
    #   사람은 '광주'라고 친다. 실측: 광주 자동차정비 90회 · 광주 카센터 540회 vs
    #   전남광주통합 자동차정비 20회. 'A+B통합' 꼴에서는 뒤쪽 이름이 부르는 말이다.
    s = re.sub(r"([가-힣]{4,})통합(?=\s|$)", lambda m: m.group(1)[-2:], s)
    toks = s.split()
    if not toks:
        return s
    head = " ".join(toks[:2])
    # 도 + 시/군 → 시/군 이름만 ('경기도 구리시' → '구리')
    head = re.sub(r"^[가-힣]{2,3}도\s+([가-힣]{2,})[시군]$", r"\1", head)
    # 도 + 시/군이 아닌 말이면 '도'만 뗀다 ('경기도 카센터' → '경기 카센터').
    #   지역을 통째로 지우면 전국 키워드가 돼 소상공인이 못 이기는 판이 된다.
    head = re.sub(r"^([가-힣]{2,3})도\s+(?=[가-힣])", r"\1 ", head)
    # 맨 앞 단독 시/군 접미사 ('구리시 카센터' → '구리 카센터')
    head = re.sub(r"^([가-힣]{2,})[시군](?=\s|$)", r"\1", head)
    return " ".join([head] + toks[2:]).strip()


def _kw_variant_hits(text: str, kw: str) -> int:
    """타깃 키워드의 자연 변형 노출 수 — 핵심 토큰(축약형)이 한 문장에 모두 있으면 1회."""
    toks = [t for t in _kw_shorten(kw).split() if len(t) >= 2]
    if not toks:
        return 0
    return sum(1 for s in re.split(r"[\n.!?]", text) if all(t in s for t in toks))


# ── 모바일 규격(E-1) — 생성 프롬프트 주입용. 네이버 트래픽 대부분이 모바일. 업종 중립. ──
MOBILE_SPEC = (
    "[모바일 규격 — 반드시 지켜라. 네이버 블로그 독자 대부분이 폰이다]\n"
    "① 문단: 모바일 3~4줄(공백 제외 90~130자) 단위로 끊어라. PC 기준 장문단 금지. 문단 사이 빈 줄 1개.\n"
    "② 표: 열 2개 이하만(모바일에서 3열+ 표는 옆으로 잘린다). 비교가 3항목+면 '| 항목 | 내용 |' 2열로 재구성하거나 서술로.\n"
    "③ 한 문장 60자 내외 — 폰에서 한 문장이 4줄 넘어가면 나눠라.\n"
    "④ 소제목 사이 본문이 모바일 5~7스크린을 넘지 않게(체류 유지하되 이탈 방지).\n"
)


def _body_char_count(body: str) -> int:
    """본문 실자수(공백 제외) — 표행·[사진N]·소제목 기호 제외, 내용만. 글자수 게이트 기준."""
    lines = []
    for ln in (body or "").split("\n"):
        s = ln.strip()
        if not s or s.startswith("|") or s.startswith("[사진"):
            continue
        s = re.sub(r"^#{1,4}\s*", "", s)              # 소제목 기호만 제거(내용은 카운트)
        lines.append(s)
    return len(re.sub(r"\s", "", "".join(lines)))


def mobile_spec_gate(body: str, content_type: str = "sell") -> dict:
    """발행 규격 게이트(item 6 + E-1) — 자수 범위·모바일 문단 길이·표 열수. 트랙 A/B 공통, 업종 중립.
    반환 {passed, fails[], char_count, range, below(하한미달), above(상한초과)}."""
    fails = []
    cc = _body_char_count(body)
    lo, hi = (1500, 3000) if content_type == "info" else (1500, 2500)
    below = cc < lo
    above = cc > hi
    if below:
        fails.append(f"자수 하한 미달({cc}/{lo})")
    elif above:
        fails.append(f"자수 상한 초과({cc}/{hi})")
    # 모바일 문단 길이 — 빈 줄 분할, 소제목·표·리스트·사진 제외, 공백제외 130자 초과 금지
    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    long_p = [p for p in paras
              if not p.startswith(("#", "|", "-", "•", "1.", "2.", "3.", "[사진"))
              and len(re.sub(r"\s", "", p)) > 130]
    if long_p:
        fails.append(f"장문단 {len(long_p)}개(모바일 130자 초과)")
    # 표 열수 ≤2
    over = 0
    for ln in (body or "").split("\n"):
        s = ln.strip()
        if s.startswith("|") and not re.match(r"^\|[\s:\-|]+\|$", s):
            if len([c for c in s.strip("|").split("|") if c.strip()]) > 2:
                over += 1
    if over:
        fails.append(f"표 3열+({over}행)")
    return {"passed": not fails, "fails": fails, "char_count": cc,
            "range": [lo, hi], "below": below, "above": above}


def photo_count(payload: dict) -> int:
    """이 글에 실제로 딸린 사진 수 — 세는 곳은 여기 하나뿐이다.

    ★ 2026-08-05 실측 사고: 두 곳이 본문의 '[사진N]' 마커를 세어 사진 수라고 했다.
      마커는 '본문에 몇 장을 걸었나'이지 사진 수가 아니다 — 생성은 상위 선별분만 마커로 넣는다.
      실제 20장짜리 글이 순위 분석에서 '사진 4장'으로, 품질 채점에서도 4장으로 세어졌다.
      전체를 부분 표면으로 세는 계열의 세 번째 재발이다(photo_pool·tenant_move.verify).
      진실은 image_paths다.
    """
    return len((payload or {}).get("image_paths") or [])


def quality_audit(channel: str, kind: str, payload: dict, source: str = "") -> dict:
    """네이버 랭킹 신호(C-Rank·D.I.A.+·플레이스) 기준 채점(0~100) + 개선 경고.
    가점: 검색의도 정합·1차 경험·구체 수치·이미지 4+·Q&A·제목-본문 일치·롱테일.
    감점/차단: 키워드 도배·낚시(제목-본문 불일치)·빈약 문서·과장/날조(표시광고법)·PII.
    source(입력 메모+사진분석) 제공 시 입력에 없는 금액·수치 날조를 기계적으로 탐지(PHASE 7·9)."""
    text = (payload.get("body") or payload.get("text") or "")
    warnings: list[str] = []
    para_penalty = 0                              # 문단 단위 감점(게이트와 분리 — 표시 전용)
    score = 100

    # 사실 검증: 출력의 금액·%·수치가 입력에 존재하는지 대조(LLM 0콜 날조 탐지)
    # source 미전달 호출(게이트 경로 등)은 생성 시 저장한 payload.gen_source로 폴백
    source = source or (payload.get("gen_source") or "")
    if source:
        _src_nums = _money_nums(source) | _clock_nums(source)   # 시계 표기도 같은 사실로 읽는다
        fabricated = [n for n in _money_nums(text) if n not in _src_nums]
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
    # 겁주기·공포 마케팅 — 영상 자막과 같은 목록으로 검사(2026-08-02 실사고: 본문 '호구'가
    # 88점으로 통과했다). 손님 불안을 파는 화법은 E-E-A-T·정직 원칙에 정면으로 어긋난다.
    _fear_hit = []
    for _fp in _fear_patterns():
        _m = re.search(_fp, text)
        if _m and _m.group(0) not in _fear_hit:
            _fear_hit.append(_m.group(0))
    if _fear_hit:
        warnings.append(f"겁주기 표현 {_fear_hit[:3]} → 불안 마케팅(사실 서술로 교체)")
        score -= min(15, 6 * len(_fear_hit))
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
        # (v2 1-5) 도입 훅 3요소 — 첫 3~4문장에 '읽을 이유 예고' 신호.
        # ★ 2026-08-02 오탐 수정: 옛 목록은 특정 낱말('알려'·'아래에서')만 인정해서, 멀쩡한
        #   예고를 못 읽고 -6점을 먹였다 — 실측 문장: "이 글에서는 A, B, 그리고 C까지 순서대로
        #   확인하실 수 있습니다"(예고의 교과서적 형태). 오탐은 불필요한 수선·재작성 비용을
        #   부르고(날조 오탐 실사고와 같은 계열), 무엇보다 좋은 글을 나쁜 글로 기록한다.
        #   → 낱말 목록이 아니라 '예고 구문'으로 판정한다: 범위 지시(이 글/아래/여기서) +
        #     제시 동사(확인·정리·비교·보여·알려·다루·담·짚) 또는 순서 예고(순서대로/차례대로).
        #   언어 규칙만 — 업종·지명 하드코딩 0.
        #   ★ 2차 보정(2026-08-02 실측): 범위 지시어 목록으로도 부족했다 —
        #     "오늘은 ... 사진으로 다 보여드리려고 합니다"가 또 빠져나갔다.
        #     예고의 본질은 범위 지시어가 아니라 '제시 동사 + 앞으로 하겠다는 어미'다.
        #     과거형('확인했습니다')은 어미가 달라 자동으로 걸러진다.
        _PREVIEW = (r"(마지막|끝까지|끝에|글 후반|확인하는 법|순서대로|차례대로|하나씩"
                    r"|(확인|정리|비교|보여|알려|다루|짚어|풀어|공개)"
                    r"[^.]{0,4}(드릴|드리려|드리겠|드립니다|볼게요|보겠|할게요|하겠|려고|ㄹ게요)"
                    r"|(이 ?글|아래|여기|이번 ?글|본문)[^.]{0,40}"
                    r"(확인|정리|비교|보여|알려|다루|다룹|담았|담고|짚|풀어))")
        _intro = " ".join(_bparas[:2])[:220]
        if _intro and not re.search(_PREVIEW, _intro):
            warnings.append("도입에 '끝까지 읽을 이유' 예고 없음 → 초반 이탈 위험(v2 도입 훅 3요소)")
            score -= 6
        # 📷 사진에 우연히 담긴 것(2026-08-03 사장님 지적) — 시계에 찍힌 시각, 배경 사물은
        #   손님이 사는 것과 무관하다. 사진 분석에 나왔다고 글감으로 쓰면 글이 산만해진다.
        #   실측 문장: "시계에 10시 21분이 찍혀 있는데, 이 검수·광택 단계에만 공을 들였습니다".
        #   ※ 소요시간('10분이면 끝납니다')·영업시간은 정보다 — 시계·화면을 가리키는 서술만 잡는다.
        if re.search(r"(시계|시각|화면)[^.\n]{0,24}\d{1,2}\s*[시:]\s*\d{1,2}\s*분?", text) \
                or re.search(r"\d{1,2}시\s?\d{1,2}분[^.\n]{0,12}(찍혀|표시|보이)", text):
            warnings.append("사진에 찍힌 시각을 본문에 서술 — 손님과 무관한 촬영 부수물(빼기)")
            score -= 6
        # 🏷 입력 식별자 누락(2026-08-03 실사고) — 사장님이 준 모델명이 본문에 없으면
        #   글이 '신차 한 대'로 뭉개진다. 검색어이자 신뢰 근거를 버리는 것이다.
        _anch = [a for a in input_anchors(source or "") if a not in text]
        if _anch:
            warnings.append(f"입력의 모델·등급명 {_anch[:2]}가 본문에 없음 → 소재가 뭉개짐(그대로 쓰기)")
            score -= min(10, 5 * len(_anch))
        # 🔢 비한국어 수 표기(2026-08-02) — 기계 교정이 있지만, 새는 경로가 생기면 눈에 보여야 한다
        _dec = _KR_DEC_UNIT.findall(title + " " + text)
        if _dec:
            _sample = f"{_dec[0][0]}.{_dec[0][1]}{_dec[0][3]}"
            warnings.append(f"한국어에 없는 수 표기('{_sample}') → '5만 7천' 식으로")
            score -= 5
        # 입력 원문 노출(생성품질 E2E #2): '썬팅,광택' 같은 쉼표 나열형이 제목/첫문단에 그대로 박히면 감점
        if re.search(r"[가-힣A-Za-z]{2,},[가-힣A-Za-z]{2,}", title + " " + text[:150]):
            warnings.append("쉼표 나열형 입력이 원문 그대로 노출 — 자연어로 풀어 쓰기('썬팅과 광택')")
            score -= 10
        # 🗣 읽는 사람 시점(2026-08-01 사장님 지적) — 제목이 '가게가 하는 일'로 쓰이면 손님 입장에서
        #   주어가 뒤집힌다("중고차판매 가격 걱정?"을 읽는 사람은 사려는 사람이다).
        #   공급자 접미어는 검색어 정규화에 쓰는 것과 같은 언어 목록을 재사용(업종 하드코딩 0).
        #   ★ 단, 타깃 키워드 자체에 그 말이 들어 있으면 정상이다 — '간판제작'처럼 손님도 실제로
        #     검색하는 업종어가 있다(검색량 승부로 이미 검증된 말). 키워드 밖에서 가게 시점 표현이
        #     새어 들어온 경우만 잡는다. 조어 검사(검수기 등)는 오탐이 커서 프롬프트 지침으로만 다룬다.
        _tkw0 = " ".join((payload.get("target_keywords") or [""])[:3])
        #   ★ '전문'⊂'전문가', '제조'⊂'제조사'처럼 다른 낱말의 앞부분으로 더 자주 쓰이는 토큰은
        #     제목 검사에서 뺀다(검토 지적 — 정상 제목이 -6점 먹었다). 축약 로직에는 그대로 쓴다.
        _sup_hit = [t for t in ("판매", "시공", "납품", "매입", "도매", "소매")
                    if t in title and t not in _tkw0]
        if _sup_hit:
            warnings.append(f"제목이 가게 시점 용어({_sup_hit[0]}) — 읽는 사람은 손님이다. "
                            "손님 행동어(구매·고르기·맡기기)로 바꿔라")
            score -= 6
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
        # 🦴 2026-08-19 — 골격이 FAQ를 요구하지 않는 글은 감점하지 않는다.
        #   전에는 모든 글에서 -4를 매겨, 점수를 지키려면 FAQ를 붙일 수밖에 없었다.
        #   그게 8편이 같은 뼈대가 된 이유 중 하나다(services/blogshape.py).
        from app.services import blogshape as _shp2
        from app.services import sections as _sec2
        if _shp2.needs_faq(payload.get("blog_shape") or "") and not _sec2.has_faq(text):
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
        _np = photo_count(payload)                # 마커가 아니라 실제 사진 수(단일 관문)
        if _np < 4:                               # 이미지 4장 미만 → 정합·체류 신호 약함(PHASE 9)
            warnings.append(f"이미지 {_np}장 < 4 → 이미지 정합·체류 신호 약함")
            score -= 4
        # 📐 문단 단위 채점(2026-08-16 사장님 지적: "점수 로직도 문단으로 채점해야 하는 거 아니야?")
        #   위 항목들은 전부 **글 전체 세기**라 흩어져 있어도 개수만 채우면 통과한다.
        #   실물: 투싼 글은 '2,990만원'이 다섯 문단에 하나씩 흩어져 '구체 수치 충분'으로 통과했지만
        #        한 문단에 모인 게 없어 네이버가 뽑아갈 단위는 0이었다.
        #   더 결정적: 90점 글이 두꺼운 문단 0개(최장 111자), 66점 글이 3개(최장 308자)로 뒤집혔다.
        #   ★ 이 감점은 **발행 게이트를 건드리지 않는다**(para_penalty로 따로 센다).
        #     기준선을 실측으로 다시 잡기 전에 봉인하면 대부분의 글이 막힌다 — 표시만 한다.
        try:
            from app.services import answerblock as _abq
            _th = _abq.thickness(text)
            if not _th["ok"]:
                warnings.append(
                    f"문단이 얇다 — {_abq.MIN_THICK_CHARS}자 이상 문단 {_th['n_thick']}개"
                    f"(최장 {_th['longest']}자). 네이버는 문단 하나를 뽑아 노출한다")
                para_penalty += 15
            _paras = _abq.paragraphs(text)
            # ★ 숫자 '글자'가 아니라 '수'를 센다 — \d로 세면 '30만원' 하나가 2로 잡혀
            #   흩어진 글도 통과했다(골든이 잡음, 2026-08-16).
            if not any(len(re.findall(r"\d[\d,]*", _p)) >= 2 for _p in _paras):
                warnings.append("수치가 한 문단에 모인 곳이 없다 → 그 질문의 답이 되는 덩어리 부재")
                para_penalty += 6
            if not any(any(w in _p for w in _EXPERIENCE_WORDS) and
                       len(re.sub(r"\s", "", _p)) >= _abq.MIN_THICK_CHARS for _p in _paras):
                warnings.append("경험 서술이 한 덩어리로 서 있지 않다 → D.I.A. 인용 단위 부재")
                para_penalty += 8
            _cov = _abq.query_coverage(text, payload.get("query_plan") or {})
            _nc = [c["query"] for c in _cov if not c["covered"]]
            if _nc:
                warnings.append(f"노린 질의에 답 문단 없음 — {', '.join(_nc[:2])}")
                para_penalty += 10 * len(_nc)
        except Exception:
            pass                                  # 채점 실패가 생성을 막지 않는다
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
    # ★ 문단 점수는 따로 낸다 — 발행 게이트(PUBLISH_MIN)는 기존 score만 본다.
    #   기준선을 실측으로 다시 잡기 전까지 봉인하지 않는다(2026-08-16 사장님 승인 순서).
    score_para = max(0, min(100, score - para_penalty))
    grade = "우수" if score >= 85 else ("양호" if score >= 70 else "개선필요")
    return {"score": score, "grade": grade, "warnings": warnings,
            "score_para": score_para, "para_penalty": para_penalty}
