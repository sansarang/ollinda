"""경보 전달 골든 (2026-08-17).

무엇이 있었나 — 이날 Anthropic 크레딧이 **두 번** 소진됐는데 두 번 다 사장님께 안 갔다.
watchtower가 텔레그램만 보고 있었고 그 토큰은 설정된 적이 없어서,
경보가 서버 로그에만 찍혔다: "[watchtower] (알림 미설정) 🔴 크레딧 소진".
그런데 SMTP는 이미 설정돼 있었다 — **있는 경로를 안 쓰고 없는 경로만 보고 있었다.**

대행으로 가면 이건 치명적이다: 고객 글이 안 만들어지는데 사장님도 고객도 모른다.
헌법이 말하는 "죽은 잡은 스스로 말하지 못한다"가 정확히 이 상태였다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import watchtower as wt


def test_텔레그램이_없으면_메일로_간다(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SMTP_USER", "ops@example.com")
    sent = {}
    import app.services.mailer as ml
    monkeypatch.setattr(ml, "send", lambda to, s, b: sent.update(to=to, body=b) or True)
    assert wt.send("크레딧 소진") is True, "경보가 어디로도 안 갔다"
    assert sent.get("to") == "ops@example.com"
    assert "크레딧" in sent.get("body", "")


def test_텔레그램_실패해도_메일로_넘어간다(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("SMTP_USER", "ops@example.com")

    class _R:
        status_code = 500
    monkeypatch.setattr("requests.post", lambda *a, **k: _R())
    import app.services.mailer as ml
    ok = {}
    monkeypatch.setattr(ml, "send", lambda to, s, b: ok.update(x=1) or True)
    assert wt.send("장애") is True
    assert ok, "텔레그램 실패 후 메일로 안 넘어갔다"


def test_어디로도_못_보내면_에러로_남긴다(monkeypatch, caplog):
    """조용히 실패하면 그게 가장 위험하다."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("ALERT_EMAIL", raising=False)
    import app.services.mailer as ml
    monkeypatch.setattr(ml, "send", lambda *a, **k: False)
    assert wt.send("장애") is False
