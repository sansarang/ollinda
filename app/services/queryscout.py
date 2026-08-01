"""
🔎 검색어 정찰 — "우리 글이 실제로 어떤 검색어에서 잡히는가"를 로그인 없이 실측(2026-08-01 사장님 승인 ①).

왜: 네이버는 블로그 유입 검색어를 공개 API로 주지 않는다(어드바이저는 로그인 화면 안). 고객 계정
자격증명을 우리가 쥐는 것은 고객 리스크라 하지 않는다. 대신 같은 '행동 가능한 정보'를 공개 검색
API만으로 얻는다 — 발행 글에서 검색어 후보를 뽑아 순위를 조회하면, 상위에 잡히는 후보가 곧
'실제로 유입되는 검색어'다(조회수 숫자는 없지만 무엇으로 들어오는지는 같다).

원칙: 자격증명 0 · 크롤 0(공개 검색 API만) · 키워드 하드코딩 0(후보는 글 자신에서 추출) ·
      실패는 조용히(파이프라인 무영향).
"""
from __future__ import annotations

import logging
import re

from app import db

_log = logging.getLogger("shopcast.queryscout")

# 실유입 판정 최소 월검색량 — 순위가 잡혀도 이 미만은 '사람이 안 치는 문장'(잡음)으로 본다.
# 기존 지역 키워드 관문(seo.REGION_MIN_VOLUME=100)과 같은 기준 — 별도 상수 남발 대신 동일 잣대.
MIN_VOLUME = 100

_STOP = {"그리고", "하지만", "그래서", "합니다", "입니다", "있습니다", "때문에", "우리", "저희",
         "오늘", "이번", "정도", "경우", "생각", "사진", "고객", "사장", "여기", "부분", "가능"}
_JOSA = re.compile(r"(은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|까지|부터|처럼|보다)$")


def _clean(tok: str) -> str:
    tok = re.sub(r"[^0-9A-Za-z가-힣]+", "", tok or "")
    return _JOSA.sub("", tok) if len(tok) > 2 else tok


