"""
🔍 SEO 편집장 (Expert #3)
품질검수 점수가 낮은 글(블로그/캡션)만 골라 1회 리라이트 → 상위노출 요인(키워드 배치·FAQ·길이) 보강.
조건부(저점수만) 실행 = 크레딧 절약. 키 없으면 원문 유지.
"""
from __future__ import annotations

import os

from app import seo


def polish(tenant, piece, threshold: int = 80) -> bool:
    """저품질 글을 SEO 관점에서 재작성. 개선하면 True(payload 갱신)."""
    kind = piece.kind.value
    if kind not in ("blog", "caption"):
        return False
    audit = piece.payload.get("ranking_audit") or {}
    warnings = audit.get("warnings") or []
    if (audit.get("score", 100) >= threshold) or not warnings:
        return False
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    from app.generators.text_claude import _call_llm, _parse_sections, _ensure_photo_markers
    kw = (piece.payload.get("target_keywords") or [""])[0]
    issues = "\n".join(f"- {w}" for w in warnings[:6])
    try:
        if kind == "blog":
            imgs = piece.payload.get("image_paths") or []
            prompt = (
                "너는 네이버 상위노출을 전문으로 하는 'SEO 편집장'이다. 아래 블로그 초안의 "
                "'검수 지적사항'을 모두 해결해 재작성하라. 사실 왜곡·과장 금지, 자연스럽게.\n\n"
                f"[핵심 키워드] {kw} (제목 맨앞·첫문장·본문 2회↑ 자연 포함)\n"
                f"[검수 지적사항]\n{issues}\n"
                "[필수] 제목 25~35자·키워드 맨앞, 첫문장에 키워드, '## 자주 묻는 질문'(Q&A 2~3), "
                f"본문 1200자↑, [사진1]..[사진{len(imgs)}] 마커 순서대로 유지.\n\n"
                f"[원본 제목]\n{piece.payload.get('title','')}\n[원본 본문]\n{piece.payload.get('body','')}\n\n"
                "아래 형식 그대로 출력:\n[제목]\n(...)\n[본문]\n(... [사진N] 포함 ...)"
            )
            raw = _call_llm(prompt, max_tokens=3000)
            d = _parse_sections(raw, ["제목", "본문"])
            if d.get("본문"):
                piece.payload["title"] = d.get("제목") or piece.payload.get("title", "")
                piece.payload["body"] = _ensure_photo_markers(d["본문"], len(imgs))
                # 셀러 구매블록 유지
                buy = piece.payload.get("buy_block") or ""
                if buy and buy not in piece.payload["body"]:
                    piece.payload["body"] = piece.payload["body"].rstrip() + "\n\n" + buy
            else:
                return False
        else:  # caption
            prompt = (
                "너는 인스타 전환을 잘 만드는 'SEO/카피 편집장'이다. 아래 캡션의 지적사항을 "
                "해결해 다시 써라. 과장 금지, 이모지 적당히, 해시태그 8~12개 유지.\n\n"
                f"[핵심 키워드] {kw}\n[검수 지적사항]\n{issues}\n\n[원본]\n{piece.payload.get('text','')}\n\n"
                "재작성한 캡션만 출력(머리표 없이)."
            )
            new = _call_llm(prompt, max_tokens=1200).strip()
            if len(new) < 20:
                return False
            piece.payload["text"] = new
    except Exception:
        return False
    # 재검수 → 개선 여부 기록
    piece.payload["ranking_audit"] = seo.quality_audit(piece.channel.value, kind, piece.payload)
    piece.payload["edited_by_seo"] = True
    return True
