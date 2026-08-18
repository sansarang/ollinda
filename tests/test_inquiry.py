"""📨 상담 문의 골든 — 대행의 유일한 입구.

2026-08-18 사장님: "대행이다. 사용자의 가입은 원하지 않는다.
카카오 채널 개설, 전화·메일로 내가 직접 받는다."

가입을 없애면 문의가 **유일한 입구**가 된다. 그런데 그 입구가 새고 있었다:
  · 메일만 시도하고(SMTP 미설정으로 늘 실패) 로그 파일에 한 줄
  · DB 저장 없음 → 누가 언제 문의했는지 **볼 화면조차 없었다**

★ 이 골든이 지키는 것은 하나다: **문의는 어떤 경우에도 남는다.**
  메일이 죽어도, 경보가 죽어도 저장은 됐어야 한다.
  놓친 문의는 그대로 잃어버린 계약이다.
"""
import os

import pytest

os.environ.setdefault("SHOPCAST_SECRET", "test")
os.environ.setdefault("SHOPCAST_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SHOPCAST_DB", "/tmp/test_inquiry.sqlite")

from fastapi.testclient import TestClient  # noqa: E402

from app import db, main  # noqa: E402

db.init_db()


@pytest.fixture
def client():
    return TestClient(main.app)


def _form(**kw):
    base = {"company": "루마썬팅", "manager": "김사장", "phone": "010-1234-5678",
            "email": "shop@test.com", "message": "대행 문의드립니다"}
    base.update(kw)
    return base


def test_메일이_죽어도_문의는_저장된다(client, monkeypatch):
    """★ 이것이 이 파일의 존재 이유다.
    전에는 메일 실패 = 문의 소실이었다(로그 파일 한 줄이 전부)."""
    from app.services import mailer
    monkeypatch.setattr(mailer, "send", lambda *a, **k: False)
    before = len(db.list_contacts(limit=500))
    r = client.post("/api/contact", data=_form(company="메일죽음테스트"))
    assert r.status_code == 200 and r.json()["ok"] is True
    rows = db.list_contacts(limit=500)
    assert len(rows) == before + 1, "메일이 실패하자 문의가 사라졌다"
    assert rows[0]["company"] == "메일죽음테스트"
    assert rows[0]["mailed"] == 0, "안 보냈으면서 보냈다고 기록했다"


def test_경보가_터져도_문의는_저장된다(client, monkeypatch):
    from app.services import watchtower
    def _boom(*a, **k):
        raise RuntimeError("텔레그램 죽음")
    monkeypatch.setattr(watchtower, "send", _boom)
    before = len(db.list_contacts(limit=500))
    r = client.post("/api/contact", data=_form(company="경보죽음테스트"))
    assert r.status_code == 200
    assert len(db.list_contacts(limit=500)) == before + 1


def test_연락처가_없으면_받지_않는다(client):
    """전화도 메일도 없으면 우리가 연락할 방법이 없다 — 접수해봐야 못 이어진다."""
    r = client.post("/api/contact", data=_form(phone="", email=""))
    assert r.status_code == 400
    assert "연락처" in r.json().get("error", "")


def test_저장에_실패하면_성공한_척하지_않는다(client, monkeypatch):
    """정직 게이트 — 접수됐다고 해놓고 잃어버리면 그게 최악이다."""
    def _boom(*a, **k):
        raise RuntimeError("디스크 만차")
    monkeypatch.setattr(db, "save_contact", _boom)
    r = client.post("/api/contact", data=_form())
    assert r.status_code == 500
    assert r.json()["ok"] is False
    assert "010-9796-9009" in r.json()["error"], "대안 연락처를 안 알려준다"


def test_메일은_단일_관문만_쓴다():
    """Railway는 Pro 미만에서 SMTP 발신을 차단한다(2026-08-11 실측 OSError 101).
    그래서 mailer가 Resend를 먼저 쓴다 — 여기서 smtplib를 직접 부르면 그 우회가 죽는다."""
    import ast
    import inspect
    src = inspect.getsource(main.api_contact)
    # 주석·독스트링에는 '왜 smtplib를 안 쓰는지'가 적혀 있다 — 코드만 본다(오탐 방지).
    tree = ast.parse(src.lstrip())
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]                     # 독스트링 제거
    code = ast.unparse(fn)
    assert "smtplib" not in code, "문의가 메일 관문을 우회해 직접 SMTP를 쓴다"
    assert "mailer" in code, "단일 메일 관문을 안 쓴다"


def test_받은_문의를_볼_화면이_있다():
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/admin/inquiries" in paths, "문의를 볼 화면이 없다 — 쌓여도 모른다"
    import inspect
    src = inspect.getsource(main.admin_inquiries)
    for k in ("새 문의", "연락함", "계약", "무산"):
        assert k in src, f"처리 상태 '{k}'가 없다"


def test_문의_화면이_인증_뒤에_있다():
    """고객 연락처가 모여 있는 화면이다 — 무인증 노출은 사고다."""
    import inspect
    src = inspect.getsource(main.admin_basic_auth)
    assert 'path.startswith("/admin")' in src
