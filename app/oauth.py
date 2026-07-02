"""
OAuth 계정 연결 — 사장님이 본인 인스타/유튜브에 '발행 권한'을 위임(비번 X, 토큰 O).

- Instagram: Instagram API with Instagram Login (2024.7~). 프로(비즈/크리에이터) 계정 직접 연결.
    필요 env: IG_APP_ID, IG_APP_SECRET   (Meta 개발자 앱 + Instagram 제품)
    스코프: instagram_business_basic, instagram_business_content_publish
    ※ 남의 계정에 발행하려면 Meta 앱 심사(Advanced Access) 필요.
- YouTube: 표준 Google OAuth.
    필요 env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    스코프: youtube.upload

redirect_uri = {SHOPCAST_BASE}/oauth/callback  (각 플랫폼 콘솔에 등록 필요, https).
state = HMAC 서명된 "tenant_id:channel" (변조 방지).

docs:
  https://developers.facebook.com/docs/instagram-platform/content-publishing/
  https://developers.google.com/youtube/v3/guides/uploading_a_video
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode

import requests

from app.domain.models import Channel

SECRET = os.environ.get("SHOPCAST_SECRET", "dev-secret-change-me").encode()


def base_url() -> str:
    return os.environ.get("SHOPCAST_BASE", "http://127.0.0.1:8000")


def redirect_uri() -> str:
    return base_url() + "/oauth/callback"


# ── state 서명(변조 방지) ────────────────────────────────
def make_state(tenant_id: str, channel: Channel) -> str:
    raw = f"{tenant_id}:{channel.value}".encode()
    sig = hmac.new(SECRET, raw, hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(raw + b"." + sig).decode()


def parse_state(state: str) -> tuple[str, Channel] | tuple[None, None]:
    try:
        decoded = base64.urlsafe_b64decode(state.encode())
        raw, sig = decoded.rsplit(b".", 1)
        if not hmac.compare_digest(hmac.new(SECRET, raw, hashlib.sha256).digest()[:12], sig):
            return None, None
        tid, ch = raw.decode().split(":", 1)
        return tid, Channel(ch)
    except Exception:
        return None, None


# ── 설정 여부 ────────────────────────────────────────────
def configured(channel: Channel) -> bool:
    if channel == Channel.INSTAGRAM:
        return bool(os.environ.get("IG_APP_ID") and os.environ.get("IG_APP_SECRET"))
    if channel == Channel.YOUTUBE:
        return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))
    if channel == Channel.X:
        return bool(os.environ.get("X_CLIENT_ID") and os.environ.get("X_CLIENT_SECRET"))
    return False


def _x_verifier(state: str) -> str:
    """PKCE code_verifier — state에서 결정적으로 도출(콜백에서 재계산 가능)."""
    return hashlib.sha256(SECRET + state.encode()).hexdigest()


# ── authorize URL ───────────────────────────────────────
def authorize_url(channel: Channel, tenant_id: str) -> str:
    state = make_state(tenant_id, channel)
    if channel == Channel.INSTAGRAM:
        q = {
            "client_id": os.environ["IG_APP_ID"],
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "instagram_business_basic,instagram_business_content_publish",
            "state": state,
        }
        return "https://www.instagram.com/oauth/authorize?" + urlencode(q)
    if channel == Channel.YOUTUBE:
        q = {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.upload",
            "access_type": "offline",   # refresh_token 받기
            "prompt": "consent",
            "state": state,
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(q)
    if channel == Channel.X:
        ver = _x_verifier(state)
        q = {
            "client_id": os.environ["X_CLIENT_ID"],
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "tweet.read tweet.write users.read offline.access",
            "state": state,
            "code_challenge": ver, "code_challenge_method": "plain",
        }
        return "https://x.com/i/oauth2/authorize?" + urlencode(q)
    raise ValueError(f"unsupported channel: {channel}")


# ── code → token 교환 ───────────────────────────────────
def exchange_code(channel: Channel, code: str, state: str = "") -> dict:
    """{access_token, refresh_token, meta} 반환(채널별)."""
    if channel == Channel.INSTAGRAM:
        return _exchange_instagram(code)
    if channel == Channel.YOUTUBE:
        return _exchange_youtube(code)
    if channel == Channel.X:
        return _exchange_x(code, state)
    raise ValueError(f"unsupported channel: {channel}")


def _exchange_x(code: str, state: str) -> dict:
    import base64 as _b64
    basic = _b64.b64encode(
        f"{os.environ['X_CLIENT_ID']}:{os.environ['X_CLIENT_SECRET']}".encode()).decode()
    r = requests.post("https://api.x.com/2/oauth2/token", data={
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri(), "code_verifier": _x_verifier(state),
        "client_id": os.environ["X_CLIENT_ID"],
    }, headers={"Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    r.raise_for_status()
    tok = r.json()
    return {"access_token": tok.get("access_token", ""),
            "refresh_token": tok.get("refresh_token", ""),
            "meta": {"scope": tok.get("scope", "")}}


def _exchange_instagram(code: str) -> dict:
    # 1) 단기 토큰 + IG user_id
    r = requests.post("https://api.instagram.com/oauth/access_token", data={
        "client_id": os.environ["IG_APP_ID"],
        "client_secret": os.environ["IG_APP_SECRET"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
        "code": code,
    }, timeout=15)
    r.raise_for_status()
    short = r.json()
    access = short.get("access_token", "")
    ig_user_id = str(short.get("user_id", ""))
    # 2) 장기 토큰(60일)
    rl = requests.get("https://graph.instagram.com/access_token", params={
        "grant_type": "ig_exchange_token",
        "client_secret": os.environ["IG_APP_SECRET"],
        "access_token": access,
    }, timeout=15)
    long_token = rl.json().get("access_token", access) if rl.ok else access
    return {"access_token": long_token, "refresh_token": "",
            "meta": {"ig_user_id": ig_user_id}}


def refresh_youtube_token(refresh_token: str) -> str:
    """구글 access_token 갱신(약 1시간 만료). 실패 시 빈 문자열."""
    if not refresh_token:
        return ""
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, timeout=15)
        return r.json().get("access_token", "") if r.ok else ""
    except Exception:
        return ""


def _exchange_youtube(code: str) -> dict:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
        "code": code,
    }, timeout=15)
    r.raise_for_status()
    tok = r.json()
    return {"access_token": tok.get("access_token", ""),
            "refresh_token": tok.get("refresh_token", ""),
            "meta": {"scope": tok.get("scope", ""), "expires_in": tok.get("expires_in")}}
