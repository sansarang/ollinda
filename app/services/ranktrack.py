"""
순위 자동추적 → 학습 루프(상위노출 PHASE 3).
APScheduler가 tenant×타겟키워드 순위를 주기 스냅샷(rank_snapshots 재사용) →
- 오른 키워드: db.improving_keywords → ingest가 다음 생성 브리프에 역주입(이미 연결됨)
- 안 오른(정체) 키워드: 앵글 변경(후기형↔방법형↔가격형) 재도전 제안

정직성: 실측 스냅샷만 비교. 조회불가(None)는 기록·비교하지 않는다.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from app import config, db

_log = logging.getLogger("shopcast.ranktrack")

_ANGLE_ORDER = ["review", "howto", "price"]
_ANGLE_LABEL = {"review": "후기형", "howto": "방법·과정형", "price": "가격·비용형"}


def tenant_keywords(t, limit: int | None = None) -> list[str]:
    """추적 키워드 — 스냅샷 이력 + 최근 생성물 target_keywords(중복 제거)."""
    limit = limit or config.RANK_TRACK_KEYWORDS
    kws = list(db.tracked_keywords(t.id, limit=limit))
    if len(kws) < limit:
        for s in db.list_sets(tenant_id=t.id, limit=20):
            for p in db.get_set_pieces(s["asset_id"]):
                for k in (p.payload.get("target_keywords") or []):
                    if k and k not in kws:
                        kws.append(k)
    return kws[:limit]


def track_tenant(t) -> int:
    """한 가게 순위 스냅샷 — 지역검색(상호) + 플레이스(매장) + 블로그탭(blog_id). 기록 수 반환."""
    from app.services import place
    kws = tenant_keywords(t)
    if not kws:
        return 0
    n = 0
    bid = getattr(t, "blog_id", "") or ""
    for kw in kws:
        cur = place.rank(kw, t.name)
        db.save_rank_snapshot(t.id, kw, cur, kind="blog")          # None이면 내부에서 스킵
        if cur is not None:
            n += 1
        if (getattr(t, "biz_type", "local") or "local") in ("local", "hybrid"):
            db.save_place_rank(t.id, kw, cur)                       # 플레이스 노출(분리 추적)
        if bid:
            from app.services import blogrank
            br = blogrank.blog_rank(kw, bid)
            db.save_rank_snapshot(t.id, kw, br["rank"], kind="blog_search")
            if br["rank"] is not None:
                n += 1
    return n


def track_all() -> dict:
    """자동추적 대상 전체 — 소유자(구독자)가 있고 업종이 설정된 가게만(무의미한 API 콜 방지)."""
    ok = total = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if not t or not (t.industry or "").strip():
            continue
        total += 1
        try:
            ok += 1 if track_tenant(t) else 0
        except Exception:
            _log.exception("[ranktrack] 추적 실패 tenant=%s", tid)
    _log.info("[ranktrack] 순위 자동추적 %d/%d 완료", ok, total)
    return {"tracked": ok, "total": total}


def _last_angle(tenant_id: str, keyword: str) -> str:
    """이 키워드로 만든 최근 블로그 글의 앵글(없으면 '')."""
    for s in db.list_sets(tenant_id=tenant_id, limit=30):
        for p in db.get_set_pieces(s["asset_id"]):
            if p.kind.value != "blog":
                continue
            if keyword in (p.payload.get("target_keywords") or []):
                return p.payload.get("angle") or ""
    return ""


def next_angle(prev: str) -> str:
    """앵글 로테이션 — 후기형↔방법형↔가격형."""
    try:
        return _ANGLE_ORDER[(_ANGLE_ORDER.index(prev) + 1) % len(_ANGLE_ORDER)]
    except ValueError:
        return _ANGLE_ORDER[0]


def stagnant_keywords(tenant_id: str, limit: int = 3) -> list[dict]:
    """정체 키워드 — 스냅샷 2개+ 인데 순위가 안 오른(미노출 유지 포함) 키워드.
    반환: [{keyword, first, last, retry_angle, retry_label, href}]"""
    out = []
    for kw in db.tracked_keywords(tenant_id, limit=10):
        hist = [h for h in db.rank_history(tenant_id, kw) if h.get("rank") is not None]
        if len(hist) < 2:
            continue
        first = hist[0]["rank"] or 31           # 0(미노출)=최하 취급
        last = hist[-1]["rank"] or 31
        if last < first:                         # 오르는 중 — 학습 루프(improving)가 담당
            continue
        prev = _last_angle(tenant_id, kw)
        ang = next_angle(prev)
        out.append({"keyword": kw, "first": hist[0]["rank"], "last": hist[-1]["rank"],
                    "retry_angle": ang, "retry_label": _ANGLE_LABEL[ang],
                    "prev_label": _ANGLE_LABEL.get(prev, "기본"),
                    "href": f"/me?target_kw={quote(kw)}&angle={ang}"})
        if len(out) >= limit:
            break
    return out


def rank_deltas(tenant_id: str, limit: int = 6) -> list[dict]:
    """대시보드용 순위 변화 — [{keyword, kind, first, last, dir(up|down|flat|enter), history:[...]}]."""
    out = []
    for kw in db.tracked_keywords(tenant_id, limit=limit):
        best = None
        for kind in ("blog_search", "place", "blog"):
            hist = [h for h in db.rank_history(tenant_id, kw, kind=kind) if h.get("rank") is not None]
            if len(hist) >= 1 and (best is None or len(hist) > len(best[1])):
                best = (kind, hist)
        if not best:
            continue
        kind, hist = best
        first, last = hist[0]["rank"], hist[-1]["rank"]
        f, l = (first or 31), (last or 31)
        if not first and last:
            d = "enter"                          # 미노출 → 진입
        elif l < f:
            d = "up"
        elif l > f:
            d = "down"
        else:
            d = "flat"
        out.append({"keyword": kw, "kind": kind, "first": first, "last": last, "dir": d,
                    "history": [h["rank"] for h in hist][-10:]})
    return out
