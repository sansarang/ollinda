"""📓 에이전트 일지 — 누가·언제·무엇을·왜 했는지 사람이 읽을 수 있게 남긴다.

왜(2026-08-17 사장님 지시: "각각의 에이전트들이 어떻게 일을 하는지 로그로 보여줘"):
  자율 시스템의 가장 큰 위험은 **말없이 일하는 것**이다. 무엇을 왜 바꿨는지 남지 않으면
  결과가 나빠졌을 때 되짚을 수 없고, 좋아져도 무엇 덕분인지 모른다.
  오늘 tenant_lessons 24건이 wins 0·fails 0으로 쌓여 있던 것이 정확히 그 상태였다.

★ 두 종류를 구분해 남긴다:
  · act  — 실제로 무언가를 바꾼 것(파라미터 조정, 재작성 지시, 폐기)
  · note — 관측·판단만 하고 아무것도 안 바꾼 것
  나중에 "이 글이 왜 이렇게 나왔나"를 볼 때 act만 따라가면 된다.

★ 사장님이 읽는 문장이다 — 주방 용어(검색량·키워드·문서수)를 쓰지 않는다(헌법).
  다만 이 일지는 기본이 관리자용이라, 사장님 화면용 요약은 따로 뽑는다(`for_owner`).
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import db

_log = logging.getLogger("shopcast.agents")

KINDS = ("act", "note", "alert")


def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS agent_journal("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, agent TEXT, kind TEXT,"
              "tenant_id TEXT, piece_id TEXT, what TEXT, why TEXT, data TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_journal_at ON agent_journal(at DESC)")


def write(agent: str, what: str, why: str = "", kind: str = "note",
          tenant_id: str = "", piece_id: str = "", data: str = "") -> None:
    """일지 한 줄. **실패해도 본체를 멈추지 않는다** — 기록이 생성을 죽이면 안 된다."""
    try:
        with db._conn() as c:
            _ensure(c)
            c.execute("INSERT INTO agent_journal(at,agent,kind,tenant_id,piece_id,what,why,data)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (datetime.utcnow().isoformat(timespec="seconds"), agent,
                       (kind if kind in KINDS else "note"), tenant_id, piece_id,
                       what[:400], why[:400], (data or "")[:1000]))
        _log.info("[%s] %s%s", agent, what[:120], f" — {why[:80]}" if why else "")
    except Exception:
        _log.exception("[journal] 기록 실패 agent=%s", agent)


def recent(limit: int = 200, tenant_id: str = "", agent: str = "", kind: str = "") -> list:
    try:
        q = "SELECT * FROM agent_journal WHERE 1=1"
        args = []
        if tenant_id:
            q += " AND tenant_id=?"
            args.append(tenant_id)
        if agent:
            q += " AND agent=?"
            args.append(agent)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with db._conn() as c:
            _ensure(c)
            return [dict(r) for r in c.execute(q, args).fetchall()]
    except Exception:
        _log.exception("[journal] 조회 실패")
        return []


def as_text(rows: list) -> str:
    """로그 파일로 내보낼 형태 — 사장님이 그대로 열어 볼 수 있어야 한다."""
    if not rows:
        return "아직 기록이 없습니다.\n"
    mark = {"act": "▶", "alert": "⚠", "note": "·"}
    out = ["# 에이전트 일지", ""]
    day = ""
    for r in reversed(rows):                       # 오래된 것부터(읽는 순서)
        d = (r.get("at") or "")[:10]
        if d != day:
            day = d
            out.append(f"\n── {d} ──")
        t = (r.get("at") or "")[11:19]
        line = f"{mark.get(r.get('kind'), '·')} {t} [{r.get('agent','')}] {r.get('what','')}"
        out.append(line)
        if r.get("why"):
            out.append(f"      ↳ {r['why']}")
    out.append("")
    return "\n".join(out)


def summary(rows: list) -> dict:
    """에이전트별 활동 집계 — 누가 일하고 누가 노는지 한눈에."""
    per: dict = {}
    for r in rows:
        a = r.get("agent") or "?"
        s = per.setdefault(a, {"act": 0, "note": 0, "alert": 0, "last": ""})
        s[r.get("kind") or "note"] = s.get(r.get("kind") or "note", 0) + 1
        if not s["last"]:
            s["last"] = r.get("at") or ""
    return per
