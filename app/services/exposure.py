"""
🔍 노출 현황 — "지금 네이버에서 사장님 가게가 보이는 곳"(2026-08-02 사장님 지시).

제품의 존재 이유(CLAUDE.md): 올린다의 목표는 각 업체가 통합검색에 노출되는 것이다.
그래서 사장님 화면의 1번 숫자는 발행량이 아니라 '지면 4개의 노출 상태'다.

지면 4개:
  ① 통합검색(블로그) — 그 검색어 첫 화면에 우리 글이 실렸는가(kw_blocks 실측)
  ② 플레이스        — 지역검색 상위에 우리 가게가 있는가(rank_snapshots kind='place')
  ③ AI 브리핑       — 브리핑 블록에 우리 글이 인용됐는가(kw_blocks 블록명 실측)
  ④ 웹문서          — 우리 도메인 페이지가 잡히는가(미구현 → '측정 준비 중')

원칙: 실측만 쓴다. 측정값이 없으면 숫자를 지어내지 않고 '측정 준비 중'으로 표시한다.
      업종·지명 하드코딩 0. 표현의 주어는 가게다.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import db

# 지면 표시 순서·이름(사장 언어) — 내부 키는 코드용, label은 화면용
SURFACES = (("search", "통합검색"), ("place", "플레이스"), ("briefing", "AI 브리핑"), ("web", "웹문서"))
_BRIEF_HINT = ("브리핑", "AI 브리핑")          # 브리핑 계열 블록명(네이버 표기 변화 흡수)
NOT_MEASURED = "측정 준비 중"


def _blocks_rows(tenant_id: str) -> list:
    """이 가게의 키워드별 통합검색 지면 실측(kw_blocks) — 최근 측정 순."""
    rows = []
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            for r in c.execute("SELECT * FROM kw_blocks WHERE tenant_id=? ORDER BY checked_at DESC",
                               (tenant_id,)).fetchall():
                rows.append({"keyword": r["keyword"],
                             "blocks": [x for x in (r["blocks"] or "").split("|") if x],
                             "blog_blocks": [x for x in (r["blog_blocks"] or "").split("|") if x],
                             "mine": bool(r["mine"]),
                             "checked_at": (r["checked_at"] or "")[:16]})
    except Exception:
        pass
    return rows


def _rank_now_prev(tenant_id: str, keyword: str, kind: str) -> tuple:
    """(현재 순위, 7일 전 순위) — 실측 스냅샷만. 없으면 (None, None). 0=권외."""
    try:
        hist = [h for h in db.rank_history(tenant_id, keyword, kind=kind, limit=60)
                if h.get("rank") is not None]
    except Exception:
        return None, None
    if not hist:
        return None, None
    now = hist[-1].get("rank")
    cut = datetime.utcnow() - timedelta(days=7)
    prev = None
    for h in hist:
        try:
            if datetime.fromisoformat((h.get("checked_at") or "")[:19]) <= cut:
                prev = h.get("rank")
        except Exception:
            continue
    return now, prev


def _delta_text(now, prev) -> str:
    """전주 대비 변화(사장 언어). 순위는 작을수록 좋다. 0=권외."""
    if now is None or prev is None:
        return ""
    if not prev and now:
        return "지난주엔 안 보였어요"
    if prev and not now:
        return "지난주보다 내려갔어요"
    if now == prev:
        return "지난주와 같아요"
    return (f"지난주보다 {abs(prev - now)}칸 올랐어요" if now < prev
            else f"지난주보다 {abs(prev - now)}칸 내려갔어요")


def summary(tenant_id: str) -> dict:
    """지면 4개의 노출 상태. 화면은 이 값만 그린다(계산·판단은 전부 여기서)."""
    t = db.get_tenant(tenant_id)
    out = {"shop": (getattr(t, "name", "") or "내 가게") if t else "내 가게",
           "surfaces": {}, "measured_at": ""}
    if not t:
        return out
    rows = _blocks_rows(tenant_id)
    if rows:
        out["measured_at"] = rows[0]["checked_at"]

    # ① 통합검색 — 우리 글이 실린 검색어 / 자리는 있는데 아직인 검색어 / 자리가 없는 검색어
    shown, waiting, no_room = [], [], []
    for r in rows:
        if r["mine"]:
            shown.append({"keyword": r["keyword"], "where": ", ".join(r["blog_blocks"][:2]) or "첫 화면"})
        elif r["blog_blocks"]:
            waiting.append(r["keyword"])
        else:
            no_room.append(r["keyword"])
    out["surfaces"]["search"] = ({"state": "none", "note": NOT_MEASURED} if not rows else
                                 {"state": "shown" if shown else ("waiting" if waiting else "no_room"),
                                  "shown": shown[:5], "waiting": waiting[:5], "no_room": no_room[:5],
                                  "n_measured": len(rows)})

    # ② 플레이스 — 지역검색 순위 실측(추적된 키워드 중 순위가 잡힌 것)
    _pl = []
    for kw in (db.tracked_keywords(tenant_id, 8) or []):
        now, prev = _rank_now_prev(tenant_id, kw, "place")
        if now is not None:
            _pl.append({"keyword": kw, "rank": now, "delta": _delta_text(now, prev)})
    out["surfaces"]["place"] = ({"state": "measured", "items": _pl[:3]} if _pl else
                                {"state": "none", "note": NOT_MEASURED})

    # ③ AI 브리핑 — 브리핑 계열 블록에 우리 글이 인용됐는가(실측 블록명 기준)
    _br = [r["keyword"] for r in rows
           if r["mine"] and any(any(h in b for h in _BRIEF_HINT) for b in r["blog_blocks"])]
    _br_seen = [r["keyword"] for r in rows
                if any(any(h in b for h in _BRIEF_HINT) for b in r["blocks"])]
    out["surfaces"]["briefing"] = ({"state": "shown", "items": _br[:3]} if _br else
                                   ({"state": "waiting", "items": _br_seen[:3]} if _br_seen else
                                    {"state": "none", "note": NOT_MEASURED}))

    # ④ 웹문서 — 아직 지면을 만들지 않았다(공개 페이지 미구현). 지어내지 않는다.
    out["surfaces"]["web"] = {"state": "none", "note": NOT_MEASURED}
    return out
