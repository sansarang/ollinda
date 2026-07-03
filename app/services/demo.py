"""
랜딩 무료체험(미가입) — 업종/설명(사진 선택)만으로 5채널 중 '글' 3채널을 실시간 생성.
영상(유튜브·릴스)은 무겁고 익명 남발 시 서버 부담 → 데모에서 제외(가입 유도).
전문가 파이프라인(전략가→카피)을 그대로 태워 실제 품질을 보여준다.
"""
from __future__ import annotations

from app import db, seo, storage, vision
from app.domain.models import AssetType, ContentKind


def run_demo(industry: str, biz_type: str, note: str,
             image_bytes: bytes | None = None, image_name: str | None = None):
    """데모 생성 → (tenant_id, asset_id, pieces, brief). 텍스트 3채널."""
    from app.services.generate import generate_for
    from app.generators.strategist import build_brief, brief_to_directive

    industry = (industry or "").strip() or "우리 가게"
    biz_type = biz_type if biz_type in ("local", "seller", "hybrid") else "local"
    t = db.create_tenant(f"{industry[:16]} 체험", industry, "", biz_type)
    db.mark_tenant_demo(t.id)

    paths: list[str] = []
    if image_bytes:
        paths.append(storage.save_upload(image_bytes, image_name or "photo.jpg", t.id))
    asset = db.create_asset(t.id, AssetType.IMAGE, paths[0] if paths else "", note or "")
    if paths:
        analysis = vision.analyze(paths[0], industry)
        if analysis:
            asset.note = f"{note}\n\n[사진 분석]\n{analysis}"

    brief = build_brief(t, asset)                        # 🎯 전략가
    asset.note = asset.note + brief_to_directive(brief)
    brief_pub = {k: v for k, v in brief.items() if not k.startswith("_")}

    kinds = [ContentKind.CAPTION, ContentKind.BLOG, ContentKind.X_POST]   # 텍스트만
    pieces = generate_for(t, asset, kinds, images=(paths or None))         # ✍️ 카피
    for p in pieces:
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_pub
        p.payload["experts"] = ["🎯 전략가", "✍️ 카피라이터"]
        db.save_piece(p)
    return t.id, asset.id, pieces, brief_pub
