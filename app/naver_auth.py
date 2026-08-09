"""
네이버 간편가입/로그인 — 랜딩 '네이버로 시작' 버튼. 타겟 고객(네이버 블로그 사용 사장님)의
기본 계정이라 카카오 다음 2순위 소셜.

env: NAVER_LOGIN_CLIENT_ID, NAVER_LOGIN_CLIENT_SECRET, (선택) NAVER_LOGIN_REDIRECT.
redirect_uri 기본 = {SHOPCAST_BASE}/login/naver/callback (개발자센터 Callback URL과 일치 필수).

카카오·구글과 다른 점 두 가지(의도):
- 키 미설정 시 게스트 즉시가입 폴백 없음 — fail-closed. 랜딩 버튼 자체가 configured()일 때만
  렌더되므로, 미설정 상태에서 이 라우트를 치는 것은 비정상 경로다(무인증 가입 구멍을 늘리지 않는다).
- state 검증 — 네이버는 state가 필수 파라미터이고, 콜백에서 대조하지 않으면 CSRF로
  타인 세션이 심긴다. 랜덤 state를 단기 쿠키에 두고 콜백에서 대조한다.
"""
from __future__ import annotations

import os
import secrets

import requests
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app import auth, db

AUTHORIZE = "https://nid.naver.com/oauth2.0/authorize"
TOKEN = "https://nid.naver.com/oauth2.0/token"
ME = "https://openapi.naver.com/v1/nid/me"

_STATE_COOKIE = "nv_state"


def configured() -> bool:
    return bool(os.environ.get("NAVER_LOGIN_CLIENT_ID") and os.environ.get("NAVER_LOGIN_CLIENT_SECRET"))


def _redirect_uri() -> str:
    base = os.environ.get("SHOPCAST_BASE", "http://127.0.0.1:8000")
    return os.environ.get("NAVER_LOGIN_REDIRECT", base + "/login/naver/callback")


def make_router() -> APIRouter:
    r = APIRouter()

    @r.get("/login/naver")
    def login():
        if not configured():
            # fail-closed: 게스트 가입으로 새지 않는다 — 이메일 가입으로 안내
            return RedirectResponse("/signup?err=네이버_준비중")
        state = secrets.token_urlsafe(24)
        url = (f"{AUTHORIZE}?response_type=code&client_id={os.environ['NAVER_LOGIN_CLIENT_ID']}"
               f"&redirect_uri={_redirect_uri()}&state={state}")
        resp = RedirectResponse(url)
        resp.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True,
                        samesite="lax", secure=auth.cookie_secure())
        return resp

    @r.get("/login/naver/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse("/?err=네이버_취소")
        if not state or state != (request.cookies.get(_STATE_COOKIE) or ""):
            return RedirectResponse("/?err=네이버_상태오류")
        try:
            tok = requests.get(TOKEN, params={
                "grant_type": "authorization_code",
                "client_id": os.environ["NAVER_LOGIN_CLIENT_ID"],
                "client_secret": os.environ["NAVER_LOGIN_CLIENT_SECRET"],
                "code": code, "state": state,
            }, timeout=15).json()
            access = tok.get("access_token")
            me = requests.get(ME, headers={"Authorization": f"Bearer {access}"}, timeout=15).json()
            info = me.get("response") or {}
            email = (info.get("email") or "").lower().strip()
            if not email:
                return RedirectResponse("/?err=네이버_이메일없음")
            user = db.get_user_by_email(email) or db.create_user(
                email=email, name=info.get("name") or info.get("nickname") or "네이버회원")
        except Exception:
            return RedirectResponse("/?err=네이버_실패")
        resp = RedirectResponse("/me", status_code=303)
        resp.set_cookie(auth.COOKIE, auth.make_session(user["id"]), max_age=5184000,
                        httponly=True, samesite="lax", secure=auth.cookie_secure())
        resp.delete_cookie(_STATE_COOKIE)
        return resp

    return r
