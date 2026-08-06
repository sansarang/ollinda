"""🙅 가게별 '안 합니다' 학습 — 우리가 판정하지 않고 사장님이 정한다.

★ 2026-08-07: 이상한 글감이 나올 때마다 코드에 필터를 추가했다(날씨·사이트·할부).
  업종이 100개면 100번 고쳐야 한다 — 표면별 수정이고, 조항 위반이다.
  더 근본적으로 **내가 나쁘다고 판정한 것 중 절대적으로 나쁜 건 없었다.**
  같은 질문이 어떤 가게엔 최고의 글감이고 어떤 가게엔 무의미하다.
  그 차이는 사장님만 안다.

한 번 '안 합니다'를 누르면 그 계열이 **그 가게에서만** 영영 안 나온다.
"""
from __future__ import annotations

import re

from app import db

_TOK = re.compile(r"[가-힣A-Za-z0-9]+")
KEY = "vacantq_declined"


def _tok(s: str) -> list:
    return [t for t in _TOK.findall(s or "") if len(t) >= 2]


def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS vacantq_declined("
              "tenant_id TEXT, word TEXT, from_query TEXT, at TEXT, "
              "PRIMARY KEY(tenant_id, word))")


def declined(tenant_id: str) -> set:
    """이 가게가 '안 한다'고 한 주제 낱말들."""
    try:
        with db._conn() as c:
            _ensure(c)
            return {r["word"] for r in c.execute(
                "SELECT word FROM vacantq_declined WHERE tenant_id=?", (tenant_id,))}
    except Exception:
        return set()


def declined_detail(tenant_id: str) -> list:
    """무엇 때문에 배웠는지 — 되돌릴 때 사장님이 보고 판단할 수 있어야 한다."""
    try:
        with db._conn() as c:
            _ensure(c)
            return [dict(r) for r in c.execute(
                "SELECT word, from_query, at FROM vacantq_declined "
                "WHERE tenant_id=? ORDER BY at DESC", (tenant_id,))]
    except Exception:
        return []


def _lcs_len(a: str, b: str) -> int:
    """가장 긴 연속 공통 부분 문자열 길이."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


SIM_MIN = 0.5          # 거절한 질문과 이만큼 겹치면 같은 계열로 본다


def is_declined(query: str, declined_queries: set) -> bool:
    """★ 계열을 우리가 정의하지 않는다 — 거절한 질문과의 **겹침**으로 판정한다.

    2026-08-07 실측: '중고차할부이자율'을 통째로 저장하니 '중고차할부금리'가 또 나왔다.
    낱말로 쪼개면 '할부'를 우리가 뽑아내야 하는데, 그건 또 우리 판정이다.
    거절한 질문과 문자가 많이 겹치면 같은 계열이다 — 사장님이 준 답만으로 판정한다.
    """
    q = re.sub(r"\s+", "", query or "")
    if not q:
        return False
    for d in (declined_queries or set()):
        dd = re.sub(r"\s+", "", d or "")
        if not dd:
            continue
        if _lcs_len(q, dd) / max(1, min(len(q), len(dd))) >= SIM_MIN:
            return True
    return False


def decline(tenant_id: str, query: str, work_terms: list = None) -> dict:
    """'안 합니다' 한 번 — 그 질문을 그대로 저장한다.

    ★ 계열 판정은 저장할 때가 아니라 쓸 때(is_declined) 겹침으로 한다.
      우리가 '할부'라는 계열어를 뽑아내려 하면 그게 또 우리 판정이다.
    """
    new = {" ".join((query or "").split())}
    if not any(new):
        return {"ok": False, "why": "빈 질문"}
    try:
        from datetime import datetime as _d
        with db._conn() as c:
            _ensure(c)
            for w in new:
                c.execute("INSERT OR IGNORE INTO vacantq_declined"
                          "(tenant_id, word, from_query, at) VALUES(?,?,?,?)",
                          (tenant_id, w, query[:120], _d.utcnow().isoformat()))
    except Exception as e:
        return {"ok": False, "why": f"저장 실패: {repr(e)[:60]}"}
    return {"ok": True, "learned": sorted(new), "total": len(declined(tenant_id)),
            "note": "같은 계열 질문은 겹침으로 함께 막힌다"}


def undo(tenant_id: str, word: str) -> dict:
    """되돌리기 — 잘못 눌렀을 때. 학습은 되돌릴 수 있어야 한다."""
    if word not in declined(tenant_id):
        return {"ok": False, "why": "그런 거절어가 없다"}
    try:
        with db._conn() as c:
            _ensure(c)
            c.execute("DELETE FROM vacantq_declined WHERE tenant_id=? AND word=?",
                      (tenant_id, word))
    except Exception as e:
        return {"ok": False, "why": f"저장 실패: {repr(e)[:60]}"}
    return {"ok": True, "removed": word, "total": len(declined(tenant_id))}
