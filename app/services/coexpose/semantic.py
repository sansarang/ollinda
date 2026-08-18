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
        raw = _llm.call_task("judge", p, max_tokens=300)
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


# ── 판정 해상도 검증(2026-08-06) ──────────────────────────────────────────
# ★ 실측에서 answer_fit이 1.0·3.0만 나왔다. 차이가 없어서인지 자가 둔해서인지 구분이 안 된다.
#   명백히 다른 글을 넣어 점수가 벌어지는지 본다 — 안 벌어지면 자가 고장난 것이다.
CALIBRATION = [
    {"name": "완전 무관", "expect": "low",
     "title": "집에서 김치 담그는 법",
     "text": ("배추 절이는 시간이 관건입니다. 소금물 농도는 10% 정도로 맞추고 6시간 두었습니다. "
              "고춧가루는 태양초를 쓰면 색이 곱게 나옵니다. 젓갈은 멸치액젓과 새우젓을 반씩 "
              "섞었어요. 무채는 얇게 썰어야 양념이 잘 뱁니다. 김치통에 눌러 담고 하루 실온에 "
              "둔 뒤 김치냉장고로 옮깁니다. 이렇게 하면 두 달은 아삭합니다. ") * 4},
    {"name": "정확히 답함", "expect": "high",
     "title": "강남 미용실 추천 - 가격·예약·디자이너 정리",
     "text": ("강남역 3번 출구 도보 5분 거리 미용실 세 곳을 직접 다녀보고 정리했습니다. "
              "A샵은 커트 3만원, 펌 12만원이고 평일 오전에 가면 대기가 없습니다. "
              "B샵은 커트 4만5천원인데 두피 클리닉이 포함됩니다. 예약은 네이버로만 받습니다. "
              "C샵은 디자이너별로 가격이 달라 원장님은 6만원, 실장님은 4만원입니다. "
              "주차는 A샵만 2시간 무료이고 나머지는 인근 공영주차장을 써야 합니다. "
              "저는 곱슬머리라 매직을 받았는데 A샵이 가장 자연스러웠습니다. ") * 3},
    {"name": "같은 업종 겉돎", "expect": "mid",
     "title": "미용실 고르는 법",
     "text": ("미용실을 고를 때는 여러 가지를 고려해야 합니다. 위치도 중요하고 가격도 중요합니다. "
              "디자이너의 실력이 가장 중요하다고 볼 수 있습니다. 후기를 잘 살펴보시고 "
              "본인에게 맞는 곳을 선택하시기 바랍니다. 저희 샵은 항상 최선을 다하고 있습니다. "
              "언제든 편하게 문의 주세요. 친절하게 상담해 드리겠습니다. ") * 4},
]


def calibrate(query: str = "강남 미용실 추천") -> dict:
    """자가 도구인지 확인 — 명백히 다른 글에서 점수가 벌어지는가.

    벌어지지 않으면 answer_fit은 도구가 아니다(둔한 자). 그러면 의미 축을 그것으로 판정할 수 없다.
    """
    rows = []
    for c in CALIBRATION:
        d = judge({"title": c["title"], "text": c["text"]}, query)
        rows.append({"name": c["name"], "expect": c["expect"], **d})
    got = [r.get("answer_fit") for r in rows if isinstance(r.get("answer_fit"), (int, float))]
    spread = (max(got) - min(got)) if len(got) >= 2 else None
    return {"query": query, "rows": rows, "spread": spread,
            "verdict": ("자 정상 — 명백히 다른 글에서 점수가 벌어진다" if (spread or 0) >= 3 else
                        ("자 둔함 — 극단 케이스에서도 안 벌어진다(의미 축 판정 불가)"
                         if spread is not None else "측정 실패")),
            "note": "이 검증을 통과해야 answer_fit으로 인자를 말할 수 있다"}
