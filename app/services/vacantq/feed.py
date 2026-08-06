"""📬 빈자리 → 글감 큐 — 사장님은 사진만 올리면 된다.

★ 목록만 나오면 사장님이 제목을 옮겨 적어야 한다. 그럼 노동이 는다.
  큐에 넣어야 기존 파이프라인이 이어받는다.
★ 빈자리는 시간이 지나면 남이 채운다 — 야간에 다시 훑고, 이미 찬 자리는 큐에서 뺀다.
★ 선점 추적: 우리가 쓴 뒤 그 질문에서 실제로 뜨는지 계속 본다.
  이게 '쓰면 뜬다'가 맞는지 자동으로 검증한다 — 따로 실험할 필요가 없다.
"""
from __future__ import annotations

import json
import logging
import os
import time

from app import db
from app.services.immune import data_root as _dr

_log = logging.getLogger("shopcast.vacantq.feed")
SOURCE = "vacant_q"                       # 큐에서 이 출처로 구분한다
CLAIM_PATH = os.environ.get("SHOPCAST_VACANTQ_CLAIM", "") or os.path.join(_dr(), "vacantq_claim.jsonl")
WEEKLY_CAP = 3                            # 주당 편입 상한 — 큐가 넘치면 사장님이 안 본다


def already_ours(tenant_id: str, query: str, taken_by: str = "") -> bool:
    """우리가 이미 그 자리를 잡았나 — 같은 질문으로 두 번 쓰면 우리 글끼리 부딪힌다."""
    blog = (getattr(db.get_tenant(tenant_id), "blog_id", "") or "")
    if blog and taken_by and taken_by.split("/")[0] == blog:
        return True
    try:
        for r in (db.writing_queue_rows(tenant_id, limit=200) or []):
            if (r.get("target_keyword") or "").strip() == query.strip():
                return True
    except Exception:
        pass
    return False


def feed(tenant_id: str, vacant: list, cap: int = WEEKLY_CAP) -> dict:
    """빈자리를 글감 큐에 넣는다. 근거(상위 글 제목)를 함께 남긴다."""
    added, skipped = [], []
    for v in (vacant or [])[:cap * 3]:
        q = (v.get("q") or "").strip()
        if not q:
            continue
        if already_ours(tenant_id, q, v.get("answered_by") or ""):
            skipped.append({"q": q, "why": "이미 우리 자리이거나 큐에 있음"})
            continue
        reason = ("이 질문에 답하는 글이 아직 없습니다. "
                  f"지금 상위: {', '.join((v.get('top_titles') or [])[:2])[:160]}")
        try:
            # ★ 2026-08-07 사고 교정: content_type을 안 주면 기본값 'sell'(트랙 A)로 들어가
            #   시공기 파이프라인이 글을 쓴다. '썬팅 계급도 버텍스' 글감으로 'PV5 시공기'가 나온 원인이다.
            #   빈 질문은 **그 질문에 답하는 정보 글**이라 트랙 B(info/GEO)로 보내야 한다 —
            #   질문형 소제목·결론 먼저·비교표·FAQ 구조가 거기 있다.
            ok = db.enqueue_writing(tenant_id, SOURCE, q, angle="review", reason=reason,
                                    content_type="info")
        except Exception as e:
            skipped.append({"q": q, "why": f"큐 추가 실패: {repr(e)[:60]}"})
            continue
        (added if ok else skipped).append({"q": q} if ok else {"q": q, "why": "중복"})
        if len(added) >= cap:
            break
    if added:
        _claim(tenant_id, added, vacant)
    return {"added": added, "skipped": skipped, "n_added": len(added),
            "note": f"주당 {cap}건까지만 넣는다 — 큐가 넘치면 사장님이 안 본다"}


def _claim(tenant_id: str, added: list, vacant: list) -> None:
    """선점 시도 기록 — 언제 어떤 자리를 노렸는지. 나중에 '떴나'를 대조한다(R8)."""
    by = {v.get("q"): v for v in (vacant or [])}
    os.makedirs(os.path.dirname(CLAIM_PATH) or ".", exist_ok=True)
    with open(CLAIM_PATH, "a", encoding="utf-8") as f:
        for a in added:
            v = by.get(a["q"]) or {}
            f.write(json.dumps({"tenant_id": tenant_id, "q": a["q"], "at": int(time.time()),
                                "was_vacant": True, "top_titles": v.get("top_titles") or [],
                                "best_cover": v.get("best_cover")},
                               ensure_ascii=False) + "\n")


