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


EXT_MAX_PER_RUN = 6      # 외부 글 자동 등록 상한(첫 소급 시 RSS 폭주 방지)
EXT_MAX_AGE_DAYS = 30    # 이보다 오래된 외부 글은 자동 추적 안 함(관심 밖 이력)


def extract_kw(title: str, industry: str = "", region: str = "") -> str:
    """외부 글 제목 → 추적 키워드 자동 추출. 검색형 제목은 키워드를 앞에 두므로
    업종 토큰이 나오는 지점까지를 키워드로(예: '부산광역시 동구 썬팅업체 후기…' → '부산광역시 동구 썬팅업체').
    업종 토큰이 없으면 앞 3어절. 날조 없음 — 제목에 있는 말만 쓴다."""
    import re
    t = re.split(r"[,|(\[]", (title or "").strip())[0].strip()
    toks = [w for w in re.split(r"[\s·—–-]+", t) if w]
    if not toks:
        return ""
    ind = (industry or "").strip()
    idx = None
    for i, w in enumerate(toks[:6]):
        if ind and (ind in w or w in ind) and len(w) >= 2:
            idx = i
    if idx is not None:
        return " ".join(toks[:idx + 1])[:30]
    return " ".join(toks[:3])[:30]


def _ext_id(tenant_id: str, url: str) -> str:
    """외부 글(올린다 미생성)의 발행 기록 키 — tenant+URL 결정적 id.
    ⚠️ tenant 스코프 필수: URL만 해시하면 같은 블로그를 여러 가게가 추적할 때 piece_id(PK)가
    충돌해 upsert가 서로의 행을 뺏는다(마지막 동기화 tenant가 전부 가져감 — 실측으로 확인된 유실 원인)."""
    import hashlib
    return "ext_" + hashlib.sha1(f"{tenant_id}|{_norm(url)}".encode()).hexdigest()[:12]


def auto_sync_tenant(t) -> dict:
    """한 가게 RSS 완전 자동 동기화 — 새 글 감지 시 버튼 없이:
    올린다 글이면 자동 매칭 연결, 외부 글이어도 키워드 자동 추출로 추적 시작.
    반환 {auto, external}. 스케줄러(2시간)·'지금 새로고침' 버튼 공용."""
    bid = getattr(t, "blog_id", "") if t else ""
    if not (t and bid):
        return {"auto": 0, "external": 0}
    from app.services import blogsync
    feed = blogsync.fetch_feed(bid)
    posts = feed.get("posts") or []
    if not (feed.get("ok") and posts):
        return {"auto": 0, "external": 0}
    known = {(_norm(p.get("published_url"))) for p in db.list_blog_publishes(t.id, limit=50)}
    new_posts = [p for p in posts if _norm(p.get("link")) not in known]
    if not new_posts:
        return {"auto": 0, "external": 0}
    auto = ext = 0
    pending = [p for p in _blog_pieces(t.id) if not db.get_blog_publish(p.id)]
    found = blogsync.find_published(pending, new_posts)
    by_id = {p.id: p for p in pending}
    matched_urls = set()
    for f in found:
        piece = by_id.get(f["piece_id"])
        if piece and (f.get("score") or 0) >= AUTO_MATCH_MIN:
            confirm_publish(t, piece, f["url"], "rss", f["score"], f["post_title"],
                            (f["published_at"].isoformat() if f.get("published_at") else ""))
            matched_urls.add(_norm(f["url"]))
            auto += 1
    # 외부(또는 매칭 실패) 글 — 그래도 자동 추적: 제목에서 키워드 추출 → 발행 기록 + 생존신고
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=EXT_MAX_AGE_DAYS)
    for p in new_posts:
        if ext >= EXT_MAX_PER_RUN:
            break
        nu = _norm(p.get("link"))
        if nu in matched_urls:
            continue
        pa = p.get("published_at")
        if pa is not None and hasattr(pa, "isoformat") and pa.replace(tzinfo=None) < cutoff:
            continue
        kw = extract_kw(p.get("title") or "", t.industry or "", t.region or "")
        if not kw:
            continue
        pid = _ext_id(t.id, p.get("link") or "")
        db.record_blog_publish(t.id, pid, p.get("link") or "",
                               (pa.isoformat() if pa is not None and hasattr(pa, "isoformat") else ""),
                               "rss_auto", 0.0, p.get("title") or "", target_kw=kw)
        pub = db.get_blog_publish(pid)
        try:
            from app.services import race
            race.track_publish(t, None, pub or {})
        except Exception:
            _log.exception("[pipesync] 외부 글 추적 시작 실패 %s", pid)
        ext += 1
    if auto or ext:
        db.add_notice(t.id, "pipe_auto",
                      f"블로그 새 글 {auto + ext}건을 자동으로 추적하기 시작했어요 — "
                      "색인·순위는 리포트 '내 네이버 블로그'에서 실시간으로 보여드려요.")
        _log.info("[pipesync] auto_sync tenant=%s auto=%d ext=%d", t.id, auto, ext)
    return {"auto": auto, "external": ext}


def auto_sync_all() -> dict:
    """전 가게 RSS 완전 자동 동기화(스케줄 2시간 + 배포 직후 1회 소급)."""
    auto = ext = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if not (t and getattr(t, "blog_id", "")):
            continue
        try:
            r = auto_sync_tenant(t)
            auto += r["auto"]
            ext += r["external"]
        except Exception:
            _log.exception("[pipesync] auto_sync 실패 tenant=%s", tid)
    if auto or ext:
        _log.info("[pipesync] RSS 완전자동 동기화 auto=%d ext=%d", auto, ext)
    return {"auto": auto, "external": ext}


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
