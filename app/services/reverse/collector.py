"""📥 정답지 수집 — 공개 결과만 읽는다.

★ R1: 로그인·세션·우리 계정 일절 없음. 일반 사용자가 보는 화면만 읽는다.
★ R2: 키워드 사이 간격·무작위 지연(session이 담당). 차단 신호가 보이면 즉시 중단, 재시도 없음.
★ R4: 브라우저·파싱은 scout.session / reverse.surfaces 하나씩만 쓴다. 사본을 만들지 않는다.
★ R6: 인용 라벨은 aipickItem(브리핑 인용 채널)에서 확인된 것만. 순위 라벨과 섞지 않는다.
★ R8: 수집 원문을 그대로 보존한다 — 나중에 다시 분해할 수 있어야 한다.
"""
from __future__ import annotations

import json
import logging
import os
import time

from app.services.immune import data_root as _dr
from app.services.reverse import surfaces as _sf

_log = logging.getLogger("shopcast.reverse")
RAW_PATH = os.environ.get("SHOPCAST_REVERSE_RAW", "") or os.path.join(_dr(), "reverse_raw.jsonl")


def collect(keywords: list, show: bool = False) -> dict:
    """키워드군의 검색 결과를 지면별로 수집. 원문은 그대로 남긴다.

    반환 {rows, blocked, failed}. blocked면 그 시점에 멈춘 것이다(사유 포함).
    """
    from playwright.sync_api import sync_playwright
    from app.services.scout import session as _ss
    rows, blocked, failed = [], None, []
    with sync_playwright() as p:
        b, pg = _ss.open_page(p, show)
        try:
            for kw in (keywords or []):
                try:
                    _ss.load_query(pg, kw)
                except _ss.Blocked as e:
                    blocked = f"{kw}: {e}"
                    _log.warning("[reverse] 차단 감지 — 수집 중단(재시도 안 함): %s", blocked)
                    break                       # ★ 재시도가 차단을 확정시킨다(R2)
                except Exception as e:
                    failed.append({"keyword": kw, "error": repr(e)[:120]})
                    _ss.gap()
                    continue
                d = pg.evaluate(_sf.EXTRACT_JS)
                # ★ 브리핑 인용 글은 template-id가 없어 별도로 캔다(2026-08-06 실물 판정).
                #   이것만이 '이 글이 인용됐다'의 공개 근거다 — aipickItem은 채널 소개 카드다(R6).
                brief = pg.evaluate(_sf.BRIEF_JS)
                v = _sf.verify(d["items"], d["hasBrief"])
                if not v["ok"]:
                    failed.append({"keyword": kw, "error": "지면 식별 실패", "verify": v})
                    _ss.gap()
                    continue                    # ★ 지면이 안 갈리면 정답지로 쓰지 않는다(R3)
                rows.append({"keyword": kw, "at": int(time.time()),
                             "text_len": d["textLen"], "has_brief": d["hasBrief"],
                             "verify": v, "items": d["items"],
                             "brief": brief})
                _ss.gap()
        finally:
            b.close()
    if rows:
        _save_raw(rows)
    return {"rows": rows, "blocked": blocked, "failed": failed,
            "collected": len(rows), "note": ("차단으로 중단" if blocked else "")}


# 글 본문 추출 — 모바일 블로그는 iframe 없이 열린다. 파싱 규칙은 여기 하나뿐이다(R4).
POST_JS = """() => {
  // ★ 2026-08-06: 본문까지 \s+로 뭉개면 개행이 사라져 문단을 못 센다
  //   (실측: para_avg_len이 text_len과 같아졌다 = 문단 1개로 잡힘).
  //   제목만 정리하고 본문은 줄바꿈을 살린다.
  const norm = t => (t || '').replace(/[ \\t]+/g, ' ').replace(/\\n{3,}/g, '\\n\\n').trim();
  const flat = t => (t || '').replace(/\\s+/g, ' ').trim();
  const body = document.querySelector('.se-main-container, #postViewArea, .post_ct, article')
            || document.body;
  const html = body ? body.innerHTML : '';
  const txt = norm(body ? body.innerText : '');
  const title = flat((document.querySelector('.se-title-text, .pcol1, h3.tit_h3, meta[property="og:title"]') || {}).innerText
                     || (document.querySelector('meta[property="og:title"]') || {}).content || document.title);
  return {
    title: title.slice(0, 200), text: txt.slice(0, 20000), html_len: html.length,
    h2: body.querySelectorAll('h2, .se-text-paragraph-align-center strong, .se_textarea h2').length,
    h3: body.querySelectorAll('h3').length,
    tables: body.querySelectorAll('table, .se-table').length,
    lists: body.querySelectorAll('ul, ol').length,
    images: body.querySelectorAll('img').length,
    videos: body.querySelectorAll('video, iframe[src*="video"], .se-video').length,
    links: body.querySelectorAll('a[href]').length
  };
}"""


