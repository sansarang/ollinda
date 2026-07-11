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

# 순위진단(/api/rank-check) 남용 방지 — 스캔당 네이버 API 최대 4콜이라 IP 레이트리밋 + TTL 캐시
RANK_RATE_PER_MIN = int(os.environ.get("SHOPCAST_RANK_RPM", "5"))     # 동일 IP 분당 허용(넉넉히: 자기 가게+경쟁사 몇 개)
RANK_RATE_PER_HOUR = int(os.environ.get("SHOPCAST_RANK_RPH", "20"))   # 동일 IP 시간당 허용
RANK_CACHE_TTL = int(os.environ.get("SHOPCAST_RANK_CACHE_TTL", "3600"))  # 동일 상호+지역 캐시 1시간(네이버 콜 절감)

# ── 신규 기능 플랜 게이팅(경쟁사 추적 / 인쇄물 생성) — 여기서만 조정(-1=무제한) ──
PLAN_LIMITS = {
    "free":   {"competitor_scans": 5,   "print_items": 3,  "competitors_max": 1},
    "basic":  {"competitor_scans": 30,  "print_items": 10, "competitors_max": 2},
    "pro":    {"competitor_scans": 300, "print_items": 50, "competitors_max": 5},
    "self":   {"competitor_scans": 300, "print_items": 50, "competitors_max": 5},   # pro 별칭
    "agency": {"competitor_scans": -1,  "print_items": -1, "competitors_max": -1},   # 무제한
}


def plan_limit(plan: str, feature: str) -> int:
    """플랜별 기능 한도. -1=무제한. 미지정 플랜은 free로 취급."""
    return PLAN_LIMITS.get(plan or "free", PLAN_LIMITS["free"]).get(feature, 0)


# ── 상위노출 실행 루프(상위노출 PHASE 1~6) ──
TARGET_CONTENT_SUGGEST = int(os.environ.get("SHOPCAST_TARGET_SUGGEST", "3"))  # 미노출→타겟 콘텐츠 제안 수

# ── 블로그 추적(블로그등록 PHASE 4) — 발행 일관성·주간 리포트 ──
BLOG_WEEKLY_TARGET = int(os.environ.get("SHOPCAST_BLOG_WEEKLY", "3"))   # 기본 주 3회(C-Rank 지속성)
WEEKLY_REPORT_DOW = int(os.environ.get("SHOPCAST_REPORT_DOW", "0"))     # 발송 요일(0=월요일, KST)
WEEKLY_REPORT_HOUR = int(os.environ.get("SHOPCAST_REPORT_HOUR", "9"))   # 발송 시각(KST)
