"""
온보딩/랜딩 '내 가게 현재 순위 즉시진단' — 결제 트리거(성장 PHASE 1).
넓은→좁은 롱테일 키워드 3~5개를 각각 조회해, 네이버 지역검색 5건 한계 안에서도
'어느 키워드에서 몇 위인지'를 최대한 수집한다. 미노출 키워드는 가짜 순위 없이 '기회'로 표기하고,
실검색량(SearchAd)을 붙여 '놓치는 검색량'을 손실 프레이밍한다.

정직성: 6위 이하(5위 밖)는 임의 숫자 금지 → '미노출'. '무조건 1위' 보장 금지 → '상위노출 목표'.
크롤링 금지 — 공식 지역검색 API 범위 내에서만.
"""
from __future__ import annotations

from app.services import place

MAX_SCAN = 4   # 스캔 키워드 상한(네이버 Local API 호출 수 제한)


def _rank_keywords(region: str, industry: str) -> list[str]:
    """넓은→좁은 [지역+업종] 키워드 생성(중복 제거, 최대 MAX_SCAN)."""
    region, industry = (region or "").strip(), (industry or "").strip()
    if not industry:
        return []
    toks = [t for t in region.split() if t]
    cands: list[str] = []
    if not toks:
        cands.append(industry)                                  # 지역 모르면 업종만
    else:
        cands.append(f"{toks[0]} {industry}")                   # 시/도(넓음): '부산 중고차'
        if len(toks) >= 2:
            cands.append(f"{toks[0]} {toks[1]} {industry}")     # 구(중간): '부산 동구 중고차'
        if len(toks) >= 3 or (len(toks) >= 1 and toks[-1] != toks[0]):
            cands.append(f"{toks[-1]} {industry}")              # 동/역(롱테일): '초량동 중고차'
        cands.append(f"{region} {industry}")                    # 풀
    # 중복 제거(순서 유지) + 상한
    out, seen = [], set()
    for k in cands:
        k = " ".join(k.split())
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:MAX_SCAN]


def _volumes(keywords: list[str]) -> dict:
    """키워드 → 월 검색량(total). 무키/실패 시 빈 dict. 공백제거 키로 매칭."""
    try:
        from app.services import searchad
        rows = searchad.keyword_volumes(keywords)
        return {(r.get("keyword") or "").replace(" ", ""): r.get("total", 0) for r in rows}
    except Exception:
        return {}


