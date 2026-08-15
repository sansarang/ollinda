"""남의 상위글 궤적 추적 — 네이버가 순위를 어떻게 정하는지 '남의 글'로 배운다.

왜 필요한가 (2026-08-16):
  우리 글로 본 궤적은 6개뿐이었다. 그걸로 "19위 진입 → 3일 뒤 1위", "17위 진입 → 21위로 밀림",
  "1위 3주 유지 후 통째로 사라짐" 같은 모양이 보였지만 **표본이 얇아 규칙으로 못 쓴다**(규율 6).
  남의 상위글은 이미 네이버가 올려놓은 것이라, 매일 찍으면 표본이 수십 개로 늘어난다.
  우리가 글을 더 쓰지 않고도 네이버의 판정 규칙을 관측할 수 있는 유일한 창구다.

무엇을 남기나 (원문 저장 금지 — bloganatomy와 같은 원칙):
  키워드 × 글 URL × 날짜 → 순위. 제목·블로그명·발행일은 식별과 나이 계산에만 쓴다.
  본문은 가져오지도 저장하지도 않는다.

여기서 나오는 것:
  · 진입 → 재평가 → 안착 곡선 (네이버가 자리를 정하는 방식)
  · 탈락 순간(어제 있다 오늘 없음) — 1위도 영구 계약이 아니라는 실측
  · 신규 진입 (새 글이 어느 자리로 들어오는가)

저속·소량 원칙: 키워드당 하루 1회, 요청 간 1초+. 공식 검색 API만 쓴다(크롤링 아님).
"""
from __future__ import annotations

import logging
import time

from app import db

_log = logging.getLogger("shopcast.rivaltrack")

TOP_N = 10                # 키워드당 관측할 상위 글 수
MAX_KEYWORDS = 24         # 하루 총 관측 키워드 상한(API 쿼터 보호)
SLEEP = 1.0               # 요청 간격(저속 원칙)


