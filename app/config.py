"""
가격·플랜 중앙 설정 — 여기만 바꾸면 pay/billing/landing 전부 반영(성장 개선 규칙3).
연 결제는 월가×12×(1-YEARLY_DISCOUNT). Paddle/Toss priceId는 env로 매핑(하드코딩 금지).
"""
from __future__ import annotations

import os

# ── 월 요금(원) ─────────────────────────────────────────
PRICE_BASIC = int(os.environ.get("SHOPCAST_PRICE_BASIC", "29000"))    # 미끼 진입 티어(기존 39,000 → 인하)
PRICE_PRO = int(os.environ.get("SHOPCAST_PRICE_PRO", "79000"))        # 메인 · 순위추적·성과실측
AGENCY_FROM = int(os.environ.get("SHOPCAST_PRICE_AGENCY", "150000"))  # 대행 시작가(월 15만~25만)
AGENCY_TO = int(os.environ.get("SHOPCAST_PRICE_AGENCY_TO", "250000"))

YEARLY_DISCOUNT = 0.30    # 연 결제 할인율(약 30%)

# 무료체험(성과증명형): 첫 콘텐츠 발행 → N일 뒤 순위 리포트
FREE_GENERATIONS = int(os.environ.get("SHOPCAST_FREE_LIMIT", "2"))
REPORT_AFTER_DAYS = 7     # 발행 후 순위 리포트 발송 시점


def yearly_price(monthly: int) -> int:
    """월가 → 연 결제 총액(할인 적용, 100원 반올림)."""
    raw = monthly * 12 * (1 - YEARLY_DISCOUNT)
    return int(round(raw / 100) * 100)


def yearly_monthly_equiv(monthly: int) -> int:
    """연 결제 시 월 환산가(마케팅 표기용)."""
    return int(round(yearly_price(monthly) / 12 / 100) * 100)


# 플랜 정의 — pay.PLANS·billing·landing 공용 소스
PLANS = {
    "basic":  {"name": "베이직", "price": PRICE_BASIC, "monthly": 8},   # 월 8건
    "pro":    {"name": "프로", "price": PRICE_PRO, "monthly": 0},       # 무제한 + 성과기능
    "self":   {"name": "프로", "price": PRICE_PRO, "monthly": 0},
    "agency": {"name": "대행", "price": AGENCY_FROM, "monthly": 0},     # 사진만 보내면 발행까지 대행
}

# 성과형(1페이지 진입 시 과금) — 스텁: 임계 순위 도달 이벤트 기록용
PERFORMANCE_RANK_THRESHOLD = int(os.environ.get("SHOPCAST_PERF_RANK", "10"))  # 1페이지(상위 10위) 진입
