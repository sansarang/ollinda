"""
플랜별 기능 게이팅 — 신규기능(경쟁사 추적 / 인쇄물) 공용.
한도 수치는 app/config.PLAN_LIMITS. 초과 시 '업그레이드' CTA(하드블록). -1=무제한.
"""
from __future__ import annotations

from app import config, db

_LABEL = {"competitor_scans": "경쟁사 스캔", "print_items": "인쇄물",
          "angle_variants": "앵글 변형 생성"}


def check_limit(user: dict | None, feature: str) -> dict | None:
    """한도 초과/미로그인 시 CTA dict 반환, 통과면 None.
    로그인 전 = 가입 유도(무료 free 한도로 시작). 로그인 후 = 플랜 한도."""
    label = _LABEL.get(feature, feature)
    if not user:
        return {"error": f"가입하면 {label}을 무료로 체험할 수 있어요!", "need_signup": True,
                "cta": "무료로 시작하기"}
    plan = (user.get("plan") or "free")
    limit = config.plan_limit(plan, feature)
    if limit == -1:
        return None
    used = db.feature_usage(user["id"], feature)
    if used >= limit:
        return {"error": f"이번 달 {label} {limit}회를 다 쓰셨어요. 업그레이드하면 더 쓸 수 있어요!",
                "upgrade": True, "cta": "요금제 업그레이드", "limit": limit, "used": used, "plan": plan}
    return None


def consume(user: dict | None, feature: str, n: int = 1) -> None:
    """성공 시 사용량 차감(무제한 플랜은 스킵)."""
    if not user:
        return
    if config.plan_limit(user.get("plan") or "free", feature) == -1:
        return
    db.incr_feature_usage(user["id"], feature, n)


def usage_summary(user: dict | None, feature: str) -> dict:
    """UI 표시용 — {used, limit(-1=무제한), remaining}."""
    if not user:
        limit = config.plan_limit("free", feature)
        return {"used": 0, "limit": limit, "remaining": limit}
    plan = user.get("plan") or "free"
    limit = config.plan_limit(plan, feature)
    used = db.feature_usage(user["id"], feature)
    remaining = -1 if limit == -1 else max(0, limit - used)
    return {"used": used, "limit": limit, "remaining": remaining, "plan": plan}
