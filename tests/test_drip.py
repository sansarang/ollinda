"""리드·미전환 자동 이메일 드립 골든(마케팅 F, 2026-08-11).
계약: 광고표기·수신거부 필수(정보통신망법), 단계 간격 준수, 수신거부자 제외, mailer 미설정 시 0건.
"""
import uuid

import pytest

from app import db


@pytest.fixture
def _mailer(monkeypatch):
    import app.services.mailer as m
    box = []
    monkeypatch.setattr(m, "configured", lambda: True)
    monkeypatch.setattr(m, "send", lambda to, s, b: box.append((to, s, b)) or True)
    return box


def test_drip_sends_with_ad_label_and_unsub(_mailer):
    from app.services import drip
    em = f"drip-{uuid.uuid4().hex[:8]}@t.kr"
    db.save_landing_lead(em, "local|부산|카페")
    r = drip.run(limit=100)
    assert r["ok"] and r["sent"] >= 1
    mine = [x for x in _mailer if x[0] == em]
    assert mine, "리드에게 발송 안 됨"
    _, subj, body = mine[0]
    assert subj.startswith("(광고)"), "광고 표기 누락(정보통신망법 위반)"
    assert "/u/unsub?e=" in body, "수신거부 링크 누락(법 위반)"


def test_drip_respects_interval(_mailer):
    from app.services import drip
    em = f"drip2-{uuid.uuid4().hex[:8]}@t.kr"
    db.save_landing_lead(em, "x")
    drip.run(limit=100)
    n_after_first = len([x for x in _mailer if x[0] == em])
    drip.run(limit=100)                         # 바로 재실행 — 간격 때문에 재발송 없어야
    assert len([x for x in _mailer if x[0] == em]) == n_after_first, "간격 무시하고 재발송"


def test_unsubscribe_excludes(_mailer):
    from app.services import drip
    em = f"drip3-{uuid.uuid4().hex[:8]}@t.kr"
    db.save_landing_lead(em, "x")
    assert drip.unsub_ok(em, drip.unsub_token(em)), "정상 토큰 거부"
    assert not drip.unsub_ok(em, "wrong"), "위조 토큰 통과"
    db.drip_unsub(em)
    before = len(_mailer)
    drip.run(limit=100)
    assert all(x[0] != em for x in _mailer[before:]), "수신거부자에게 발송됨"


def test_mailer_off_sends_nothing(monkeypatch):
    import app.services.mailer as m
    monkeypatch.setattr(m, "configured", lambda: False)
    from app.services import drip
    r = drip.run(limit=100)
    assert r.get("sent", 0) == 0, "mailer 꺼졌는데 발송 시도"
