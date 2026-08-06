"""🔗 동시노출 역설계 파이프라인 — 뽑힌 글 vs 안 뽑힌 글의 구조 차이.

★ 발행 이력은 인자에 없다. 대조군이 같은 채널이라 이미 상수로 통제된다(프레임 보호).
"""
from __future__ import annotations

import json
import os
import time

from app.services.immune import data_root as _dr
from app.services.coexpose import collector as _col
from app.services.coexpose import control as _ctl
from app.services.coexpose import features as _fx
from app.services.reverse import collector as _rc
from app.services.reverse import contrast as _con

OUT_PATH = os.environ.get("SHOPCAST_COEXPOSE_OUT", "") or os.path.join(_dr(), "coexpose_features.jsonl")


def run(queries: list, per_query_fetch: int = 10) -> dict:
    got = _col.collect(queries)
    if got.get("blocked"):
        return {"ok": False, "stage": "collect", "blocked": got["blocked"]}
    rows, seen = [], set()
    for r in got["rows"]:
        picked = [p for p in (r.get("posts") or []) if p.get("kind") == "blog"][:5]
        c = _ctl.build(r["q"], picked)
        targets = ([{**p, "picked": True} for p in c["picked"]]
                   + [{**p, "picked": False} for p in c["control"]])[:per_query_fetch]
        if not targets:
            continue
        fetched = _rc.fetch_posts(targets, limit=len(targets))
        if fetched.get("blocked"):
            return {"ok": False, "stage": "fetch", "blocked": fetched["blocked"]}
        by = {(t["blog"], t["post"]): t.get("picked") for t in targets}
        for p in fetched["posts"]:
            k = (p.get("blog"), p.get("post"))
            if k in seen:
                continue
            seen.add(k)
            rows.append({**_fx.measure(p, r["q"]),
                         "q": r["q"], "industry": r.get("industry"), "region": r.get("region"),
                         "blog": p.get("blog"), "post": p.get("post"), "url": p.get("url"),
                         "picked": bool(by.get(k))})
    if rows:
        os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
        with open(OUT_PATH, "a", encoding="utf-8") as f:
            for x in rows:
                f.write(json.dumps({**x, "saved_at": int(time.time())}, ensure_ascii=False) + "\n")
    return {"ok": True, "posts": len(rows), **analyze(rows)}


def analyze(rows: list) -> dict:
    """뽑힌 글 vs 안 뽑힌 글 대조 + 업종 교차 검증(R5)."""
    hi = [r for r in rows if r.get("picked")]
    lo = [r for r in rows if not r.get("picked")]
    keys = [k for k in (rows[0] if rows else {})
            if isinstance((rows[0] if rows else {}).get(k), (int, float, bool))
            and k != "picked"]
    overall = _con.compare(hi, lo, keys)
    # ★ 한 업종에만 나오는 신호는 잡음이다 — 업종별로 따로 돌려 공통 후보만 남긴다
    per_ind, inds = {}, sorted({r.get("industry") for r in rows if r.get("industry")})
    for ind in inds:
        sub = [r for r in rows if r.get("industry") == ind]
        h2 = [r for r in sub if r.get("picked")]
        l2 = [r for r in sub if not r.get("picked")]
        if len(h2) >= 2 and len(l2) >= 2:
            per_ind[ind] = {f["factor"]: f["verdict"] for f in _con.compare(h2, l2, keys)}
    cross = []
    for f in overall:
        hits = [i for i, m in per_ind.items() if m.get(f["factor"]) == "인자 후보"]
        f["industries_confirmed"] = hits
        f["cross_industry"] = len(hits) >= 2
        if f["verdict"] == "인자 후보" and not f["cross_industry"]:
            f["verdict"] = "단일 업종 신호(잡음 의심)"
        if f["verdict"] == "인자 후보":
            cross.append(f)
    return {"n_hi": len(hi), "n_lo": len(lo), "industries": inds,
            "factors": overall, "candidates": cross, "n_candidates": len(cross),
            "note": "여러 업종에서 공통으로 나온 것만 인자 후보다. 표본 부족은 미확정."}


def load(limit: int = 800) -> list:
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()][-limit:]
    except Exception:
        return []
