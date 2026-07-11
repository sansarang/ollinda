"""
성과증명 루프(성장 PHASE 2) — 발행 시점 순위 자동 스냅샷 + 7일 뒤 리포트 예약/발송.
사용자 버튼 의존 제거: publish 훅에서 자동 기록. 발송은 스텁(payload·스케줄 자리 확보).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import db, config


def _target_keyword(tenant, piece) -> str:
    kws = (piece.payload.get("target_keywords") or piece.payload.get("seo_keywords") or [])
    if kws:
        return kws[0]
    reg = (getattr(tenant, "region", "") or "").strip()
    ind = (getattr(tenant, "industry", "") or "").strip()
    return (f"{reg} {ind}").strip() or ind


def on_publish(tenant, piece) -> None:
    """발행 직후: 현재 순위 자동 스냅샷 + 7일 리포트 예약(사용자 버튼 불필요)."""
    try:
        from app.services import place
        kw = _target_keyword(tenant, piece)
        if not kw:
            return
        cur = place.rank(kw, getattr(tenant, "name", ""))   # 현재 순위(무키/실패 None)
        db.save_rank_snapshot(tenant.id, kw, cur)
        if cur and 0 < cur <= config.PERFORMANCE_RANK_THRESHOLD:   # 1페이지 진입 → 성과형 과금 이벤트(스텁)
            db.record_perf_event(tenant.id, kw, cur)
        due = (datetime.utcnow() + timedelta(days=config.REPORT_AFTER_DAYS)).isoformat()
        db.schedule_report(tenant.id, kw, cur, due)
    except Exception:
        import logging
        logging.exception("[growth] on_publish 실패 tenant=%s", getattr(tenant, "id", "?"))


def _report_body(tenant, r: dict) -> dict:
    """리포트 payload — 발송 채널(카톡/이메일)이 렌더할 자료(스텁)."""
    from app.services import place
    kw = r.get("keyword", "")
    before = r.get("baseline_rank")
    after = place.rank(kw, getattr(tenant, "name", ""))
    improved = (before is not None and after is not None and after < before and after > 0)
    return {
        "tenant_id": tenant.id, "keyword": kw, "before": before, "after": after,
        "improved": improved,
        "headline": (f"'{kw}' 순위 {before}위 → {after}위 ⬆️" if improved
                     else f"'{kw}' 순위 리포트 (현재 {after if after else '집계중'})"),
    }


def send_due_reports(limit: int = 50) -> dict:
    """발송 시점 도달 리포트 처리 — 실제 발송은 스텁(로그만). 크론/운영자가 호출."""
    now = datetime.utcnow().isoformat()
    sent = 0
    for r in db.due_reports(now)[:limit]:
        t = db.get_tenant(r.get("tenant_id"))
        if not t:
            db.mark_report_sent(r["id"])
            continue
        body = _report_body(t, r)
        # TODO(발송): 카톡 알림톡/이메일 — 현재는 스텁(payload 확보). SMTP/kakao 훅 연결 지점.
        import logging
        logging.info("[growth] 7일 리포트(스텁) %s", body)
        db.mark_report_sent(r["id"])
        sent += 1
    return {"sent": sent}