def _ensure(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS rival_ranks(
        keyword TEXT, post_url TEXT, rank INTEGER, blogger TEXT,
        postdate TEXT, title TEXT, checked_at TEXT,
        PRIMARY KEY(keyword, post_url, checked_at))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rival_kw_at ON rival_ranks(keyword, checked_at)")


def _today() -> str:
    from app.db import _now
    return _now()[:10]


def snapshot(keyword: str, top_n: int = TOP_N) -> int:
    """키워드 1개의 상위 글 순위를 오늘자로 기록. 기록한 글 수 반환.

    ★ 같은 날 재실행은 갱신(하루 1개) — rank_snapshots와 같은 규약.
    """
    from app.services import blogrank
    kw = " ".join((keyword or "").split())
    if not kw:
        return 0
    items = blogrank._search_blog(kw, top_n)[:top_n]
    if not items:
        _log.warning("[rivaltrack] 조회 실패(빈 결과) — 궤적에 구멍이 생긴다 kw=%r", kw)
        return 0
    day = _today()
    n = 0
    with db._conn() as c:
        _ensure(c)
        for i, it in enumerate(items, 1):
            url = (it.get("link") or "").strip()
            if not url:
                continue
            c.execute(
                "INSERT INTO rival_ranks(keyword, post_url, rank, blogger, postdate, title, checked_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(keyword, post_url, checked_at) DO UPDATE SET rank=excluded.rank",
                (kw, url, i, (it.get("bloggername") or "")[:80],
                 (it.get("postdate") or "")[:8], (it.get("title") or "")[:200], day))
            n += 1
    return n


def _keywords() -> list:
    """관측 키워드 — 우리 가게들이 실제로 추적 중인 키워드에서 모은다(수동 등록 0)."""
    out: list = []
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        for kw in db.tracked_keywords(tid, limit=8):
            k = (kw or "").strip()
            if k and k not in out:
                out.append(k)
    return out[:MAX_KEYWORDS]


def sweep() -> dict:
    """스케줄러용 — 관측 키워드 전체 1회 스냅샷. 반환 {keywords, rows, failed}."""
    kws = _keywords()
    rows = failed = 0
    for kw in kws:
        try:
            got = snapshot(kw)
            rows += got
            failed += 0 if got else 1
        except Exception:
            failed += 1
            _log.exception("[rivaltrack] 스냅샷 실패 kw=%r", kw)
        time.sleep(SLEEP)
    _log.info("[rivaltrack] 남의 상위글 관측 — 키워드 %d개 · 기록 %d행 · 실패 %d",
              len(kws), rows, failed)
    return {"keywords": len(kws), "rows": rows, "failed": failed}


# ── 분석 (읽기 전용) ─────────────────────────────────────────────────────

def trajectories(keyword: str, min_days: int = 2) -> list:
    """글별 순위 궤적 — [{post_url, blogger, title, postdate, points:[(날짜, 순위)...]}].

    min_days 이상 관측된 글만. 하루짜리는 궤적이 아니다.
    """
    kw = " ".join((keyword or "").split())
    with db._conn() as c:
        _ensure(c)
        rows = c.execute(
            "SELECT post_url, blogger, title, postdate, rank, checked_at FROM rival_ranks "
            "WHERE keyword=? ORDER BY post_url, checked_at", (kw,)).fetchall()
    byurl: dict = {}
    for r in rows:
        d = byurl.setdefault(r["post_url"], {"post_url": r["post_url"], "blogger": r["blogger"],
                                             "title": r["title"], "postdate": r["postdate"],
                                             "points": []})
        d["points"].append((r["checked_at"], r["rank"]))
    out = [v for v in byurl.values() if len(v["points"]) >= min_days]
    out.sort(key=lambda v: v["points"][-1][1])
    return out


def dropouts(keyword: str) -> list:
    """탈락 포착(⑤) — 직전 관측일에는 있었는데 최근 관측일에 사라진 글.

    "1위는 영구 계약이 아니다"의 실측 증거. 사라진 자리와 그때 순위를 남긴다.
    """
    kw = " ".join((keyword or "").split())
    with db._conn() as c:
        _ensure(c)
        days = [r["d"] for r in c.execute(
            "SELECT DISTINCT checked_at d FROM rival_ranks WHERE keyword=? "
            "ORDER BY d DESC LIMIT 2", (kw,)).fetchall()]
        if len(days) < 2:
            return []
        cur, prev = days[0], days[1]
        rows = c.execute(
            "SELECT post_url, blogger, title, rank FROM rival_ranks "
            "WHERE keyword=? AND checked_at=? AND post_url NOT IN "
            "(SELECT post_url FROM rival_ranks WHERE keyword=? AND checked_at=?) "
            "ORDER BY rank", (kw, prev, kw, cur)).fetchall()
    return [{**dict(r), "last_seen": prev, "gone_on": cur} for r in rows]


def entrants(keyword: str) -> list:
    """신규 진입 — 최근 관측일에 처음 나타난 글(어느 자리로 들어오는가)."""
    kw = " ".join((keyword or "").split())
    with db._conn() as c:
        _ensure(c)
        days = [r["d"] for r in c.execute(
            "SELECT DISTINCT checked_at d FROM rival_ranks WHERE keyword=? "
            "ORDER BY d DESC LIMIT 2", (kw,)).fetchall()]
        if len(days) < 2:
            return []
        cur, prev = days[0], days[1]
        rows = c.execute(
            "SELECT post_url, blogger, title, postdate, rank FROM rival_ranks "
            "WHERE keyword=? AND checked_at=? AND post_url NOT IN "
            "(SELECT post_url FROM rival_ranks WHERE keyword=? AND checked_at=?) "
            "ORDER BY rank", (kw, cur, kw, prev)).fetchall()
    return [dict(r) for r in rows]


def summary() -> dict:
    """관측 현황 — 표본이 실제로 쌓이고 있는지 확인용(운영자)."""
    with db._conn() as c:
        _ensure(c)
        r = c.execute("SELECT COUNT(DISTINCT keyword) k, COUNT(DISTINCT post_url) p, "
                      "COUNT(DISTINCT checked_at) d, COUNT(*) n FROM rival_ranks").fetchone()
        tr = c.execute("SELECT keyword, post_url, COUNT(*) n FROM rival_ranks "
                       "GROUP BY keyword, post_url HAVING n >= 2").fetchall()
    return {"keywords": r["k"], "posts": r["p"], "days": r["d"], "rows": r["n"],
            "trajectories": len(tr)}
