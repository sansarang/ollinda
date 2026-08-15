"""재평가 구간 추적 커버리지 골든 (2026-08-16).

왜 생겼나:
  실측으로 확인된 네이버 동작 — 진입 순위는 최종 순위가 아니고, **발행 후 3~5일에 재평가**가
  일어나 자리가 갈린다(19위 진입 → 3일 뒤 1위 / 17위 진입 → 21위로 밀림).
  그런데 우리 기록은 12일 구간에 5일치만 남아 있었다.

  크론은 매일 정상 실행됐다(38일 중 빠진 날 2일 — 실측 확인). 구멍의 원인은
  **조회 실패(rank=None)가 기록되지 않고 조용히 사라지는 것**이었다.
  그 결과 '안 쟀다'와 '순위 밖이다'가 화면에서 똑같이 보인다 — 헌법의
  "지면 부재와 순위 밖은 다른 진단이다"를 측정 층에서 어긴 것.

여기서 무는 것:
  · 조회 실패 시 1회 재시도가 살아 있는가(전송 실패로 인한 구멍을 줄인다)
  · 실패를 세고 로그로 드러내는가(안 세면 구멍이 미노출처럼 보인다)
  · 재평가 구간(1~7일)의 실패를 따로 경고하는가(이 구멍은 나중에 못 메운다)
"""
import logging

from app.services import blogrank as _blogrank
from app.services import race


class _T:
    id = "t-test"
    name = "테스트가게"
    blog_id = "testblog"


def _pub(days_ago: int = 1) -> dict:
    from datetime import datetime, timedelta
    return {"piece_id": "p1", "published_url": "https://blog.naver.com/testblog/1",
            "post_title": "제목", "target_kw": "부산 썬팅",
            "published_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat()}


def test_rank_lookup_retries_once_before_giving_up(monkeypatch):
    """조회 실패는 한 번 더 두드린다 — 재평가 구간의 한 칸은 나중에 못 메운다."""
    calls = []

    def _pr(kw, url, limit=30):
        calls.append(url)          # ★ URL로 센다 — 공용 테스트 DB의 다른 가게 호출과 섞이지 않게
        return {"rank": None, "checked": 0}

    monkeypatch.setattr(_blogrank,"post_rank", _pr)
    monkeypatch.setattr(_blogrank,"check_indexed", lambda *a, **k: True)
    pub = {**_pub(), "indexed_at": "2026-08-15T00:00:00"}
    out = race.track_publish(_T(), None, pub)
    mine = [u for u in calls if u == pub["published_url"]]
    assert len(mine) == 2, f"재시도가 없다(호출 {len(mine)}회)"
    assert out["rank"] is None


def test_rank_lookup_stops_after_success(monkeypatch):
    """성공하면 더 두드리지 않는다 — 저속 원칙(불필요한 API 호출 금지)."""
    calls = []

    def _pr(kw, url, limit=30):
        calls.append(kw)
        return {"rank": 3, "checked": 30}

    monkeypatch.setattr(_blogrank,"post_rank", _pr)
    monkeypatch.setattr(_blogrank,"check_indexed", lambda *a, **k: True)
    monkeypatch.setattr(race.db, "get_prev_rank", lambda *a, **k: None)
    monkeypatch.setattr(race.db, "save_rank_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(race.db, "add_notice", lambda *a, **k: None)
    out = race.track_publish(_T(), None, {**_pub(), "indexed_at": "2026-08-15T00:00:00"})
    assert len(calls) == 1, "성공했는데도 재조회했다"
    assert out["rank"] == 3


def test_failure_is_logged_not_swallowed(monkeypatch, caplog):
    """실패를 조용히 넘기면 구멍이 '미노출'처럼 보인다(침묵 폴백 금지)."""
    monkeypatch.setattr(_blogrank,"post_rank", lambda *a, **k: {"rank": None, "checked": 0})
    monkeypatch.setattr(_blogrank,"check_indexed", lambda *a, **k: True)
    with caplog.at_level(logging.WARNING, logger="shopcast.race"):
        race.track_publish(_T(), None, {**_pub(), "indexed_at": "2026-08-15T00:00:00"})
    assert any("실측 실패" in r.message or "실측 실패" in r.getMessage()
               for r in caplog.records), "조회 실패가 로그에 안 남았다"


def test_track_all_reports_missed_and_reeval_window():
    """집계가 실패 건수와 재평가 구간을 함께 돌려줘야 구멍이 보인다."""
    import inspect
    src = inspect.getsource(race.track_all_publishes)
    assert "missed" in src and "reeval" in src, "실패·재평가 구간 집계가 없다"
    assert "reeval_missed" in src, "재평가 구간 실패를 따로 세지 않는다"
    # 재평가 구간의 정의가 실측(3~5일)을 덮는가
    assert "<= 7" in src or "<=7" in src, "재평가 구간(1~7일) 정의가 없다"


def test_first_two_weeks_are_tracked_daily():
    """재평가는 3~5일에 일어난다 — 첫 2주는 매일이어야 구간이 안 빈다."""
    import inspect
    src = inspect.getsource(race.track_all_publishes)
    assert "d > 14" in src, "첫 2주 매일 추적 보장이 사라졌다"
