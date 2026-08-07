"""키워드 선점 골든 — 2026-08-07 실측 박제.

트랙 A로 잘못 나온 글을 삭제(묘비)한 뒤 같은 글감(qid 2599)을 재생성하려 하자
done 큐 행의 키워드 선점이 그대로 남아 '이미 같은 키워드 글 준비/발행됨'으로
영원히 skip됐다. 대조는 존재가 아니라 사용 — 글이 실재할 때만 선점이다.
"""
from types import SimpleNamespace

from app import db
from app.services.autoqueue import _existing_kw_set

TID = "t-dedupe-test"


def _done_row(kw: str, piece_id: str):
    assert db.enqueue_writing(TID, "P1", kw, reason="골든")
    row = [r for r in db.writing_queue_rows(TID, limit=50) if r["target_keyword"] == kw][0]
    db.mark_writing(row["id"], "done", piece_id=piece_id)


def test_삭제된_글의_done_행은_키워드를_선점하지_않는다():
    _done_row("지워진 글 키워드", "ghost-piece-없는-id")
    t = SimpleNamespace(id=TID)
    assert "지워진 글 키워드" not in _existing_kw_set(t), \
        "없는 글이 키워드를 선점한다 — 삭제 후 재생성이 영원히 막힌다"


def test_글이_실재하는_done_행은_선점을_유지한다():
    """선점 완화가 중복 방지 자체를 죽이면 안 된다 — piece_id 없는 done 행은 보수적으로 유지."""
    _done_row("피스없는 done 키워드", "")
    t = SimpleNamespace(id=TID)
    assert "피스없는 done 키워드" in _existing_kw_set(t)
