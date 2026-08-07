"""옛 사진 재사용 금지 골든 — 2026-08-07 사장님 지시 박제.

photo_pool 폴백이 과거 세트의 사진으로 새 글을 썼다 — 생성기는 사진 내용대로 쓰므로
주지 않은 사진이 글의 내용을 결정했고, 사장님은 그 사진을 첨부해야만 발행할 수 있는
글을 받았다. 새 글의 사진은 명시로 온 것만 쓴다. 허용 재사용은 진단·실측(admin)뿐.
"""
import inspect
from types import SimpleNamespace

from app import db
from app.services import autoqueue as AQ


def test_소비는_사진_미제공이면_옛사진이_있어도_멈춘다(monkeypatch):
    monkeypatch.setattr(AQ, "photo_pool", lambda t: ["/tmp/옛사진.jpg"])
    monkeypatch.setattr(AQ, "refill", lambda t, plan: None)
    r = AQ.consume(SimpleNamespace(id="t-reuse-ban"), files=None)
    assert r.get("need_photos") is True, f"옛 사진으로 글을 만들려 한다: {r}"


def test_생성_소비_경로에_photo_pool이_없다():
    """대조는 사용 기준 — 함수가 남아 있어도 생성 경로가 부르면 금지가 아니다."""
    for fn in (AQ.consume, AQ.slot_fill_all, AQ.state):
        assert "photo_pool(" not in inspect.getsource(fn), f"{fn.__name__}이 옛 사진을 재사용한다"
    from app.services import gapscout as GS
    assert "photo_pool(" not in inspect.getsource(GS._materials), "빈자리 재료 판정이 옛 사진을 재료로 센다"


def test_빈자리_재료판정은_옛사진을_재료로_세지_않는다():
    from app.services.gapscout import _materials
    m = _materials("t-reuse-ban-없는-tenant")
    assert "사진" in m["need"], "옛 사진 없이도 '사진 필요'를 안내해야 한다"


def test_홈_상태는_글감이_있고_준비글이_없으면_사진을_요청한다():
    tid = "t-reuse-ban-state"
    assert db.enqueue_writing(tid, "P1", "상태 골든 키워드", reason="골든")
    st = AQ.state(SimpleNamespace(id=tid))
    assert st["need_photos"] is True
