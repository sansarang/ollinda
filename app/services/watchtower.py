"""
🗼 서버 자가진단 + 텔레그램 알림(2026-07-29 사장님 승인) — 대시보드가 꺼져 있어도 서버가 스스로 감시.

감시 항목: ① 앤트로픽 크레딧 소진/부족 ② 생성 실패 ③ 복구 대기 잡(재시작 흔적)
④ 발행 봉인 급증(품질 이상) ⑤ 디스크 위험. 이상 시 텔레그램으로 즉시 통보(중복 억제 6시간).

설정(Render 환경변수): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — 없으면 로그만 남기고 조용히 스킵.
호출: scheduler._fresh_index_check(30분 크론). 알림 이력은 watchtower_alerts 테이블.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from app import db

_log = logging.getLogger("shopcast.watchtower")
DEDUP_HOURS = 6          # 같은 종류 경보 재발송 억제
BLOCK_RATE_ALERT = 0.5   # 최근 세트 중 봉인 비율이 이 이상이면 품질 이상


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def _mail_fallback(text: str) -> bool:
    """텔레그램이 없거나 실패하면 **메일로 보낸다**.

    ★ 2026-08-17 — 이날 Anthropic 크레딧이 두 번 소진됐는데 두 번 다 사장님께 안 갔다.
      텔레그램 토큰이 설정된 적이 없어서 경보가 서버 로그에만 찍혔다("(알림 미설정)").
      대행으로 가면 이건 치명적이다 — 고객 글이 안 만들어지는데 아무도 모른다.
      SMTP는 이미 설정돼 있었다. 있는 경로를 안 쓰고 없는 경로만 보고 있었던 것이다.
    """
    try:
        from app.services import mailer
        to = (os.environ.get("ALERT_EMAIL") or os.environ.get("SMTP_USER") or "").strip()
        if not to:
            return False
        return bool(mailer.send(to, "[올린다] 시스템 경보", text))
    except Exception:
        _log.exception("[watchtower] 메일 폴백 실패")
        return False


def send(text: str) -> bool:
    """경보 발송 — 텔레그램 → 메일 순. **어느 하나라도 나가야 한다**(침묵 금지)."""
    if configured():
        try:
            import requests
            r = requests.post(
                f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
                      "disable_web_page_preview": True}, timeout=10)
            if r.status_code == 200:
                return True
            _log.warning("[watchtower] 텔레그램 %s — 메일로 넘긴다", r.status_code)
        except Exception:
            _log.exception("[watchtower] 텔레그램 발송 실패 — 메일로 넘긴다")
    if _mail_fallback(text):
        return True
    _log.error("[watchtower] 경보를 어디로도 못 보냈다: %s", text[:200])
    return False


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS watchtower_alerts("
              "kind TEXT PRIMARY KEY, last_at TEXT, detail TEXT)")


def _recent(kind: str) -> bool:
    """최근 DEDUP_HOURS 안에 같은 경보를 보냈으면 True(중복 억제)."""
    try:
        with db._conn() as c:
            _ensure(c)
            r = c.execute("SELECT last_at FROM watchtower_alerts WHERE kind=?", (kind,)).fetchone()
        if not r:
            return False
        return datetime.utcnow() - datetime.fromisoformat((r["last_at"] or "")[:19]) < timedelta(hours=DEDUP_HOURS)
    except Exception:
        return False


def _mark(kind: str, detail: str) -> None:
    try:
        with db._conn() as c:
            _ensure(c)
            c.execute("INSERT OR REPLACE INTO watchtower_alerts(kind, last_at, detail) VALUES(?,?,?)",
                      (kind, datetime.utcnow().isoformat(), detail[:300]))
    except Exception:
        pass


def _alert(kind: str, text: str) -> None:
    if _recent(kind):
        return
    if send(text):
        _mark(kind, text)
    else:
        _mark(kind, text)          # 미설정이어도 기록(로그 도배 방지)


def check() -> dict:
    """자가진단 1회 — 발견한 문제 목록 반환(크론이 호출)."""
    found: list[str] = []

    # ① 크레딧 — 초저가 핑으로 사용 가능 여부 확인(소진 확정만 잡음)
    try:
        from app import llm
        if os.environ.get("ANTHROPIC_API_KEY") and not llm.ping():
            found.append("credit")
            _alert("credit", "🔴 올린다: 앤트로픽 크레딧 소진 — 글 생성이 실패합니다.\n"
                             "지금 충전: https://console.anthropic.com/settings/billing")
    except Exception:
        pass

    # ② 생성 실패(가게별 최신 진행률) ③ 봉인 비율
    fails, blocked, total = [], 0, 0
    try:
        for t in (db.list_tenants() or [])[:40]:
            p = db.get_gen_progress(t.id) or {}
            if p.get("status") == "failed":
                fails.append(f"{t.name}: {(p.get('error') or '')[:80]}")
            for s in db.list_sets(tenant_id=t.id, limit=4):
                for pc in db.get_set_pieces(s["asset_id"]):
                    if pc.kind.value != "blog":
                        continue
                    total += 1
                    if (pc.payload or {}).get("publish_blocked_score"):
                        blocked += 1
    except Exception:
        pass
    if fails:
        found.append("gen_failed")
        _alert("gen_failed", "🔴 올린다: 콘텐츠 생성 실패 발생\n" + "\n".join(fails[:3]))
    if total >= 6 and blocked / total >= BLOCK_RATE_ALERT:
        found.append("quality")
        _alert("quality", f"🟡 올린다: 최근 글 {total}건 중 {blocked}건이 품질 기준 미달로 발행 보류 "
                          f"({100*blocked/total:.0f}%) — 제품 점검이 필요합니다.")

    # ④ 복구 대기 잡(재시작으로 죽었던 생성)
    try:
        pend = db.pending_gen_jobs()
        if pend:
            found.append("jobs")
            _alert("jobs", f"🟡 올린다: 재시작으로 중단됐던 생성 {len(pend)}건을 복구 중입니다.")
    except Exception:
        pass

    # ⑤ 디스크
    try:
        import shutil
        st = shutil.disk_usage(os.environ.get("SHOPCAST_STORAGE", "storage"))
        free_pct = 100 * st.free / st.total
        if free_pct < 10:
            found.append("disk")
            _alert("disk", f"🔴 올린다: 디스크 여유 {free_pct:.1f}% — 정리가 필요합니다(/admin/disk-sos).")
    except Exception:
        pass

    if found:
        _log.warning("[watchtower] 이상 감지: %s", found)
    return {"found": found, "blocked": blocked, "sets": total}


def daily_summary() -> None:
    """하루 1회 요약(정상일 때도 '살아있음'을 알림) — 크론이 아침에 호출."""
    if _recent("daily"):
        return
    try:
        users = db.list_users()
        paid = sum(1 for u in users if (u.get("plan") or "free").lower() in ("basic", "pro", "self", "agency"))
        tn = len(db.list_tenants() or [])
        r = check()
        msg = (f"☀️ 올린다 아침 브리핑\n"
               f"· 가입 {len(users)}명 / 유료 {paid}명 / 가게 {tn}곳\n"
               f"· 최근 글 {r['sets']}건 중 보류 {r['blocked']}건\n"
               f"· 상태: {'정상' if not r['found'] else '이상 ' + ','.join(r['found'])}")
        if send(msg):
            _mark("daily", msg)
    except Exception:
        _log.exception("[watchtower] 일일 요약 실패")
