"""📧 메일 발송 단일 관문(2026-08-11) — SMTP env를 읽는 소비자가 셋째로 늘어 함수화(헌법 2회 규칙).

경로 2종(env 게이트, Resend 우선):
  RESEND_API_KEY (+MAIL_FROM) — HTTPS API. Railway는 Pro 미만 플랜에서 SMTP 발신을 차단하므로
    (2026-08-11 실측: OSError 101) 이쪽이 기본 권장.
  SMTP_HOST / SMTP_PORT(기본 587, STARTTLS) / SMTP_USER / SMTP_PASS / SMTP_FROM — Pro 플랜 이상.
미설정이면 False — 발송 여부 게이트는 호출자가 configured()로 명시적으로 건다(침묵 폴백 금지).
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

_log = logging.getLogger("shopcast.mailer")


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY")
                or (os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS")))


def _send_resend(to: str, subject: str, body: str) -> bool:
    import requests
    r = requests.post("https://api.resend.com/emails",
                      headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                      json={"from": os.environ.get("MAIL_FROM", "올린다 <onboarding@resend.dev>"),
                            "to": [to], "subject": subject, "text": body}, timeout=15)
    if r.status_code // 100 != 2:
        raise RuntimeError(f"resend {r.status_code}: {r.text[:200]}")
    return True


def send(to: str, subject: str, body: str) -> bool:
    """발송 성공 시 True. 실패는 False + 명시 로그 — 조용히 성공한 척하지 않는다."""
    if not (configured() and to):
        return False
    if os.environ.get("RESEND_API_KEY"):
        try:
            return _send_resend(to, subject, body)
        except Exception as e:
            _log.warning("[mailer] resend 발송 실패 to=%s: %s", to, repr(e)[:200])
            return False
    try:
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
        msg["To"] = to
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
        return True
    except Exception as e:
        _log.warning("[mailer] 발송 실패 to=%s subj=%s: %s", to, subject, repr(e)[:200])
        return False
