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
    # 텍스트는 즉시(빠름), 영상(SHORT)+릴스+캐러셀은 비동기 → 요청이 타임아웃 없이 바로 끝남
    kinds = kinds or [ContentKind.CAPTION, ContentKind.BLOG, ContentKind.X_POST]
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
    # 👁 비전: 대표 사진을 실제 분석해 생성 프롬프트에 반영(키 없으면 ""). DB엔 원본 메모 유지.
    analysis = vision.analyze(paths[0], tenant.industry)
    if analysis:
        asset.note = f"{note}\n\n[사진 분석(실제 이미지 기반)]\n{analysis}"
    # 🎯 마케팅 전략가 — 전 채널이 공유할 크리에이티브 브리프(1콜). 프롬프트에 주입 → 채널 일관성.
    from app.generators.strategist import build_brief, brief_to_directive
    from app.generators.editor import polish
    brief = build_brief(tenant, asset)
    asset.note = asset.note + brief_to_directive(brief)
    brief_public = {k: v for k, v in brief.items() if not k.startswith("_")}
    pieces = generate_for(tenant, asset, kinds, images=paths)   # ✍️ 카피라이터·🎬 영상감독
    for p in pieces:
        p.payload.setdefault("image_path", paths[0])
        p.payload.setdefault("biz_type", getattr(tenant, "biz_type", "local") or "local")
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        polish(tenant, p)                                       # 🔍 SEO 편집장(저점수만 리라이트)
        p.payload["reach"] = reach.estimate(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_public
        ex = ["🎯 전략가", "✍️ 카피라이터"]
        if p.kind == ContentKind.SHORT:
            ex.append("🎬 영상감독")
        if p.payload.get("edited_by_seo"):
            ex.append("🔍 SEO편집장")
        p.payload["experts"] = ex
        db.save_piece(p)
    # 🎬 영상(SHORT)+릴스+캐러셀 = 백그라운드에서 생성(요청 막지 않음, /kit·폴링으로 표시)
    _spawn_video_bundle(tenant, asset, paths, brief_public)
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


def _spawn_video_bundle(tenant: Tenant, asset, paths: list[str], brief_public: dict) -> None:
    """영상(SHORT)+릴스+캐러셀을 별도 스레드에서 생성·저장 — 요청을 막지 않음(폴링/새로고침으로 표시)."""
    import threading

    def _run():
        try:
            _make_video_bundle(tenant, asset, paths, brief_public)
        except Exception:
            import logging
            logging.exception("[ingest] 비동기 영상 번들 실패 tenant=%s", tenant.id)
    threading.Thread(target=_run, daemon=True).start()


def _make_video_bundle(tenant: Tenant, asset, paths: list[str], brief_public: dict) -> None:
    shorts = generate_for(tenant, asset, [ContentKind.SHORT], images=paths)   # 🎬 영상감독
    for p in shorts:
        p.payload.setdefault("image_path", paths[0])
        p.payload.setdefault("biz_type", getattr(tenant, "biz_type", "local") or "local")
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        p.payload["reach"] = reach.estimate(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_public
        p.payload["experts"] = ["🎯 전략가", "✍️ 카피라이터", "🎬 영상감독"]
        db.save_piece(p)
    short = next((p for p in shorts if p.kind == ContentKind.SHORT
                  and p.channel == Channel.YOUTUBE and p.payload.get("video_path")), None)
    if not short:
        return
    saved = db.get_set_pieces(asset.id)
    caption = next((p for p in saved if p.kind == ContentKind.CAPTION), None)
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
    # 𝕏 X에도 같은 숏폼 영상 첨부(글 + 영상)
    xp = next((p for p in saved if p.kind == ContentKind.X_POST), None)
    if xp:
        xp.payload["video_path"] = short.payload.get("video_path")
        xp.payload["image_paths"] = short.payload.get("image_paths", [])
        db.save_piece(xp)
    try:
        from app.generators.carousel import build_carousel
        blog = next((p for p in saved if p.kind == ContentKind.BLOG), None)
        pts = short.payload.get("scene_texts") or []
        title = ((blog.payload.get("title") if blog else None) or short.payload.get("title") or tenant.name)
        if caption and pts:
            cdir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id, "carousel")
            caption.payload["carousel_paths"] = build_carousel(
                tenant, title, pts, getattr(tenant, "biz_type", "local") or "local", cdir)
            db.save_piece(caption)
    except Exception:
        pass
    # ☁ 생성된 영상을 R2에 미러(설정 시) — 로컬 자동정리 후에도 영구 서빙
    try:
        from app import storage as _st
        _st.mirror_to_r2(short.payload.get("video_path"))
    except Exception:
        pass
