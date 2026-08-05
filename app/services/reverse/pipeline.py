"""🔗 역설계 1단계 파이프라인 — 수집 → 본문 → 계측 → 대조.

라벨 두 축을 섞지 않는다(R6):
  · 순위 라벨: 그 지면에서의 등장 순서(상위 N vs 하위)
  · 인용 라벨: 브리핑 인용 채널로 표시된 것(채널 단위 사실)
"""
from __future__ import annotations

import json
import os
import time

from app.services.immune import data_root as _dr
from app.services.reverse import collector as _col
from app.services.reverse import contrast as _con
from app.services.reverse import features as _fx

OUT_PATH = os.environ.get("SHOPCAST_REVERSE_OUT", "") or os.path.join(_dr(), "reverse_features.jsonl")
TOP_N = 5                  # 상위군 경계 — 그 지면 등장 순서 기준


def run(keywords: list, fetch_limit: int = 14, use_llm: bool = False) -> dict:
    got = _col.collect(keywords)
    if not got["rows"]:
        return {"ok": False, "stage": "collect", **got}
    posts = _col.labeled_posts(got["rows"])
    fetched = _col.fetch_posts(posts, limit=fetch_limit)
    if fetched.get("blocked"):
        return {"ok": False, "stage": "fetch", "blocked": fetched["blocked"],
                "note": "차단 감지 — 수집 중단, 재시도 안 함"}
    kw0 = keywords[0] if keywords else ""
    rows = []
    for p in fetched["posts"]:
        m = _fx.measure(p, kw0)
        rows.append({**m, "keyword": p.get("keyword"), "rank": p.get("rank"),
                     "blog": p.get("blog"), "post": p.get("post"), "kind": p.get("kind"),
                     "url": p.get("url"), "channel_cited": p.get("channel_cited"),
                     "has_brief": p.get("has_brief"), "title": p.get("title"),
                     "text": (p.get("text") or "")[:1500]})
    llm_note = {"applied": 0, "note": "LLM 분석 안 함(요청 없음)"}
    if use_llm:
        llm_note = _fx.enrich_with_llm(rows)
    _save(rows)
    # 순위 라벨로 대조 — 인용 라벨과 섞지 않는다
    hi, lo = _con.split_by(rows, "rank", lambda r: (r.get("rank") or 99) <= TOP_N)
    keys = [k for k in (rows[0].keys() if rows else [])
            if isinstance(rows[0].get(k), (int, float, bool)) and k not in ("rank",)]
    res = _con.summarize(_con.compare(hi, lo, keys))
    return {"ok": True, "collected": got["collected"], "posts": len(rows),
            "fetch_failed": len(fetched.get("failed") or []),
            "group_hi": len(hi), "group_lo": len(lo),
            "llm": llm_note, "contrast": res,
            "brief_present": any(r.get("has_brief") for r in rows),
            "cited_channels": sorted({r["blog"] for r in rows if r.get("channel_cited")}),
            "blocked": got.get("blocked")}


def _save(rows: list) -> None:
    os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
    with open(OUT_PATH, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "saved_at": int(time.time())}, ensure_ascii=False) + "\n")


def load(limit: int = 800) -> list:
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()][-limit:]
    except Exception:
        return []
