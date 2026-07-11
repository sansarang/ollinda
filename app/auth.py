"""
사용자 회원/세션 — 랜딩 가입(이메일/카카오) 실동작.
세션은 HMAC 서명 쿠키(gm_session). 비번은 pbkdf2.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid

from app import db

_secret = os.environ.get("SHOPCAST_SECRET")
if not _secret:
    # fail-closed: 서명 키가 없으면 세션 위조가 가능하므로 기동을 중단한다.
    raise RuntimeError(
        "SHOPCAST_SECRET 환경변수가 설정되지 않았습니다. 세션 서명 키 없이는 서버를 기동할 수 없습니다."
    )
SECRET = _secret.encode()
COOKIE = "shop_session"


# ── 비밀번호 ──
def hash_pw(pw: str, salt: str = "") -> tuple[str, str]:
    salt = salt or uuid.uuid4().hex
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()
    return h, salt


def verify_pw(pw: str, salt: str, h: str) -> bool:
    calc, _ = hash_pw(pw, salt)
    return hmac.compare_digest(calc, h)


# ── 세션 쿠키 ──
def make_session(uid: str) -> str:
    raw = f"{uid}.{int(time.time())}"
    sig = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def read_session(cookie: str | None) -> str | None:
    if not cookie:
        return None
    try:
        uid, ts, sig = cookie.rsplit(".", 2)
        raw = f"{uid}.{ts}"
        good = hmac.new(SECRET, raw.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(good, sig):
            return uid
    except Exception:
        pass
    return None


def current_user(request) -> dict | None:
    uid = read_session(request.cookies.get(COOKIE))
    return db.get_user(uid) if uid else None
