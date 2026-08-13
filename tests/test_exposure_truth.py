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


def test_miss_label_says_out_of_top5_not_missing(monkeypatch):
    """'미노출'은 아예 없는 것처럼 오해된다 — 실제로는 5위까지만 스캔한 결과다(2026-08-12 지적).
    정직 원칙: 측정 범위를 그대로 말한다.

    ★ 2026-08-14 계약 정밀화 — 이 규칙은 '순위를 말할 때'의 규칙이다. 블로그 글이 없어
      순위 주장 자체를 하지 않는 경우까지 '5위' 표기를 강제하면, 없는 측정을 있는 것처럼
      말하게 된다(사장님 지적: 글을 안 쓴 사람에게 순위는 의미가 없다).
      그래서 '내 글을 찾은 경우'로 조건을 좁히고, 못 찾은 경우는 별도 계약으로 검증한다.
    """
    from app.services import diagnose
    import app.services.blogrank as br
    import app.services.place as pl
    monkeypatch.setattr(br, "find_blog_by_name", lambda n, limit=20: {"blog_id": "myblog", "blog_name": n})
    monkeypatch.setattr(br, "blog_rank", lambda kw, bid, limit=5: {"rank": 0, "url": "", "post_title": "", "checked": 5})
    monkeypatch.setattr(pl, "search", lambda kw, limit=5: [{"name": "남의가게", "address": "서울"}])
    r = diagnose.diagnose_rank("썬팅", "부산 동구", "내가게")
    assert r.get("miss_label") == "상위 5위 밖", "측정 범위를 숨긴 라벨(미노출) 회귀"
    assert "미노출" not in r["subline"], "본문이 여전히 '미노출'로 단정"
    assert "5위" in r["subline"] or "5위" in r["headline"], "범위 표기 사라짐"


def test_no_blog_means_no_rank_claim(monkeypatch):
    """사장님 지적(2026-08-14): "블로그 글을 쓰지도 않은 사람이 뭘 순위를 알겠어?"
    맞다. 글이 없으면 순위는 당연히 없고, 그걸 '5위 밖'이라고 부르면 없는 것을
    있는 것처럼 말하는 셈이다. 순위 대신 '무엇이 없는지'를 말해야 한다."""
    from app.services import diagnose
    import app.services.blogrank as br
    import app.services.place as pl
    monkeypatch.setattr(br, "find_blog_by_name", lambda n, limit=20: {})   # 블로그 없음
    monkeypatch.setattr(pl, "search", lambda kw, limit=5: [{"name": "남의가게", "address": "서울"}])
    r = diagnose.diagnose_rank("썬팅", "부산 동구", "내가게")
    assert "5위" not in r["headline"], "글도 없는데 순위를 말한다"
    assert "블로그" in r["headline"] or "글" in r["headline"], "무엇이 없는지 말하지 않는다"
    assert "찾지 못했" in r["subline"] or "없" in r["subline"]
