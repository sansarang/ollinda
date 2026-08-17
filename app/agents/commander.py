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
    #   ★ 2026-08-17 실측 결함: 골든 실행이 600초 타임아웃으로 죽었는데 signals=0을 반환했다.
    #     그러면 일지에 "이상 없음 — 골든 전체 통과"가 찍힌다. **거짓 안심**이다.
    #     못 돌린 것과 통과한 것은 다르다 — 침묵 폴백 금지(헌법).
    try:
        r = subprocess.run(["python", "-m", "pytest", "tests/", "-q", "--tb=no", "-x", "-p", "no:cacheprovider"],
                           capture_output=True, text=True, timeout=1800,
                           env={**os.environ, "SHOPCAST_SECRET": os.environ.get("SHOPCAST_SECRET", "t")})
        fails = re.findall(r"FAILED (\S+)", r.stdout or "")
        if fails:
            sigs.append({"kind": "golden_fail", "detail": fails[:10],
                         "why": f"골든 {len(fails)}건 실패 — 계약이 깨졌다"})
        elif r.returncode != 0:
            sigs.append({"kind": "golden_unknown", "detail": [(r.stdout or "")[-200:]],
                         "why": f"골든이 비정상 종료(코드 {r.returncode}) — 통과를 확인 못 했다"})
    except Exception as e:
        # 실행 자체를 못 했다 → 그것이 신호다. 조용히 넘기면 '이상 없음'으로 둔갑한다.
        sigs.append({"kind": "golden_unknown", "detail": [repr(e)[:150]],
                     "why": "골든을 돌리지 못했다 — 계약 상태를 모른다(이상 없음이 아니다)"})
        _log.error("[commander] 골든 실행 실패: %s", repr(e)[:150])
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


def _test_target(test_id: str) -> str:
    """실패한 테스트 → 고쳐야 할 소스 파일 추정. tests/test_X.py → app/**/X.py

    ★ 추정이 틀리면 제안이 엉뚱해진다. 그래서 **찾은 파일만** 돌려주고,
      못 찾으면 빈 문자열을 준다(추측으로 아무 파일이나 고르지 않는다).
    """
    import glob
    m = re.match(r"tests/test_([a-z0-9_]+)\.py", (test_id or "").split("::")[0])
    if not m:
        return ""
    stem = m.group(1)
    for pat in (f"app/services/{stem}.py", f"app/agents/{stem}.py",
                f"app/generators/{stem}.py", f"app/{stem}.py"):
        if os.path.exists(pat):
            return pat
    hits = [p for p in glob.glob(f"app/**/{stem}.py", recursive=True)]
    return hits[0] if len(hits) == 1 else ""


def draft(signal: dict) -> dict:
    """결함 신호 → 수정안 초안. **LLM은 여기서만 쓴다.**

    ★ 실제 파일을 고치지 않는다. 패치 텍스트만 만들어 대기열에 올린다.
      코드가 바뀌는 순간은 사람이 승인하고 배포할 때뿐이다.
    ★ 실패한 테스트 본문을 같이 준다 — '무엇을 지켜야 하는지'가 테스트에 적혀 있고,
      그걸 안 보면 게이트를 끄는 방향으로 고치게 된다(가장 위험한 실패 모드).
    """
    if (signal or {}).get("kind") != "golden_fail":
        return {"ok": False, "error": "지금은 골든 실패만 다룬다"}
    tid = (signal.get("detail") or [""])[0]
    src_path = _test_target(tid)
    if not src_path:
        journal.write(AGENT, f"수정안 보류 — 대상 파일을 특정 못 함({tid})",
                      why="추측으로 아무 파일이나 고치지 않는다.", kind="note")
        return {"ok": False, "error": "대상 파일 불명"}
    if forbidden_hit([src_path]):
        journal.write(AGENT, f"수정안 거부 — 금지 구역 {src_path}", why=tid, kind="alert")
        return {"ok": False, "error": "금지 구역"}
    test_path = tid.split("::")[0]
    try:
        src = open(src_path, encoding="utf-8").read()[:12000]
        tst = open(test_path, encoding="utf-8").read()[:8000]
    except Exception as e:
        return {"ok": False, "error": f"읽기 실패 {repr(e)[:80]}"}

    prompt = (
        "아래 골든 테스트가 실패한다. **테스트가 지키려는 계약을 그대로 지키면서** 소스를 고쳐라.\n\n"
        "[절대 금지]\n"
        "- 테스트를 고치는 것(테스트는 계약이다 — 계약을 바꿔 통과시키면 그건 수정이 아니다)\n"
        "- 게이트·검증을 끄거나 느슨하게 만드는 것\n"
        "- 요청과 무관한 부분을 손대는 것\n\n"
        "[출력 형식] 다음 두 줄만. 설명·머리말 금지.\n"
        "이유: (한 문장 — 무엇이 왜 틀렸나)\n"
        "수정: (바꿀 코드 전체가 아니라 **바뀌는 줄만**, 앞뒤 한 줄씩 문맥 포함)\n\n"
        f"[실패한 테스트] {tid}\n```python\n{tst}\n```\n\n"
        f"[소스 {src_path}]\n```python\n{src}\n```\n")
    try:
        from app import llm as _llm
        out = _llm.call_task("aux", prompt, max_tokens=1500)
    except Exception as e:
        journal.write(AGENT, "수정안 작성 실패", why=repr(e)[:150], kind="alert")
        return {"ok": False, "error": repr(e)[:150]}
    if not (out or "").strip():
        return {"ok": False, "error": "빈 응답"}
    m = re.search(r"이유\s*:\s*(.+)", out)
    why = (m.group(1).strip() if m else tid)[:300]
    return order(title=f"골든 실패 수정 — {tid.split('::')[-1]}",
                 why=why, files=[src_path], patch=out, kind="golden_fix")


def sweep() -> dict:
    """결함을 훑고 수정안까지 만든다 — 사령관의 한 주기.

    ★ 한 번에 하나만 만든다. 여러 제안이 동시에 대기하면 사람이 판단을 못 한다.
    """
    sigs = scan()
    if not sigs:
        # ★ 여기 오려면 골든이 **실제로 끝까지 돌아 통과**했어야 한다.
        #   못 돌린 경우는 scan이 golden_unknown 신호를 넣으므로 여기 오지 않는다.
        journal.write(AGENT, "이상 없음 — 골든 전체 통과", kind="note")
        return {"ok": True, "signals": 0, "drafted": 0}
    made = 0
    for s in sigs:
        if s.get("kind") == "golden_unknown":
            # 계약 상태를 모르는 것은 '이상 없음'이 아니다. 사람이 봐야 한다.
            journal.write(AGENT, "⚠ 골든을 돌리지 못했다 — 계약 상태 불명",
                          why=f"{s['why']} · {str(s.get('detail'))[:150]}", kind="alert")
            continue
        if s.get("kind") != "golden_fail":
            journal.write(AGENT, f"신호 감지 — {s['why']}",
                          why=str(s.get("detail"))[:200], kind="alert")
            continue
        r = draft(s)
        if r.get("ok"):
            made += 1
            break                       # 한 주기에 하나만
    return {"ok": True, "signals": len(sigs), "drafted": made}


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
