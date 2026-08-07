"""writing_queue 죽은 잡 골든 — 2026-08-07 실사고 박제.

배포가 queue-gen 생성을 죽여 행이 'generating'으로 영구 고착됐다(qid 2599).
① /admin/busy가 queue-gen 생성을 못 봤다(gen_progress를 안 찍음 — busy 사각)
② writing_queue에 시간 기준 자동 해제가 없었다(아키텍처 원칙 위반).
"""
import inspect
from datetime import datetime, timedelta

from app import db

TID = "t-liveness-test"


def _enqueue(kw: str) -> int:
    assert db.enqueue_writing(TID, "vacant_q", kw, reason="골든", content_type="info")
    row = [r for r in db.writing_queue_rows(TID, limit=50) if r["target_keyword"] == kw][0]
    return row["id"]


def _set_claimed(qid: int, when: str):
    with db._conn() as c:
        c.execute("UPDATE writing_queue SET status='generating', claimed_at=? WHERE id=?", (when, qid))


def test_클레임이_심박을_찍는다():
    qid = _enqueue("심박 키워드")
    q = db.claim_writing(TID, only_id=qid)
    assert q and q["claimed_at"], "클레임에 claimed_at(심박)이 없다 — 죽으면 영구 고착"
    db.mark_writing(qid, "skipped")


def test_심박_없는_generating은_자동_해제된다():
    qid = _enqueue("고착 키워드")
    _set_claimed(qid, None)                            # 구버전 고착 재현(심박 없음)
    with db._conn() as c:
        c.execute("UPDATE writing_queue SET claimed_at=NULL WHERE id=?", (qid,))
    n = db.release_dead_claims(TID)
    assert n >= 1
    row = [r for r in db.writing_queue_rows(TID, limit=50) if r["id"] == qid][0]
    assert row["status"] == "pending", "죽은 잡이 회수되지 않았다"
    assert "자동 해제" in (row["reason"] or ""), "회수 사유가 없다 — 침묵 폴백 금지"
    db.mark_writing(qid, "skipped")


def test_오래된_심박도_자동_해제된다():
    qid = _enqueue("낡은심박 키워드")
    _set_claimed(qid, (datetime.utcnow() - timedelta(hours=2)).isoformat())
    db.release_dead_claims(TID)
    row = [r for r in db.writing_queue_rows(TID, limit=50) if r["id"] == qid][0]
    assert row["status"] == "pending", "심박 끊긴 잡이 회수되지 않았다"
    db.mark_writing(qid, "skipped")


def test_신선한_잡은_건드리지_않고_busy로_보인다():
    qid = _enqueue("진행중 키워드")
    _set_claimed(qid, datetime.utcnow().isoformat())
    db.release_dead_claims(TID)
    row = [r for r in db.writing_queue_rows(TID, limit=50) if r["id"] == qid][0]
    assert row["status"] == "generating", "진행 중인 잡을 죽은 잡으로 회수했다"
    st = db.stuck_generating()
    assert any(g["qid"] == qid for g in st["fresh"]), "진행 중 생성이 배포 차단(busy) 대상에 없다"
    db.mark_writing(qid, "skipped")


def test_유령은_stale로_분류된다():
    qid = _enqueue("유령 키워드")
    _set_claimed(qid, (datetime.utcnow() - timedelta(hours=3)).isoformat())
    st = db.stuck_generating()
    assert any(g["qid"] == qid for g in st["stale"]), "죽은 잡이 유령으로 분류되지 않았다"
    db.mark_writing(qid, "skipped")


def test_클레임_경로가_자동_해제를_거친다():
    """자가 치유는 별도 크론이 아니라 소비 경로 자체에 산다 — 안 부르면 없는 기능이다."""
    src = inspect.getsource(db.claim_writing)
    assert "release_dead_claims" in src


def test_busy_표면이_큐_생성을_실제로_본다():
    """대조는 '존재'가 아니라 '사용' 기준 — helper가 있어도 busy가 안 부르면 사각 그대로다."""
    import app.main as M
    src = inspect.getsource(M.admin_busy)
    assert "stuck_generating" in src, "/admin/busy가 queue-gen 생성을 안 본다(사각 재발)"
    assert "queue-gen" in src
