"""
발행 캘린더 + 일관성 시스템(상위노출 PHASE 2) — C-Rank의 핵심 신호는
'같은 주제(topic_axis)로 꾸준한 발행'. 주 N회 권장 스케줄을 제안하고 진행률을 추적한다.

정직성: "무조건 상위" 금지 — "꾸준히 발행하면 C-Rank 신뢰도가 쌓인다"는 사실 기반 안내만.
"""
from __future__ import annotations

from urllib.parse import quote

from app import config, db

# 앵글 로테이션(스마트블록 다중진입과 연결) — 같은 주제라도 검색의도별로 다른 블록을 노린다
# 사업형태별 분기(C2 버그수정): 매장형=지역·방문 의도 / 셀러형=상품·구매 의도
_ANGLES = [("review", "후기형", "실제 경험담 — '후기' 스마트블록"),
           ("howto", "방법·과정형", "단계별 과정 — '방법' 블록·지식스니펫"),
           ("price", "가격·비용형", "가격·구성 정리 — '가격/비용' 블록")]
_ANGLES_SELLER = [("review", "내돈내산 후기형", "실사용 후기 — '후기' 스마트블록·구매 전환"),
                  ("howto", "사용법·비교형", "사용법·타제품 비교 — '방법/비교' 블록"),
                  ("price", "가성비·구성형", "가격·구성·혜택 정리 — '가격' 블록")]


def weekly_target(tenant, plan: str = "free") -> int:
    """주간 발행 목표 — 가게 설정(publish_schedule) 우선, 없으면 플랜별 권장."""
    n = getattr(tenant, "publish_schedule", 0) or 0
    if n > 0:
        return n
    return config.PLAN_WEEKLY_TARGET.get(plan or "free", config.BLOG_WEEKLY_TARGET)


def _topics(tenant, limit: int = 3) -> list[str]:
    """이번 주 제안 주제 — 전문 주제 축(topic_axis) 우선. 없으면 사업형태 자동분기(C2):
    매장형 = 지역+업종(방문 검색), 셀러형 = 상품·브랜드 키워드(구매 검색)."""
    axis = (getattr(tenant, "topic_axis", "") or "").strip()
    if axis:
        toks = [t.strip() for t in axis.replace("\n", ",").split(",") if t.strip()]
        if toks:
            return (toks * ((limit // len(toks)) + 1))[:limit]
    if (getattr(tenant, "biz_type", "local") or "local") == "seller":
        # 셀러: 검색어 유도(search_kw) > 브랜드+상품 > 상품명 — 지역 키워드 금지(구매 의도와 무관)
        prod = ((getattr(tenant, "search_kw", "") or "").strip()
                or f"{(getattr(tenant, 'brand_name', '') or '').strip()} {getattr(tenant, 'industry', '')}".strip()
                or (getattr(tenant, "industry", "") or "").strip())
        return [prod or "내 상품 핵심 키워드"] * limit
    base = (f"{getattr(tenant, 'region', '')} {getattr(tenant, 'industry', '')}").strip()
    return [base or "내 가게 핵심 주제"] * limit


def week_plan(tenant, plan: str = "free") -> dict:
    """이번 주 발행 계획 — {target, done, remaining, basis, gap_days, streak_weeks,
    suggestions:[{topic, angle, angle_label, why, href}], coach}."""
    target = weekly_target(tenant, plan)
    act = db.publish_activity(tenant.id)
    done = act["this_week"]
    remaining = max(0, target - done)
    topics = _topics(tenant, max(remaining, 1))
    angles = (_ANGLES_SELLER if (getattr(tenant, "biz_type", "local") or "local") == "seller"
              else _ANGLES)
    sugg = []
    for i in range(remaining):
        angle, label, why = angles[i % len(angles)]
        topic = topics[i % len(topics)]
        sugg.append({"topic": topic, "angle": angle, "angle_label": label, "why": why,
                     "href": f"/me?target_kw={quote(topic)}&angle={angle}"})
    if done >= target:
        coach = f"이번 주 {done}/{target}회 완료 🎉 이 페이스가 C-Rank '활동 지속성' 신호를 쌓아요."
    elif act["gap_days"] is not None and act["gap_days"] >= config.REMIND_GAP_DAYS:
        coach = (f"{act['gap_days']}일째 발행이 없어요. 발행 간격이 벌어지면 꾸준함 신호가 식어요 — "
                 f"오늘 1편으로 다시 페이스를 잡아요. (이번 주 {done}/{target})")
    else:
        coach = f"이번 주 {done}/{target}회. 같은 주제로 꾸준히 발행하면 C-Rank 신뢰도가 쌓여요."
    return {"target": target, "done": done, "remaining": remaining,
            "basis": act["basis"], "gap_days": act["gap_days"],
            "week_counts": act["week_counts"], "streak_weeks": act["streak_weeks"],
            "suggestions": sugg, "coach": coach}


def remind_stale_tenants() -> dict:
    """발행 공백 리마인더(APScheduler 일일 잡) — gap ≥ REMIND_GAP_DAYS 인 가게에
    앱내 알림 + 이메일(SMTP 시) + 카톡(스텁). 콘텐츠를 만든 적 있는 가게만(신규 스팸 방지)."""
    import logging
    import os
    n = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if not t or not (t.industry or "").strip():
            continue
        act = db.publish_activity(tid)
        if not act["last_at"] or act["gap_days"] is None:
            continue                        # 활동 이력 없음 → 리마인더 대상 아님
        if act["gap_days"] < config.REMIND_GAP_DAYS:
            continue
        plan = week_plan(t, u.get("plan") or "free")
        text = (f"{act['gap_days']}일째 새 발행이 없어요. 이번 주 {plan['done']}/{plan['target']}회 — "
                "꾸준한 발행이 C-Rank 지속성 신호예요. 오늘 1편 어때요?")
        db.add_notice(tid, "publish_reminder", text)
        email = (u.get("email") or "")
        if email and not email.endswith((".guest", ".local")) and os.environ.get("SMTP_HOST"):
            try:
                from app.services.weekly_report import _send_email
                _send_email(email, "[올린다] 발행 리마인더", text + "\n\nhttps://ollinda.kr/me")
            except Exception:
                logging.exception("[pubcal] 리마인더 이메일 실패 uid=%s", u.get("id"))
        # TODO(kakao): 알림톡 템플릿 승인 후 발송. 현재는 스텁(로그만).
        logging.info("[pubcal] 발행 리마인더(카톡 스텁) tenant=%s gap=%s일", tid, act["gap_days"])
        n += 1
    logging.info("[pubcal] 발행 리마인더 %d건", n)
    return {"reminded": n}
