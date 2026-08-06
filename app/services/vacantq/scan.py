"""🛰 빈자리 실측 — 후보 질문을 실제로 검색해 답하는 글이 있는지 본다.

★ 추측하지 않는다. 화면을 보고 정한다.
★ 파서·브라우저는 공통 모듈만 쓴다(scout.session + reverse.surfaces) — 사본 금지.
★ 사람 속도(R2), 차단 감지 시 즉시 중단·재시도 없음.
"""
from __future__ import annotations

import json
import os
import time

from app.services.immune import data_root as _dr
from app.services.vacantq import finder as _fd

OUT_PATH = os.environ.get("SHOPCAST_VACANTQ", "") or os.path.join(_dr(), "vacantq.jsonl")


def with_demand(candidates: list, min_volume: int = 10) -> dict:
    """★ 빈자리이기만 하면 소용없다 — **실제로 검색되는 말**이어야 한다.

    2026-08-06 실측: 'EV6 기아 얼마나 걸리나요' 같은 조합은 아무도 안 친다.
    비어 있는 게 당연하고, 써도 아무도 안 온다.
    검색량이 확인되는 축(seed·work)만 남겨 헛질문을 거른다.
    ★ 조회 실패는 '수요 미확인'으로 남긴다 — 0으로 단정하지 않는다(정직 게이트).
    """
    from app.services import searchad as _sa
    axes = list(dict.fromkeys([c.get("seed") for c in candidates if c.get("seed")]
                              + [c.get("work") for c in candidates if c.get("work")]))
    vol = {}
    try:
        for r in (_sa.keyword_volumes(axes, limit=40) or []):
            k = (r.get("keyword") or r.get("kw") or "").strip()
            v = r.get("volume") or r.get("total") or r.get("pc", 0) + r.get("mobile", 0)
            if k:
                vol[k] = int(v or 0)
    except Exception as e:
        return {"candidates": candidates, "volumes": {}, "checked": False,
                "note": f"검색량 조회 실패 — 수요 미확인으로 진행: {repr(e)[:60]}"}
    out = []
    for c in candidates:
        sv = vol.get(c.get("seed") or "", None)
        wv = vol.get(c.get("work") or "", None)
        known = [x for x in (sv, wv) if x is not None]
        c2 = {**c, "seed_vol": sv, "work_vol": wv,
              "demand": (max(known) if known else None)}
        # 수요가 확인된 축이 하나도 없으면 버리지 않고 표시만 한다(정직 게이트)
        if known and max(known) < min_volume:
            c2["skip"] = f"수요 부족(<{min_volume})"
        out.append(c2)
    return {"candidates": [c for c in out if not c.get("skip")],
            "dropped": [c for c in out if c.get("skip")],
            "volumes": vol, "checked": True,
            "note": "검색량이 확인된 축만 남긴다. 조회 실패는 미확인으로 두고 버리지 않는다."}


def scan(candidates: list, limit: int = 12) -> dict:
    """후보 질문을 검색해 빈자리를 가른다. 반환 {vacant, taken, blocked, failed}."""
    from playwright.sync_api import sync_playwright
    from app.services.reverse import surfaces as _sf
    from app.services.scout import session as _ss
    vacant, taken, failed, blocked = [], [], [], None
    with sync_playwright() as p:
        b, pg = _ss.open_page(p)
        try:
            for c in (candidates or [])[:limit]:
                q = c["q"] if isinstance(c, dict) else str(c)
                try:
                    _ss.load_query(pg, q)
                except _ss.Blocked as e:
                    blocked = f"{q}: {e}"
                    break
                except Exception as e:
                    failed.append({"q": q, "error": repr(e)[:80]})
                    _ss.gap()
                    continue
                d = pg.evaluate(_sf.PLACE_JS)
                posts = [x for x in (d.get("posts") or []) if x.get("kind") == "blog"]
                verdict = _fd.is_answered(q, posts)
                row = {**(c if isinstance(c, dict) else {"q": q}),
                       "at": int(time.time()), "n_posts": len(posts),
                       "best_cover": verdict["best"], "answered_by": verdict["by"],
                       # ★ 근거를 남긴다 — 나중에 '왜 비었다고 봤나'를 검증할 수 있어야 한다
                       "top_titles": [(x.get("title") or "")[:70] for x in posts[:3]]}
                (taken if verdict["answered"] else vacant).append(row)
                _ss.gap()
        finally:
            b.close()
    rows = vacant + taken
    if rows:
        os.makedirs(os.path.dirname(OUT_PATH) or ".", exist_ok=True)
        with open(OUT_PATH, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({**r, "vacant": r in vacant}, ensure_ascii=False) + "\n")
    return {"vacant": vacant, "taken": taken, "blocked": blocked, "failed": failed,
            "n_vacant": len(vacant), "n_taken": len(taken),
            "note": "빈자리 판정은 실제 검색 화면 근거다. 상위 제목 3개를 함께 남겼다."}


def load(limit: int = 500) -> list:
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()][-limit:]
    except Exception:
        return []