def fetch_posts(posts: list, limit: int = 12, show: bool = False) -> dict:
    """정답지 글의 본문을 읽는다 — 공개 글 열람이다(R1). 간격은 session 규칙을 따른다(R2)."""
    from playwright.sync_api import sync_playwright
    from app.services.scout import session as _ss
    got, blocked, failed = [], None, []
    targets = [p0 for p0 in (posts or []) if p0.get("blog") and p0.get("post")][:limit]
    if not targets:
        return {"posts": [], "blocked": None, "failed": [], "note": "본문 대상 없음(글 URL 미확보)"}
    with sync_playwright() as p:
        b, pg = _ss.open_page(p, show)
        try:
            for t in targets:
                _k = t.get("kind") or "blog"
                url = f"https://m.{_k}.naver.com/{t['blog']}/{t['post']}"
                try:
                    r = pg.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if r is not None and r.status in (403, 429):
                        blocked = f"{url}: HTTP {r.status}"
                        break                   # 재시도 금지(R2)
                    pg.wait_for_timeout(1200)
                    d = pg.evaluate(POST_JS)
                except Exception as e:
                    failed.append({"url": url, "error": repr(e)[:100]})
                    _ss.gap()
                    continue
                got.append({**t, "url": url, **d})
                _ss.gap()
        finally:
            b.close()
    return {"posts": got, "blocked": blocked, "failed": failed}


def _save_raw(rows: list) -> None:
    """원문 보존(R8) — 분해 결과와 분리해 둔다. 지우지 않는다."""
    os.makedirs(os.path.dirname(RAW_PATH) or ".", exist_ok=True)
    with open(RAW_PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_raw(limit: int = 500) -> list:
    try:
        with open(RAW_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()][-limit:]
    except Exception:
        return []


def labeled_posts(rows: list) -> list:
    """수집 결과 → 글 단위 정답지. 라벨을 섞지 않는다(R6).

    cited        : **이 글이** 브리핑 답변의 출처로 표시됨(글 단위 — 유일한 인용 근거)
    channel_cited: 이 채널이 aipick 소개 카드에 있음(채널 단위 사실. 글 인용이 아니다)
    rank         : 그 지면에서의 등장 순서(상위/하위 대조군 구분용)
    ★ 셋을 섞지 않는다. 섞는 순간 라벨이 거짓이 된다(R6).
    """
    out = []
    for r in (rows or []):
        by = _sf.classify(r.get("items") or [])
        # ★ 인용 라벨은 채널 ID가 확인된 것만 쓴다(R6). 이름 매칭은 동명 위험이 있어 쓰지 않는다.
        _cited = by.get("ai_brief_channel", [])
        cited_channels = {it.get("blog") for it in _cited if it.get("blog")}
        cited_names = [it.get("name") for it in _cited if not it.get("blog")]
        # 글 단위 인용 — 브리핑 답변 섹션에서 확인된 것만
        _bp = ((r.get("brief") or {}).get("posts") or [])
        cited_posts = {(p0.get("blog"), p0.get("post")) for p0 in _bp
                       if p0.get("blog") and p0.get("post")}
        for i, it in enumerate(by.get("ugc", []), 1):
            out.append({
                "keyword": r["keyword"], "at": r["at"], "surface": "ugc",
                "rank": i, "blog": it.get("blog"), "post": it.get("post"),
                "href": it.get("href"), "text": it.get("text"), "kind": it.get("kind"),
                # ★ 채널이 인용 목록에 있다는 사실이지, 이 글이 인용됐다는 뜻이 아니다(R6)
                # ★ 글 단위 인용(정답 라벨) — 브리핑 출처에서 실물 확인된 것만
                "cited": (it.get("blog"), it.get("post")) in cited_posts,
                "channel_cited": bool(it.get("blog") and it["blog"] in cited_channels),
                "has_brief": r.get("has_brief"),
                # ID를 못 캔 인용 항목이 몇 개인지 — 라벨 미확보를 숨기지 않는다
                "cited_unresolved": len(cited_names),
            })
        # 브리핑에만 나오고 ugc 목록엔 없는 인용 글도 정답지다 — 빠뜨리면 인용군이 텅 빈다.
        #   실측: 인용 4건 중 ugc와 겹친 것은 1건뿐이었다(1 vs 12).
        have = {(o["blog"], o["post"]) for o in out}
        for p0 in _bp:
            key = (p0.get("blog"), p0.get("post"))
            if not all(key) or key in have:
                continue
            out.append({"keyword": r["keyword"], "at": r["at"], "surface": "ai_brief_post",
                        "rank": None, "blog": p0["blog"], "post": p0["post"],
                        "kind": p0.get("kind"), "href": "", "text": p0.get("title") or "",
                        "cited": True,
                        "channel_cited": p0["blog"] in cited_channels,
                        "has_brief": r.get("has_brief"),
                        "cited_unresolved": len(cited_names)})
    return out
