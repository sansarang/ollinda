"""이메일 가입 봇 차단 골든(2026-08-11, 봇 가입 2건 실측 후 사장님 지시).
3중 장치: 허니팟 / 렌더시각 서명 토큰(사람 속도) / IP 속도 제한.
사람의 정상 가입은 그대로 통과해야 한다 — 차단이 과하면 그게 더 큰 사고다.
"""
import time
import uuid

from fastapi.testclient import TestClient

from app import auth, db


def _client():
    from app.main import app
    return TestClient(app)


def _aged_token(sec=5):
    return auth.signup_token(int(time.time()) - sec)


def _email():
    return f"human-{uuid.uuid4().hex[:8]}@t.kr"


def _post(c, **kw):
    data = {"email": _email(), "pw": "pw123456", "website": "", "st": _aged_token()}
    data.update(kw)
    headers = kw.pop("headers", None) or {"x-forwarded-for": "203.0.113." + uuid.uuid4().hex[:2]}
    return c.post("/signup", data=data, headers=headers, follow_redirects=False)


def test_normal_human_signup_passes():
    r = _post(_client())
    assert r.status_code == 303 and r.headers["location"] == "/me", \
        f"정상 가입이 차단됨({r.headers.get('location')}) — 과차단은 매출 사고다"


def test_honeypot_filled_blocked():
    r = _post(_client(), website="http://spam.example")
    assert "err=2" in r.headers.get("location", ""), "허니팟 채운 봇이 통과됨"


def test_instant_submit_blocked():
    r = _post(_client(), st=auth.signup_token())     # 렌더 직후 0초 제출 = 봇 속도
    assert "err=2" in r.headers.get("location", ""), "즉시 제출 봇이 통과됨"


def test_forged_token_blocked():
    r = _post(_client(), st=f"{int(time.time())-10}.deadbeefdeadbeefdeadbeef")
    assert "err=2" in r.headers.get("location", ""), "위조 토큰이 통과됨"


def test_missing_token_blocked():
    r = _post(_client(), st="")
    assert "err=2" in r.headers.get("location", ""), "토큰 없는 제출(폼 밖 직접 POST)이 통과됨"


def test_same_ip_rate_limited():
    c = _client()
    hdr = {"x-forwarded-for": "198.51.100.77"}
    codes = []
    for _ in range(4):
        r = _post(c, headers=hdr, email=_email())
        codes.append(r.headers.get("location", ""))
    assert "err=2" in codes[-1], f"같은 IP 4연속 가입이 통과됨: {codes}"


def test_signup_form_carries_guards():
    html = _client().get("/signup").text
    assert "name=website" in html, "허니팟 필드 유실"
    assert "name=st" in html, "서명 토큰 필드 유실"
