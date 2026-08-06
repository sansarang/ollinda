"""🔍 빈 질문 찾기 — 재료는 그 가게 것, 판정은 실제 검색 화면.

만드는 순서:
  ① 재료 수집 — 손님이 실제로 물은 것(경험 Q&A), 과거 글 제목, 사진 묘사의 실값
  ② 질문 조립 — [실값] + [손님이 묻는 축]. 축은 언어 규칙이지 업종어가 아니다.
  ③ 빈자리 판정 — 실제로 검색해서 그 질문에 답하는 글이 있는지 본다(추측 금지)
"""
from __future__ import annotations

import re

from app import db

_TOK = re.compile(r"[가-힣A-Za-z0-9]+")

# 손님이 묻는 축 — 업종을 가리지 않는 질문 형태다(업종어가 아니라 질문 어휘).
#   '얼마나 걸리나'는 썬팅에도 치과에도 학원에도 통한다.
ASK_AXES = (
    ("얼마나 걸리나요", "소요"),
    ("며칠 걸리나요", "소요"),
    ("당일 가능한가요", "소요"),
    ("주의할 점", "주의"),
    ("전에 알아야 할 것", "주의"),
    ("차이가 뭔가요", "비교"),
    ("어떤 걸 골라야 하나요", "비교"),
    ("얼마인가요", "비용"),
    ("추가 비용 있나요", "비용"),
    ("예약해야 하나요", "절차"),
    ("어떻게 진행되나요", "절차"),
    ("관리 어떻게 하나요", "사후"),
    ("얼마나 가나요", "사후"),
)


def _toks(s: str) -> set:
    return {t for t in _TOK.findall(s or "") if len(t) >= 2}


def materials(tenant_id: str, limit: int = 40) -> dict:
    """질문 재료 — 그 가게가 실제로 가진 것만. 없으면 없는 대로 둔다(날조 금지)."""
    out = {"answers": [], "titles": [], "anchors": []}
    try:
        for e in (db.list_owner_experience(tenant_id, limit=limit) or []):
            q = (e.get("question") or "").strip()
            if q:
                out["answers"].append(q[:80])
    except Exception:
        pass
    try:
        for p in (db.list_blog_publishes(tenant_id, limit=limit) or []):
            t = (p.get("post_title") or "").strip()
            if t:
                out["titles"].append(t[:100])
    except Exception:
        pass
    # 실값(모델명·등급명) — seo의 단일 관문을 쓴다(사본 금지)
    try:
        from app import seo as _seo
        for s in (db.list_sets(tenant_id=tenant_id, limit=20) or []):
            for pc in db.get_set_pieces(s.get("asset_id") or ""):
                for a in _seo.input_anchors((pc.payload or {}).get("gen_source") or ""):
                    if a not in out["anchors"]:
                        out["anchors"].append(a)
                break
    except Exception:
        pass
    return out


# 글 제목에 붙는 형식어 — 하는 일이 아니다. 기존 목록을 재사용한다(사본 금지, R4).
def _noise_words() -> set:
    from app.services.coexpose.control import INTENT_WORDS
    return set(INTENT_WORDS) | {"과정", "전과정", "시공기", "패키지", "고민", "끝",
                                "신차", "정리", "방법", "이유", "차이", "종류"}


def work_terms(mats: dict, region: str = "", limit: int = 6) -> list:
    """이 가게가 실제로 하는 일 — 과거 글 제목에서 반복되는 말.

    ★ 2026-08-06: 실값만 붙이면 'EV6 얼마나 걸리나요'가 된다. 무엇을 하는지가 빠져
      검색어가 안 된다. 손님은 '무엇을' 얼마나 걸리는지 묻는다.
      업종어 목록을 갖지 않는다 — 그 가게 글 제목에서 캔다.
    """
    # ★ 2026-08-06: 빈도만 세면 '동구·부산·후기·기아'가 '하는 일'로 잡힌다.
    #   지역어(가게 실값)·형식어(후기·추천)·모델명(anchors)을 빼야 시술명이 남는다.
    noise = _noise_words()
    # ★ 2026-08-06 사고: region이 '부산광역시 동구'라 '부산'이 안 걸러졌고,
    #   '부산'이 '하는 일'로 잡혀 자동완성이 '오늘 부산 날씨'를 줬다.
    #   썬팅집 글감 큐에 날씨가 들어갔다 — 부분 문자열까지 지역으로 본다.
    rt = set()
    for t in _TOK.findall(region or ""):
        if len(t) < 2:
            continue
        rt.add(t)
        for i in range(2, len(t)):
            rt.add(t[:i])                     # '부산광역시' → 부산, 부산광, …
    anchors = set(mats.get("anchors") or [])
    freq = {}
    for t in (mats.get("titles") or []):
        for w in _TOK.findall(t):
            if len(w) < 2 or w.isdigit() or w in noise or w in rt or w in anchors:
                continue
            # 지역 접미사로 끝나는 말도 지역이다(구·동·시…)
            if len(w) >= 2 and w[-1] in ("구", "동", "시", "군", "읍", "면", "역"):
                continue
            freq[w] = freq.get(w, 0) + 1
    # 여러 글에 반복되는 말 = 이 가게가 하는 일. 한 번 나온 건 우연이다.
    return [w for w, n in sorted(freq.items(), key=lambda x: -x[1]) if n >= 2][:limit]


def candidates(mats: dict, industry_term: str = "", limit: int = 24,
               region: str = "") -> list:
    """질문 후보 조립 — [실값] + [하는 일] + [묻는 축].

    ★ 셋이 다 있어야 검색어가 된다. '무엇을' 없이 'EV6 얼마나 걸리나요'는 아무도 안 친다.
    ★ 실값이 있으면 그것을 쓴다 — 구체적일수록 경쟁이 없다(그게 이 방법의 전부다).
    """
    seeds = list(dict.fromkeys([a for a in (mats.get("anchors") or []) if a]))[:4]
    works = work_terms(mats, region)
    if industry_term and industry_term not in works:
        works = [industry_term] + works
    if not works:
        return []                              # 하는 일을 모르면 질문을 만들지 않는다(날조 금지)
    out = []
    for w in works[:3]:
        for s in (seeds or [""]):
            for ask, kind in ASK_AXES:
                q = (f"{s} {w} {ask}" if s else f"{w} {ask}").strip()
                out.append({"q": q, "seed": s, "work": w, "kind": kind})
                if len(out) >= limit:
                    return out
    return out


def is_answered(query: str, posts: list) -> dict:
    """이 질문에 **답하는 글**이 이미 있는가.

    ★ 판정 기준: 상위 글 제목에 질문의 핵심 토큰이 **대부분** 들어 있으면 답한 것으로 본다.
      토큰 일부만 겹치는 것은 답이 아니다 — 그건 그냥 같은 업종 글이다.
      느슨하게 잡으면 '빈 질문'이 하나도 안 나오고, 빡빡하게 잡으면 다 비어 보인다.
      기준을 코드에 박아두고 실측으로 조정한다.
    """
    qt = _toks(query)
    if not qt:
        return {"answered": False, "best": 0.0, "by": None}
    best, by = 0.0, None
    for p in (posts or []):
        tt = _toks(p.get("title") or "")
        if not tt:
            continue
        cov = len(qt & tt) / len(qt)
        if cov > best:
            best, by = cov, f"{p.get('blog')}/{p.get('post')}"
    return {"answered": best >= 0.7, "best": round(best, 2), "by": by}
