"""
되돌릴 수 없는 운영 도구의 안전장치 박제(2026-08-03).

이관·정리는 실행하면 끝이다. 실수 한 번이 사장님 데이터를 지운다.
그래서 이 도구들은 ①기본이 미리보기 ②남길 목록이 비면 거부 ③테이블 목록을 손으로 안 적는다.
"""
from __future__ import annotations

import inspect

from app import main as m


def test_destructive_tools_are_dry_by_default():
    """A. 기본이 실행이면 사고가 조용히 난다 — 둘 다 dry=1이 기본이어야 한다."""
    for fn in (m.admin_migrate_tenant, m.admin_purge_except):
        assert inspect.signature(fn).parameters["dry"].default == 1, f"{fn.__name__} 기본이 실행"


def test_purge_refuses_empty_keep_list():
    """B. 남길 목록이 비면 전체 삭제가 된다 — 거부해야 한다."""
    r = m.admin_purge_except(keep_email="", keep_tenants="", dry=1)
    assert r.status_code == 400, "빈 목록으로도 지울 수 있다"
    r2 = m.admin_purge_except(keep_email="a@b.c", keep_tenants="", dry=1)
    assert r2.status_code == 400, "가게 목록 없이 지울 수 있다"


def test_purge_verifies_keep_targets_exist():
    """C. 남길 가게 id를 잘못 적으면 '남길 게 없는' 상태로 전체가 지워진다 — 실재를 확인한다."""
    src = inspect.getsource(m.admin_purge_except)
    assert "db.get_tenant(tid)" in src, "남길 가게의 실재를 확인하지 않는다"
    assert "실재하지 않음" in src


def test_table_list_is_introspected_not_handwritten():
    """D. 테이블 목록을 손으로 적으면 반드시 빠뜨린다 — 스키마에서 읽는다."""
    src = inspect.getsource(m._tables_with)
    assert "sqlite_master" in src and "PRAGMA table_info" in src
    tabs = m._tables_with("tenant_id")
    for must in ("content_pieces", "assets", "writing_queue"):
        assert must in tabs, f"{must}가 이관·정리 대상에서 빠진다"
    # ★ 나중에 생기는 테이블도 자동으로 잡혀야 한다(손목록이면 못 잡는다)
    from app import db as _db
    with _db._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS _t_probe(tenant_id TEXT, v TEXT)")
    try:
        assert "_t_probe" in m._tables_with("tenant_id"), "새 테이블을 못 잡는다"
    finally:
        with _db._conn() as c:
            c.execute("DROP TABLE IF EXISTS _t_probe")


def test_migrate_requires_two_real_tenants():
    """E. 같은 가게로 옮기거나 없는 가게로 옮기면 데이터가 사라진다."""
    r = m.admin_migrate_tenant(src="X", dst="X", dry=1)
    assert r.status_code == 400
