"""📥 1~10위 전수 + 반응 신호(공감·댓글·발행경과) 수집.

★ 반응·발행경과는 오늘 안 본 축이다 — 글 밖 신호인데 공개 표면에서 잴 수 있다.
  실측(2026-08-06): 공감 0~27, 댓글 0~26으로 실제로 벌어진다.
★ 우리 채널은 뺀다 — 우리 글 습관이 인자로 둔갑하는 것이 오늘의 FAQ 함정이었다.
"""
from __future__ import annotations

import json
import os
import re
import time

from app.services.immune import data_root as _dr

RAW_PATH = os.environ.get("SHOPCAST_RANKORDER_RAW", "") or os.path.join(_dr(), "rankorder_raw.jsonl")

# 반응·날짜 추출. 파싱 규칙은 여기 하나뿐이다(R4·사본 금지).
REACT_JS = """() => {
  const t = document.body.innerText || '';
  const g = (re) => { const m = t.match(re); return m ? Number(m[1].replace(/,/g, '')) : null; };
  const body = document.querySelector('.se-main-container, #postViewArea, .post_ct, article')
            || document.body;
  return {
    like: g(/공감\\s*([\\d,]+)/), comment: g(/댓글\\s*([\\d,]+)/),
    date: (t.match(/20\\d\\d[.\\-]\\s?\\d{1,2}[.\\-]\\s?\\d{1,2}/) || [''])[0],
    text: (body.innerText || '').replace(/[ \\t]+/g, ' ').slice(0, 20000),
    title: ((document.querySelector('meta[property="og:title"]') || {}).content
            || document.title || '').slice(0, 200),
    images: body.querySelectorAll('img').length,
    videos: body.querySelectorAll('video, iframe[src*="video"], .se-video').length,
    tables: body.querySelectorAll('table, .se-table').length,
    lists: body.querySelectorAll('ul, ol').length,
    h2: body.querySelectorAll('h2').length, h3: body.querySelectorAll('h3').length
  };
}"""


def _days_since(datestr: str) -> int | None:
    m = re.findall(r"\d+", datestr or "")
    if len(m) < 3:
        return None
    try:
        import datetime as _d
        d = _d.date(int(m[0]), int(m[1]), int(m[2]))
        return (_d.date.today() - d).days
    except Exception:
        return None


def collect(queries: list, top_n: int = 10, exclude_blogs: tuple = ()) -> dict:
    """질의별 1~N위 글 + 반응 신호. 순위는 실제 등장 순서다(R4)."""
    from playwright.sync_api import sync_playwright
    from app.services.coexpose import scope as _sc
    from app.services.reverse import surfaces as _sf
    from app.services.scout import session as _ss
    queries, dropped = _sc.filter_queries(queries)
    rows, blocked, failed = [], None, []
    for d in dropped:
        failed.append({"q": str(d), "error": "실운영 업종 — 영구 제외"})
    with sync_playwright() as p:
        b, pg = _ss.open_page(p)
        try:
            for q in queries:
                kw = q.get("q") if isinstance(q, dict) else str(q)
                try:
                    _ss.load_query(pg, kw)
                except _ss.Blocked as e:
                    blocked = f"{kw}: {e}"
                    break
                d0 = pg.evaluate(_sf.PLACE_JS)
                posts = [x for x in (d0.get("posts") or [])
                         if x.get("kind") == "blog" and x.get("blog") not in exclude_blogs][:top_n]
                for rank, x in enumerate(posts, 1):
                    url = f"https://m.blog.naver.com/{x['blog']}/{x['post']}"
                    try:
                        r = pg.goto(url, wait_until="networkidle", timeout=40000)
                        if r is not None and r.status in (403, 429):
                            blocked = f"{url}: HTTP {r.status}"
                            break
                        pg.wait_for_timeout(2500)
                        pg.mouse.wheel(0, 30000)
                        pg.wait_for_timeout(1800)
                        m = pg.evaluate(REACT_JS)
                    except Exception as e:
                        failed.append({"url": url, "error": repr(e)[:80]})
                        _ss.gap()
                        continue
                    rows.append({"q": kw, "industry": (q.get("industry") if isinstance(q, dict) else ""),
                                 "region": (q.get("region") if isinstance(q, dict) else ""),
                                 "rank": rank, "blog": x["blog"], "post": x["post"], "url": url,
                                 "at": int(time.time()),
                                 "like": m.get("like"), "comment": m.get("comment"),
                                 "date": m.get("date"), "age_days": _days_since(m.get("date")),
                                 "title": m.get("title"), "text": m.get("text"),
                                 "images": m.get("images"), "videos": m.get("videos"),
                                 "tables": m.get("tables"), "lists": m.get("lists"),
                                 "h2": m.get("h2"), "h3": m.get("h3")})
                    _ss.gap()
                if blocked:
                    break
                _ss.gap()
        finally:
            b.close()
    if rows:
        os.makedirs(os.path.dirname(RAW_PATH) or ".", exist_ok=True)
        with open(RAW_PATH, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"rows": rows, "blocked": blocked, "failed": failed, "collected": len(rows)}


def load(limit: int = 2000) -> list:
    try:
        with open(RAW_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()][-limit:]
    except Exception:
        return []
