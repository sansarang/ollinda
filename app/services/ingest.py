"""
Ingest 서비스 — 사장님 업로드(사진+메모) → Asset 저장 → 캡션 초안 생성 → DRAFT 저장.
'1소스 → 멀티채널'의 진입점. MVP에선 인스타 캡션 1종만 생성(Phase 2에서 확장).
"""
from __future__ import annotations

import os
import uuid

from app import db, storage, seo, vision, reach
from app.domain.models import (AssetType, Channel, ContentKind, ContentPiece,
                               ContentStatus, Tenant)
from app.services.generate import generate_for
from app.strategies import resolve_strategy, ordered_kinds, kind_rank


def ingest_upload(tenant: Tenant, files: list[tuple[bytes, str]], note: str,
                  kinds: list[ContentKind] | None = None) -> list[ContentPiece]:
    """files: [(bytes, filename), ...] 여러 장. 1소스(여러 사진) → 멀티채널 생성."""
    kinds = kinds or [ContentKind.CAPTION, ContentKind.BLOG, ContentKind.SHORT, ContentKind.X_POST]
    # 사업형태 전략에 따라 생성 순서 정렬 (셀러=영상 우선, 소상공인=블로그 우선)
    strat = resolve_strategy(tenant)
    kinds = ordered_kinds(strat, kinds)
    if not files:
        return []
    paths: list[str] = []
    for data, fname in files:
        paths.append(storage.save_upload(data, fname or "photo.jpg", tenant.id))
    # 대표 Asset(첫 장)에 메모 기록 — 나머지 장은 images로 전달
    asset = db.create_asset(tenant.id, AssetType.IMAGE, paths[0], note)
    # 비전: 대표 사진을 실제 분석해 생성 프롬프트에 반영(키 없으면 ""). DB엔 원본 메모 유지.
    analysis = vision.analyze(paths[0], tenant.industry)
    if analysis:
        asset.note = f"{note}\n\n[사진 분석(실제 이미지 기반)]\n{analysis}"
    pieces = generate_for(tenant, asset, kinds, images=paths)
    for p in pieces:
        p.payload.setdefault("image_path", paths[0])
        p.payload.setdefault("biz_type", getattr(tenant, "biz_type", "local") or "local")
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        p.payload["reach"] = reach.estimate(p.channel.value, p.kind.value, p.payload)
        db.save_piece(p)
    # 인스타 릴스: 숏 영상을 인스타 채널로도 발행(캐러셀 + 릴스 둘 다)
    short = next((p for p in pieces if p.kind == ContentKind.SHORT
                  and p.channel == Channel.YOUTUBE and p.payload.get("video_path")), None)
    if short:
        caption = next((p for p in pieces if p.kind == ContentKind.CAPTION), None)
        reel = ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.INSTAGRAM, kind=ContentKind.SHORT,
            payload={"text": (caption.payload.get("text") if caption else short.payload.get("title", "")),
                     "title": short.payload.get("title", ""),
                     "video_path": short.payload.get("video_path"),
                     "image_path": short.payload.get("image_path"),
                     "image_paths": short.payload.get("image_paths", []),
                     "duration_sec": short.payload.get("duration_sec", 0), "is_reel": True,
                     "target_keywords": short.payload.get("target_keywords", [])},
            status=ContentStatus.DRAFT)
        reel.payload["ranking_audit"] = seo.quality_audit(reel.channel.value, reel.kind.value, reel.payload)
        reel.payload["reach"] = reach.estimate(reel.channel.value, reel.kind.value, reel.payload)
        db.save_piece(reel)
        pieces.append(reel)
    # 인스타 캐러셀(정보 슬라이드) — 사진 1장 → 여러 장 카드 (#2)
    try:
        from app.generators.carousel import build_carousel
        cap_piece = next((p for p in pieces if p.kind == ContentKind.CAPTION), None)
        short_piece = next((p for p in pieces if p.kind == ContentKind.SHORT
                            and p.channel == Channel.YOUTUBE), None)
        blog_piece = next((p for p in pieces if p.kind == ContentKind.BLOG), None)
        pts = (short_piece.payload.get("scene_texts") if short_piece else None) or []
        title = ((blog_piece.payload.get("title") if blog_piece else None)
                 or (short_piece.payload.get("title") if short_piece else None) or tenant.name)
        if cap_piece and pts:
            cdir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id, "carousel")
            cap_piece.payload["carousel_paths"] = build_carousel(
                tenant, title, pts, getattr(tenant, "biz_type", "local") or "local", cdir)
            db.save_piece(cap_piece)
    except Exception:
        pass
    # 네이버 플레이스 연동 — 매장(local/hybrid)이면 블로그에 플레이스 키워드 + 리뷰요청 문구 첨부 (#플레이스전략)
    try:
        if (getattr(tenant, "biz_type", "local") or "local") in ("local", "hybrid"):
            blog_piece = next((p for p in pieces if p.kind == ContentKind.BLOG), None)
            if blog_piece:
                blog_piece.payload["place_keywords"] = seo.place_keywords(tenant.industry, tenant.region)
                blog_piece.payload["review_request"] = seo.review_request(
                    tenant.name, tenant.region, tenant.industry)
                blog_piece.payload["place_search"] = f'네이버에서 "{tenant.name}" 검색 → 플레이스 찜·예약'
                db.save_piece(blog_piece)
    except Exception:
        pass
    _autopilot(tenant, pieces)
    return pieces


def _autopilot(tenant: Tenant, pieces: list[ContentPiece]) -> None:
    """가게 신뢰레벨(autonomy)에 따라 자동 발행 — 운영자 검수 최소화.
    0=수동(전부 검수) / 1=점수게이트(85+ 자동, 미달만 검수) / 2=완전자동(검증 통과 전부)."""
    level = getattr(tenant, "autonomy", 0) or 0
    if level < 1:
        return
    from app.registry import get_publisher
    from app.services.publish import publish_and_record
    # 전략 우선순위대로 발행(셀러=영상 먼저)
    strat = resolve_strategy(tenant)
    for p in sorted(pieces, key=lambda x: kind_rank(strat, x.kind.value)):
        pub = get_publisher(p.channel)
        if not pub.supports_auto_publish:        # 네이버 등 반자동은 자동 발행 제외
            continue
        score = (p.payload.get("ranking_audit") or {}).get("score") or 0
        if level == 1 and score < 85:            # 점수 미달 → 예외(검수 큐로 남김)
            continue
        if pub.validate(p):                      # 채널 규칙 위반 → 예외
            continue
        db.set_piece_status(p.id, ContentStatus.APPROVED)
        p.status = ContentStatus.APPROVED
        publish_and_record(p)
