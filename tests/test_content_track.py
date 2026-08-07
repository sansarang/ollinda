"""글감 트랙 판정 단일 관문 골든 — 2026-08-07 같은 계열 결함 2회 박제.

①enqueue가 기본값 'sell'로 넣어 빈자리 글감으로 시공기가 나왔다(c4276ea).
②행 데이터 마이그레이션(retrack)이 status='pending'만 고쳐 'generating' 행을 놓쳤고,
  자동 해제로 살아난 그 행이 또 sell(트랙 A)로 소비됐다 — GEO 게이트가 아예 안 돌았다.
행에 박힌 값을 고치는 방식은 놓친 행마다 재발한다 — 적재·소비가 같은 함수로 판정한다.
"""
import inspect

from app import db


def test_빈자리_글감은_행_값과_무관하게_트랙B다():
    assert db.content_track("vacant_q", "sell") == "info", "옛 sell 행이 또 시공기로 간다"
    assert db.content_track("vacant_q", "") == "info"
    assert db.content_track("vacant_q", "info") == "info"


def test_다른_출처는_행_값을_따른다():
    assert db.content_track("P1", "sell") == "sell"
    assert db.content_track("P1", "") == "sell"
    assert db.content_track("P1", "info") == "info"


def test_적재와_소비가_같은_관문을_쓴다():
    """규칙이 두 곳에 살면 그 자체가 결함이다 — 존재가 아니라 사용을 문다."""
    from app.services import autoqueue as AQ
    assert "content_track" in inspect.getsource(AQ.consume), "소비가 관문을 안 거친다(재발 예약)"
    assert "content_track" in inspect.getsource(db.enqueue_writing), "적재가 관문을 안 거친다"


def test_옛_sell_행도_소비되면_트랙B로_간다():
    """실사고 재현 — sell로 박힌 vacant_q 행을 적재해도 판정은 info여야 한다."""
    tid = "t-track-test"
    assert db.enqueue_writing(tid, "vacant_q", "옛행 키워드", reason="골든")
    with db._conn() as c:                              # 관문 도입 전에 적재된 옛 행 재현
        c.execute("UPDATE writing_queue SET content_type='sell' WHERE tenant_id=?", (tid,))
    q = db.claim_writing(tid)
    assert q and q["content_type"] == "sell"          # 행 데이터는 여전히 sell(마이그레이션 없음)
    assert db.content_track(q["source_type"], q["content_type"]) == "info"
    db.mark_writing(q["id"], "skipped")
