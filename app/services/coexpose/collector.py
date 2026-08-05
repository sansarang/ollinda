"""📥 동시 노출 화면 수집 — 같은 화면에 플레이스와 글이 함께 떴는가.

R1 공개 열람만 · R2 사람 속도 · R4 파서 단일 소스(scout.session + reverse.surfaces)
R6 '동시노출'은 실제 같은 화면 근거로만 · R8 원본 보존
"""
from __future__ import annotations

import json
import os
import time

from app.services.immune import data_root as _dr
from app.services.reverse import surfaces as _sf

RAW_PATH = os.environ.get("SHOPCAST_COEXPOSE_RAW", "") or os.path.join(_dr(), "coexpose_raw.jsonl")


def collect(queries: list, show: bool = False) -> dict:
    """queries: [{"q": 질의, "industry": 업종, "region": 지역}] — 업종·지역을 함께 남긴다."""
    from playwright.sync_api import sync_playwright
    from app.services.scout import session as _ss
    rows, blocked, failed = [], None, []
    with sync_playwright() as p:
        b, pg = _ss.open_page(p, show)
        try:
            for q in (queries or []):
                kw = q.get("q") if isinstance(q, dict) else str(q)
                try:
                    _ss.load_query(pg, kw)
                except _ss.Blocked as e:
                    blocked = f"{kw}: {e}"
                    break                        # 재시도 금지(R2)
                except Exception as e:
                    failed.append({"q": kw, "error": repr(e)[:100]})
                    _ss.gap()
                    continue
                d = pg.evaluate(_sf.PLACE_JS)
                v = _sf.coexpose_verify(d)
                if not v["ok"]:
                    failed.append({"q": kw, "error": "지면 없음(플레이스·글 모두 0)"})
                    _ss.gap()
                    continue
                rows.append({"q": kw, "at": int(time.time()),
                             "industry": (q.get("industry") if isinstance(q, dict) else ""),
                             "region": (q.get("region") if isinstance(q, dict) else ""),
                             "text_len": d.get("textLen"),
                             "coexposed": v["coexposed"], "n_place": v["n_place"],
                             "n_post": v["n_post"], "evidence": v["evidence"],
                             "places": d.get("places") or [], "posts": d.get("posts") or []})
                _ss.gap()
        finally:
            b.close()
    if rows:
        os.makedirs(os.path.dirname(RAW_PATH) or ".", exist_ok=True)
        with open(RAW_PATH, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"rows": rows, "blocked": blocked, "failed": failed, "collected": len(rows)}


# ★ 2026-08-06: 블로그 홈 HTML에서 '전체글 수'를 못 읽었다(전부 ?). 정규식이 구조와 안 맞았다.
#   공개 RSS 피드(rss.blog.naver.com/{id}.xml)가 더 확실하고 가볍다 — 브라우저도 필요 없다.
#   총 발행 수는 안 나오지만, C-RANK가 말하는 것은 '꾸준함'이므로
#   최근 글들의 **발행 간격**이 오히려 더 직접적인 지표다.
def rss_history(blog: str, timeout: int = 20) -> dict:
    """채널의 최근 발행 리듬 — 공개 RSS만 읽는다(R1). 실패는 실패로 남긴다."""
    import re as _re
    import urllib.request as _u
    from datetime import datetime as _dt
    try:
        req = _u.Request(f"https://rss.blog.naver.com/{blog}.xml",
                         headers={"User-Agent": "Mozilla/5.0"})
        x = _u.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception as e:
        return {"blog": blog, "error": repr(e)[:80]}
    ds = []
    for d in _re.findall(r"<pubDate>([^<]+)</pubDate>", x):
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S"):
            try:
                ds.append(_dt.strptime(d.strip()[:31], fmt).replace(tzinfo=None))
                break
            except Exception:
                pass
    if not ds:
        return {"blog": blog, "items": len(_re.findall(r"<item>", x)), "per_month": None,
                "note": "발행일을 못 읽었다 — 빈도 미확정"}
    ds.sort(reverse=True)
    span = (ds[0] - ds[-1]).days or 1
    return {"blog": blog, "items": len(ds), "span_days": span,
            "newest": ds[0].strftime("%Y-%m-%d"), "oldest": ds[-1].strftime("%Y-%m-%d"),
            "per_month": round(len(ds) * 30.0 / span, 1)}


