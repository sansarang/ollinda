"""
노출(도달) 예측 — 발행 시 예상 도달/유입을 '범위'로 추정.
※ 정확한 수치는 불가(알고리즘·팔로워·시점 변수). 근거 있는 추정 범위만 제공.
   계정 API(팔로워/인사이트) 연동 시, 그리고 발행 실측이 쌓일수록 정밀화.
입력 신호: 채널 벤치마크 × 품질점수(상위노출) × 타겟키워드 수.
"""
from __future__ import annotations

# (채널, 종류) → 소형/신규 계정 기준 1건당 도달 벤치마크 (low, high)
_BENCH = {
    ("instagram", "caption"): (150, 450),    # 피드/캐러셀
    ("instagram", "short"): (400, 1800),     # 릴스(도달 큼)
    ("youtube", "short"): (120, 900),        # 쇼츠 초기 조회
    ("naver_blog", "blog"): (60, 220),       # 월 검색 유입(누적)
    ("x", "x_post"): (50, 350),
}


def estimate(channel: str, kind: str, payload: dict) -> dict:
    lo, hi = _BENCH.get((channel, kind), (80, 300))
    score = (payload.get("ranking_audit") or {}).get("score") or 70
    mult = 0.7 + (score / 100) * 0.8                 # 0.7~1.5 (품질 반영)
    kw = len(payload.get("target_keywords") or [])
    kw_boost = 1 + min(kw, 8) * 0.02                 # 키워드 많을수록 검색노출↑
    low = int(lo * mult)
    high = int(hi * mult * kw_boost)
    seller = (payload.get("biz_type") or "local") in ("seller", "hybrid")
    if channel == "naver_blog":
        unit = "월 검색유입→상세페이지" if seller else "월 검색유입"
    else:
        unit = "도달→상세페이지 유입" if seller else "예상 도달"
    return {
        "low": low, "high": high, "unit": unit,
        "label": f"{low:,}~{high:,}",
        "basis": f"품질 {score}점 · 키워드 {kw}개 · 업종 벤치마크",
        "note": "추정 범위(계정 연동·실측 쌓이면 정밀화)",
    }


def set_total(pieces_reach: list[dict]) -> dict:
    """세트(여러 채널) 합산 예상 도달."""
    low = sum(r.get("low", 0) for r in pieces_reach)
    high = sum(r.get("high", 0) for r in pieces_reach)
    return {"low": low, "high": high, "label": f"{low:,}~{high:,}"}
