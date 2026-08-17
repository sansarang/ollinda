"""🎛 파라미터 저장소 — 에이전트가 스스로 조정하는 숫자들이 사는 곳.

왜 생겼나(2026-08-17 사장님 지시: "너가 계속 수정하지 말고 에이전트들이 24시간 일해라"):
  이날 하루에만 내가 손으로 박은 상수가 셋이고 셋 다 틀렸다.
      PER_PARA = 0.7          → 실측 3건 보고 정함 → 뭉침 5곳
      CHARS_PER_PHOTO = 200   → 문단 수를 예측 못 함 → 또 틀림
      MIN_PHOTOS = 3          → 사진 17장이 3장으로 잘림
  이런 **숫자**는 사람이 정할 것이 아니다. 결과를 보고 시스템이 정해야 한다.

설계 원칙(오늘 실패에서 나온 것):
  ① 파라미터는 자율, 규칙은 사람.
     "사진이 뭉치면 안 된다"는 규칙이라 사람이 정한다. 몇 장이 상한인지는 숫자라 여기서 정한다.
     규칙을 AI가 바꾸면 금지선도 바꾼다 — 그건 L3이고 금지다.
  ② 검증 없는 자율은 금지.
     지금 tenant_lessons 24건이 전부 wins 0·fails 0이다. 효과를 모른 채 쌓이기만 했다.
     여기서는 **판정을 통과하지 못한 값은 승격되지 않고 자동으로 되돌아간다.**
  ③ 한 번에 하나만, 1건에만.
     실험값은 다음 생성 1건에만 적용한다. 틀려도 1건만 잃는다.
  ④ 교란을 뺀다.
     판 전체가 흔들린 날의 순위 변화를 우리 탓으로 세면 학습이 오염된다(2026-08-17 조사:
     Google 3월 업데이트에서 상위 10위 중 1/4이 100위 밖으로 나갔다 — 그런 날이 있다).

값의 우선순위: 실험값(pending) → 학습된 기본값(active) → 코드 기본값(fallback)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from app import db

_log = logging.getLogger("shopcast.agents.params")

#: 승격·폐기 기준 — 표본이 얇을 때 성급히 확정하지 않기 위한 값
PROMOTE_WINS = 3        # 이만큼 연속 이겨야 기본값으로 승격
RETIRE_FAILS = 2        # 이만큼 지면 즉시 폐기하고 이전 값으로 복귀
MAX_STEP = 0.35         # 한 번에 바꿀 수 있는 폭(35%) — 급격한 변경은 원인 추적을 불가능하게 한다


def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS agent_params("
              "scope TEXT, name TEXT, value TEXT, kind TEXT DEFAULT 'active',"
              "wins INTEGER DEFAULT 0, fails INTEGER DEFAULT 0,"
              "prev TEXT, reason TEXT, agent TEXT,"
              "created_at TEXT, updated_at TEXT,"
              "PRIMARY KEY(scope, name, kind))")
    c.execute("CREATE TABLE IF NOT EXISTS agent_trials("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, name TEXT,"
              "value TEXT, piece_id TEXT, agent TEXT, applied_at TEXT,"
              "verdict TEXT DEFAULT '', judged_at TEXT, note TEXT)")


def get(scope: str, name: str, fallback):
    """지금 쓸 값. 실험값이 있으면 그것, 없으면 학습된 값, 그것도 없으면 코드 기본값.

    ★ fallback은 지우지 않는다 — 저장소가 비어도 시스템은 그대로 돌아야 한다.
      자율 계층이 죽었다고 생성이 멈추면 그게 더 큰 사고다.
    """
    try:
        with db._conn() as c:
            _ensure(c)
            for kind in ("pending", "active"):
                r = c.execute("SELECT value FROM agent_params WHERE scope=? AND name=? AND kind=?",
                              (scope, name, kind)).fetchone()
                if r and r["value"] not in (None, ""):
                    return json.loads(r["value"])
    except Exception:
        _log.exception("[params] 조회 실패 %s.%s — 코드 기본값 사용", scope, name)
    return fallback


def propose(scope: str, name: str, value, agent: str, reason: str, fallback=None) -> bool:
    """실험값 제안. **다음 생성 1건에만** 적용된다.

    이미 실험 중이면 거절한다 — 두 값을 동시에 바꾸면 어느 것이 효과인지 못 가른다.
    변경 폭이 MAX_STEP을 넘으면 잘라낸다.
    """
    try:
        cur = get(scope, name, fallback)
        if isinstance(value, (int, float)) and isinstance(cur, (int, float)) and cur:
            lo, hi = cur * (1 - MAX_STEP), cur * (1 + MAX_STEP)
            clipped = max(lo, min(hi, value))
            if clipped != value:
                reason += f" (변경폭 제한: {value}→{round(clipped, 3)})"
                value = round(clipped, 3) if isinstance(cur, float) else int(round(clipped))
        now = datetime.utcnow().isoformat(timespec="seconds")
        with db._conn() as c:
            _ensure(c)
            busy = c.execute("SELECT 1 FROM agent_params WHERE scope=? AND kind='pending'",
                             (scope,)).fetchone()
            if busy:
                return False                 # 한 번에 하나만 — 동시 변경은 원인 추적을 죽인다
            c.execute("INSERT OR REPLACE INTO agent_params"
                      "(scope,name,value,kind,wins,fails,prev,reason,agent,created_at,updated_at)"
                      " VALUES(?,?,?,'pending',0,0,?,?,?,?,?)",
                      (scope, name, json.dumps(value), json.dumps(cur), reason, agent, now, now))
        _log.info("[params] 실험 제안 %s.%s: %s → %s (%s · %s)", scope, name, cur, value, agent, reason)
        return True
    except Exception:
        _log.exception("[params] 제안 실패 %s.%s", scope, name)
        return False


def mark_applied(scope: str, piece_id: str, agent: str = "") -> None:
    """실험값이 실제로 어느 글에 쓰였는지 기록 — 이게 없으면 나중에 판정할 대상을 못 찾는다."""
    try:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with db._conn() as c:
            _ensure(c)
            r = c.execute("SELECT name, value FROM agent_params WHERE scope=? AND kind='pending'",
                          (scope,)).fetchone()
            if not r:
                return
            c.execute("INSERT INTO agent_trials(scope,name,value,piece_id,agent,applied_at)"
                      " VALUES(?,?,?,?,?,?)",
                      (scope, r["name"], r["value"], piece_id, agent, now))
    except Exception:
        _log.exception("[params] 적용 기록 실패 %s", scope)


def judge(scope: str, piece_id: str, won: bool, note: str = "") -> str:
    """실험 판정. 이겼으면 wins, 졌으면 fails. 임계에 닿으면 승격 또는 폐기.

    ★ 교란된 판정은 넘기지 않는다 — 호출부가 '판 전체가 흔들린 날'을 걸러서 부른다.
    반환: '' | 'promoted' | 'retired'
    """
    out = ""
    try:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with db._conn() as c:
            _ensure(c)
            row = c.execute("SELECT * FROM agent_params WHERE scope=? AND kind='pending'",
                            (scope,)).fetchone()
            if not row:
                return ""
            p = dict(row)
            wins = p["wins"] + (1 if won else 0)
            fails = p["fails"] + (0 if won else 1)
            c.execute("UPDATE agent_trials SET verdict=?, judged_at=?, note=? "
                      "WHERE scope=? AND piece_id=? AND verdict=''",
                      ("win" if won else "fail", now, note[:200], scope, piece_id))
            if fails >= RETIRE_FAILS:
                c.execute("DELETE FROM agent_params WHERE scope=? AND kind='pending'", (scope,))
                _log.info("[params] 실험 폐기 %s.%s — 이전 값 복귀 (%s)", scope, p["name"], note[:80])
                out = "retired"
            elif wins >= PROMOTE_WINS:
                c.execute("INSERT OR REPLACE INTO agent_params"
                          "(scope,name,value,kind,wins,fails,prev,reason,agent,created_at,updated_at)"
                          " VALUES(?,?,?,'active',?,?,?,?,?,?,?)",
                          (scope, p["name"], p["value"], wins, fails, p["prev"],
                           p["reason"], p["agent"], p["created_at"], now))
                c.execute("DELETE FROM agent_params WHERE scope=? AND kind='pending'", (scope,))
                _log.info("[params] 승격 %s.%s = %s (%d승)", scope, p["name"], p["value"], wins)
                out = "promoted"
            else:
                c.execute("UPDATE agent_params SET wins=?,fails=?,updated_at=? "
                          "WHERE scope=? AND kind='pending'", (wins, fails, now, scope))
    except Exception:
        _log.exception("[params] 판정 실패 %s", scope)
    return out


def pending_for(piece_id: str) -> list:
    """그 글에 어떤 실험값이 쓰였나 — 판정할 때 되짚는다."""
    try:
        with db._conn() as c:
            _ensure(c)
            return [dict(r) for r in c.execute(
                "SELECT * FROM agent_trials WHERE piece_id=? AND verdict=''", (piece_id,)).fetchall()]
    except Exception:
        return []


def snapshot() -> dict:
    """지금 저장소 상태 — 관제·진단용."""
    try:
        with db._conn() as c:
            _ensure(c)
            rows = [dict(r) for r in c.execute(
                "SELECT scope,name,value,kind,wins,fails,agent,reason,updated_at "
                "FROM agent_params ORDER BY scope,name").fetchall()]
            trials = c.execute("SELECT COUNT(*) n FROM agent_trials WHERE verdict=''").fetchone()
        return {"params": rows, "open_trials": (trials["n"] if trials else 0)}
    except Exception:
        return {"params": [], "open_trials": 0}
