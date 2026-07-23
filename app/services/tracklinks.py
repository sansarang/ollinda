"""
콘텐츠별 추적링크(추적 P1) — 발행물마다 /r/{code}?src={channel}&content={id} 를 본문에 자동 포함.

정직성: 측정하는 것은 '올린다 추적링크를 경유한 클릭'뿐이다. 네이버 조회수·인스타 노출수가
아니며, UI에서도 그렇게 표기하지 않는다(TRACKING.md).
설계: code(목적지 단축링크)는 가게당 재사용하고, 채널·콘텐츠는 쿼리 파라미터로 구분해
행 단위(link_clicks)로 기록 — 링크 남발 없이 콘텐츠 단위 어트리뷰션.
"""
from __future__ import annotations

import os
import re

from app import db
from app.domain.models import ContentKind


def _base() -> str:
    return os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")


def tenant_link(t) -> "dict | None":
    """가게 대표 목적지(매장=플레이스/지도, 셀러=스토어) 추적 링크 get-or-create."""
    biz = getattr(t, "biz_type", "local") or "local"
    if biz == "seller":
        target, label = (getattr(t, "buy_url", "") or getattr(t, "map_url", "")), "스토어"
    else:
        target, label = (getattr(t, "map_url", "") or getattr(t, "buy_url", "")), "네이버 플레이스"
    if not target and getattr(t, "name", ""):        # 폴백: 상호로 네이버 지도 검색
        from urllib.parse import quote as _q
        target, label = "https://map.naver.com/p/search/" + _q(t.name), "네이버 지도"
    return db.ensure_track_link(t.id, target, label)


def tracked_url(t, channel: str, content_id: str, set_id: str = "") -> str:
    """콘텐츠·채널별 추적 URL. 목적지 없으면 ''(억지 링크 금지). set_id=세트(asset) 앞8자."""
    link = tenant_link(t)
    if not link:
        return ""
    _s = f"&set={(set_id or '')[:8]}" if set_id else ""
    return f"{_base()}/r/{link['code']}?src={channel}&content={(content_id or '')[:8]}{_s}"


def inject(t, piece) -> bool:
    """발행 소재 본문에 추적링크 자동 포함(추적 P1). 변경 여부 반환.
    - 셀러: 본문 속 원본 구매링크(buy_url)를 콘텐츠별 추적 URL로 치환.
    - 매장: 원본 지도링크(map_url) 치환 + 블로그 본문에 링크가 없으면 '찾아오는 길' 유도 한 줄 추가.
    - X는 제외(외부 링크 = 도달 50~90% 감소 정책 유지), 영상 제외.
    """
    if piece.kind not in (ContentKind.BLOG, ContentKind.CAPTION, ContentKind.MARKETPLACE):
        return False
    # ★ 네이버 스팸 게이트 — 네이버가 단축링크를 저품질/스팸 판정할 위험. 실계정 테스트 글로
    #   발행 정상·저품질 무영향 확인 전까지 네이버(블로그) 본문 치환 보류(SNS·마켓·당근만).
    #   확인 후 env NAVER_SHORTLINK_OK=1 로 전면 적용.
    if piece.kind == ContentKind.BLOG and os.environ.get("NAVER_SHORTLINK_OK") != "1":
        return False
    url = tracked_url(t, piece.channel.value, piece.id, set_id=getattr(piece, "asset_id", ""))
    if not url:
        return False
    import re as _r
    # 세트 매물 링크 — 목적지 보존이 관건: 대표 링크(지도/스토어)가 아니라 '매물 URL로 가는' 전용
    # 추적 링크를 만들어 치환한다(플레이스행 코드로 바꿔치기하면 손님이 엉뚱한 곳으로 감 — 실측 결함).
    _lm_src = piece.payload.get("gen_source") or ""
    if not _lm_src:                                     # 캡션 등 gen_source 없는 피스 → 세트 blog에서 폴백
        _blog = next((q for q in db.get_set_pieces(piece.asset_id)
                      if q.kind == ContentKind.BLOG and (q.payload or {}).get("gen_source")), None)
        _lm_src = (_blog.payload.get("gen_source") if _blog else "") or ""
    _lm = _r.search(r"\[매물 링크\(실제[^\]]*\]\s*(https?://\S+)", _lm_src)
    listing_raw = (_lm.group(1) if _lm else "").rstrip(".,)")
    listing_url = ""
    if listing_raw:
        _ll = db.ensure_track_link(t.id, listing_raw, "매물")
        if _ll:
            listing_url = (f"{_base()}/r/{_ll['code']}?src={piece.channel.value}"
                           f"&content={(piece.id or '')[:8]}&set={(getattr(piece, 'asset_id', '') or '')[:8]}")
    raws = [u for u in {(getattr(t, "buy_url", "") or "").strip(),
                        (getattr(t, "map_url", "") or "").strip()} if u]
    changed = False
    for key in ("body", "text", "detail_body"):
        v = piece.payload.get(key)
        if not v:
            continue
        nv = v
        if listing_raw and listing_url and listing_raw in nv:
            nv = nv.replace(listing_raw, listing_url)   # 매물 → 매물행 추적 URL(목적지 보존)
        for raw in raws:
            if raw in nv:
                nv = nv.replace(raw, url)
        if nv != v:
            piece.payload[key] = nv
            changed = True
    # 매장 블로그: 본문에 추적 URL이 없으면 방문 유도 라인 추가(지도/예약으로 가는 실측 경로)
    biz = getattr(t, "biz_type", "local") or "local"
    if piece.kind == ContentKind.BLOG and biz != "seller":
        body = piece.payload.get("body") or ""
        if body and url not in body and not re.search(r"/r/[a-z0-9]{7}", body):
            piece.payload["body"] = body.rstrip() + f"\n\n찾아오는 길·예약: {url}"
            changed = True
    if changed:
        piece.payload["tracked_url"] = url
    return changed
