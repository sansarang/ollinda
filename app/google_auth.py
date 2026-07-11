"""
구글 간편가입/로그인 — 승인 한 번이면 가입(scope: openid email profile).
env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, (선택) GOOGLE_LOGIN_REDIRECT.
redirect_uri 기본 = {SHOPCAST_BASE}/login/google/callback (구글 콘솔에 등록 필요).
키 없으면 /signup(이메일 가입)으로 폴백.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlencode

import requests
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app import auth, db


def _instant_signup(name: str = "구글회원"):
    """앱 키 미설정 시: 버튼 한 번에 즉시 가입(임시 게스트 계정) → /welcome.
    GOOGLE_CLIENT_ID/SECRET을 넣으면 이 폴백 대신 실제 OAuth 동의창으로 진행됨."""
    u = db.create_user(email=f"g_{uuid.uuid4().hex[:12]}@ollinda.guest", name=name)
    resp = RedirectResponse("/me", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(u["id"]), max_age=5184000, httponly=True, samesite="lax", secure=auth.cookie_secure())
    return resp

AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"


def configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def _redirect_uri() -> str:
    base = os.environ.get("SHOPCAST_BASE", "http://127.0.0.1:8000")
    return os.environ.get("GOOGLE_LOGIN_REDIRECT", base + "/login/google/callback")


def make_router() -> APIRouter:
    r = APIRouter()

    @r.get("/login/google")
    def login():
        if not configured():
            return _instant_signup("구글회원")   # 키 없으면 버튼 한 번에 즉시 가입
        q = {"client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": _redirect_uri(),
             "response_type": "code", "scope": "openid email profile",
             "access_type": "online"}   # prompt 제거 → 이미 구글 로그인돼 있으면 재선택 없이 바로 통과
        return RedirectResponse(AUTHORIZE + "?" + urlencode(q))

    @r.get("/login/google/callback")
    def callback(code: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/?err=구글_취소")
        try:
            tok = requests.post(TOKEN, data={
                "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": _redirect_uri(), "grant_type": "authorization_code",
            }, timeout=15).json()
            access = tok.get("access_token")
            info = requests.get(USERINFO, headers={"Authorization": f"Bearer {access}"}, timeout=15).json()
            email = (info.get("email") or "").lower().strip()
            if not email:
                return RedirectResponse("/?err=구글_이메일없음")
            user = db.get_user_by_email(email) or db.create_user(email=email, name=info.get("name", "구글회원"))
        except Exception:
            return RedirectResponse("/?err=구글_실패")
        resp = RedirectResponse("/me", status_code=303)
        resp.set_cookie(auth.COOKIE, auth.make_session(user["id"]), max_age=5184000, httponly=True, samesite="lax", secure=auth.cookie_secure())
        return resp

    return r
