"""
랜딩 '티저' — 미가입자가 업종을 입력하면 실제로 글을 생성(진짜 품질)하되,
결과는 흐리게 가려 '가입해야 전체 공개'로 유도한다. 영상은 흐린 목업(실렌더 X = 비용/부담 0).
전문가 파이프라인(전략가→카피)을 그대로 태워 실제 퀄리티를 보여준다.
"""
from __future__ import annotations

from app import db, seo, storage, vision
from app.domain.models import AssetType, ContentKind


def run_teaser(industry: str, biz_type: str, note: str,
               images: list[tuple[bytes, str]] | None = None,
               intake: dict | None = None):
    """(tenant_id, asset_id, pieces, brief) — 텍스트 3채널 실제 생성. images=여러 장 지원.
    intake(스마트 입력, PHASE 4): {confirmed, analysis, answers, experience} — 확인된 사진내용·
    질문답·경험을 note에 구조 주입(D.I.A.+ 재료). analysis 있으면 vision 재호출 생략(비용 1콜 유지)."""
    from app.services.generate import generate_for
    from app.generators.strategist import build_brief, brief_to_directive

    industry = (industry or "").strip() or "우리 가게"
    biz_type = biz_type if biz_type in ("local", "seller", "hybrid") else "local"
    intake = intake or {}
    t = db.create_tenant(f"{industry[:16]} 미리보기", industry, "", biz_type)
    db.mark_tenant_demo(t.id)

    paths: list[str] = []
    for data, name in (images or [])[:10]:
        if data:
            paths.append(storage.save_upload(data, name or "photo.jpg", t.id))
    # ✨ 사진 자동 보정(전문가 톤) — 데모도 동일하게
    try:
        from app.media import photo_boost
        photo_boost.enhance_all(paths, industry)
        for _p in paths:
            storage.mirror_to_r2(_p)
    except Exception:
        pass
    # 스마트 입력 블록 — 사용자가 확인·입력한 '사실'을 note 맨 앞에(전략가·생성기가 최우선 참조)
    try:
        from app.services import smart_intake
        block = smart_intake.build_intake_note(industry, intake.get("confirmed", ""),
                                               intake.get("answers"), intake.get("experience", ""))
        if block:
            note = (block + "\n" + (note or "")).strip()
        smart_intake.record_insight(industry, intake.get("answers"), intake.get("experience", ""))
    except Exception:
        pass
    asset = db.create_asset(t.id, AssetType.IMAGE, paths[0] if paths else "", note or "")
    if paths:
        # 선추측 단계에서 이미 분석했으면 재호출 생략(같은 사진, 비용 절감)
        analysis = (intake.get("analysis") or "").strip() or vision.analyze(paths[0], industry)
        note_add = f"\n\n[사진 {len(paths)}장 · 캐러셀]" if len(paths) > 1 else ""
        # 확인 절차(SEO_CURRENT §5-3): 사용자 확인 없인 '추측' 라벨 + 단정 금지 — 사실로 각인 방지
        from app.services import smart_intake as _si
        asset.note = f"{note}{note_add}" + _si.analysis_block(analysis, intake.get("confirmed", ""))

    brief = build_brief(t, asset)                          # 🎯 전략가
    asset.note = asset.note + brief_to_directive(brief)
    brief_pub = {k: v for k, v in brief.items() if not k.startswith("_")}

    # 영상(SHORT)을 텍스트보다 '먼저' 백그라운드 시작 — 글 쓰는 60~90초 동안 병렬 렌더
    # (기존엔 텍스트 완료 후 시작이라 영상 대기가 순차로 30~60초 추가됐음, 무료UX 수정3-b)
    _spawn_video(t, asset, paths)                                    # 🎬 영상(비동기·병렬)
    # 텍스트 3채널은 즉시(빠름)
    kinds = [ContentKind.CAPTION, ContentKind.BLOG, ContentKind.X_POST]
    pieces = generate_for(t, asset, kinds, images=(paths or None))   # ✍️ 카피
    _exp = (intake.get("experience") or "").strip()[:200]            # 사장님 경험담(A2 하이라이트)
    for p in pieces:
        if _exp:
            p.payload["owner_story"] = _exp
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_pub
        db.save_piece(p)
    return t.id, asset.id, pieces, brief_pub


def _spawn_video(t, asset, paths):
    """영상(SHORT)을 별도 스레드에서 생성·저장 — 요청을 막지 않음(폴링으로 표시)."""
    import threading

    def _run():
        try:
            from app.services.generate import generate_for
            for p in generate_for(t, asset, [ContentKind.SHORT], images=(paths or None)):
                db.save_piece(p)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()
