"""'오늘'의 기준 골든 (2026-08-13 사장님 지적: "오늘 8월 13일이다").

사고: 서버가 UTC로 도는데 날짜 집계를 date.today()/utcnow()로 했다. 그래서
  · '오늘 방문자'가 실제로는 [한국시간 오늘 09:00 ~ 내일 09:00] 구간이었고
    한국 새벽 0~9시 방문자는 어제 칸에 들어갔다(실측: 오늘 8명이 아니라 18명).
  · 아침 브리핑은 KST 08:00에 도는데 그 순간 UTC는 전날이라 날짜가 하루 밀렸다.
숫자는 서버 실값이었지만 '오늘'의 뜻이 사장님 달력과 달랐다 — 측정 원칙 위반이다.
저장 타임스탬프는 UTC 그대로 둔다. 바뀌는 것은 '날짜로 묶는 기준' 하나뿐이다.
"""
import datetime

from app import db


class _FixedDT(datetime.datetime):
    """utcnow()를 고정하는 가짜 datetime — 경계 시각을 재현한다."""
    _now = datetime.datetime(2026, 8, 12, 20, 0, 0)

    @classmethod
    def utcnow(cls):
        return cls._now


def _at(monkeypatch, y, mo, d, h, mi=0):
    _FixedDT._now = datetime.datetime(y, mo, d, h, mi)
    monkeypatch.setattr(datetime, "datetime", _FixedDT)


def test_korean_dawn_belongs_to_korean_today(monkeypatch):
    """UTC 8/12 20:00 = 한국 8/13 05:00 — 사장님에게는 '오늘 8월 13일'이다."""
    _at(monkeypatch, 2026, 8, 12, 20)
    assert db.kst_today() == "2026-08-13"


def test_korean_late_night_still_same_day(monkeypatch):
    """UTC 8/13 14:00 = 한국 8/13 23:00 — 아직 같은 날이다."""
    _at(monkeypatch, 2026, 8, 13, 14)
    assert db.kst_today() == "2026-08-13"


def test_day_rolls_at_korean_midnight(monkeypatch):
    """한국 자정(UTC 15:00)에 날이 바뀐다 — 서버 자정이 아니다."""
    _at(monkeypatch, 2026, 8, 13, 14, 59)
    assert db.kst_today() == "2026-08-13"
    _at(monkeypatch, 2026, 8, 13, 15, 0)
    assert db.kst_today() == "2026-08-14"


def test_briefing_hour_is_not_shifted_a_day(monkeypatch):
    """아침 브리핑은 한국 08:00 = UTC 전날 23:00에 돈다.
    옛 코드(utcnow)는 여기서 전날 날짜를 찍어 브리핑이 하루 밀렸다."""
    _at(monkeypatch, 2026, 8, 12, 23)
    assert db.kst_today() == "2026-08-13", "브리핑 날짜가 하루 밀림"


def test_no_utc_day_bucketing_left_in_user_facing_paths():
    """사장님이 보는 '오늘'을 서버 날짜로 계산하는 코드가 다시 들어오면 실패한다.

    ★ 2026-08-18 — 검사 대상을 파일 두 개(main.py·briefing.py)로 못 박아뒀었다.
      briefing.py가 삭제되자 골든이 FileNotFoundError로 죽었다. 목록을 고정하면
      파일이 사라질 때 깨지고, 새 파일이 생길 때는 **아무것도 안 잡는다**(더 나쁘다).
      그래서 app/ 전체를 훑는다 — 오늘 추가한 날짜별 목록 그룹핑도 이제 대상이다.
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    targets = []
    for cur, dirs, files in os.walk(os.path.join(root, "app")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        targets += [os.path.relpath(os.path.join(cur, f), root)
                    for f in files if f.endswith(".py")]
    assert targets, "검사할 파일을 못 찾았다"
    for rel in sorted(targets):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        for i, ln in enumerate(src.splitlines(), 1):
            if ln.lstrip().startswith("#"):
                continue
            if 'utcnow().strftime("%Y-%m-%d")' in ln or "_date.today()" in ln:
                bad.append(f"{rel}:{i}")
    assert not bad, f"UTC 날짜로 '오늘'을 계산하는 곳이 남아 있다: {bad}"
