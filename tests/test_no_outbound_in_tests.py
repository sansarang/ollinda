"""테스트가 바깥 세상에 닿지 않는다 — 2026-08-19 실사고 박제.

사고: 생성 실측을 하려고 railway env를 source한 셸에서 골든 전체를 돌렸다.
  `tests/test_inquiry.py::test_경보가_터져도_문의는_저장된다`는 watchtower만 대역으로
  바꾸고 mailer는 진짜를 썼다. RESEND_API_KEY가 셸에 있었으므로 **진짜 메일이 나갔다.**
  사장님 받은편지함에 '[올린다 문의] 경보죽음테스트'가 도착했다 — 두 번(오후 2:00, 3:43).

  conftest에는 "외부 키는 모두 미설정"이라고 **주석만** 있었다. 가정을 코드가 지키지 않았다.

★ 케이스마다 mailer를 monkeypatch하는 것으로는 못 막는다 — 새 테스트가 하나만 빠뜨려도
  다시 나간다. **입구(conftest)에서 자격증명을 지운다.**
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

OUTBOUND = ("RESEND_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASS",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SLACK_WEBHOOK_URL",
            "ALERT_EMAIL", "SHOPCAST_OWNER_EMAILS")


def test_발신_자격증명이_테스트에_남아있지_않다():
    """★ 이 파일의 존재 이유. 하나라도 살아 있으면 골든이 실물을 건드린다."""
    live = [k for k in OUTBOUND if os.environ.get(k)]
    assert not live, f"발신 자격증명이 살아 있다: {live} — 골든이 진짜로 보낸다"


def test_메일러가_보낼_수_없는_상태다():
    from app.services import mailer
    assert mailer.configured() is False, "테스트에서 메일러가 발신 가능 상태다"
    assert mailer.send("x@example.com", "제목", "본문") is False, "테스트가 메일을 보냈다"


def test_경보도_보낼_수_없는_상태다():
    from app.services import watchtower
    assert watchtower.configured() is False, "테스트에서 경보가 발신 가능 상태다"


def test_conftest가_주석이_아니라_코드로_막는다():
    """주석은 지켜지지 않는다 — 실제로 pop 하는 코드가 있어야 한다."""
    src = open("tests/conftest.py").read()
    assert "os.environ.pop" in src, "conftest가 자격증명을 지우지 않는다"
    for k in ("RESEND_API_KEY", "TELEGRAM_BOT_TOKEN", "SMTP_HOST"):
        assert k in src, f"{k}를 안 지운다"
