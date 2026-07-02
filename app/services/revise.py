"""
AI 수정 지시 + 자동 보완.
- revise_piece: 기존 콘텐츠 + 사용자 지시 → 해당 부분 재생성(채널/종류별).
- autofix_instruction: 상위노출 점검 경고 → 자동 수정 지시문 생성.
재생성 후 점수 재계산 + 저장. 키 없으면 더미(_call_llm)로도 동작.
"""
from __future__ import annotations

import os

from app import db, seo
from app.domain.models import ContentKind, ContentPiece
from app.generators.text_claude import (MODEL, _call_llm, _ensure_photo_markers,
                                        _parse_sections)
from app.industries import resolve_industry


def revise_piece(piece: ContentPiece, instruction: str) -> ContentPiece:
    tenant = db.get_tenant(piece.tenant_id)
    prof = resolve_industry(tenant.industry if tenant else "")
    gname = tenant.name if tenant else ""
    p = piece.payload

    if piece.kind == ContentKind.BLOG:
        cur = f"[제목]\n{p.get('title','')}\n[본문]\n{p.get('body','')}"
        prompt = (f"[가게] {gname} ({prof.name})\n[페르소나] {prof.persona}\n"
                  f"[기존 블로그 글]\n{cur}\n\n[사용자 수정 지시] {instruction}\n\n"
                  f"{seo.BLOG_DIRECTIVES}\n\n수정 지시를 반영해 다시 써라. 형식 그대로:\n"
                  "[제목]\n..\n[메타설명]\n..\n[본문]\n..\n[키워드]\n..")
        raw = _call_llm(prompt, MODEL, 3000)
        d = _parse_sections(raw, ["제목", "메타설명", "본문", "키워드"])
        imgs = p.get("image_paths") or []
        if d.get("제목"):
            p["title"] = d["제목"]
        if d.get("메타설명"):
            p["meta_description"] = d["메타설명"]
        if d.get("본문"):
            p["body"] = _ensure_photo_markers(d["본문"], len(imgs))
        if d.get("키워드"):
            kws = [k.strip().lstrip("#") for k in d["키워드"].replace("\n", ",").split(",") if k.strip()]
            if kws:
                p["tags"] = kws
                p["seo_keywords"] = kws

    elif piece.kind == ContentKind.SHORT:
        cur = (f"제목:{p.get('title','')}\n훅:{p.get('hook_strategy','')}\n"
               f"자막:{p.get('subtitle','')}\n내레이션:{p.get('narration','')}")
        prompt = (f"[가게] {gname} ({prof.name})\n[페르소나] {prof.persona}\n"
                  f"[기존 숏 기획]\n{cur}\n\n[사용자 수정 지시] {instruction}\n\n"
                  f"{seo.SHORT_DIRECTIVES}\n\n형식 그대로:\n[제목]\n..\n[훅]\n..\n[내레이션]\n..\n[자막]\n..")
        raw = _call_llm(prompt, MODEL, 1200)
        d = _parse_sections(raw, ["제목", "훅", "내레이션", "자막"])
        if d.get("제목"):
            p["title"] = p["video_title"] = d["제목"]
        if d.get("훅"):
            p["hook_strategy"] = d["훅"]
        if d.get("내레이션"):
            p["narration"] = d["내레이션"]
        new_sub = d.get("자막") or d.get("훅")
        if new_sub and new_sub != p.get("subtitle"):
            p["subtitle"] = new_sub
            _reassemble_short(piece)

    else:  # CAPTION, X_POST
        directive = seo.CAPTION_DIRECTIVES if piece.kind == ContentKind.CAPTION else seo.X_DIRECTIVES
        prompt = (f"[가게] {gname} ({prof.name})\n[페르소나] {prof.persona}\n"
                  f"[기존 글]\n{p.get('text','')}\n\n[사용자 수정 지시] {instruction}\n\n"
                  f"{directive}\n\n수정 지시를 반영해 다시 써라. 한 덩어리 텍스트로만 출력.")
        new = _call_llm(prompt, MODEL, 800)
        p["text"] = new[:280] if piece.kind == ContentKind.X_POST else new

    p["ranking_audit"] = seo.quality_audit(piece.channel.value, piece.kind.value, p)
    db.save_piece(piece)
    return piece


def _reassemble_short(piece: ContentPiece) -> None:
    """자막 변경 시 영상 재조립(기존 생성기 메서드 재사용)."""
    from app.generators.video import ShortVideoGenerator, _per_image
    p = piece.payload
    imgs = [x for x in (p.get("image_paths") or []) if x and os.path.exists(x)]
    if not imgs:
        return
    gen = ShortVideoGenerator()
    vp, note = gen._assemble(imgs, p.get("subtitle", ""), piece.tenant_id, _per_image(len(imgs)))
    vp, _tts, _bgm, anote = gen._add_audio(vp, p.get("narration", ""), piece.tenant_id)
    p["video_path"] = vp
    p["assemble_note"] = note
    p["audio_note"] = anote


def autofix_instruction(audit: dict, kind: str) -> str:
    """점검 경고 → 자동 수정 지시문."""
    tips: list[str] = []
    for w in (audit or {}).get("warnings", []):
        if "1000" in w or "체류" in w:
            tips.append("본문을 1200자 이상으로 늘리고 가격·위치·영업시간 등 실용정보 추가")
        elif "경험" in w or "D.I.A" in w:
            tips.append("1인칭 실제 경험·후기 문장 추가")
        elif "과장" in w or "스팸" in w:
            tips.append("과장·광고성·낚시 표현 제거")
        elif "소제목" in w:
            tips.append("## 소제목 3개 이상으로 구조화")
        elif "사진 마커" in w:
            tips.append("문단 사이에 [사진N] 마커 배치")
        elif "훅" in w:
            tips.append("0~3초 강한 훅 추가")
        elif "해시태그" in w:
            tips.append("해시태그 8개 이상으로")
        elif "남발" in w:
            tips.append("키워드 반복 줄이기")
    return " / ".join(dict.fromkeys(tips)) or "전반적으로 더 자연스럽고 성과나게 다듬어줘"
