"""
주간 성과 리포트(블로그등록 PHASE 4) — 블로그 연결 가게 대상.
발행 수(RSS 실측) · 순위 변화(rank_snapshots) · 놓친 키워드 진행상황을 종합해
앱내(weekly_reports 테이블) + 이메일(SMTP 설정 시) 발송. 카카오 알림톡은 스텁.

정직성: 발행 수는 올린다 사용량이 아니라 '실제 블로그 RSS'로 잰다.
순위는 실측 스냅샷만 비교하고, 데이터가 없으면 '집계중'으로 표기(임의 수치 금지).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from app import db, config

_log = logging.getLogger("shopcast.weekly_report")


def _week_key(dt: datetime | None = None) -> str:
    d = dt or datetime.utcnow()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _rank_change_7d(tenant_id: str, keyword: str, kind: str) -> dict | None:
    """최근 7일 순위 변화 — {keyword, kind, before, after}. 스냅샷 2개 미만이면 None."""
    hist = db.rank_history(tenant_id, keyword, kind=kind, limit=60)
    if not hist:
        return None
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    before_rows = [h for h in hist if (h.get("checked_at") or "") < cutoff]
    after = hist[-1]
    before = before_rows[-1] if before_rows else (hist[0] if len(hist) >= 2 else None)
    if not before or before is after:
        return None
    return {"keyword": keyword, "kind": kind,
            "before": before.get("rank"), "after": after.get("rank")}


def build_report(tenant, weekly_target: int | None = None) -> dict:
    """한 가게의 주간 리포트 데이터 — 발행·일관성·순위변화·놓친키워드·코칭."""
    from app.services import blogsync
    target = weekly_target or (getattr(tenant, "publish_schedule", 0) or 0) or config.BLOG_WEEKLY_TARGET
    out: dict = {"week": _week_key(), "tenant_id": tenant.id, "name": tenant.name,
                 "blog_id": getattr(tenant, "blog_id", "") or ""}
    # ① 발행 일관성(RSS 실측)
    feed = blogsync.fetch_feed(tenant.blog_id) if getattr(tenant, "blog_id", "") else {"ok": False, "posts": []}
    if feed.get("ok") and feed.get("exists"):
        out["consistency"] = blogsync.posting_consistency(feed["posts"], weekly_target=target)
    else:
        out["consistency"] = None                    # 조회 실패 — '집계중' 표기(가짜 수치 금지)
    # ② 순위 변화(7일) — 추적 키워드별, 소스(blog/place/blog_search) 구분
    changes = []
    for kw in db.tracked_keywords(tenant.id, limit=8):
        for kind in ("blog_search", "place", "blog"):
            ch = _rank_change_7d(tenant.id, kw, kind)
            if ch and ch["before"] is not None and ch["after"] is not None:
                changes.append(ch)
                break                                # 키워드당 가장 정확한 소스 1개만
    ups = [c for c in changes
           if (c["after"] or 99) < (c["before"] or 99) and (c["after"] or 0) > 0]
    downs = [c for c in changes if (c["after"] or 99) > (c["before"] or 99)]
    out["rank_changes"] = changes
    out["ups"], out["downs"] = len(ups), len(downs)
    # ③ 놓친 키워드 진행상황 — 진입(미노출→N위) 여부
    entered = [c for c in ups if not c["before"]]    # before=0(미노출) → after>0
    out["entered"] = entered
    # ④ 코칭 한 줄(사실 기반 — '무조건 상위' 금지)
    cons = out["consistency"]
    if cons and cons["this_week"] >= cons["weekly_target"]:
        coach = f"이번 주 {cons['this_week']}회 발행 — 목표(주 {cons['weekly_target']}회) 달성! 꾸준함이 C-Rank 신뢰도를 쌓아요."
    elif cons:
        coach = (f"이번 주 {cons['this_week']}/{cons['weekly_target']}회 발행. "
                 "같은 주제로 꾸준히 발행하면 C-Rank 신뢰도가 쌓여요 — 남은 요일에 채워봐요.")
    else:
        coach = "블로그 발행 현황 집계중 — 블로그 연결 상태를 확인해 주세요."
    if entered:
        coach += f" 🎉 '{entered[0]['keyword']}' 검색결과 진입!"
    out["coaching"] = coach
    return out


def _email_body(rep: dict) -> str:
    lines = [f"[올린다] {rep.get('name', '')} 주간 리포트 ({rep.get('week', '')})", ""]
    cons = rep.get("consistency")
    if cons:
        lines.append(f"📝 이번 주 발행 {cons['this_week']}/{cons['weekly_target']}회"
                     f" · 최근 4주 {cons['week_counts']} · 주평균 {cons['avg_per_week']}회")
    for c in rep.get("rank_changes", [])[:6]:
        b = c["before"] if c["before"] else "미노출"
        a = c["after"] if c["after"] else "미노출"
        arrow = "⬆️" if (c["after"] or 99) < (c["before"] or 99) and c["after"] else ("⬇️" if (c["after"] or 0) > (c["before"] or 0) and c["before"] else "—")
        src = {"blog_search": "블로그탭", "place": "플레이스", "blog": "지역검색"}.get(c["kind"], c["kind"])
        lines.append(f"🔎 {c['keyword']} ({src}): {b} → {a} {arrow}")
    lines += ["", rep.get("coaching", ""), "", "자세히 보기: https://ollinda.kr/me?tab=report"]
    return "\n".join(lines)


def _send_email(to: str, subject: str, body: str) -> bool:
    """SMTP 설정 시에만 발송(competitor.notify_alerts 패턴). 실패는 조용히."""
    if not (to and os.environ.get("SMTP_HOST")):
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = os.environ.get("SMTP_USER", "no-reply@ollinda.kr")
        msg["To"] = to
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
            s.send_message(msg)
        return True
    except Exception:
        _log.exception("[weekly_report] 이메일 발송 실패 to=%s", to)
        return False


def _send_kakao_stub(user: dict | None, rep: dict) -> None:
    # TODO(kakao): 알림톡 템플릿 승인 후 발송 연결. 현재는 스텁(로그만).
    _log.info("[weekly_report] 카톡 알림톡(스텁) tenant=%s week=%s", rep.get("tenant_id"), rep.get("week"))


def send_all() -> dict:
    """블로그 연결 가게 전체 주간 리포트 생성·저장·발송. 스케줄러(주 1회)가 호출."""
    sent = 0
    tenants = db.list_tenants_with_blog()
    for t in tenants:
        try:
            rep = build_report(t)
            owner = db.get_user_by_tenant(t.id)
            mailed = False
            email = (owner or {}).get("email") or ""
            if email and not email.endswith((".guest", ".local")):   # 게스트 가짜 이메일 제외
                mailed = _send_email(email, f"[올린다] 주간 리포트 — {t.name}", _email_body(rep))
            _send_kakao_stub(owner, rep)
            db.save_weekly_report(t.id, rep["week"], rep, sent_email=mailed)
            sent += 1
        except Exception:
            _log.exception("[weekly_report] 리포트 실패 tenant=%s", t.id)
    _log.info("[weekly_report] 주간 리포트 %d/%d 완료", sent, len(tenants))
    return {"sent": sent, "total": len(tenants)}