def candidates(payload: dict, region: str = "", industry: str = "", limit: int = 14,
               biz: str = "local", brand: str = "", search_kw: str = "") -> list[str]:
    """발행 글 자체에서 검색어 후보 추출 — 소제목·FAQ 질문·본문 빈출 명사구(언어 구조만, 하드코딩 0).
    biz: local(동네매장)=지역+업종 축 / seller(온라인셀러)=브랜드·상품 축(지역 토큰 없음) / hybrid=둘 다.
    축 판단은 seo.canonical_region(전 표면 단일 소스)에 위임 — 여기서 별도 규칙을 만들지 않는다."""
    body = (payload.get("body") or "")
    title = (payload.get("title") or "")
    out: list[str] = []
    # 주제어 집합 — 제목·지역·업종·브랜드·상품검색어의 어휘. 이 중 하나도 안 겹치는 후보는
    #   '구조 제목'(자주 묻는 질문·한눈 요약 등 템플릿 문구)이라 유입 검색어로 볼 수 없다(전 업종 공통).
    _topic = {_clean(t) for t in re.findall(
        r"[0-9A-Za-z가-힣]{2,}", f"{title} {region} {industry} {brand} {search_kw}")}
    _topic = {t for t in _topic if len(t) >= 2}

    def _add(s: str):
        s = " ".join((s or "").split())
        s = re.sub(r"^[#\-•\d.\)\s]+", "", s).strip(" ?!·|")
        if not (4 <= len(s) <= 28) or s in out:
            return
        if _topic:                                       # 주제 무관 구조 제목 배제
            toks = {_clean(t) for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", s)}
            if not (toks & _topic):
                return
        out.append(s)

    for m in re.findall(r"^#{2,3}\s*(.+)$", body, re.M):        # 소제목 = 글이 답하는 질문 단위
        _add(m)
    for m in re.findall(r"^\s*(?:Q[.:]|질문)\s*(.+)$", body, re.M):   # FAQ 질문
        _add(m)
    _add(title)
    # 본문 빈출 2어절 조합(지역·업종 축과 결합) — 검색자가 실제로 칠 법한 형태
    toks = [_clean(t) for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", body)]
    toks = [t for t in toks if len(t) >= 2 and t not in _STOP]
    freq: dict = {}
    for t in toks:
        freq[t] = freq.get(t, 0) + 1
    top = [t for t, n in sorted(freq.items(), key=lambda x: -x[1])[:12] if n >= 3]
    # ★ 실검색어 확장(2026-08-01 실측 교훈): 후보를 '지어내면' 아무도 안 치는 조합만 나온다
    #   ('부산 부산', '계기판 중고차판매'…). 씨앗(축+업종+제목 고유명사)만 우리가 뽑고,
    #   실제 사람이 치는 검색어는 검색광고 API의 연관 키워드에서 가져온다(전 업종·업태 공통).
    _seeds: list = []
    try:
        from app import seo as _seo0
        _reg0 = _seo0.canonical_region(region or "", biz or "local", industry or "")
    except Exception:
        _reg0 = "" if (biz or "local") == "seller" else (region or "").split()[0]
    _reg0 = (_reg0 or "").split()[0] if _reg0 else ""
    _ind0 = ((industry or "").replace("/", ",").split(",")[0] or "").strip()
    for a in [x for x in (_reg0, brand.strip(), (search_kw or "").split(",")[0].strip()) if x]:
        if _ind0:
            _seeds.append(f"{a} {_ind0}")
    if _ind0:
        _seeds.append(_ind0)
    # 제목의 고유명사(모델명·상품명 등) — 업종 무관하게 '숫자·영문 포함' 또는 긴 명사를 씨앗으로
    for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", title):
        c = _clean(t)
        if c and c not in _STOP and (re.search(r"[0-9A-Za-z]", c) or len(c) >= 3) and c != _reg0:
            _seeds.append(c)
    # ★ 시장 도메인 용어를 씨앗에 합류(2026-08-01) — 상위 글들이 공통으로 쓰는 단일 용어
    #   ('유리막코팅','성능점검기록부')를 검색광고에 물으면 그 판의 실검색어가 대량으로 나온다.
    #   (실측: 중고차 판은 2~3어절 교차만으로는 후보가 2개뿐이었다)
    _mkt_terms: list = []
    try:
        from app.services import bloganatomy as _ba1
        for _sd in _seeds[:2]:
            _an1 = _ba1.cached(_sd)
            for cp in ((_an1 or {}).get("common_phrases") or [])[:12]:
                _p = (cp.get("p") or "").strip()
                if _p and len(_p.split()) == 1 and len(_p) >= 3:
                    _mkt_terms.append(_p)
    except Exception:
        pass
    try:
        from app.services import searchad as _sa0
        _seed_all = (_seeds[:3] + _mkt_terms[:2]) or _seeds
        rel = _sa0.keyword_volumes(_seed_all[:5], limit=120) if _seed_all else []
    except Exception:
        rel = []
    _axis_tokens = {t for t in (_reg0, _ind0, brand.strip()) if t}
    # ★ 3번째 공급원(2026-08-01 사장님 설계): 상위 글 본문에서 뽑힌 '시장 공통 검색 의도 구절'.
    #   검색광고 연관어는 씨앗과 글자가 겹치는 것만 주는 한계가 있어(실측: 주안모터스 후보 2개),
    #   이 판에서 실제로 통하는 표현을 상위 글들의 교차 등장으로 가져온다(캐시만 — 크롤 대기 0).
    try:
        from app.services import bloganatomy as _ba0
        for _seed in _seeds[:2]:
            _an = _ba0.cached(_seed)
            if _an is None:
                _ba0.ensure_async(_seed)               # 없으면 예열만(다음 회차부터 반영)
                continue
            for cp in (_an.get("common_phrases") or [])[:10]:
                _add(cp.get("p") or "")
    except Exception:
        pass
    for v in sorted(rel, key=lambda x: -(x.get("total") or 0)):
        kw, vol = (v.get("keyword") or "").strip(), (v.get("total") or 0)
        if vol < MIN_VOLUME or not kw:
            continue
        # 이 가게와 무관한 연관어 배제 — 축(지역·업종·브랜드) 어휘를 하나는 포함해야 함
        if _axis_tokens and not any(a and a in kw for a in _axis_tokens):
            continue
        _add(kw)
    # ★ 지역 토큰은 canonical(축약형)으로 — 실측: '부산광역시 썬팅'은 아무도 안 친다(검색량 0).
    #   전 표면이 쓰는 단일 소스(seo.canonical_region)를 그대로 재사용(별도 규칙 만들지 않음).
    reg = ""
    try:                                                 # 셀러면 canonical_region이 ''를 준다(지역 미주입)
        from app import seo as _seo
        reg = _seo.canonical_region(region or "", biz or "local", industry or "")
    except Exception:
        reg = "" if (biz or "local") == "seller" else (region or "").split()[0]
    reg = (reg or "").split()[0] if reg else ""          # '부산 기장' → '부산'(광역 우선, 조합 폭발 방지)
    ind0 = ((industry or "").replace("/", ",").split(",")[0] or "").strip()
    # 축(axis): 매장=지역+업종 / 셀러=브랜드·상품+업종 — 어느 쪽이든 '가게가 가진 값'만 조합(하드코딩 0)
    axes = [a for a in ([reg] if reg else []) + [brand.strip(), (search_kw or "").split(",")[0].strip()] if a]
    for a in axes[:2]:
        if ind0:
            _add(f"{a} {ind0}")                          # 가장 흔한 검색형(축+업종)
    for t in top[:8]:
        if t in axes or t == ind0:                       # '부산 부산' 류 중복 조합 방지(실측)
            continue
        for a in axes[:2]:
            _add(f"{a} {t}")
        _add(t if len(t) >= 4 else f"{t} {ind0}".strip())
    return out[:limit]


def scout(tenant_id: str, max_posts: int = 3, per_post: int = 10) -> dict:
    """발행 글별 후보를 순위 조회해 '잡히는 검색어'를 찾는다.
    잡히면(1~30위) 추적 스냅샷 저장, 검색량 있는데 미노출이면 글감 큐 적재. 반환: 요약 dict."""
    t = db.get_tenant(tenant_id)
    bid = (getattr(t, "blog_id", "") or "").strip() if t else ""
    if not (t and bid):
        return {"ok": False, "error": "블로그 미연결"}
    from app.services import blogrank as _br
    if not _br.configured():
        return {"ok": False, "error": "네이버 검색 키 없음"}
    try:
        from app.services import searchad as _sa
    except Exception:
        _sa = None

    def _vols(words: list) -> dict:
        """검색량 배치 조회 — 5개 단위(API 상한), 물어본 키워드 정확 매칭만."""
        if not (_sa and words):
            return {}
        try:
            return _sa.volume_map(words[:40])
        except Exception:
            return {}

    hits, misses, checked, noise = [], [], 0, []
    seen_kw: set = set()                                 # 글마다 후보가 겹쳐 같은 검색어를 중복 조회하던 것 방지
    for pub in db.list_blog_publishes(tenant_id, limit=max_posts):
        p = None
        try:
            p = db.get_piece(pub.get("piece_id") or "")
        except Exception:
            pass
        pl = (p.payload if p else None) or {"title": pub.get("post_title") or ""}
        # ★ 순서 교정(2026-08-01 실측): 후보를 먼저 자르면 정작 검색량 큰 키워드가 잘려나간다
        #   ('부산 썬팅' 760회가 신규 용어들에 밀려 탈락 → 실유입 0). 전 후보를 모아 검색량으로
        #   먼저 거르고, 살아남은 것만 순위 조회한다(무의미 키워드에 순위 API를 안 쓰니 쿼터도 절약).
        all_cands = candidates(pl, region=getattr(t, "region", "") or "",
                               industry=getattr(t, "industry", "") or "",
                               limit=40,
                               biz=getattr(t, "biz_type", "local") or "local",
                               brand=getattr(t, "brand_name", "") or "",
                               search_kw=getattr(t, "search_kw", "") or "")
        _cv = _vols([c for c in all_cands if c.replace(" ", "") not in seen_kw])
        scored = [(c, _cv.get(c.replace(" ", ""), 0)) for c in all_cands]
        noise += [{"kw": c, "rank": None, "volume": v} for c, v in scored if v < MIN_VOLUME][:10]
        cands = [c for c, v in sorted([s for s in scored if s[1] >= MIN_VOLUME],
                                      key=lambda x: -x[1])][:per_post]
        for kw in cands:
            k_norm = kw.replace(" ", "")
            if k_norm in seen_kw:
                continue
            seen_kw.add(k_norm)
            checked += 1
            try:
                r = _br.blog_rank(kw, bid)
            except Exception:
                continue
            rank = r.get("rank")
            if isinstance(rank, int) and rank >= 1:
                hits.append({"kw": kw, "rank": rank, "url": r.get("url", ""),
                             "volume": _cv.get(k_norm, 0)})
            else:
                misses.append(kw)

    # 후보 단계에서 이미 검색량 관문을 통과했으므로 여기 오는 것은 전부 '실수요 검색어'다.
    real = hits
    for h in real:                                       # 추적 편입(성과 관측)
        db.save_rank_snapshot(tenant_id, h["kw"], h["rank"], kind="blog_search")

    # 미노출분은 '수요는 있는데 아직 안 잡히는 것' → 글감 큐(기회)
    queued = 0
    mv = _vols(misses)
    for kw in misses[:20]:
        vol = mv.get(kw.replace(" ", ""), 0)
        if vol >= MIN_VOLUME and db.enqueue_writing(
                tenant_id, "queryscout", kw, "review",
                f"우리 글이 답하는 주제인데 미노출(월 {vol:,}회) — 전용 글로 공략"):
            queued += 1
    real.sort(key=lambda h: (h["rank"], -h["volume"]))
    _log.info("[queryscout] %s 검사 %d → 실유입 %d(잡음 %d) · 큐 %d",
              tenant_id[:8], checked, len(real), len(noise), queued)
    return {"ok": True, "checked": checked, "hits": real[:20], "hit_count": len(real),
            "noise_filtered": len(noise), "queued": queued,
            "noise_sample": [n["kw"] for n in noise[:5]]}


def sweep(limit: int = 20) -> None:
    """크론 진입점 — 블로그 연결 가게 전체 정찰(가게당 최근 글 2편·후보 8개, 검색 API만)."""
    for t in db.list_tenants_with_blog()[:limit]:
        try:
            scout(t.id, max_posts=2, per_post=8)
        except Exception:
            _log.exception("[queryscout] 실패 t=%s", getattr(t, "id", "?"))
