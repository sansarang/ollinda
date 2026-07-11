"""
온보딩/랜딩 '내 가게 현재 순위 즉시진단' — 결제 트리거(성장 PHASE 1).
업종+지역+상호만으로 네이버 지역검색 현재 순위를 보여주고, 낮거나 미노출이면 CTA로 연결.
키 없음/실패 시 규칙 기반 폴백('추정' 표기). 결과를 tenant baseline으로 저장 가능.
"""
from __future__ import annotations

from app.services import place


def diagnose_rank(industry: str, region: str, name: str) -> dict:
    """반환: {keyword, rank(int|None), estimated(bool), headline, subline, cta}."""
    industry, region, name = (industry or "").strip(), (region or "").strip(), (name or "").strip()
    keyword = (f"{region} {industry}").strip() or industry or "내 지역 업종"
    detail = place.rank_detail(keyword, name) if name else {"rank": None}
    rank = detail.get("rank")
    rival = detail.get("rival") or ""

    if rank is None:
        # 조회 불가(무키/네트워크) → 규칙 기반 추정(정직하게 '추정' 표기)
        return {
            "keyword": keyword, "rank": None, "estimated": True,
            "headline": f"'{keyword}' 검색에서 우리 가게, 아직 상위에 없을 가능성이 커요",
            "subline": "대부분의 소상공인 블로그·플레이스는 상위 노출 구조가 아니에요. (실측은 연결 후 정확히)",
            "cta": "올린다로 상위노출 구조 만들기",
        }
    if rank == 0:
        return {
            "keyword": keyword, "rank": 0, "estimated": False,
            "headline": f"'{keyword}' 검색 상위 5위 안에 우리 가게가 안 보여요",
            "subline": "지금은 손님이 검색해도 우리 가게를 찾기 어려운 상태예요.",
            "cta": "지금 상위 진입 시작하기",
        }
    # 노출은 되나 상위가 아님 → 추월 프레임
    over = f" 바로 위 '{rival}'만 넘으면 돼요." if rival else ""
    return {
        "keyword": keyword, "rank": rank, "estimated": False,
        "headline": f"'{keyword}' 검색 현재 {rank}위" + (" — 상위권!" if rank <= 2 else ""),
        "subline": (f"올린다로 콘텐츠를 쌓으면 더 위로 올라갈 수 있어요.{over}"
                    if rank > 1 else "이미 최상위! 유지·강화가 중요해요."),
        "cta": ("1위 도전하기" if rank > 1 else "상위 유지·강화하기"),
    }


def save_baseline(tenant_id: str, result: dict) -> None:
    """진단 결과를 rank_snapshots에 baseline으로 저장 — before/after 기준점(PHASE 1·2)."""
    try:
        from app import db
        if result.get("rank") is not None and not result.get("estimated"):
            db.save_rank_snapshot(tenant_id, result.get("keyword", ""), result.get("rank"))
    except Exception:
        pass