def diagnose_rank(industry: str, region: str, name: str) -> dict:
    """다중 키워드 스캔 결과. 반환:
    {keyword, rank, estimated, headline, subline, cta,
     scan:[{keyword,rank,volume,status}], caught:[...], missing:[...], missed_volume}
    status: 'top'(1~5위) | 'missing'(5위 밖) | 'unknown'(조회불가). 하위호환 위해 top-level 필드 유지."""
    industry, region, name = (industry or "").strip(), (region or "").strip(), (name or "").strip()
    keywords = _rank_keywords(region, industry)
    primary = keywords[-1] if keywords else (f"{region} {industry}".strip() or industry or "내 지역 업종")

    # 상호 없으면 순위 조회 불가 → 정직한 추정 폴백(검색량은 붙여 기회 제시)
    if not name or not keywords:
        return {
            "keyword": primary, "rank": None, "estimated": True,
            "scan": [], "caught": [], "missing": [], "missed_volume": 0,
            "headline": f"'{primary}' 등에서 우리 가게, 아직 상위에 없을 가능성이 커요",
            "subline": "상호까지 입력하면 키워드별 실제 순위를 바로 보여드려요.",
            "cta": "올린다로 상위노출 시작하기",
        }

    vol = _volumes(keywords)
    scan, any_measured = [], False
    for kw in keywords:
        rank = place.rank(kw, name)            # 1~5 / 0(5위밖) / None(조회불가)
        v = vol.get(kw.replace(" ", ""), None)
        if rank is None:
            status = "unknown"
        elif rank == 0:
            status = "missing"                 # 5위 밖 — 가짜 순위 절대 안 만듦
            any_measured = True
        else:
            status = "top"
            any_measured = True
        scan.append({"keyword": kw, "rank": rank, "volume": v, "status": status})

    caught = [s for s in scan if s["status"] == "top"]
    missing = [s for s in scan if s["status"] == "missing"]
    missed_volume = sum(s["volume"] for s in missing if s["volume"])

    # 전부 unknown = 조회 자체 실패(무키/네트워크) → 추정
    if not any_measured:
        return {
            "keyword": primary, "rank": None, "estimated": True,
            "scan": scan, "caught": [], "missing": [], "missed_volume": 0,
            "headline": f"'{primary}' 등에서 우리 가게, 아직 상위에 없을 가능성이 커요",
            "subline": "대부분의 소상공인은 상위 노출 구조가 아니에요. (실측은 연결 후 정확히)",
            "cta": "올린다로 상위노출 시작하기",
        }

    # 하위호환: 대표 순위 = 가장 잘 잡힌 키워드
    best = min(caught, key=lambda s: s["rank"]) if caught else None
    top_rank = best["rank"] if best else 0

    def _mv(v):
        return f"(월 {v:,}회 검색)" if v else ""

    if caught:
        lead = f"'{best['keyword']}' {best['rank']}위"
        more = f" 외 {len(caught) - 1}개 키워드 상위 노출 중" if len(caught) > 1 else " 상위 노출 중!"
        headline = f"🎯 {lead}{more}"
    else:
        headline = "아직 상위 노출된 키워드가 없어요 — 기회가 큽니다"

    # 놓치는 키워드(검색량 큰 순)로 손실 프레이밍
    miss_sorted = sorted(missing, key=lambda s: -(s["volume"] or 0))
    if miss_sorted:
        top_miss = miss_sorted[0]
        sub = f"'{top_miss['keyword']}'{_mv(top_miss['volume'])}는 아직 놓치고 있어요."
        if missed_volume:
            sub += f" 미노출 키워드 합계 월 {missed_volume:,}회 검색을 놓치는 중."
    else:
        sub = "잡은 키워드를 더 넓혀 상위노출을 늘릴 수 있어요."

    cta = (f"놓치는 검색 월 {missed_volume:,}회 잡으러 가기" if missed_volume
           else ("상위 유지·강화하기" if top_rank == 1 else "상위노출 시작하기"))

    return {
        "keyword": primary, "rank": top_rank, "estimated": False,
        "scan": scan, "caught": caught, "missing": missing, "missed_volume": missed_volume,
        "headline": headline, "subline": sub, "cta": cta,
    }


MAX_SCAN_SHOP = 3   # 쇼핑검색 스캔 키워드 상한(대표 1 + 롱테일 2)


def _product_keywords(product_kw: str) -> list[str]:
    """대표 상품 키워드 + searchad 연관 롱테일(500~5,000 스윗스팟) 2개. 무키면 대표만."""
    product_kw = " ".join((product_kw or "").split())
    if not product_kw:
        return []
    out = [product_kw]
    try:
        from app.services import searchad
        for kw in searchad.sweet_spot_keywords([product_kw], limit=MAX_SCAN_SHOP + 2):
            k = " ".join(kw.split())
            if k and k.replace(" ", "") != product_kw.replace(" ", "") and k not in out:
                out.append(k)
            if len(out) >= MAX_SCAN_SHOP:
                break
    except Exception:
        pass
    return out[:MAX_SCAN_SHOP]


