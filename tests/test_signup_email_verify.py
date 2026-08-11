"""이메일 가입 인증 골든(2026-08-11 사장님 지시) — SMTP 설정 시 코드 인증을 통과해야만 계정 생성.

계약: ① 코드 입력 전엔 계정이 없다 ② 발송 실패 = 가입 실패(침묵 통과 금지)
③ 오답·만료·위조 전부 거부 ④ SMTP 미설정이면 기존 즉시 가입 유지(env 게이트).
"""
import re
import time
import uuid

from fastapi.testclient import TestClient

from app import auth, db
from app.services import mailer


def _client():
    from app.main import app
    return TestClient(app)


def _signup(c, email, **extra):
    data = {"email": email, "pw": "pw123456", "website": "",
            "st": auth.signup_token(int(time.time()) - 5)}
    data.update(extra)
    return c.post("/signup", data=data,
                  headers={"x-forwarded-for": "203.0.113." + uuid.uuid4().hex[:2]},
                  follow_redirects=False)


def _smtp_on(monkeypatch, outbox):
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subj, body: outbox.append((to, subj, body)) or True)


def test_code_required_before_account(monkeypatch):
    outbox = []
    _smtp_on(monkeypatch, outbox)
    email = f"verify-{uuid.uuid4().hex[:8]}@t.kr"
    c = _client()
    r = _signup(c, email)
    assert r.status_code == 200 and "인증 코드" in r.text, "인증 페이지가 안 뜸"
    assert db.get_user_by_email(email) is None, "코드 입력 전에 계정이 생김 — 인증이 장식이다"
    assert outbox and outbox[0][0] == email, "인증 메일 미발송"
    code = re.search(r"인증 코드: (\d{6})", outbox[0][2]).group(1)
    fields = dict(re.findall(r"name=(\w+) value='([^']*)'", r.text))
    r2 = c.post("/signup/verify", data={**fields, "code": code},
                headers={"x-forwarded-for": "203.0.113.99"}, follow_redirects=False)
    assert r2.status_code == 303 and r2.headers["location"] == "/me", "올바른 코드 인증이 실패"
    assert db.get_user_by_email(email) is not None, "인증 후에도 계정 미생성"


def test_wrong_code_rejected(monkeypatch):
    outbox = []
    _smtp_on(monkeypatch, outbox)
    email = f"wrong-{uuid.uuid4().hex[:8]}@t.kr"
    c = _client()
    r = _signup(c, email)
    fields = dict(re.findall(r"name=(\w+) value='([^']*)'", r.text))
    r2 = c.post("/signup/verify", data={**fields, "code": "000000"},
                headers={"x-forwarded-for": "203.0.113.98"}, follow_redirects=False)
    assert "맞지 않아요" in r2.text and db.get_user_by_email(email) is None, "오답 코드가 통과됨"


def test_expired_token_rejected(monkeypatch):
    _smtp_on(monkeypatch, [])
    email = f"exp-{uuid.uuid4().hex[:8]}@t.kr"
    h, salt = auth.hash_pw("pw123456")
    exp, sig = auth.signup_verify_token(email, h, salt, "123456", exp=int(time.time()) - 10)
    r = _client().post("/signup/verify",
                       data={"email": email, "h": h, "salt": salt, "exp": exp, "sig": sig, "code": "123456"},
                       headers={"x-forwarded-for": "203.0.113.97"}, follow_redirects=False)
    assert db.get_user_by_email(email) is None, "만료 토큰이 통과됨"


def test_forged_sig_rejected(monkeypatch):
    _smtp_on(monkeypatch, [])
    email = f"forge-{uuid.uuid4().hex[:8]}@t.kr"
    h, salt = auth.hash_pw("pw123456")
    r = _client().post("/signup/verify",
                       data={"email": email, "h": h, "salt": salt,
                             "exp": str(int(time.time()) + 900), "sig": "deadbeef" * 4, "code": "123456"},
                       headers={"x-forwarded-for": "203.0.113.96"}, follow_redirects=False)
    assert db.get_user_by_email(email) is None, "위조 서명이 통과됨"


def test_resend_path_used_when_key_set(monkeypatch):
    """Railway가 SMTP를 막아도(Pro 미만 실측) Resend HTTPS 경로로 발송돼야 한다."""
    sent = []

    class _R:
        status_code = 200
        text = "ok"

    import app.services.mailer as m
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr("requests.post", lambda url, **kw: sent.append((url, kw)) or _R())
    assert m.configured()
    assert m.send("a@b.kr", "제목", "본문") is True
    assert sent and "api.resend.com" in sent[0][0], "Resend API 경로를 안 탔다"


def test_send_failure_blocks_signup(monkeypatch):
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda *a: False)
    email = f"fail-{uuid.uuid4().hex[:8]}@t.kr"
    r = _signup(_client(), email)
    assert "err=3" in r.headers.get("location", ""), "발송 실패인데 가입이 진행됨 — 침묵 폴백"
    assert db.get_user_by_email(email) is None
