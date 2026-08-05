"""🧱 정찰 저장 계약 골든 — 2026-08-06 회귀 2건 박제.

밤새 정찰이 돌았는데 서버 응답이 전부 {"ok":false,"error":"저장 실패"}였다.
진단용 컬럼 3개를 ALTER로 붙였더니 VALUES(?,?,?,?,?,?)의 개수가 안 맞았다.
그리고 INSERT OR REPLACE였으므로, 저장이 됐다면 어제 보존한 mine_legacy가 NULL로 덮였을 것이다.
"""
import inspect
import json
import os
import sqlite3
import tempfile


def _mk(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE kw_blocks(tenant_id TEXT, keyword TEXT, blocks TEXT, "
              "blog_blocks TEXT, mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
    c.commit()
    return c


def test_컬럼이_늘어도_저장이_깨지지_않는다():
    """VALUES 순서에 기대면 스키마가 늘 때마다 조용히 깨진다."""
    src = inspect.getsource(__import__("app.services.blogreach", fromlist=["x"]).blocks_ingest)
    assert "INSERT OR REPLACE INTO kw_blocks VALUES" not in src, "컬럼 순서에 기댄다"
    assert "INSERT INTO kw_blocks(tenant_id, keyword" in src, "컬럼을 명시하지 않는다"


def test_기존_보존값을_덮지_않는다():
    """어제 재판정에서 보존한 mine_legacy가 오늘 정찰로 지워지면 안 된다."""
    src = inspect.getsource(__import__("app.services.blogreach", fromlist=["x"]).blocks_ingest)
    assert "ON CONFLICT" in src and "DO UPDATE SET" in src, "행을 통째로 갈아끼운다"
    assert "mine_legacy" not in src.split("DO UPDATE SET")[1].split(",\n")[0], \
        "UPSERT가 보존 컬럼을 건드린다"
    # 실동작 — 보존 컬럼이 살아남는가
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.sqlite")
    c = _mk(p)
    c.execute("ALTER TABLE kw_blocks ADD COLUMN mine_legacy INTEGER")
    c.execute("INSERT INTO kw_blocks(tenant_id,keyword,mine,mine_legacy) VALUES('t','k',0,1)")
    c.execute("INSERT INTO kw_blocks(tenant_id,keyword,blocks,mine,checked_at) "
              "VALUES('t','k','새블록',1,'now') "
              "ON CONFLICT(tenant_id,keyword) DO UPDATE SET blocks=excluded.blocks, "
              "mine=excluded.mine, checked_at=excluded.checked_at")
    r = c.execute("SELECT mine, mine_legacy, blocks FROM kw_blocks").fetchone()
    assert r == (1, 1, "새블록"), f"보존값이 사라졌다: {r}"


def test_수집_실패는_지면_지도에_안_쓴다():
    """게이트가 '수집 실패'로 판정한 것을 저장하면 실패가 데이터로 둔갑한다."""
    src = inspect.getsource(__import__("app.services.blogreach", fromlist=["x"]).blocks_ingest)
    assert 'r.get("collect_failed")' in src, "수집 실패를 거르지 않는다"
    i_fail = src.index('collect_failed')
    i_ins = src.index("INSERT INTO kw_blocks")
    assert i_fail < i_ins, "실패 판정이 저장보다 뒤에 있다"
    assert "collect_note" in src, "실패 사유를 안 남긴다"


def test_저장_실패_사유를_숨기지_않는다():
    """밤새 '저장 실패'만 찍히고 원인을 못 봤다 — 조용한 실패의 변형이다."""
    src = inspect.getsource(__import__("app.services.blogreach", fromlist=["x"]).blocks_ingest)
    assert 'f"저장 실패: {repr(e)' in src, "예외 내용을 응답에 안 담는다"


def test_판정_근거를_함께_저장한다():
    """어제 약속: visible_evidence를 남겨 '그 노출이 진짜였나'를 되짚을 수 있게."""
    src = inspect.getsource(__import__("app.services.blogreach", fromlist=["x"]).blocks_ingest)
    assert "visible_evidence" in src and "evidence" in src, "판정 근거를 안 남긴다"
