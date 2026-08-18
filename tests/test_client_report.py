"""📬 성과 보고 골든 — 대행에서 리포트는 고객이 돈을 내는 이유다.

2026-08-18 실측: 주간 리포트가 **8건 만들어졌는데 발송 0건**이었다. 원인이 둘이었다:
  ① `send_all()`이 **가입자(owner)의 이메일**을 찾았다. 대행 가게는 가입하지 않으므로
     owner가 없다 → 주소가 늘 빈 문자열
  ② `_send_email`이 SMTP_HOST를 요구했다. 그런데 Railway는 Pro 미만에서 SMTP 발신을
     차단한다(2026-08-11 실측 OSError 101) — 그래서 그 값이 비어 있는 게 정상이고,
     이 함수는 **항상 False를 반환했다**

★ 이 골든이 지키는 것: **보낼 곳이 있으면 반드시 보낸다. 없으면 없다고 말한다.**
"""
import os

import pytest

os.environ.setdefault("SHOPCAST_SECRET", "test")
os.environ.setdefault("SHOPCAST_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SHOPCAST_DB", "/tmp/test_client_report.sqlite")

from app import db  # noqa: E402
from app.services import weekly_report as wr  # noqa: E402

db.init_db()


@pytest.fixture
def shop():
    t = db.create_tenant("리포트가게", "썬팅", "부산")
    return db.get_tenant(t.id)


def test_고객_연락처가_가입계정보다_먼저다(shop, monkeypatch):
    """★ 대행 고객은 가입하지 않는다 — owner를 먼저 보면 영원히 못 보낸다."""
    db.update_client_contact(shop.id, "client@shop.com", "김사장")
    sent_to = []
    monkeypatch.setattr(wr, "_send_email", lambda to, s, b: sent_to.append(to) or True)
    monkeypatch.setattr(db, "list_tenants_with_blog", lambda: [db.get_tenant(shop.id)])
    monkeypatch.setattr(wr, "build_report", lambda t: {"week": "2026-W34", "tenant_id": t.id})
    monkeypatch.setattr(wr, "_email_body", lambda rep: "본문")
    wr.send_all()
    assert sent_to == ["client@shop.com"], f"고객 주소로 안 갔다: {sent_to}"


def test_보낼_곳이_없으면_없다고_말한다(shop, monkeypatch, caplog):
    """침묵 폴백 금지 — 조용히 건너뛰면 '리포트가 나가는 줄' 알게 된다(실제로 그랬다)."""
    import logging
    monkeypatch.setattr(db, "list_tenants_with_blog", lambda: [db.get_tenant(shop.id)])
    monkeypatch.setattr(wr, "build_report", lambda t: {"week": "2026-W34", "tenant_id": t.id})
    monkeypatch.setattr(wr, "_email_body", lambda rep: "본문")
    monkeypatch.setattr(db, "get_user_by_tenant", lambda tid: None)
    with caplog.at_level(logging.WARNING):
        wr.send_all()
    assert any("보낼 주소 없음" in r.message for r in caplog.records), \
        "주소가 없는데 아무 말도 안 했다"


def test_가짜_소셜주소로는_보내지_않는다(shop, monkeypatch):
    """소셜 가입이 만들던 k_xxx@ollinda.guest 는 실재하지 않는 주소다."""
    sent_to = []
    monkeypatch.setattr(wr, "_send_email", lambda to, s, b: sent_to.append(to) or True)
    monkeypatch.setattr(db, "list_tenants_with_blog", lambda: [db.get_tenant(shop.id)])
    monkeypatch.setattr(wr, "build_report", lambda t: {"week": "2026-W34", "tenant_id": t.id})
    monkeypatch.setattr(wr, "_email_body", lambda rep: "본문")
    monkeypatch.setattr(db, "get_user_by_tenant", lambda tid: {"email": "k_abc@ollinda.guest"})
    wr.send_all()
    assert sent_to == [], f"실재하지 않는 주소로 보냈다: {sent_to}"


def test_메일은_단일_관문만_쓴다():
    """Railway가 SMTP를 막기 때문에 mailer가 Resend를 먼저 쓴다.
    여기서 smtplib를 직접 부르면 그 우회가 죽고 발송이 0이 된다(실제로 그랬다)."""
    import ast
    import inspect
    src = inspect.getsource(wr._send_email)
    tree = ast.parse(src.lstrip())
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    code = ast.unparse(fn)
    assert "smtplib" not in code, "리포트가 메일 관문을 우회한다"
    assert "mailer" in code, "단일 메일 관문을 안 쓴다"


def test_리포트_본문에_죽은_링크가_없다():
    """고객은 우리 화면에 로그인하지 않는다 — 로그인이 필요한 링크는 죽은 링크다."""
    import inspect
    src = inspect.getsource(wr._email_body)
    assert "/me?tab=report" not in src, "사라진 탭으로 보내는 링크가 남아 있다"
    assert "/me" not in src.replace("ollinda.kr", ""), "로그인이 필요한 화면으로 안내한다"


def test_연락처_입력칸이_화면에_있다():
    """저장 함수만 있고 넣을 칸이 없으면 영영 비어 있다 — 그게 발송 0건의 원인이었다."""
    import inspect
    from app import main
    card = inspect.getsource(main._store_info_card)
    assert "client_email" in card, "리포트 받을 주소를 넣을 칸이 없다"
    save = inspect.getsource(main.my_store_info)
    assert "update_client_contact" in save, "입력칸은 있는데 저장하지 않는다"
