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
SESSION_TTL = 60 * 24 * 3600   # 세션 유효기간 60일(쿠키 max-age와 동일)


def cookie_secure() -> bool:
    """HTTPS 배포(SHOPCAST_BASE=https…)에서만 Secure 쿠키. 로컬 http 개발은 False 유지."""
    return os.environ.get("SHOPCAST_BASE", "").startswith("https")


# ── 비밀번호 ──
def hash_pw(pw: str, salt: str = "") -> tuple[str, str]:
    salt = salt or uuid.uuid4().hex
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100_000).hex()
    return h, salt


def verify_pw(pw: str, salt: str, h: str) -> bool:
    calc, _ = hash_pw(pw, salt)
    return hmac.compare_digest(calc, h)


# ── 미디어 서명 URL(시한부) ──
# 피스 미디어(/asset·/video)는 소유자 세션이 원칙이지만, 인스타그램 발행처럼 외부 서버가
# 무인증으로 가져가야 하는 경로가 있다 — 그쪽엔 이 서명을 붙인 시한부 URL만 내준다.
def media_sig(pid: str, exp: int) -> str:
    return hmac.new(SECRET, f"media:{pid}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def media_sig_ok(pid: str, exp: str | int, sig: str) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    return hmac.compare_digest(media_sig(pid, exp_i), sig or "")


def signed_media_url(base: str, kind: str, pid: str, ttl_sec: int = 3600) -> str:
    """kind: 'asset' | 'video' — 외부 발행용 공개 URL(만료 기본 1시간)."""
    exp = int(time.time()) + ttl_sec
    return f"{base}/{kind}/{pid}?exp={exp}&sig={media_sig(pid, exp)}"


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
        if not hmac.compare_digest(good, sig):
            return None
        if int(time.time()) - int(ts) > SESSION_TTL:   # 만료된 세션 거부(B14)
            return None
        return uid
    except Exception:
        pass
    return None


def current_user(request) -> dict | None:
    uid = read_session(request.cookies.get(COOKIE))
    return db.get_user(uid) if uid else None
