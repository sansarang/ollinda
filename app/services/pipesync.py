"""
발행 URL 등록 → 파이프라인 자동 연쇄(파이프 A1·A2).

네이버 블로그는 자동 발행이 불가하므로 'URL 한 번 붙여넣기'(또는 RSS 자동 매칭)를 방아쇠로,
그 뒤는 전부 자동: 발행일 보정(RSS pubDate) → 발행 기록 → 생존신고 즉시 시작(색인+첫 순위
스냅샷, kind='post') → 키워드 순위 섹션 자동 편입 → 추적링크 유무 안내.

정직성: 매칭 임계 미달은 발행으로 만들지 않고 '확인 요청' 알림만. 외부 글도 사실만 알린다.
"""
from __future__ import annotations

import logging
import threading

from app import db

_log = logging.getLogger("shopcast.pipesync")

AUTO_MATCH_MIN = 0.75    # RSS 자동 연결 임계(이상=자동+확인 알림, 미만=선택 요청 알림)


def _rss_meta_for_url(t, url: str) -> dict:
    """RSS에서 이 URL 글의 실제 제목·발행일 조회 — '0일차' 오표기(수동 등록 시각) 해결(A1)."""
    try:
        from app.services import blogrank, blogsync
        bid = getattr(t, "blog_id", "") or blogsync.normalize_blog_id(url)
        if not bid:
            return {}
        for post in (blogsync.fetch_feed(bid).get("posts") or []):
            if blogrank._norm_post_url(post.get("link", "")) == blogrank._norm_post_url(url):
                pa = post.get("published_at")
                return {"post_title": post.get("title") or "",
                        "published_at": (pa.isoformat() if pa is not None and hasattr(pa, "isoformat") else "")}
    except Exception:
        pass
    return {}


def confirm_publish(t, piece, url: str, matched_by: str, score: float = 1.0,
                    post_title: str = "", published_at: str = "") -> None:
    """발행 확인 공통 처리 + 자동 연쇄(A2). main·RSS 폴링 잡 공용."""
    from app.domain.models import Channel, ContentStatus
    # ① 발행일·제목 보정(RSS pubDate) — 수동 등록도 실제 발행일로 기록
    if not (published_at and post_title):
        meta = _rss_meta_for_url(t, url)
        published_at = published_at or meta.get("published_at", "")
        post_title = post_title or meta.get("post_title", "")
    db.record_blog_publish(t.id, piece.id, url, published_at, matched_by, score, post_title)
    try:
        db.create_publication(piece.id, Channel.NAVER_BLOG, url,
                              {"manual": matched_by == "manual", "source": matched_by, "url": url})
        db.set_piece_status(piece.id, ContentStatus.PUBLISHED)
    except Exception:
        pass
    try:                                   # 발행 시점 순위 baseline + 7일 리포트 예약(기존 성과 루프)
        from app.services import growth
        growth.on_publish(t, piece)
    except Exception:
        pass
    # ② 생존신고 즉시 시작(A2-b·c) — 색인 확인 + 첫 순위 스냅샷(kind='post').
    #    스냅샷이 생기면 '키워드 순위' 섹션에도 자동 편입된다. 네이버 콜이라 백그라운드로.
    def _kick():
        try:
            from app.services import race
            race.track_publish(t, piece, db.get_blog_publish(piece.id) or {})
        except Exception:
            _log.exception("[pipesync] 생존신고 시작 실패 piece=%s", piece.id)
    threading.Thread(target=_kick, daemon=True).start()
    # ③ 추적링크 유무(A2-d) — 없으면 안내(있으면 콘텐츠별 유입이 자동 매핑됨)
    try:
        if not (piece.payload or {}).get("tracked_url"):
            db.add_notice(t.id, "pipe_link",
                          "방금 등록한 글엔 올린다 추적링크가 없어요 — 링크가 있어야 '이 글로 온 손님'이 집계돼요. "
                          "다음 글부턴 자동으로 넣어드려요.")
    except Exception:
        pass


def auto_sync_all() -> dict:
    """RSS 폴링(A1 보조 경로, 스케줄 2~4시간) — 새 글 감지 → 생성글 자동 매칭/확인 요청/외부 글 안내.
    반환 {auto, ask, external}."""
    from app import ratelimit
    from app.services import blogsync
    auto = ask = ext = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        bid = getattr(t, "blog_id", "") if t else ""
        if not (t and bid):
            continue
        try:
            feed = blogsync.fetch_feed(bid)
            posts = feed.get("posts") or []
            if not (feed.get("ok") and posts):
                continue
            known = {(_norm(p.get("published_url"))) for p in db.list_blog_publishes(tid, limit=30)}
            new_posts = [p for p in posts if _norm(p.get("link")) not in known]
            if not new_posts:
                continue
            pending = [p for p in _blog_pieces(tid) if not db.get_blog_publish(p.id)]
            found = blogsync.find_published(pending, new_posts)
            by_id = {p.id: p for p in pending}
            matched_urls = set()
            for f in found:
                piece = by_id.get(f["piece_id"])
                if not piece:
                    continue
                if (f.get("score") or 0) >= AUTO_MATCH_MIN:
                    confirm_publish(t, piece, f["url"], "rss", f["score"], f["post_title"],
                                    (f["published_at"].isoformat() if f.get("published_at") else ""))
                    db.add_notice(tid, "pipe_auto",
                                  f"새 발행 글을 자동 연결했어요 — '{(f['post_title'] or '')[:40]}'. "
                                  "맞는지 리포트에서 한 번만 확인해주세요. 순위 추적은 이미 시작했어요.")
                    matched_urls.add(_norm(f["url"]))
                    auto += 1
                else:
                    db.add_notice(tid, "pipe_ask",
                                  f"블로그에서 새 글을 발견했어요 — '{(f['post_title'] or '')[:40]}'. "
                                  "올린다에서 만든 글이면 리포트 → 발행 확인에서 주소를 붙여넣어 주세요.")
                    ask += 1
            # 외부에서 직접 쓴 글(매칭 후보조차 없음) — 사실만 알림(재알림은 4일 캐시로 억제)
            for p in new_posts:
                nu = _norm(p.get("link"))
                if nu in matched_urls:
                    continue
                ck = "extpost:" + nu
                if ratelimit.cache_get(ck, 4 * 86400) is not None:
                    continue
                ratelimit.cache_set(ck, 1)
                db.add_notice(tid, "pipe_ext",
                              f"직접 쓰신 글 '{(p.get('title') or '')[:40]}'을 발견했어요 — 이 글도 순위 추적을 원하시면 "
                              "그 글 주소와 타겟 키워드를 알려주세요. (리포트 → 발행 확인)")
                ext += 1
        except Exception:
            _log.exception("[pipesync] auto_sync 실패 tenant=%s", tid)
    if auto or ask or ext:
        _log.info("[pipesync] RSS 자동매칭 auto=%d ask=%d ext=%d", auto, ask, ext)
    return {"auto": auto, "ask": ask, "external": ext}


def _norm(u: str) -> str:
    from app.services import blogrank
    return blogrank._norm_post_url(u or "")


def _blog_pieces(tid: str, limit_sets: int = 30) -> list:
    out = []
    for s in db.list_sets(tenant_id=tid, limit=limit_sets):
        for p in db.get_set_pieces(s["asset_id"]):
            if p.kind.value == "blog":
                out.append(p)
    return out