def claims(tenant_id: str = "", limit: int = 200) -> list:
    try:
        with open(CLAIM_PATH, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return [r for r in rows if not tenant_id or r.get("tenant_id") == tenant_id][-limit:]
    except Exception:
        return []


def verify_claims(tenant_id: str, limit: int = 6) -> dict:
    """★ 선점이 실제로 됐나 — 우리가 쓴 뒤 그 질문에서 뜨는지 본다.

    이게 '빈 자리에 쓰면 뜬다'가 맞는지 자동으로 검증한다. 따로 실험할 필요가 없다.
    ★ 아직 발행 안 한 것은 판정하지 않는다(미발행을 실패로 세면 거짓 음성이다).
    """
    from playwright.sync_api import sync_playwright
    from app.services.reverse import surfaces as _sf
    from app.services.scout import session as _ss
    blog = (getattr(db.get_tenant(tenant_id), "blog_id", "") or "")
    rows = claims(tenant_id)[-limit:]
    if not rows:
        return {"checked": 0, "note": "선점 시도 기록 없음"}
    out, blocked = [], None
    with sync_playwright() as p:
        b, pg = _ss.open_page(p)
        try:
            for r in rows:
                try:
                    _ss.load_query(pg, r["q"])
                except _ss.Blocked as e:
                    blocked = str(e)
                    break
                except Exception:
                    _ss.gap()
                    continue
                d = pg.evaluate(_sf.PLACE_JS)
                posts = [x for x in (d.get("posts") or []) if x.get("kind") == "blog"]
                mine = [i for i, x in enumerate(posts, 1) if x.get("blog") == blog]
                out.append({"q": r["q"], "claimed_at": r["at"],
                            "days": int((time.time() - r["at"]) / 86400),
                            "our_rank": (mine[0] if mine else None),
                            "n_posts": len(posts),
                            "verdict": ("선점 성공" if mine else "아직 안 뜸")})
                _ss.gap()
        finally:
            b.close()
    got = [x for x in out if x["our_rank"]]
    return {"checked": len(out), "won": len(got), "blocked": blocked, "rows": out,
            "note": ("빈 자리에 쓰면 뜨는가 — 이 숫자가 그 답이다. "
                     "발행 후 며칠 지났는지(days)를 함께 본다.")}


# ── 주제 일치 게이트 ──────────────────────────────────────────────────────
# ★ 2026-08-07 실물 사고: '썬팅 계급도 버텍스' 글감으로 글을 뽑았더니
#   본문이 'PV5 열차단 썬팅 시공기'였다. 계급도 0회·등급 1회.
#   원인은 photo_pool이 과거 사진을 재활용했고 생성기는 **사진 내용대로** 쓰기 때문이다.
#   기존 파이프라인은 '이 사진을 설명하는 글'을 만들고,
#   빈자리 선점은 '그 질문에 답하는 글'이 필요하다 — 근본적으로 다르다.
#   ★ 게이트가 없으면 자리도 못 먹고 유사 시공기만 늘어난다.
TOPIC_MIN = 0.5            # 질문 낱말이 본문에 이만큼은 살아 있어야 '답한 글'이다


def topic_match(query: str, title: str, body: str, work_terms: list = None) -> dict:
    """이 글이 그 질문에 답하는가.

    ★ 낱말 개수만 세면 안 된다(2026-08-07 실측): '썬팅 계급도 버텍스'에서
      '썬팅·버텍스'만 있어도 2/3으로 통과했는데, 정작 **핵심어 '계급도'가 빠져 있었다.**
      하는 일 낱말(썬팅)은 그 가게 글이면 어디나 있다 — 그건 답의 증거가 아니다.
      **질문에서 하는 일을 뺀 나머지**가 핵심어이고, 그게 본문에 없으면 답한 글이 아니다.
    """
    import re as _re
    qt = [w for w in _re.findall(r"[가-힣A-Za-z0-9]+", query or "") if len(w) >= 2]
    if not qt:
        return {"ok": True, "cover": 1.0, "missing": [], "core_missing": []}
    text = f"{title or ''}\n{body or ''}"
    ws = {w for w in (work_terms or []) if w}
    core = [w for w in qt if w not in ws] or qt          # 하는 일을 뺀 핵심어
    hit = [w for w in qt if w in text]
    miss = [w for w in qt if w not in text]
    core_miss = [w for w in core if w not in text]
    cover = len(hit) / len(qt)
    return {"ok": (not core_miss) and cover >= TOPIC_MIN,
            "cover": round(cover, 2), "missing": miss,
            "core": core, "core_missing": core_miss}


def seal_if_offtopic(asset_id: str, query: str) -> dict:
    """주제가 어긋나면 발행을 막는다 — 사장님 화면에서 발행 버튼이 사라진다.

    ★ 지우지 않는다. 왜 어긋났는지 봐야 다음에 안 만든다(원본 보존).
    """
    from app.domain.models import ContentKind as _CK
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return {"ok": False, "error": "블로그 피스 없음"}
    pl = blog.payload or {}
    try:
        from app.services.vacantq import finder as _fn
        t = db.get_tenant(blog.tenant_id)
        works = _fn.work_terms(_fn.materials(blog.tenant_id), getattr(t, "region", "") or "")
    except Exception:
        works = []
    m = topic_match(query, pl.get("title") or "", pl.get("body") or "", works)
    if m["ok"]:
        return {"ok": True, "sealed": False, **m}
    try:
        db.update_piece_payload(blog.id, {
            "_publish_blocked": "vacantq_offtopic",
            "vacantq_topic": {"query": query, **m},
        })
    except Exception as e:
        return {"ok": False, "error": repr(e)[:80]}
    _log.warning("[vacantq] 주제 불일치 봉인 %s — '%s' 커버 %.2f, 빠진 말 %s",
                 asset_id[:8], query, m["cover"], m["missing"][:4])
    return {"ok": True, "sealed": True, **m}
