"""
플레이스 '소식' 자동 작성 — 짧은 공지/이벤트/팁 (2~4문장).
네이버 소식은 공식 등록 API가 없어 '반자동'(사장님이 스마트플레이스에 붙여넣기).
소식을 주 2~3회 올리면 정보 신선도 = 플레이스 상위노출 요인 ↑.
"""
from __future__ import annotations

import os


def _fallback(tenant, n: int) -> list[str]:
    name = tenant.name
    base = [
        f"[{name}] 이번 주도 정성껏 준비했어요 🙏 방문 전 네이버에서 '{name}' 검색 후 영업시간 확인해 주세요!",
        f"[{name}] 찾아주시는 모든 분께 감사드려요. 네이버 플레이스 '찜' 해두시면 새 소식을 가장 먼저 받아보실 수 있어요 ⭐",
        f"[{name}] 방문 후 리뷰 한 줄이 큰 힘이 됩니다. 소중한 후기 남겨주시면 감사하겠습니다 😊",
    ]
    return base[:n]


def generate(tenant, n: int = 3) -> list[str]:
    """소식 n개 생성. 키 없으면 안전 폴백."""
    from app.industries import resolve_industry
    prof = resolve_industry(tenant.industry)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback(tenant, n)
    try:
        from app.generators.text_claude import _call_llm
        prompt = (
            f"[가게] {tenant.name} (업종: {prof.name}, 지역: {tenant.region or '-'})\n"
            f"네이버 스마트플레이스 '소식'에 올릴 짧은 글 {n}개를 작성하라.\n"
            "- 각 소식 2~4문장, 이모지 1~2개, 과장·허위 금지\n"
            "- 방문/예약/찜/리뷰를 자연스럽게 유도\n"
            "- 주제는 서로 다르게(예: 이번주 안내 / 신메뉴·신상품 / 팁·후기유도)\n"
            "- 번호·머리표 없이, 소식마다 === 로만 구분해서 출력"
        )
        raw = _call_llm(prompt, max_tokens=900)
        items = [s.strip() for s in raw.split("===") if s.strip() and len(s.strip()) > 10]
        return items[:n] if items else _fallback(tenant, n)
    except Exception:
        return _fallback(tenant, n)
