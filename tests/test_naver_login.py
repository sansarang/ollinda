"""네이버 로그인 골든 — 2026-08-09 가입 흐름 정비(카카오 → 네이버 → 구글).

계약: ① 키 미설정 시 fail-closed(게스트 즉시가입 폴백 금지 — 카카오·구글의 fail-open
비대칭을 늘리지 않는다) + 버튼 미노출(허위 버튼 금지) ② 키 설정 시 랜딩·로그인·가입에
버튼 노출, authorize로 state 쿠키와 함께 리다이렉트 ③ 콜백은 state 불일치를 거부(CSRF).
"""
import os
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import landing
from app.main import app

client = TestClient(app)

_ENVS = ("NAVER_LOGIN_CLIENT_ID", "NAVER_LOGIN_CLIENT_SECRET")


def _unset(monkeypatch):
    for k in _ENVS:
        monkeypatch.delenv(k, raising=False)


def _set(monkeypatch):
    monkeypatch.setenv("NAVER_LOGIN_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("NAVER_LOGIN_CLIENT_SECRET", "test-secret")


def test_unconfigured_is_fail_closed_no_guest_signup(monkeypatch):
    from app import db
    _unset(monkeypatch)
    with db._conn() as c:
        before = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    r = client.get("/login/naver", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/signup" in r.headers["location"], "미설정 시 이메일 가입 안내로 보내야 한다"
    with db._conn() as c:
        after = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert after == before, "키 미설정인데 게스트 계정이 만들어졌다(fail-open 회귀)"


def test_unconfigured_hides_all_naver_buttons(monkeypatch):
    _unset(monkeypatch)
    assert "/login/naver" not in landing.render(), "키 없는데 랜딩에 네이버 버튼(허위 버튼)"
    assert "/login/naver" not in client.get("/login").text
    assert "/login/naver" not in client.get("/signup").text


def test_configured_shows_buttons_kakao_first(monkeypatch):
    _set(monkeypatch)
    h = landing.render()
    assert h.count("/login/naver") >= 2, "히어로·최종 CTA에 네이버 버튼이 있어야 한다"
    assert h.index("/login/kakao") < h.index("/login/naver"), "순서는 카카오 → 네이버"
    lg = client.get("/login").text
    assert "/login/naver" in lg and lg.index("/login/kakao") < lg.index("/login/naver")
    su = client.get("/signup").text
    assert "/login/naver" in su
    assert su.index("/login/kakao") < su.index("/login/naver") < su.index("/login/google"), \
        "가입 페이지 순서는 카카오 → 네이버 → 구글"


def test_configured_login_redirects_to_naver_with_state(monkeypatch):
    _set(monkeypatch)
    r = client.get("/login/naver", follow_redirects=False)
    assert r.status_code in (302, 307)
    loc = r.headers["location"]
    assert loc.startswith("https://nid.naver.com/oauth2.0/authorize")
    assert "client_id=test-client-id" in loc and "state=" in loc
    assert "nv_state=" in r.headers.get("set-cookie", ""), "state 쿠키가 심겨야 콜백 대조가 된다"
    assert "/login/naver/callback" in loc


def test_callback_rejects_state_mismatch(monkeypatch):
    _set(monkeypatch)
    client.cookies.set("nv_state", "real-state")
    r = client.get("/login/naver/callback?code=abc&state=forged", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "상태오류" in unquote(r.headers["location"]), "state 불일치(CSRF)를 거부해야 한다"
    client.cookies.delete("nv_state")


def test_callback_without_code_is_cancel(monkeypatch):
    _set(monkeypatch)
    r = client.get("/login/naver/callback", follow_redirects=False)
    assert "취소" in unquote(r.headers["location"])
