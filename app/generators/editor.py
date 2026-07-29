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
            # ★ 부분 수정(패치) 프로토콜 — 전문 재작성 폐지(실측: 재작성 60~90초가 다듬기 병목의 본체).
            #   지적사항은 대부분 국소적(제목·누락 섹션·특정 문단) → 문제 부분만 받아 원문에 적용.
            #   출력 3,000→~600토큰: 속도 ↑ + 원문 보존(재작성이 잘 쓴 문단까지 갈아엎는 품질 롤백 방지).
            imgs = piece.payload.get("image_paths") or []
            body0 = piece.payload.get("body", "")
            prompt = (
                "너는 네이버 상위노출을 전문으로 하는 'SEO 편집장'이다. 아래 블로그의 '검수 지적사항'을 "
                "해결하되 글 전체를 다시 쓰지 마라 — **문제 부분만 최소 수정**한다(원문 유지 원칙).\n\n"
                f"{seo.FACTS_RULE}\n"
                f"[핵심 키워드] {kw} — 제목 앞부분(첫 12자 안)에 자연스럽게. 기계적으로 맨 앞에 박지 말고 "
                "어색하면 어순·조사를 바꿔 자연스러운 문장을 우선하라.\n"
                f"[검수 지적사항]\n{issues}\n\n"
                f"[원본 제목]\n{piece.payload.get('title','')}\n[원본 본문]\n{body0}\n\n"
                "아래 형식 그대로 출력(고칠 것 없는 항목은 '유지' 또는 '없음' 한 단어):\n"
                "[제목]\n(새 제목 22~35자, 또는 '유지')\n"
                "[추가섹션]\n(누락 지적된 필수 섹션(FAQ·표·요약)이 있으면 그 섹션 전체 마크다운, 없으면 '없음')\n"
                "[문단수정]\n(고칠 문단이 있으면 반복 —\n<<<원문 문단의 첫 6어절 그대로>>>\n교체할 새 문단\n— 없으면 '없음')"
            )
            raw = _call_llm(prompt, "claude-sonnet-5", 1400)
            d = _parse_sections(raw, ["제목", "추가섹션", "문단수정"])
            import re as _r
            changed = False
            t_new = (d.get("제목") or "").strip()
            if t_new and t_new not in ("유지", "없음") and 10 <= len(t_new) <= 60:
                piece.payload["title"] = t_new
                changed = True
            pm = (d.get("문단수정") or "").strip()
            if pm and pm != "없음":
                paras = body0.split("\n\n")
                for m in _r.finditer(r"<<<(.+?)>>>\s*\n(.+?)(?=\n?<<<|\Z)", pm, _r.S):
                    anchor = " ".join(m.group(1).split())[:30]
                    repl = m.group(2).strip()
                    if not anchor or len(repl) < 10:
                        continue
                    for i, pgh in enumerate(paras):
                        if " ".join(pgh.split()).startswith(anchor):
                            mk = _r.findall(r"\[사진\d+\]", pgh)     # 문단의 사진 마커 보존
                            if mk and not _r.search(r"\[사진\d+\]", repl):
                                repl = repl + "\n" + "\n".join(mk)
                            paras[i] = repl
                            changed = True
                            break
                body0 = "\n\n".join(paras)
            add = (d.get("추가섹션") or "").strip()
            if add and add != "없음" and len(add) >= 20:
                # 고정정보 블록(찾아오는 길) 앞에 삽입 — 없으면 끝에
                paras = body0.split("\n\n")
                _fi = next((i for i, pgh in enumerate(paras) if "찾아오는 길" in pgh), None)
                if _fi is not None:
                    paras.insert(_fi, add)
                    body0 = "\n\n".join(paras)
                else:
                    body0 = body0.rstrip() + "\n\n" + add
                changed = True
            if not changed:
                return False
            piece.payload["body"] = _ensure_photo_markers(body0, len(imgs))
            # 셀러 구매블록 유지
            buy = piece.payload.get("buy_block") or ""
            if buy and buy not in piece.payload["body"]:
                piece.payload["body"] = piece.payload["body"].rstrip() + "\n\n" + buy
        else:  # caption
            prompt = (
                "너는 인스타 전환을 잘 만드는 'SEO/카피 편집장'이다. 아래 캡션의 지적사항을 "
                "해결해 다시 써라. 과장 금지, 이모지 적당히, 해시태그 3~5개만(과다 시 도달↓).\n\n"
                f"{seo.FACTS_RULE}\n"
                f"[핵심 키워드] {kw}\n[검수 지적사항]\n{issues}\n\n[원본]\n{piece.payload.get('text','')}\n\n"
                "재작성한 캡션만 출력(머리표 없이)."
            )
            new = _call_llm(prompt, "claude-sonnet-5", 1200).strip()
            if len(new) < 20:
                return False
            piece.payload["text"] = new
    except Exception:
        return False
    # 재검수 → 개선 여부 기록
    piece.payload["ranking_audit"] = seo.quality_audit(piece.channel.value, kind, piece.payload)
    piece.payload["edited_by_seo"] = True
    return True
