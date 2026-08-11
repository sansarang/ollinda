"""테스트 계정 표적 삭제 골든(2026-08-11) — 지우라는 것만 지우고, 지우면 안 되는 건 거부.
오폭(운영자·실계정 삭제)이 통과하면 이 파일이 커밋을 막아야 한다.
"""
import base64
import uuid

from fastapi.testclient import TestClient

from app import auth, db


def _client():
    from app.main import app
    return TestClient(app)


def _basic():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:test-admin-pass").decode()}


def _mk_test_user(email):
    h, salt = auth.hash_pw("pw123456")
    u = db.create_user(email=email, pw_hash=h, salt=salt)
    t = db.create_tenant(name="삭제대상가게", industry="카페", region="부산 동구")
    with db._conn() as c:
        c.execute("UPDATE users SET tenant_id=? WHERE id=?", (t.id, u["id"]))
    return u, t


def test_deletes_test_user_and_sole_tenant():
    email = f"del-{uuid.uuid4().hex[:8]}@t.kr"
    u, t = _mk_test_user(email)
    r = _client().post(f"/admin/testaccount/delete?email={email}", headers=_basic())
    assert r.json().get("ok"), r.json()
    assert db.get_user_by_email(email) is None, "사용자 행이 남음"
    assert db.get_tenant(t.id) is None, "단독 tenant가 남음"


def test_refuses_owner_email():
    r = _client().post("/admin/testaccount/delete?email=etetetetet5ea@kakao.com", headers=_basic())
    assert not r.json().get("ok"), "운영자 계정 삭제가 통과됨 — 오폭"


def test_refuses_production_tenant(monkeypatch):
    email = f"prod-{uuid.uuid4().hex[:8]}@t.kr"
    u, t = _mk_test_user(email)
    from app import config as _cfg
    monkeypatch.setattr(_cfg, "PRODUCTION_TENANTS", tuple(_cfg.PRODUCTION_TENANTS) + (t.id,))
    r = _client().post(f"/admin/testaccount/delete?email={email}", headers=_basic())
    assert not r.json().get("ok"), "실계정 tenant 삭제가 통과됨 — 오폭"
    assert db.get_user_by_email(email) is not None


def test_shared_tenant_survives():
    email = f"shared-{uuid.uuid4().hex[:8]}@t.kr"
    u, t = _mk_test_user(email)
    h, salt = auth.hash_pw("pw123456")
    other = db.create_user(email=f"keep-{uuid.uuid4().hex[:8]}@t.kr", pw_hash=h, salt=salt)
    with db._conn() as c:
        c.execute("UPDATE users SET tenant_id=? WHERE id=?", (t.id, other["id"]))
    r = _client().post(f"/admin/testaccount/delete?email={email}", headers=_basic())
    assert r.json().get("ok")
    assert db.get_tenant(t.id) is not None, "공유 tenant가 같이 지워짐 — 오폭"
