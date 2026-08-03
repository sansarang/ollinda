"""
가게 이관 원자성 박제(2026-08-03 사고 봉인).

사고: DB의 tenant_id만 옮기고 미디어 파일을 안 옮겼다. 사장님 화면의 사진이 전부 깨졌고
나는 '이관 완료'라고 보고했다. 원인은 절차가 둘로 나뉘어 있었던 것 —
"DB 따로, 미디어 따로"면 언젠가 한쪽만 하게 된다.

이 테스트는 그 반쪽 이관이 통과하지 못하게 만든다.
"""
from __future__ import annotations

import os
import uuid

from app import db
from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus
from app.services import tenant_move as tm


def _make_tenant_with_media(tmp_root, n_photos=3):
    t = db.create_tenant(f"이관검증-{uuid.uuid4().hex[:6]}", "썬팅")
    d = os.path.join(tmp_root, t.id)
    os.makedirs(d, exist_ok=True)
    paths = []
    for i in range(n_photos):
        fp = os.path.join(d, f"p{i}.jpg")
        open(fp, "wb").write(b"x")
        paths.append(fp)
    aid = str(uuid.uuid4())
    db.save_piece(ContentPiece(id=str(uuid.uuid4()), tenant_id=t.id, asset_id=aid,
                               channel=Channel.NAVER_BLOG, kind=ContentKind.BLOG,
                               payload={"body": "본문", "image_paths": paths},
                               status=ContentStatus.DRAFT))
    return t, aid, paths


def _cleanup(*tids):
    with db._conn() as c:
        for tid in tids:
            for tbl in tm.tables_with_tenant():
                try:
                    c.execute(f"DELETE FROM {tbl} WHERE tenant_id=?", (tid,))
                except Exception:
                    pass
            c.execute("DELETE FROM tenants WHERE id=?", (tid,))


def test_migration_moves_db_and_media_together(tmp_path, monkeypatch):
    """A. 이관은 DB와 미디어를 함께 옮긴다 — 한쪽만 옮기면 사진이 깨진다."""
    monkeypatch.setenv("SHOPCAST_STORAGE", str(tmp_path))
    src, aid, paths = _make_tenant_with_media(str(tmp_path))
    dst = db.create_tenant("받는가게", "썬팅")
    try:
        r = tm.migrate_tenant(src.id, dst.id, dry=False)
        assert r["ok"], r.get("errors")
        assert r["files_moved"] == len(paths), "미디어가 안 옮겨졌다"
        assert r["db_rows"] >= 1, "DB가 안 옮겨졌다"
        # 완결 대조가 함께 나와야 한다 — '했다'가 아니라 표가 완료의 정의다
        v = r["verify"]
        assert v["ok"], f"이관 미완결: {v}"
        assert v["left_files"] == 0 and not v["left_db"] and v["stale_paths"] == 0
        assert v["photos_missing"] == 0, "새 가게에서 사진을 못 찾는다"
        # payload 경로가 새 폴더를 가리켜야 한다
        pcs = db.get_set_pieces(aid)
        newp = (pcs[0].payload or {}).get("image_paths") or []
        assert newp and all(dst.id in x for x in newp), "경로가 옛 가게를 가리킨다"
        assert all(os.path.exists(x) for x in newp), "가리키는 파일이 없다"
    finally:
        _cleanup(src.id, dst.id)


def test_db_only_migration_fails_verification(tmp_path, monkeypatch):
    """B. ★ 이 사고를 그대로 재현한다 — DB만 옮기면 완결 대조가 반드시 실패해야 한다.
    오늘은 이 대조가 없어서 '완료'라고 보고했다."""
    monkeypatch.setenv("SHOPCAST_STORAGE", str(tmp_path))
    src, aid, paths = _make_tenant_with_media(str(tmp_path))
    dst = db.create_tenant("받는가게2", "썬팅")
    try:
        with db._conn() as c:                       # 옛 사고 재현: DB만 손으로 옮긴다
            c.execute("UPDATE content_pieces SET tenant_id=? WHERE tenant_id=?", (dst.id, src.id))
        v = tm.verify(src.id, dst.id)
        assert not v["ok"], "반쪽 이관이 통과했다 — 오늘 사고가 그대로 재발한다"
        assert v["left_files"] == len(paths), "옛 폴더 잔존물을 못 본다"
        assert v["stale_paths"] >= 1, "payload에 남은 옛 경로를 못 본다"
    finally:
        _cleanup(src.id, dst.id)


def test_table_list_comes_from_schema():
    """C. 옮길 표 목록을 손으로 적으면 반드시 빠뜨린다 — 스키마에서 읽는다."""
    import inspect
    src = inspect.getsource(tm.tables_with_tenant)
    assert "sqlite_master" in src and "PRAGMA table_info" in src
    with db._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS _tm_probe(tenant_id TEXT, v TEXT)")
    try:
        assert "_tm_probe" in tm.tables_with_tenant(), "새 표를 못 잡는다"
    finally:
        with db._conn() as c:
            c.execute("DROP TABLE IF EXISTS _tm_probe")


def test_migration_is_dry_by_default_and_single_path():
    """D. 기본은 미리보기. 그리고 이관 경로는 이 함수 하나뿐이어야 한다(수동 작업 금지)."""
    import inspect
    assert inspect.signature(tm.migrate_tenant).parameters["dry"].default is True
    from app import main as _m
    src = inspect.getsource(_m.admin_migrate_tenant)
    assert "tenant_move" in src, "엔드포인트가 함수를 안 쓴다"
    assert "UPDATE" not in src, "엔드포인트가 직접 SQL을 돌린다(이관 경로가 둘이 된다)"
    assert not hasattr(_m, "admin_migrate_media"), "수동 미디어 이관 경로가 남아 있다"
