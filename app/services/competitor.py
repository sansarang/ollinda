"""
경쟁사 추적기(신규기능①) — place.py 순위조회 재사용.
등록 키워드별로 '내 순위 vs 경쟁사 순위'를 조회·저장하고, 지난 스냅샷 대비 변화를 판정.
정직성: place.py의 상위 5위 한계 그대로(가짜 순위 금지) — 5위 밖은 '미노출'로만 표기.
"""
from __future__ import annotations

from app import db
from app.services import place

SCAN_KEYWORDS_MAX = 3   # 스캔당 키워드 상한(경쟁사×본인 = 네이버 콜 2배라 제한)


def _rank_label(r) -> str:
    if r is None:
        return "조회불가"
    return f"{r}위" if r >= 1 else "5위권 밖"


def _better(a, b) -> bool:
    """순위 a가 b보다 상위인가(작을수록 상위, 0/None=미노출은 최하위 취급)."""
    av = a if (a and a >= 1) else 99
    bv = b if (b and b >= 1) else 99
    return av < bv


def _verdict(my_rank, comp_rank) -> str:
    """정직한 비교 문구 — 가짜 순위 없이 실측/미노출만."""
    if my_rank is None and comp_rank is None:
        return "아직 순위 조회 전이에요"
    if _better(my_rank, comp_rank):
        return "🟢 우리가 앞서고 있어요"
    if _better(comp_rank, my_rank):
        return "🔴 경쟁사가 앞서고 있어요"
    return "🟡 비슷해요 (둘 다 5위권 밖이거나 동일)"


def _change(prev: list, my_rank, comp_rank) -> str:
    """직전 스냅샷 대비 변화(역전/따라잡힘/벌어짐). 이력 없으면 빈 문자열."""
    if not prev:
        return ""
    p = prev[0]
    pm, pc = p.get("my_rank"), p.get("competitor_rank")
    was_ahead = _better(pm, pc)
    now_ahead = _better(my_rank, comp_rank)
    if was_ahead and not now_ahead:
        return "⚠️ 역전당했어요 (경쟁사가 앞섬)"
    if not was_ahead and now_ahead:
        return "🎉 역전했어요 (우리가 앞섬)"
    # 격차 변화
    def _gap(m, c):
        mv = m if (m and m >= 1) else 6
        cv = c if (c and c >= 1) else 6
        return cv - mv   # +면 내가 앞섬
    dg = _gap(my_rank, comp_rank) - _gap(pm, pc)
    if dg > 0:
        return "↗ 격차 벌리는 중"
    if dg < 0:
        return "↘ 따라잡히는 중"
    return "→ 변화 없음"


def scan_competitor(tenant, competitor: dict) -> dict:
    """내 순위 vs 경쟁사 순위 조회 → 스냅샷 저장 + 변화 판정. 네이버 미키/실패 시 조회불가."""
    my_name = getattr(tenant, "name", "") or ""
    comp_name = competitor.get("name", "") or ""
    kws = [k for k in (competitor.get("keywords") or []) if k]
    if not kws:
        reg = (competitor.get("region") or getattr(tenant, "region", "") or "").strip()
        ind = (getattr(tenant, "industry", "") or "").strip()
        base = f"{reg} {ind}".strip()
        kws = [base] if base else []

    results = []
    for kw in kws[:SCAN_KEYWORDS_MAX]:
        my_r = place.rank(kw, my_name)          # 1~5 / 0(5위밖) / None(조회불가)
        comp_r = place.rank(kw, comp_name)
        prev = db.competitor_snapshots(competitor["id"], kw, limit=1)
        db.save_competitor_snapshot(competitor["id"], kw, my_r, comp_r)
        results.append({
            "keyword": kw,
            "my_rank": my_r, "my_label": _rank_label(my_r),
            "competitor_rank": comp_r, "competitor_label": _rank_label(comp_r),
            "verdict": _verdict(my_r, comp_r),
            "change": _change(prev, my_r, comp_r),
        })
    return {"competitor_id": competitor.get("id"), "competitor": comp_name, "results": results}


def report(tenant, competitors: list) -> dict:
    """대시보드용 — 등록 경쟁사별 최신 스냅샷 요약 + 경보(역전/뒤처짐)."""
    cards, alerts = [], []
    for comp in competitors:
        snaps = db.competitor_snapshots(comp["id"], limit=SCAN_KEYWORDS_MAX * 3)
        latest = {}
        for s in snaps:                          # 키워드별 최신만
            latest.setdefault(s["keyword"], s)
        rows = []
        for kw, s in latest.items():
            v = _verdict(s.get("my_rank"), s.get("competitor_rank"))
            rows.append({"keyword": kw, "my_label": _rank_label(s.get("my_rank")),
                         "competitor_label": _rank_label(s.get("competitor_rank")), "verdict": v})
            if "경쟁사가 앞서" in v:
                alerts.append(f"'{comp['name']}'가 '{kw}'에서 우리보다 위예요 — 콘텐츠로 따라잡을 때!")
        cards.append({"id": comp["id"], "name": comp["name"], "rows": rows,
                      "scanned": bool(latest)})
    return {"cards": cards, "alerts": alerts}


def notify_alerts(user_email: str, alerts: list) -> None:
    """경보 발송 — 앱 내 표시(report)가 1차. 여기선 이메일(SMTP 있으면) + 카톡(스텁).
    실패해도 조용히(스캔 자체를 막지 않음)."""
    if not alerts:
        return
    import os
    import logging
    # ① 이메일 — SMTP 설정 시에만
    if user_email and os.environ.get("SMTP_HOST"):
        try:
            import smtplib
            from email.mime.text import MIMEText
            body = "경쟁사 추적 알림\n\n" + "\n".join(f"- {a}" for a in alerts)
            msg = MIMEText(body, _charset="utf-8")
            msg["Subject"] = "[올린다] 경쟁사 순위 변화 알림"
            msg["From"] = os.environ.get("SMTP_USER", "no-reply@ollinda.kr")
            msg["To"] = user_email
            host = os.environ["SMTP_HOST"]
            port = int(os.environ.get("SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                s.send_message(msg)
        except Exception:
            logging.exception("[competitor] 경보 이메일 발송 실패")
    # ② 카카오 알림톡 — Phase 후속(발송 함수 자리만)
    # TODO(kakao): 알림톡 템플릿 승인 후 여기서 발송. 현재는 스텁.
