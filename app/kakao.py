"""
카카오 간편가입/로그인 — 랜딩 '카카오로 가입' 버튼.
env: KAKAO_REST_KEY, (선택) KAKAO_CLIENT_SECRET, KAKAO_REDIRECT_URI(미설정시 BASE/login/kakao/callback).
키 없으면 /signup(이메일 가입)으로 폴백.
"""
from __future__ import annotations

import os
import uuid

import requests
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app import auth, db


def _instant_signup(name: str = "카카오회원"):
    """앱 키 미설정 시: 버튼 한 번에 즉시 가입(임시 게스트) → /welcome.
    KAKAO_REST_KEY를 넣으면 이 폴백 대신 실제 카카오 로그인으로 진행됨."""
    u = db.create_user(email=f"k_{uuid.uuid4().hex[:12]}@ollinda.guest", name=name)
    resp = RedirectResponse("/me", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(u["id"]), max_age=5184000, httponly=True, samesite="lax", secure=auth.cookie_secure())
    return resp

AUTHORIZE = "https://kauth.kakao.com/oauth/authorize"
TOKEN = "https://kauth.kakao.com/oauth/token"
ME = "https://kapi.kakao.com/v2/user/me"


def configured() -> bool:
    return bool(os.environ.get("KAKAO_REST_KEY"))


def _redirect_uri() -> str:
    base = os.environ.get("SHOPCAST_BASE", "http://127.0.0.1:8000")
    return os.environ.get("KAKAO_REDIRECT_URI", base + "/login/kakao/callback")


def make_router() -> APIRouter:
    r = APIRouter()

    @r.get("/login/kakao")
    def login():
        if not configured():
            return _instant_signup("카카오회원")   # 키 없으면 버튼 한 번에 즉시 가입
        url = (f"{AUTHORIZE}?response_type=code&client_id={os.environ['KAKAO_REST_KEY']}"
               f"&redirect_uri={_redirect_uri()}")
        return RedirectResponse(url)

    @r.get("/login/kakao/callback")
    def callback(code: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/?err=카카오_취소")
        data = {"grant_type": "authorization_code",
                "client_id": os.environ.get("KAKAO_REST_KEY", ""),
                "redirect_uri": _redirect_uri(), "code": code}
        if os.environ.get("KAKAO_CLIENT_SECRET"):
            data["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
        try:
            tok = requests.post(TOKEN, data=data, timeout=15).json()
            access = tok.get("access_token")
            me = requests.get(ME, headers={"Authorization": f"Bearer {access}"}, timeout=15).json()
            kid = str(me.get("id", ""))
            email = ((me.get("kakao_account") or {}).get("email")) or f"kakao_{kid}@kakao.local"
            user = db.get_user_by_kakao(kid) or db.create_user(email=email, kakao_id=kid, name="카카오회원")
        except Exception as e:
            return RedirectResponse(f"/?err=카카오_실패")
        resp = RedirectResponse("/me", status_code=303)
        resp.set_cookie(auth.COOKIE, auth.make_session(user["id"]), max_age=5184000, httponly=True, samesite="lax", secure=auth.cookie_secure())
        return resp

    return r
