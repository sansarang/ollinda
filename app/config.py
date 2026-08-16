"""
가격·플랜 중앙 설정 — 여기만 바꾸면 pay/billing/landing 전부 반영(성장 개선 규칙3).
연 결제는 월가×12×(1-YEARLY_DISCOUNT). Paddle/Toss priceId는 env로 매핑(하드코딩 금지).
"""
from __future__ import annotations

import os

# ── 월 요금(원) ─────────────────────────────────────────
PRICE_BASIC = int(os.environ.get("SHOPCAST_PRICE_BASIC", "129000"))   # 라이트(2026-07-29 원가 실측 기반 개편)
PRICE_PRO = int(os.environ.get("SHOPCAST_PRICE_PRO", "199000"))       # 스탠다드(주력) · 순위추적·성과실측
# ★ 2026-08-17 사장님 결정 — 대행 단일 상품으로 전환.
#   왜: 월 12.9만원은 시세의 1/3이면서 **고객이 도구를 배워야 하는** 구조였다.
#       소상공인 사장님은 도구를 배울 시간이 없다. 그래서 안 팔렸다(유료 1건).
#   무엇으로: 월 39만원 대행 — 사진만 보내면 우리가 돌리고 리포트를 드린다.
#       크몽 블로그 대행 실판매가 38~77만원 안이라 비싸지 않고, 고객은 배울 게 없다.
#       한 곳 = 옛 SaaS 고객 3명분. 5곳이면 월 195만원.
AGENCY_FROM = int(os.environ.get("SHOPCAST_PRICE_AGENCY", "390000"))  # 대행(주력 · 단일 상품)
# 정가(표시용, 2026-07-30 AI 무빙 전면 적용 개편) — 판매가(PRICE_*)는 '런칭가'로 표기.
# 나중에 인상할 땐 PRICE_*를 LIST_*로 올리기만 하면 됨(기존 구독자는 결제 시점 가격 유지).
LIST_BASIC = int(os.environ.get("SHOPCAST_LIST_BASIC", "149000"))
LIST_PRO = int(os.environ.get("SHOPCAST_LIST_PRO", "249000"))
LIST_AGENCY = int(os.environ.get("SHOPCAST_LIST_AGENCY", "590000"))   # 정가(시세 중간값 근처)
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
    # 2026-07-29 개편(원가 실측 ₩1.5~3.5천/세트 기반, 마진 75%+): 라이트/스탠다드/프로
    "basic":  {"name": "라이트", "price": PRICE_BASIC, "monthly": 6},     # 월 6세트 + 영상 2
    "pro":    {"name": "스탠다드", "price": PRICE_PRO, "monthly": 12},    # 월 12세트 + 영상 8(주력)
    "self":   {"name": "스탠다드", "price": PRICE_PRO, "monthly": 12},
    "agency": {"name": "프로", "price": AGENCY_FROM, "monthly": 20},      # 월 20세트 + 영상 무제한 + 우선
}

# 성과형(1페이지 진입 시 과금) — 스텁: 임계 순위 도달 이벤트 기록용
PERFORMANCE_RANK_THRESHOLD = int(os.environ.get("SHOPCAST_PERF_RANK", "10"))  # 1페이지(상위 10위) 진입

# 순위진단(/api/rank-check) 남용 방지 — 스캔당 네이버 API 최대 4콜이라 IP 레이트리밋 + TTL 캐시
RANK_RATE_PER_MIN = int(os.environ.get("SHOPCAST_RANK_RPM", "5"))     # 동일 IP 분당 허용(넉넉히: 자기 가게+경쟁사 몇 개)
RANK_RATE_PER_HOUR = int(os.environ.get("SHOPCAST_RANK_RPH", "20"))   # 동일 IP 시간당 허용
RANK_CACHE_TTL = int(os.environ.get("SHOPCAST_RANK_CACHE_TTL", "3600"))  # 동일 상호+지역 캐시 1시간(네이버 콜 절감)

