"""
랜딩 '티저' — 미가입자가 업종을 입력하면 실제로 글을 생성(진짜 품질)하되,
결과는 흐리게 가려 '가입해야 전체 공개'로 유도한다. 영상은 흐린 목업(실렌더 X = 비용/부담 0).
전문가 파이프라인(전략가→카피)을 그대로 태워 실제 퀄리티를 보여준다.
"""
from __future__ import annotations

from app import db, seo, storage, vision
from app.domain.models import AssetType, ContentKind


def run_teaser(industry: str, biz_type: str, note: str,
               image_bytes: bytes | None = None, image_name: str | None = None):
    """(tenant_id, asset_id, pieces, brief) — 텍스트 3채널 실제 생성."""
    from app.services.generate import generate_for
    from app.generators.strategist import build_brief, brief_to_directive

    industry = (industry or "").strip() or "우리 가게"
    biz_type = biz_type if biz_type in ("local", "seller", "hybrid") else "local"
    t = db.create_tenant(f"{industry[:16]} 미리보기", industry, "", biz_type)
    db.mark_tenant_demo(t.id)

    paths: list[str] = []
    if image_bytes:
        paths.append(storage.save_upload(image_bytes, image_name or "photo.jpg", t.id))
    asset = db.create_asset(t.id, AssetType.IMAGE, paths[0] if paths else "", note or "")
    if paths:
        analysis = vision.analyze(paths[0], industry)
        if analysis:
            asset.note = f"{note}\n\n[사진 분석]\n{analysis}"

    brief = build_brief(t, asset)                          # 🎯 전략가
    asset.note = asset.note + brief_to_directive(brief)
    brief_pub = {k: v for k, v in brief.items() if not k.startswith("_")}

    kinds = [ContentKind.CAPTION, ContentKind.BLOG, ContentKind.X_POST]
    pieces = generate_for(t, asset, kinds, images=(paths or None))   # ✍️ 카피
    for p in pieces:
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_pub
        db.save_piece(p)
    return t.id, asset.id, pieces, brief_pub
