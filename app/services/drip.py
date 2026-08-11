"""📨 리드·미전환 자동 이메일 드립(2026-08-11 마케팅 F) — Resend/SMTP로 발송(네이버 무관).

원칙:
- 광고성 메일은 제목에 (광고) + 수신거부 안내 필수(정보통신망법). 서명 토큰 수신거부 링크.
- 발송은 mailer.configured()일 때만. 침묵 폴백 금지 — 미설정이면 0건.
- 단계별 1통, 단계 간 최소 간격(기본 48시간). max_step 넘으면 종료.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from app import db

BASE = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
_SECRET = (os.environ.get("SHOPCAST_SECRET") or "x").encode()

# 시퀀스 — 실값·실링크만(날조 금지). 사장님 연락처/무료 시작으로 유도.
SEQUENCE = [
    ("[올린다] 사장님 가게, 네이버에 이렇게 노출됩니다",
     "안녕하세요, 올린다입니다.\n\n"
     "사진 몇 장만 올리면 네이버 블로그·플레이스 상위노출에 유리한 글과 영상을 AI가 만들어 드립니다.\n"
     "직접 쓰실 필요도, 마케팅을 배우실 필요도 없습니다.\n\n"
     "1분 영상으로 확인해보세요: {intro}\n"
     "무료로 시작: {base}\n"),
    ("[올린다] 안 쓰면 손해인 무료 진단",
     "안녕하세요, 올린다입니다.\n\n"
     "내 가게가 어떤 키워드에서 몇 위인지, 어떤 키워드가 '미노출'인지 30초면 확인됩니다.\n"
     "그 미노출 키워드를 잡는 글을 올린다가 대신 써 드립니다.\n\n"
     "무료 진단·시작: {base}\n"
     "궁금한 점은 편하게 연락 주세요 — {email} · {phone}\n"),
    ("[올린다] 마지막 안내드립니다",
     "안녕하세요, 올린다입니다.\n\n"
     "바쁘셔서 아직 못 보셨을 것 같아 한 번 더 안내드립니다.\n"
     "사진만 올리면 글·영상이 나오는 올린다, 부담 없이 무료로 먼저 써보세요.\n\n"
     "시작: {base}\n"
     "문의 {email} · {phone}\n"),
]
MAX_STEP = len(SEQUENCE)
MIN_HOURS = float(os.environ.get("OLLINDA_DRIP_MIN_HOURS", "48"))


def unsub_token(email: str) -> str:
    return hmac.new(_SECRET, f"unsub:{email.lower()}".encode(), hashlib.sha256).hexdigest()[:24]


def unsub_ok(email: str, token: str) -> bool:
    return hmac.compare_digest(unsub_token(email), token or "")


def configured() -> bool:
    from app.services import mailer
    return mailer.configured()


def run(limit: int = 50, dry: bool = False) -> dict:
    """도래한 드립 발송. 반환 {due, sent, step_counts}."""
    from app.services import mailer
    from app import landing
    if not mailer.configured():
        return {"ok": False, "reason": "mailer 미설정", "sent": 0}
    due = db.drip_due(MIN_HOURS, MAX_STEP, limit)
    sent = 0
    for r in due:
        em, step = r["email"], r["step"]
        if step >= MAX_STEP:
            continue
        subj, tmpl = SEQUENCE[step]
        foot = (f"\n\n---\n수신거부: {BASE}/u/unsub?e={em}&t={unsub_token(em)}\n"
                f"광고 · 올린다({getattr(landing, 'BIZ_ADDR', '')})")
        body = tmpl.format(base=BASE, intro=f"{BASE}/intro",
                           email=landing.CONTACT_EMAIL, phone=landing.BIZ_PHONE) + foot
        # 광고성 표기(정보통신망법) — 제목에 (광고)
        subject = subj if subj.startswith("(광고)") else "(광고) " + subj
        if dry:
            sent += 1
            continue
        if mailer.send(em, subject, body):
            db.drip_mark(em, step)
            sent += 1
    return {"ok": True, "due": len(due), "sent": sent}
