"""📐 인자 분해 — 기계 계측이 1급, LLM은 보조(R7).

★ 길이·구조·미디어·수치는 전부 LLM 없이 잰다(0원). 크레딧이 마르면 분해가 멈추는 구조를
  만들지 않는다 — 2026-08-04에 게이트 재실행 한 번이 크레딧을 말린 사고가 있었다.
★ LLM은 실경험 판정 같은 의미 분석에만, 그것도 크레딧이 있을 때만 부른다.
★ 여기서 나오는 것은 **인자 후보의 재료**이지 인과가 아니다(R5). 대조는 contrast가 한다.
"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d[\d,.]*")
_MONEY = re.compile(r"\d[\d,]*\s*(원|만원|천원)")
_DATE = re.compile(r"\d{4}[.\-/년]\s?\d{1,2}[.\-/월]|\d{1,2}월\s?\d{1,2}일")
_Q_TITLE = re.compile(r"(\?|무엇|어떻게|왜|어디|얼마|될까|할까|나요|가요|인가)")
_FAQ = re.compile(r"(자주\s?묻는|Q\s?[.:]|Q&A|질문)")
# 1인칭 실경험 신호 — 언어 규칙만(업종 무관). harvest.FIRST_PERSON과 같은 계열.
_EXP = re.compile(r"(직접|제가|저는|저희는|해보니|해봤|겪었|느꼈|방문했|받아봤|써보니|사용해보)")


def measure(post: dict, keyword: str = "") -> dict:
    """글 하나의 기계 계측. LLM 0콜."""
    title = (post.get("title") or "").strip()
    text = (post.get("text") or "")
    n = len(text)
    words = max(1, len(text.split()))
    paras = [p for p in re.split(r"(?<=[.!?다요])\s{2,}|\n{2,}", text) if p.strip()]
    nums = _NUM.findall(text)
    kw = (keyword or "").replace(" ", "")
    return {
        # 제목
        "title_len": len(title),
        "title_kw_pos": (title.replace(" ", "").find(kw) if kw and kw in title.replace(" ", "") else -1),
        "title_is_question": bool(_Q_TITLE.search(title)),
        # 구조
        "h2": int(post.get("h2") or 0),
        "h3": int(post.get("h3") or 0),
        "heads": int(post.get("h2") or 0) + int(post.get("h3") or 0),
        "tables": int(post.get("tables") or 0),
        "lists": int(post.get("lists") or 0),
        "has_faq": bool(_FAQ.search(text)),
        "paras": len(paras),
        "para_avg_len": round(n / max(1, len(paras)), 1),
        # 미디어
        "images": int(post.get("images") or 0),
        "videos": int(post.get("videos") or 0),
        "img_per_1k": round(int(post.get("images") or 0) * 1000.0 / max(1, n), 2),
        # 정보 밀도
        "text_len": n,
        "numbers": len(nums),
        "num_per_1k": round(len(nums) * 1000.0 / max(1, n), 2),
        "money": len(_MONEY.findall(text)),
        "dates": len(_DATE.findall(text)),
        # 실경험 단서(기계 근사 — LLM 판정은 아래 enrich가 한다)
        "exp_hits": len(_EXP.findall(text)),
        "exp_per_1k": round(len(_EXP.findall(text)) * 1000.0 / max(1, n), 2),
        "words": words,
    }


def enrich_with_llm(rows: list, limit: int = 8) -> dict:
    """의미 분석 보조 — 크레딧이 있을 때만(R7). 없으면 아무것도 하지 않고 사유를 돌려준다."""
    from app import llm as _llm
    if _llm.credit_out():
        return {"applied": 0, "skipped": len(rows or []),
                "note": "크레딧 없음 — 기계 계측만 사용(의미 분석 보류)"}
    done = 0
    for r in (rows or [])[:limit]:
        txt = (r.get("text") or "")[:2500]
        if not txt:
            continue
        try:
            out = _llm.call_task("judge",
                "아래 글이 검색자에게 어떤 유형인지 한 줄로 판정하라. "
                "형식: 유형=<정보형|구매형|후기형>, 실경험=<있음|없음>, 근거=<15자 이내>\n\n" + txt,
                max_tokens=80)
        except Exception:
            continue
        m = re.search(r"유형=\s*(\S+).*?실경험=\s*(\S+)", out or "", re.S)
        if m:
            r["intent"] = m.group(1).strip(" ,")
            r["exp_llm"] = m.group(2).strip(" ,")
            done += 1
    return {"applied": done, "skipped": max(0, len(rows or []) - done), "note": ""}