def crank_check(channels: list, low_per_month: float = 8.0) -> dict:
    """C-RANK 반증 판정 — '발행 이력 적은데 상위 뜬 채널'이 있는가.

    ★ 이건 구조 인자가 아니라 반증 축이다(프레임 오염 금지).
    ★ 있으면 '꾸준함이 필요조건은 아니다'까지만 말한다.
      '구조 때문에 떴다'는 아직 아니다 — 대조군(안 뜬 글)이 있어야 그 말을 할 수 있다(R5).
    """
    rows, seen = [], []
    for c in (channels or []):
        if c and c not in seen:
            seen.append(c)
    import time as _t
    for ch in seen:
        rows.append(rss_history(ch))
        _t.sleep(1.5)                            # 사람 수준(R2)
    ok = [r for r in rows if r.get("per_month") is not None]
    low = [r for r in ok if r["per_month"] <= low_per_month]
    return {"channels": rows, "measured": len(ok), "failed": len(rows) - len(ok),
            "low_freq": low, "n_low": len(low),
            "range": ((min(r["per_month"] for r in ok), max(r["per_month"] for r in ok))
                      if ok else None),
            "verdict": ("C-RANK 반증 사례 있음(발행 적은 채널도 상위에 뜬다)" if low else
                        ("이 표본에선 C-RANK 반증 사례 없음" if ok else "측정 실패 — 판정 불가")),
            "caveat": "꾸준함이 필요조건은 아니라는 것까지다. "
                      "'구조 때문에 떴다'는 대조군 없이 말할 수 없다(R5)."}


# (구버전) 블로그 홈 HTML에서 읽던 방식 — 발행 수를 못 읽어 rss_history로 대체했다.
HISTORY_JS = """() => {
  const t = document.body.innerText || '';
  const m = t.match(/전체보기\\s*([\\d,]+)\\s*개/) || t.match(/글\\s*([\\d,]+)\\s*개/);
  const links = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    let h = a.getAttribute('href') || '';
    try { h = decodeURIComponent(h); } catch (e) {}
    const mm = h.match(/(blog|cafe)\\.naver\\.com\\/[^/]+\\/(\\d{6,})/);
    if (mm) links.add(mm[2]);
  }
  return {total_text: m ? m[1] : null, visible_posts: links.size};
}"""


def channel_history(channels: list, limit: int = 10) -> dict:
    """상위 뜬 글의 채널이 '꾸준히 발행해온 곳'인지 공개 범위에서 확인.

    ★ 이건 구조 인자가 아니라 **반증 축**이다(프레임 오염 금지).
      발행 이력이 적은데 상위에 뜬 글이 있으면 C-RANK로 설명되지 않는 노출이다.
    """
    from playwright.sync_api import sync_playwright
    from app.services.scout import session as _ss
    out, blocked = [], None
    seen = []
    for c in (channels or []):
        if c and c not in seen:
            seen.append(c)
    with sync_playwright() as p:
        b, pg = _ss.open_page(p)
        try:
            for ch in seen[:limit]:
                try:
                    r = pg.goto(f"https://m.blog.naver.com/{ch}",
                                wait_until="domcontentloaded", timeout=25000)
                    if r is not None and r.status in (403, 429):
                        blocked = f"{ch}: HTTP {r.status}"
                        break
                    pg.wait_for_timeout(1200)
                    d = pg.evaluate(HISTORY_JS)
                except Exception as e:
                    out.append({"blog": ch, "error": repr(e)[:80]})
                    _ss.gap()
                    continue
                out.append({"blog": ch, "total_text": d.get("total_text"),
                            "visible_posts": d.get("visible_posts")})
                _ss.gap()
        finally:
            b.close()
    return {"channels": out, "blocked": blocked}