# ── 신규 기능 플랜 게이팅(경쟁사 추적 / 인쇄물 생성) — 여기서만 조정(-1=무제한) ──
# clip_video: 네이버 클립 전용 파생본(15~22초) 제공 여부 — 1=제공, 0=미제공(2026-08-01 사장님 방침).
#   네이버 영상을 '요청한' 사용자에게만 만들어지고(온디맨드), 그중에서도 플랜이 허용해야 한다.
#   렌더 원가는 0에 가깝지만 통합검색 클립 지면 진입이라는 실효 가치가 커 상위 플랜 차별점으로 둔다.
PLAN_LIMITS = {
    "free":   {"competitor_scans": 5,   "print_items": 3,  "competitors_max": 1,  "angle_variants": 2,  "clip_video": 0},
    "basic":  {"competitor_scans": 30,  "print_items": 10, "competitors_max": 2,  "angle_variants": 8,  "clip_video": 0},
    "pro":    {"competitor_scans": 300, "print_items": 50, "competitors_max": 5,  "angle_variants": 60, "clip_video": 1},
    "self":   {"competitor_scans": 300, "print_items": 50, "competitors_max": 5,  "angle_variants": 60, "clip_video": 1},   # pro 별칭
    "agency": {"competitor_scans": -1,  "print_items": -1, "competitors_max": -1, "angle_variants": -1, "clip_video": -1},  # 무제한
}


def plan_limit(plan: str, feature: str) -> int:
    """플랜별 기능 한도. -1=무제한. 미지정 플랜은 free로 취급."""
    return PLAN_LIMITS.get(plan or "free", PLAN_LIMITS["free"]).get(feature, 0)


# ── 상위노출 실행 루프(상위노출 PHASE 1~6) ──
TARGET_CONTENT_SUGGEST = int(os.environ.get("SHOPCAST_TARGET_SUGGEST", "3"))  # 미노출→타겟 콘텐츠 제안 수
# 발행 캘린더(PHASE 2) — 플랜별 주간 권장 발행 수(가게 publish_schedule 설정이 우선)
PLAN_WEEKLY_TARGET = {"free": 1, "basic": 2, "pro": 3, "self": 3, "agency": 5}
REMIND_GAP_DAYS = int(os.environ.get("SHOPCAST_REMIND_GAP", "3"))    # 발행 공백 며칠부터 리마인더
RANK_TRACK_KEYWORDS = int(os.environ.get("SHOPCAST_TRACK_KW", "5"))  # 가게당 자동추적 키워드 수(PHASE 3)

# ── 블로그 추적(블로그등록 PHASE 4) — 발행 일관성·주간 리포트 ──
BLOG_WEEKLY_TARGET = int(os.environ.get("SHOPCAST_BLOG_WEEKLY", "3"))   # 기본 주 3회(C-Rank 지속성)
WEEKLY_REPORT_DOW = int(os.environ.get("SHOPCAST_REPORT_DOW", "0"))     # 발송 요일(0=월요일, KST)
WEEKLY_REPORT_HOUR = int(os.environ.get("SHOPCAST_REPORT_HOUR", "9"))   # 발송 시각(KST)


# ── 실계정 canonical(2026-08-03 사건: tenant 오배송) ──────────────
#   사건: 오늘 생성물이 사장님 실계정이 아닌 동명 tenant로 들어갔다.
#   원인: 검증용 tenant가 실계정과 '같은 이름'으로 공존했고, 대상을 이름으로 잡았다.
#   ★ 이름은 식별자가 아니다. ID만이 식별자다.
#   등록은 환경변수로 덮을 수 있다(SHOPCAST_PROD_TENANTS="id,id").
PRODUCTION_TENANTS = tuple(
    x.strip() for x in os.environ.get(
        "SHOPCAST_PROD_TENANTS",
        "d9e0fbde-9a71-48d6-92e4-e97dc75dd41e,"      # 루마썬팅 현대상사
        "95d0243f-7c9c-493a-aaf7-186fee2898b0"       # 주안모터스
    ).split(",") if x.strip()
)


def is_production_tenant(tenant_id: str) -> bool:
    """이 가게가 사장님 실계정인가 — 이름이 아니라 ID로만 판정한다."""
    return (tenant_id or "").strip() in PRODUCTION_TENANTS


def assert_target(tenant_id: str, where: str = "") -> dict:
    """작업 시작 시 대상을 확인·명시한다(2026-08-03 절대 원칙).

    실계정이 아니면 경고 로그를 남긴다 — 조용히 진행하면 오늘 같은 오배송을 또 못 본다.
    반환 {tenant_id, production, label} — 보고서에 그대로 붙일 수 있는 형태.
    """
    import logging
    prod = is_production_tenant(tenant_id)
    if not prod:
        logging.getLogger("shopcast.target").warning(
            "[target] 실계정 아님 — %s tenant=%s (테스트 대상)", where or "?", tenant_id)
    return {"tenant_id": tenant_id, "production": prod,
            "label": "실계정" if prod else "테스트 tenant"}