def diagnose_product_rank(product_kw: str, store: str, brand: str = "") -> dict:
    """셀러용 상품 순위 진단 — 네이버 쇼핑검색 상위 40위 안에서 내 스토어/브랜드 위치.
    반환 구조는 diagnose_rank와 동일(+mode/scan_depth/miss_label) → 랜딩/대시보드 렌더러 재사용.
    정직성: 40위 밖은 임의 숫자 금지 → '40위 밖'. 조회 불가는 추정 라벨."""
    product_kw, store, brand = (product_kw or "").strip(), (store or "").strip(), (brand or "").strip()
    keywords = _product_keywords(product_kw)
    primary = keywords[0] if keywords else (product_kw or "내 상품 키워드")
    base = {"mode": "seller", "scan_depth": place.SHOP_SCAN_DEPTH,
            "miss_label": f"{place.SHOP_SCAN_DEPTH}위 밖"}

    if not keywords or not (store or brand):
        return {**base, "keyword": primary, "rank": None, "estimated": True,
                "scan": [], "caught": [], "missing": [], "missed_volume": 0,
                "headline": f"'{primary}' 쇼핑 검색에서 내 상품, 아직 상위에 없을 가능성이 커요",
                "subline": "스토어명(또는 브랜드)까지 입력하면 키워드별 실제 순위를 바로 보여드려요.",
                "cta": "올린다로 상품 노출 시작하기"}

    vol = _volumes(keywords)
    scan, any_measured = [], False
    for kw in keywords:
        rank = place.shop_rank(kw, store, brand)     # 1~40 / 0(40위 밖) / None(조회불가)
        v = vol.get(kw.replace(" ", ""), None)
        if rank is None:
            status = "unknown"
        elif rank == 0:
            status = "missing"                       # 40위 밖 — 가짜 순위 절대 안 만듦
            any_measured = True
        else:
            status = "top"
            any_measured = True
        scan.append({"keyword": kw, "rank": rank, "volume": v, "status": status})

    caught = [s for s in scan if s["status"] == "top"]
    missing = [s for s in scan if s["status"] == "missing"]
    missed_volume = sum(s["volume"] for s in missing if s["volume"])

    if not any_measured:
        return {**base, "keyword": primary, "rank": None, "estimated": True,
                "scan": scan, "caught": [], "missing": [], "missed_volume": 0,
                "headline": f"'{primary}' 쇼핑 검색에서 내 상품, 아직 상위에 없을 가능성이 커요",
                "subline": "대부분의 셀러 상품은 검색 상위 구조가 아니에요. (실측은 연결 후 정확히)",
                "cta": "올린다로 상품 노출 시작하기"}

    best = min(caught, key=lambda s: s["rank"]) if caught else None

    def _mv(v):
        return f"(월 {v:,}회 검색)" if v else ""

    if caught:
        more = f" 외 {len(caught) - 1}개 키워드 노출 중" if len(caught) > 1 else " 노출 중!"
        headline = f"🎯 '{best['keyword']}' 쇼핑 {best['rank']}위{more}"
    else:
        headline = f"쇼핑 상위 {place.SHOP_SCAN_DEPTH}위 안에 내 상품이 안 보여요 — 기회가 큽니다"

    miss_sorted = sorted(missing, key=lambda s: -(s["volume"] or 0))
    if miss_sorted:
        top_miss = miss_sorted[0]
        sub = f"'{top_miss['keyword']}'{_mv(top_miss['volume'])} — 내 상품 미노출이에요."
        if missed_volume:
            sub += f" 미노출 키워드 합계 월 {missed_volume:,}회 검색을 놓치는 중."
        sub += " 후기 콘텐츠가 검색 유입의 지렛대예요."
    else:
        sub = "잡은 키워드를 후기 콘텐츠로 굳혀 순위를 지킬 수 있어요."

    cta = (f"놓치는 검색 월 {missed_volume:,}회 잡으러 가기" if missed_volume
           else ("노출 유지·강화하기" if best and best["rank"] == 1 else "상품 노출 시작하기"))

    return {**base, "keyword": primary, "rank": (best["rank"] if best else 0), "estimated": False,
            "scan": scan, "caught": caught, "missing": missing, "missed_volume": missed_volume,
            "headline": headline, "subline": sub, "cta": cta}


def save_baseline(tenant_id: str, result: dict) -> None:
    """실측된 키워드 순위를 rank_snapshots에 baseline으로 저장 — before/after 기준점(PHASE 1·2).
    미노출(5위 밖)·조회불가는 저장 안 함(가짜 순위 방지). 셀러(쇼핑)는 kind='shop'으로 구분."""
    try:
        from app import db
        if result.get("estimated"):
            return
        # 매장은 기존 관례(kind 기본값) 유지 — ranktrack 일일 잡·이력과 연속성. 셀러만 'shop' 분리.
        kind = "shop" if result.get("mode") == "seller" else "blog"
        for s in (result.get("caught") or []):
            if s.get("rank"):
                db.save_rank_snapshot(tenant_id, s.get("keyword", ""), s["rank"], kind=kind)
    except Exception:
        pass
