"""🔬 검진 규칙 — 사고 1회 = 항체 1개.

★ 기계 규칙이 1급이다(R8). LLM에게 "이 diff가 경로 이원화냐"고 묻지 않는다 —
  오탐이 쌓이면 사장님이 검진을 꺼버린다. 그게 최악의 결말이다.
  LLM은 이미 탐지된 항목의 설명 문장을 다듬는 데만 쓴다.

★ 규칙은 단조 증가하지 않는다(R4). 3개월 무탐지면 주 1회로 강등하고, 강등 이력도 원장에 남는다.

★ 기계로 못 잡는 유형은 '기계검출불가'로 정직하게 표시한다.
  의미 판정(모델명 오인)·런타임 경합(완성 후 이동 실패)이 그것이다 — 야간 런타임 스캔이 맡는다.
"""
from __future__ import annotations

import json
import os
import re
import time

from app.services.immune import path as _ipath

# 규칙 강등 이력(R4)도 배포를 넘어 살아야 한다 — 안 그러면 매 배포마다 규칙이 되살아난다
STATE_PATH = os.environ.get("SHOPCAST_RULE_STATE", "") or _ipath("immune_rules.json")
RETIRE_DAYS = 90                      # 3개월 무탐지 → 주 1회로 강등(R4)


class Rule:
    """검진 규칙 하나. 판정은 순수 함수 — 부작용 없음."""

    def __init__(self, rid, cause, title, fn, static=True):
        self.id, self.cause, self.title, self.fn, self.static = rid, cause, title, fn, static

    def check(self, ctx: dict) -> list:
        try:
            return self.fn(ctx) or []
        except Exception as e:
            return [{"rule": self.id, "detail": f"규칙 실행 실패: {repr(e)[:80]}", "error": True}]


# ── 기계 규칙: diff에서 잡는 것 ────────────────────────────────────────────
_ADDED = re.compile(r"^\+(?!\+\+)(.*)$", re.M)
# ★ 처음엔 '긴 문자열 리터럴'을 다 봤다가 오탐이 났다(2026-08-05 소급 검진 실측:
#   diff 줄을 가로질러 " 기준\n for leak in [" 같은 코드 조각을 문자열로 잡았다).
#   경로 이원화의 실제 모양은 **규칙이 정규식으로 두 곳에 복제되는 것**이다 —
#   그래서 정규식다운 리터럴(메타문자 2개 이상)만 본다. 오탐은 검진을 끄게 만든다(R3).
_STR_LIT = re.compile(r"""r["']([^"'\n]{16,120})["']""")
_RE_META = re.compile(r"[\\\[\]()|+*?{}^$]")
_EXCEPT_SILENT = re.compile(r"except[^\n:]*:\s*(pass|return\s*(None|\"\"|''|\[\]|\{\})?)\s*$", re.M)
_MAXTOK_CONST = re.compile(r"max_tokens\s*=\s*(\d{3,6})")
_NEW_ROUTE = re.compile(r"@app\.(get|post)\(")
_GATE_WORDS = ("gate", "게이트", "_ok(", "audit", "check", "verify", "assert")


def _added(diff: str) -> str:
    return "\n".join(m.group(1) for m in _ADDED.finditer(diff or ""))


def _r_path_dup(ctx: dict) -> list:
    """경로 이원화 — 새로 넣은 긴 문자열/정규식이 이미 다른 파일에 살고 있다."""
    add = _added(ctx.get("diff") or "")
    hits, seen = [], set()
    for line in add.split("\n"):                      # 줄을 가로지르지 않는다(오탐의 원인이었다)
        for lit in _STR_LIT.findall(line):
            if lit in seen or lit.count(" ") > 3 or len(_RE_META.findall(lit)) < 2:
                continue                              # 정규식다운 것만 — 산문은 규칙이 아니다
            seen.add(lit)
            where = ctx.get("grep")(lit) if ctx.get("grep") else []
            files = {w.split(":")[0] for w in where}
            if len(files) >= 2:
                hits.append({"rule": "path-dup",
                             "detail": f"같은 규칙(정규식)이 {len(files)}개 파일에: {lit[:44]}…",
                             "where": sorted(files)[:4]})
    return hits[:5]


def _r_silent_fallback(ctx: dict) -> list:
    """침묵 폴백 — 사유 없이 삼키는 except가 추가됐다."""
    add = _added(ctx.get("diff") or "")
    n = len(_EXCEPT_SILENT.findall(add))
    return ([{"rule": "silent-fallback",
              "detail": f"사유를 남기지 않는 except {n}곳이 추가됐다(빈 반환·pass)"}] if n else [])


