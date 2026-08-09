"""발행 기록 키워드 생존 골든 — 2026-08-09 주안모터스 실사고 박제.

사고: 올린다 생성 글 2건이 RSS 매칭으로 발행 기록될 때 target_kw 없이 저장됐고(폴백은
piece payload), 이후 세트 정리로 piece가 삭제되자 keyword 원천이 증발 → readview keyword
빈칸 → gowatch가 매일 "keyword 없음 — 수집 스킵"(순위 추적 영구 중단).

계약: ① 발행 확인 시점에 keyword를 blog_publishes.target_kw에 박제한다(생성 경로와
참조 경로는 같은 파서 db.piece_target_kw 하나). ② piece가 삭제돼도 readview keyword는
생존한다. ③ 이미 뚫린 행은 backfill_publish_kw가 실물 제목 기반으로만 복원한다(날조 금지).
"""
import uuid

from app import db
from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus


def _mk_tenant():
    return db.create_tenant(f"kw생존-{uuid.uuid4().hex[:6]}", "중고차판매", region="부산 기장")


def _mk_blog_piece(t, kw: str) -> ContentPiece:
    p = ContentPiece(id=str(uuid.uuid4()), tenant_id=t.id, asset_id=str(uuid.uuid4()),
                     channel=Channel.NAVER_BLOG, kind=ContentKind.BLOG,
                     payload={"title": f"{kw} 안내", "body": "본문", "target_keywords": [kw]},
                     status=ContentStatus.DRAFT)
    return db.save_piece(p)


def _cleanup(t):
    with db._conn() as c:
        c.execute("DELETE FROM blog_publishes WHERE tenant_id=?", (t.id,))
        c.execute("DELETE FROM content_pieces WHERE tenant_id=?", (t.id,))
        c.execute("DELETE FROM tenants WHERE id=?", (t.id,))


def test_piece_target_kw_single_parser():
    """파서 단일화 — dict·JSON 문자열 모두 같은 답, 없으면 빈칸(추정 금지)."""
    assert db.piece_target_kw({"target_keywords": ["부산 기장 중고차"]}) == "부산 기장 중고차"
    assert db.piece_target_kw('{"target_keywords": ["부산 기장 중고차"]}') == "부산 기장 중고차"
    assert db.piece_target_kw({}) == ""
    assert db.piece_target_kw(None) == ""
    assert db.piece_target_kw("깨진 json{") == ""


def test_confirm_publish_stamps_kw_and_survives_piece_delete():
    """발행 확인이 target_kw를 박제하고, 세트 삭제 후에도 readview keyword가 산다."""
    from app.services import pipesync
    t = _mk_tenant()
    try:
        kw = "부산 기장 중고차판매"
        piece = _mk_blog_piece(t, kw)
        pipesync.confirm_publish(t, piece, "https://blog.naver.com/x/1234567890", "rss", 0.9,
                                 post_title=f"{kw} 그랜저", published_at="2026-08-09T00:00:00")
        pub = db.get_blog_publish(piece.id)
        assert pub and (pub.get("target_kw") or "") == kw, "발행 기록에 keyword가 박제되지 않았다"
        # piece 삭제(발행 기록은 남는 delete_set 의도 동작) 후에도 keyword 생존
        db.delete_set(piece.asset_id, t.id)
        assert db.get_piece(piece.id) is None
        view = {r["publish_id"]: r for r in db.published_posts_view()}
        assert view[piece.id]["keyword"] == kw, "piece 삭제로 readview keyword가 증발했다"
    finally:
        _cleanup(t)


def test_backfill_fills_only_empty_and_stays_honest(monkeypatch):
    """백필 — 빈 target_kw만 실물 제목 기반으로 채우고, 원천 없으면 빈칸+사유(날조 금지)."""
    from app.services import pipesync
    monkeypatch.setattr(pipesync, "_rss_meta_for_url", lambda t, url: {})   # 실네트워크 차단
    t = _mk_tenant()
    try:
        # 사고 재현: piece 없는 발행 기록, target_kw 빈 값, post_title은 실물 제목
        pid_titled = str(uuid.uuid4())
        db.record_blog_publish(t.id, pid_titled, "https://blog.naver.com/x/111",
                               matched_by="rss", post_title="부산 기장 중고차판매 그랜저 IG, 서류·실물")
        pid_bare = str(uuid.uuid4())    # 제목마저 없는 행 — 채울 근거가 없다
        db.record_blog_publish(t.id, pid_bare, "https://blog.naver.com/x/222", matched_by="rss")
        # 이미 값이 있는 행은 백필 대상이 아니다
        pid_kept = str(uuid.uuid4())
        db.record_blog_publish(t.id, pid_kept, "https://blog.naver.com/x/333",
                               matched_by="rss", target_kw="기존 키워드")

        dry = pipesync.backfill_publish_kw(t, dry=True)
        assert {r["piece_id"] for r in dry["rows"]} == {pid_titled, pid_bare}
        assert (db.get_blog_publish(pid_titled) or {}).get("target_kw") in ("", None), \
            "dry-run이 DB를 썼다"

        res = pipesync.backfill_publish_kw(t, dry=False)
        by_id = {r["piece_id"]: r for r in res["rows"]}
        assert by_id[pid_titled]["after"] == "부산 기장 중고차판매"      # extract_kw(외부 글과 같은 함수)
        assert by_id[pid_titled]["written"] is True
        assert (db.get_blog_publish(pid_titled) or {}).get("target_kw") == "부산 기장 중고차판매"
        assert by_id[pid_bare]["after"] == "" and by_id[pid_bare].get("skipped"), \
            "원천 없는 행을 조용히 채우거나 사유 없이 넘겼다"
        assert (db.get_blog_publish(pid_kept) or {}).get("target_kw") == "기존 키워드"
    finally:
        _cleanup(t)
