"""🩺 배포 전 검진 — 이 변경이 과거 사고와 같은 모양인가.

★ 기본 동작은 경고다(R3). 차단은 원장상 **재발 2회 이상** 유형에만.
  오탐이 쌓여 사장님이 검진을 꺼버리는 것이 최악의 결말이다 —
  그래서 못 잡는 것은 못 잡는다고 적고, 잡은 것도 근거(사고 #N)를 함께 보여준다.
"""
from __future__ import annotations

import re
import subprocess

from app.services.immune import ledger as _led
from app.services.immune import rules as _rules

BLOCK_MIN_RECURRENCE = 2              # 이 횟수 이상 재발한 유형만 차단(R3)


def _run(args: list) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=180).stdout
    except Exception:
        return ""


def staged_diff() -> str:
    """지금 커밋하려는 변경. 스테이지가 비면 마지막 커밋을 본다(소급 검진용)."""
    d = _run(["git", "diff", "--cached", "-U0"])
    return d if d.strip() else _run(["git", "diff", "-U0"])


def commit_diff(ref: str) -> str:
    return _run(["git", "show", ref, "-U0", "--format="])


def _grep_factory():
    def _grep(lit: str) -> list:
        out = _run(["git", "grep", "-n", "--fixed-strings", lit, "--", "app", "scripts"])
        return [ln for ln in out.splitlines() if ln.strip()][:12]
    return _grep


def inspect(diff: str, ledger_rows: list = None) -> dict:
    """변경 검진 — 탐지 목록 + 차단 여부. LLM 호출 없음(R8)."""
    rows = ledger_rows if ledger_rows is not None else _led.read()
    rec = _led.recurrence(rows)
    ctx = {"diff": diff or "", "grep": _grep_factory()}
    st = _rules.load_state()
    findings, blocking = [], []
    for r in _rules.STATIC_RULES:
        for f in r.check(ctx):
            if f.get("error"):
                findings.append({**f, "cause": r.cause, "severity": "규칙오류"})
                continue
            n = rec.get(r.cause, 0)
            same = [x["id"] for x in rows
                    if r.cause in (x.get("cause_types") or []) and x.get("confirmed")][:3]
            item = {**f, "cause": r.cause, "recurrence": n, "like": same,
                    "severity": "차단" if n >= BLOCK_MIN_RECURRENCE else "경고"}
            findings.append(item)
            _rules.note_hit(r.id, st)
            if n >= BLOCK_MIN_RECURRENCE:
                blocking.append(item)
    _rules.save_state(st)
    return {"findings": findings, "blocking": blocking,
            "blocked": bool(blocking), "recurrence": rec,
            "undetectable": _rules.UNDETECTABLE}


def render(res: dict) -> str:
    """사람이 읽는 보고 — 무엇을 왜 잡았는지, 무엇은 못 잡는지."""
    out = []
    if not res.get("findings"):
        out.append("🛡 배포 전 검진 — 과거 사고와 같은 모양은 안 보입니다")
    for f in res.get("findings") or []:
        mark = "🛑" if f.get("severity") == "차단" else "⚠️"
        like = f" (원장 {f['recurrence']}회 재발, 유사 사고 {', '.join(f.get('like') or []) or '—'})"
        out.append(f"{mark} [{f.get('cause')}] {f.get('detail')}{like}")
        for w in (f.get("where") or [])[:3]:
            out.append(f"     · {w}")
    if res.get("blocked"):
        out.append("→ 재발 2회 이상 유형이라 막습니다. 의도한 변경이면 "
                   "SHOPCAST_IMMUNE_OVERRIDE=1 로 넘기세요.")
    return "\n".join(out)
