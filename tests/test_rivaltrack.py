"""남의 상위글 궤적 관측 골든 (2026-08-16).

왜 생겼나:
  우리 글 궤적 6개로는 네이버의 순위 규칙을 못 세운다(규율 6 — 얇은 표본으로 일반화 금지).
  남의 상위글은 이미 네이버가 올려놓은 것이라 매일 찍으면 표본이 수십 개로 늘어난다.
  글을 더 쓰지 않고도 네이버의 판정을 관측할 수 있는 창구다.

여기서 무는 것:
  · 궤적·탈락·신규진입 판정의 눈금이 살아 있는가(규율 4 — 계측기부터 극단값으로)
  · 원문을 저장하지 않는가(저작권·유사문서 — bloganatomy와 같은 원칙)
  · 저속·소량 원칙과 쿼터 상한이 유지되는가
  · 관측만 하고 아무것도 자동 실행하지 않는가
"""
import os

import pytest


@pytest.fixture()
def rt():
    """★ 공용 테스트 DB를 쓴다. 여기서 SHOPCAST_DB를 갈아끼우고 app.db를 reload하면
    같은 세션의 다른 테스트가 통째로 죽는다(실제로 18개를 죽였다 — 2026-08-16).
    격리는 이 표만 비우는 것으로 충분하다."""
    from app import db as _db
    from app.services import rivaltrack as _rt
    with _db._conn() as c:
        _rt._ensure(c)
        c.execute("DELETE FROM rival_ranks")
    return _rt


def _feed(rt, monkeypatch, day: str, posts: list):
    """(url, blogger) 목록을 그날 상위 결과로 심는다."""
    from app.services import blogrank
    monkeypatch.setattr(blogrank, "_search_blog", lambda kw, n: [
        {"link": f"https://blog.naver.com/{u}/1", "bloggername": b,
         "postdate": "20260801", "title": "제목"} for u, b in posts])
    monkeypatch.setattr(rt, "_today", lambda: day)
    return rt.snapshot("부산 썬팅")


def test_trajectory_tracks_rise_and_fall(rt, monkeypatch):
    """오른 글과 밀린 글이 다르게 잡혀야 한다 — 이게 안 되면 관측 자체가 무의미."""
    _feed(rt, monkeypatch, "2026-08-14", [("u1", "A"), ("u2", "B"), ("u3", "C")])
    _feed(rt, monkeypatch, "2026-08-15", [("u2", "B"), ("u1", "A"), ("u3", "C")])
    _feed(rt, monkeypatch, "2026-08-16", [("u2", "B"), ("u4", "D"), ("u1", "A")])
    tr = {t["blogger"]: [r for _, r in t["points"]] for t in rt.trajectories("부산 썬팅")}
    assert tr["B"] == [2, 1, 1], f"오른 글 궤적이 틀렸다: {tr.get('B')}"
    assert tr["A"] == [1, 2, 3], f"밀린 글 궤적이 틀렸다: {tr.get('A')}"


def test_dropout_is_detected(rt, monkeypatch):
    """1위도 영구 계약이 아니다 — 사라진 글을 잡아야 그 실측이 남는다."""
    _feed(rt, monkeypatch, "2026-08-15", [("u1", "A"), ("u3", "C")])
    _feed(rt, monkeypatch, "2026-08-16", [("u1", "A"), ("u4", "D")])
    out = rt.dropouts("부산 썬팅")
    assert [d["blogger"] for d in out] == ["C"], out
    assert out[0]["gone_on"] == "2026-08-16" and out[0]["last_seen"] == "2026-08-15"


def test_entrant_is_detected_with_entry_rank(rt, monkeypatch):
    """새 글이 '어느 자리로' 들어오는지가 진입 순위 연구의 재료다."""
    _feed(rt, monkeypatch, "2026-08-15", [("u1", "A"), ("u3", "C")])
    _feed(rt, monkeypatch, "2026-08-16", [("u1", "A"), ("u4", "D")])
    ent = rt.entrants("부산 썬팅")
    assert [(e["blogger"], e["rank"]) for e in ent] == [("D", 2)], ent


def test_single_day_is_not_a_trajectory(rt, monkeypatch):
    """하루짜리는 궤적이 아니다 — 한 점으로 추세를 말하면 규율 6 위반."""
    _feed(rt, monkeypatch, "2026-08-16", [("u1", "A")])
    assert rt.trajectories("부산 썬팅") == []
    assert rt.dropouts("부산 썬팅") == [] and rt.entrants("부산 썬팅") == []


def test_same_day_rerun_updates_not_duplicates(rt, monkeypatch):
    """같은 날 두 번 돌아도 하루 1개 — rank_snapshots와 같은 규약."""
    _feed(rt, monkeypatch, "2026-08-16", [("u1", "A"), ("u2", "B")])
    _feed(rt, monkeypatch, "2026-08-16", [("u2", "B"), ("u1", "A")])
    s = rt.summary()
    assert s["days"] == 1 and s["rows"] == 2, s


def test_empty_result_is_logged_not_silently_zero(rt, monkeypatch, caplog):
    """조회 실패를 조용히 0으로 넘기면 궤적의 구멍이 '경쟁 없음'처럼 보인다."""
    import logging
    from app.services import blogrank
    monkeypatch.setattr(blogrank, "_search_blog", lambda kw, n: [])
    with caplog.at_level(logging.WARNING, logger="shopcast.rivaltrack"):
        assert rt.snapshot("부산 썬팅") == 0
    assert any("조회 실패" in r.getMessage() for r in caplog.records)


# ── 원칙 준수 ────────────────────────────────────────────────────────────

def _src() -> str:
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "services", "rivaltrack.py")
    return open(p, encoding="utf-8").read()


def test_does_not_fetch_or_store_post_body():
    """원문 저장·학습 금지(저작권·유사문서) — bloganatomy와 같은 원칙."""
    src = _src()
    assert "_fetch_post_html" not in src, "본문을 가져오고 있다"
    assert "body" not in src.split('"""', 2)[-1], "본문을 저장하는 필드가 있다"


def test_rate_limit_and_quota_cap_are_kept():
    """저속·소량 원칙 — 상한이 사라지면 쿼터가 마르고 다른 실측이 죽는다."""
    src = _src()
    assert "MAX_KEYWORDS" in src and "time.sleep(SLEEP)" in src
    from app.services import rivaltrack as rt0
    assert rt0.MAX_KEYWORDS <= 30 and rt0.SLEEP >= 1.0


def test_keywords_are_auto_derived_not_manual():
    """추적 키워드 수동 등록 금지(헌법) — 우리가 실제 추적 중인 것에서만 모은다."""
    src = _src()
    assert "tracked_keywords" in src, "키워드를 자동 도출하지 않는다"


def test_observation_only_no_side_effects():
    """관측만 한다 — 글감 편입·생성·발행을 건드리면 안 된다."""
    src = _src()
    for forbidden in ("enqueue_writing", "save_piece", "publish", "generate"):
        assert forbidden not in src, f"관측 모듈이 부수효과를 낸다: {forbidden}"


def test_wired_into_scheduler():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "scheduler.py")
    src = open(p, encoding="utf-8").read()
    assert "rivaltrack" in src, "스케줄러에 안 걸렸다"
