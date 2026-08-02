"""
노출 판정 정직성 골든 테스트(2026-08-02 실사고 박제).

실사고: 주안모터스는 숏텐츠를 만든 적이 없는데 대시보드가 "'부산 기장 중고차판매'
숏텐츠 NOW, 네이버 클립에 보이는 중"으로 표시했다. 원인 두 가지:
  ① 블록 귀속을 'DOM 문서순서상 앞선 마지막 제목'으로 추정 — 정확도 미검증
  ② 우리 링크가 없는 블록명까지 표시에 섞임(네이버 클립엔 타사 blog=no1motorss뿐이었다)

여기서 못 박는 두 가지:
  a. 타사 콘텐츠만 있으면 자사 노출 판정은 0이어야 한다(자사 식별자 매칭만 인정).
  b. 블록 귀속이 검증되기 전에는 화면에 블록명을 쓰면 안 된다('첫 화면'까지만).
"""
from __future__ import annotations

import sqlite3

from app import db
from app.services import exposure


def _seed(tenant_id: str, keyword: str, blocks: str, blog_blocks: str, mine: int) -> None:
    with db._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                  "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                  "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
        c.execute("INSERT OR REPLACE INTO kw_blocks VALUES(?,?,?,?,?,?)",
                  (tenant_id, keyword, blocks, blog_blocks, mine, "2026-08-02T00:00:00"))


def _clean(tenant_id: str) -> None:
    try:
        with db._conn() as c:
            c.execute("DELETE FROM kw_blocks WHERE tenant_id=?", (tenant_id,))
    except sqlite3.OperationalError:
        pass


def test_others_content_never_counts_as_ours(monkeypatch):
    """a. 남의 콘텐츠만 있는 블록 → 자사 노출 0.
    mine=0(자사 식별자 매칭 실패)이면 블록이 아무리 많아도 '보이는 중'이 나오면 안 된다."""
    tid = "T_EXPOSURE_A"
    monkeypatch.setattr(db, "get_tenant", lambda _t: type("X", (), {"id": tid, "name": "테스트가게"})())
    monkeypatch.setattr(db, "tracked_keywords", lambda *_a, **_k: [])
    _clean(tid)
    try:
        # 타사 글만 실린 판(블로그 지면은 존재) — 우리 식별자 매칭은 실패
        _seed(tid, "테스트 검색어", "숏텐츠 NOW|네이버 클립|플레이스 MY", "숏텐츠 NOW|네이버 클립", 0)
        se = exposure.summary(tid)["surfaces"]["search"]
        assert se["shown"] == [], f"타사 콘텐츠를 자사 노출로 오인: {se['shown']}"
        assert se["state"] != "shown"
        assert "테스트 검색어" in se["waiting"]          # 자리는 있으나 우리는 아직
    finally:
        _clean(tid)


def test_block_name_not_exposed_until_verified(monkeypatch):
    """b. 블록 귀속 미검증 → 화면 문구에 블록명이 들어가면 실패.
    노출 판정이 참이어도 '어느 블록인지'는 검증 전까지 주장하지 않는다."""
    tid = "T_EXPOSURE_B"
    monkeypatch.setattr(db, "get_tenant", lambda _t: type("X", (), {"id": tid, "name": "테스트가게"})())
    monkeypatch.setattr(db, "tracked_keywords", lambda *_a, **_k: [])
    _clean(tid)
    try:
        _seed(tid, "테스트 검색어", "숏텐츠 NOW|네이버 클립", "숏텐츠 NOW", 1)
        shown = exposure.summary(tid)["surfaces"]["search"]["shown"]
        assert shown and shown[0]["keyword"] == "테스트 검색어"
        assert shown[0]["where"] == "첫 화면", f"블록명이 표시값에 새어나감: {shown[0]['where']}"
        # raw 추정은 계속 보관해야 한다(복원 대비) — 표시와 기록을 분리한다
        assert shown[0].get("blocks_guess"), "블록 추정 기록이 사라짐(복원 불가)"
    finally:
        _clean(tid)
