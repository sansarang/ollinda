"""🧠 의미 계측 — 표면 세 축이 다 죽은 뒤 남은 층위.

소거된 것(2026-08-06): C-RANK(증명) · 장소 신호(반증) · 문서 구조화(반증).
남은 가설: **질의가 묻는 것에 이 글이 얼마나 정확히 답하는가.**
근거: 의료 쌍에서 761자가 뽑히고 2355자가 안 뽑혔다 — 길이가 아니라 답의 밀도일 수 있다.

★ 의미 판정은 기계보다 오판 위험이 크다. 그래서 셋을 강제한다:
  ① 판정 근거 문장을 함께 저장한다(R8) — 사람이 검증할 수 있어야 한다.
  ② 같은 글을 두 번 넣어 재현되는지 본다 — 흔들리는 판정은 인자가 아니다.
  ③ LLM 판정은 '인자 후보'까지다. 확정 인자로 바로 쓰지 않는다.
★ 업종 교차 필수 — 의미 신호도 업종 편향을 탄다(썬팅 함정과 같은 계열).
"""
from __future__ import annotations

import json
import logging
import re

_log = logging.getLogger("shopcast.coexpose.semantic")

PROMPT = (
    "너는 네이버 검색 품질 평가자다. 아래 [검색어]로 검색한 사람에게 이 [글]이 얼마나 쓸모 있는지 "
    "판정하라. 글의 홍보 여부가 아니라 **검색자가 물은 것에 답하는가**를 본다.\n\n"
    "다음 JSON만 출력하라(설명 금지):\n"
    "{\"answer_fit\": 0~5, \"answer_evidence\": \"근거 문장 그대로 25자 이내\", "
    "\"experience\": 0~5, \"experience_evidence\": \"근거 문장 그대로 25자 이내\", "
    "\"promo\": 0~5, \"promo_evidence\": \"근거 문장 그대로 25자 이내\"}\n\n"
    "answer_fit: 검색어가 묻는 것에 직접 답하는 정도(0=겉돎, 5=정확히 답함)\n"
    "experience: 직접 겪은 고유 디테일의 밀도(0=일반론만, 5=본인만 쓸 수 있는 구체 경험)\n"
    "promo: 홍보문 성격(0=순수 정보, 5=광고문)\n"
    "근거는 반드시 글에 실제로 있는 문장에서 따라 쓴다. 없으면 빈 문자열.\n\n"
    "[검색어] {q}\n[제목] {title}\n[글]\n{body}")

KEYS = ("answer_fit", "experience", "promo")


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {}
    out = {}
    for k in KEYS:
        v = d.get(k)
        if isinstance(v, (int, float)):
            out[k] = float(v)
        ev = d.get(k + "_evidence")
        if isinstance(ev, str):
            out[k + "_evidence"] = ev.strip()[:60]
    return out


def judge(post: dict, query: str, body_limit: int = 2600) -> dict:
    """글 하나의 의미 판정. 크레딧이 없으면 아무것도 하지 않고 사유를 돌려준다(R7)."""
    from app import llm as _llm
    if _llm.credit_out():
        return {"skipped": "크레딧 없음 — 의미 계측 보류"}
    body = (post.get("text") or "")[:body_limit]
    if not body:
        return {"skipped": "본문 없음"}
    p = (PROMPT.replace("{q}", query or "")
         .replace("{title}", (post.get("title") or "")[:120])
         .replace("{body}", body))
    try:
        raw = _llm.call(p, max_tokens=300)
    except Exception as e:
        return {"error": repr(e)[:120]}
    d = _parse(raw)
    if not d:
        return {"error": "판정 파싱 실패", "raw": (raw or "")[:120]}
    return d


def judge_twice(post: dict, query: str, tol: float = 1.0) -> dict:
    """★ 재현성 확인 — 같은 글을 두 번 넣어 같은 판정이 나오는지.

    흔들리는 판정은 인자가 아니다. 차이가 tol을 넘으면 그 항목을 '불안정'으로 표시하고
    값을 쓰지 않는다 — 오판을 인자로 승격시키지 않기 위해서다.
    """
    a = judge(post, query)
    if a.get("skipped") or a.get("error"):
        return {**a, "stable": False}
    b = judge(post, query)
    if b.get("skipped") or b.get("error"):
        return {**a, "stable": None, "note": "2회차 실패 — 재현성 미확인"}
    out, unstable = {}, []
    for k in KEYS:
        va, vb = a.get(k), b.get(k)
        if va is None or vb is None:
            unstable.append(k)
            continue
        if abs(va - vb) > tol:
            unstable.append(k)
            continue
        out[k] = round((va + vb) / 2, 2)
        out[k + "_evidence"] = a.get(k + "_evidence") or b.get(k + "_evidence") or ""
    out["stable"] = not unstable
    out["unstable_keys"] = unstable
    out["runs"] = [{k: a.get(k) for k in KEYS}, {k: b.get(k) for k in KEYS}]
    if unstable:
        _log.warning("[semantic] 판정 불안정 %s — 값 미사용", unstable)
    return out
