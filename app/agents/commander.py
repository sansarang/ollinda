"""🎖 코드 수정 사령관 (L3-제한) — 결함을 찾아 수정안까지 만들고 **승인을 기다린다.**

2026-08-17 사장님 지시: "코드 수정 사령관도 만들어라."

★ 왜 '지휘는 하되 방아쇠는 사람'인가
  파라미터(숫자)는 틀려도 1건을 잃는다. 코드(규칙)는 틀리면 **금지선이 열린다.**
  우리 금지선은 키워드 스터핑·내용 복제·날조이고, 하나라도 뚫리면 tenant 블로그가 죽는다.
  게이트를 끄는 한 줄이면 충분하다 — 그리고 그 한 줄은 테스트도 같이 고치면 안 보인다.
  그래서 사령관은 **수정안을 완성해 대기시키고**, 적용은 사람이 누른다.

★ 사령관이 영원히 못 건드리는 곳(FORBIDDEN)
  헌법·규율 문서, 게이트 로직, 자기 자신의 안전장치, 그리고 **테스트 전체**.
  테스트를 고칠 수 있으면 자기 검증을 무력화할 수 있다 — 그 순간 이 구조는 무의미해진다.

★ 승인 없이 통과하는 조건은 없다(AUTO_APPLY 기본 꺼짐).
  켜더라도 [금지구역 미접촉 + 골든 전체 통과 + 변경 파일 3개 이하]를 전부 만족해야 한다.

동작:
  ① scan    — 결함 신호 수집(골든 실패·반복 경고·에이전트 경보·사고 패턴)
  ② draft   — 수정안 작성(파일·이유·패치)  ※ LLM은 여기서만 쓴다
  ③ verify  — 골든 전체 실행으로 자체 검증
  ④ queue   — 승인 대기(사장님이 /admin/commander 에서 확인·승인)
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime

from app import db
from app.agents import journal

_log = logging.getLogger("shopcast.agents.commander")

AGENT = "사령관"

#: 절대 수정 금지 — 여기 걸리면 제안 자체를 만들지 않는다.
FORBIDDEN = (
    "CLAUDE.md", "docs/DISCIPLINE.md", "docs/lessons.md",          # 헌법·규율
    "app/agents/commander.py", "app/agents/params.py",             # 자기 안전장치
    "tests/",                                                       # 자기 검증 무력화 금지
    "scripts/safe-push.sh",                                        # 배포 규율
    "app/services/qualitycheck.py",                                # 정직·품질 게이트
    "app/seo.py",                                                  # canonical 단일 관문
)

#: 자동 적용은 기본 꺼짐. 켜도 아래 조건 전부를 만족해야 한다.
AUTO_APPLY = os.environ.get("COMMANDER_AUTO", "0") == "1"
MAX_FILES = 3            # 한 제안이 건드릴 수 있는 파일 수 — 넓으면 원인 추적이 죽는다
MAX_LINES = 80           # 한 제안의 변경 줄 수 상한


def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS commander_orders("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT,"
              "title TEXT, why TEXT, files TEXT, patch TEXT,"
              "verify TEXT, status TEXT DEFAULT 'pending', decided_at TEXT, note TEXT)")


def forbidden_hit(paths) -> list:
    """금지 구역에 닿았는가. **막는 쪽이 기본값이다**(경로를 모르면 막는다)."""
    out = []
    for p in (paths or []):
        q = (p or "").replace("\\", "/").lstrip("./")
        if not q:
            out.append(p)
            continue
        if any(q == f or q.startswith(f) for f in FORBIDDEN):
            out.append(q)
    return out


def scan() -> list:
    """결함 신호 수집 — 사령관이 '무엇을 고쳐야 하는가'를 찾는 눈.

    LLM을 쓰지 않는다. 신호는 사실이어야 한다(추측으로 코드를 고치면 안 된다).
    """
    sigs = []
    # ① 골든 실패 — 가장 강한 신호
    try:
        r = subprocess.run(["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
                           capture_output=True, text=True, timeout=600,
                           env={**os.environ, "SHOPCAST_SECRET": os.environ.get("SHOPCAST_SECRET", "t")})
        fails = re.findall(r"FAILED (\S+)", r.stdout or "")
        if fails:
            sigs.append({"kind": "golden_fail", "detail": fails[:10],
                         "why": f"골든 {len(fails)}건 실패 — 계약이 깨졌다"})
    except Exception as e:
        _log.warning("[commander] 골든 실행 실패: %s", repr(e)[:120])
    # ② 에이전트 경보
    try:
        alerts = journal.recent(limit=100, kind="alert")
        if len(alerts) >= 3:
            sigs.append({"kind": "agent_alert", "detail": [a["what"] for a in alerts[:5]],
                         "why": f"에이전트 경보 {len(alerts)}건 누적"})
    except Exception:
        pass
    # ③ 실험이 반복 폐기되는 파라미터 — 값이 아니라 규칙이 틀렸을 수 있다
    try:
        with db._conn() as c:
            _ensure(c)
            rows = c.execute(
                "SELECT scope,name,COUNT(*) n FROM agent_trials WHERE verdict='fail' "
                "GROUP BY scope,name HAVING n>=4").fetchall()
        for r in rows:
            sigs.append({"kind": "param_stuck",
                         "detail": [f"{r['scope']}.{r['name']} {r['n']}회 실패"],
                         "why": "파라미터를 바꿔도 안 되면 규칙 자체가 틀렸을 수 있다"})
    except Exception:
        pass
    return sigs


def verify() -> dict:
    """골든 전체 실행 — 제안이 안전한지 스스로 확인한다."""
    try:
        r = subprocess.run(["python", "-m", "pytest", "tests/", "-q", "--tb=line"],
                           capture_output=True, text=True, timeout=900,
                           env={**os.environ, "SHOPCAST_SECRET": os.environ.get("SHOPCAST_SECRET", "t")})
        tail = (r.stdout or "")[-800:]
        ok = r.returncode == 0
        return {"ok": ok, "output": tail}
    except Exception as e:
        return {"ok": False, "output": repr(e)[:300]}


def order(title: str, why: str, files: list, patch: str, kind: str = "fix") -> dict:
    """수정안을 승인 대기열에 올린다. 금지 구역·크기 제한을 여기서 강제한다."""
    hit = forbidden_hit(files)
    if hit:
        journal.write(AGENT, f"수정안 거부 — 금지 구역 {hit}", why=title,
                      kind="alert")
        return {"ok": False, "error": "금지 구역", "files": hit}
    if len(files) > MAX_FILES:
        return {"ok": False, "error": f"파일 {len(files)}개 — 상한 {MAX_FILES}"}
    if len((patch or "").splitlines()) > MAX_LINES:
        return {"ok": False, "error": f"변경 {len(patch.splitlines())}줄 — 상한 {MAX_LINES}"}
    v = verify()
    try:
        with db._conn() as c:
            _ensure(c)
            cur = c.execute(
                "INSERT INTO commander_orders(at,kind,title,why,files,patch,verify,status)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (datetime.utcnow().isoformat(timespec="seconds"), kind, title[:200], why[:600],
                 json.dumps(files, ensure_ascii=False), patch[:20000],
                 json.dumps(v, ensure_ascii=False),
                 "pending" if not (AUTO_APPLY and v["ok"]) else "auto_ready"))
            oid = cur.lastrowid
    except Exception:
        _log.exception("[commander] 대기열 등록 실패")
        return {"ok": False, "error": "저장 실패"}
    journal.write(AGENT, f"수정안 #{oid} 대기 — {title}",
                  why=f"{why[:120]} · 골든 {'통과' if v['ok'] else '실패'}",
                  kind="act", data=json.dumps(files, ensure_ascii=False))
    return {"ok": True, "id": oid, "verify": v}


def orders(status: str = "") -> list:
    try:
        with db._conn() as c:
            _ensure(c)
            q = "SELECT * FROM commander_orders"
            a = []
            if status:
                q += " WHERE status=?"
                a.append(status)
            q += " ORDER BY id DESC LIMIT 50"
            return [dict(r) for r in c.execute(q, a).fetchall()]
    except Exception:
        return []


def decide(order_id: int, approve: bool, note: str = "") -> dict:
    """사장님 결정 — 승인/반려. **적용은 사람이 배포한다**(서버가 자기 코드를 밀지 않는다).

    서버가 스스로 git push를 하면 배포가 진행 중 작업을 죽이고(헌법 5장),
    실패해도 되돌릴 사람이 없다. 승인은 '이 패치를 적용하라'는 기록이다.
    """
    try:
        with db._conn() as c:
            _ensure(c)
            c.execute("UPDATE commander_orders SET status=?, decided_at=?, note=? WHERE id=?",
                      ("approved" if approve else "rejected",
                       datetime.utcnow().isoformat(timespec="seconds"), note[:300], order_id))
        journal.write(AGENT, f"수정안 #{order_id} {'승인' if approve else '반려'}",
                      why=note[:200], kind="act")
        return {"ok": True}
    except Exception:
        _log.exception("[commander] 결정 실패")
        return {"ok": False}