def _r_budget_const(ctx: dict) -> list:
    """예산 불충분 — max_tokens를 상수로 박았다. 입력 길이에 비례해야 한다."""
    add = _added(ctx.get("diff") or "")
    vals = _MAXTOK_CONST.findall(add)
    return ([{"rule": "budget-const",
              "detail": f"max_tokens 상수 {', '.join(vals[:4])} — 글 길이에 비례하지 않으면 절단된다"}]
            if vals else [])


def _r_gate_missing(ctx: dict) -> list:
    """게이트 사각 — 새 표면(라우트)을 만들면서 게이트를 안 걸었다."""
    add = _added(ctx.get("diff") or "")
    n = len(_NEW_ROUTE.findall(add))
    if not n:
        return []
    if any(w in add for w in _GATE_WORDS):
        return []
    return [{"rule": "gate-missing", "detail": f"새 라우트 {n}개에 게이트 호출이 안 보인다"}]


def _r_first_match(ctx: dict) -> list:
    """스테일 참조 — 여러 후보 중 '첫 매치'를 정답으로 쓴다."""
    add = _added(ctx.get("diff") or "")
    hits = re.findall(r"(re\.search\([^\n]{4,80}\)\.group\(|\.setdefault\([^\n]{0,40}\bre\.)", add)
    return ([{"rule": "first-match",
              "detail": f"첫 매치를 정답으로 쓰는 코드 {len(hits)}곳 — 헤더·라벨을 집을 수 있다"}]
            if hits else [])


def _r_partial_scan(ctx: dict) -> list:
    """대조 설계 결함 — 조각 하나만 보고 세트 전체를 판정한다."""
    add = _added(ctx.get("diff") or "")
    hits = re.findall(r"for\s+\w+\s+in\s+[\w.]*pieces[^\n]{0,60}\n(?:[^\n]*\n){0,4}[^\n]*\bbreak\b", add)
    return ([{"rule": "partial-scan",
              "detail": f"조각 하나만 보고 끝내는 순회 {len(hits)}곳 — 전 조각 합집합이어야 한다"}]
            if hits else [])


STATIC_RULES = [
    Rule("path-dup", "경로 이원화", "같은 규칙이 두 곳에 산다", _r_path_dup),
    Rule("silent-fallback", "침묵 폴백", "사유 없는 except", _r_silent_fallback),
    Rule("budget-const", "예산 불충분", "max_tokens 상수", _r_budget_const),
    Rule("gate-missing", "게이트 사각", "게이트 없는 새 표면", _r_gate_missing),
    Rule("first-match", "스테일 참조", "첫 매치를 정답으로", _r_first_match),
    Rule("partial-scan", "대조 설계 결함", "조각 하나로 전체 판정", _r_partial_scan),
]

# 기계로 못 잡는 유형 — 숨기지 않고 명시한다. 야간 런타임 스캔이 맡는다.
UNDETECTABLE = {
    "식별자 혼동": "의미 판정(모델명·시각 오인)은 정적 규칙으로 못 잡는다 → 야간 스캔",
    "세션 간 덮어쓰기": "런타임 경합은 diff에 안 보인다 → 야간 스캔",
    "기계검출불가": "정적 검진 밖",
}


def load_state(path: str = STATE_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def frequency(rid: str, st: dict = None, now: float = None) -> str:
    """이 규칙을 얼마나 자주 돌릴까 — 3개월 무탐지면 주 1회로 강등(R4)."""
    st = st if st is not None else load_state()
    now = now if now is not None else time.time()
    last = (st.get(rid) or {}).get("last_hit") or 0
    if last and (now - last) > RETIRE_DAYS * 86400:
        return "weekly"
    if not last and (st.get(rid) or {}).get("created", now) < now - RETIRE_DAYS * 86400:
        return "weekly"
    return "daily"


def note_hit(rid: str, st: dict = None, now: float = None) -> dict:
    st = st if st is not None else load_state()
    now = now if now is not None else time.time()
    row = st.setdefault(rid, {"created": now})
    row["last_hit"] = now
    row["hits"] = row.get("hits", 0) + 1
    return st


def derive_for(cause: str, st: dict = None) -> dict:
    """신규 사고 유형 → 검진 항목 1개 자동 파생(사고 1회 = 항체 1개).
    이미 규칙이 있으면 그대로. 기계로 못 잡는 유형이면 그 사실을 기록한다."""
    st = st if st is not None else load_state()
    have = {r.cause for r in STATIC_RULES}
    if cause in have:
        return {"cause": cause, "status": "이미 있음"}
    if cause in UNDETECTABLE:
        st.setdefault("_undetectable", {})[cause] = UNDETECTABLE[cause]
        return {"cause": cause, "status": "기계검출불가 — 야간 스캔 위임", "note": UNDETECTABLE[cause]}
    st.setdefault("_pending", {})[cause] = "규칙 미작성 — 사람이 기계 규칙을 정의해야 함"
    return {"cause": cause, "status": "규칙 대기(사람 작성 필요)"}
