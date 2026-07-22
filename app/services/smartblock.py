"""
스마트블록 타깃팅(상위노출 v2) — 블록 세부주제 근사.

[조사 결과·소스 명시]
- 네이버는 스마트블록의 '블록 제목/세부주제 구조'를 공식 API로 노출하지 않는다.
  (검색결과 HTML 파싱 = 크롤링 → 금지.) 따라서 블록 제목 직접 수집은 불가.
- 합법 근사: 네이버 검색광고 keywordstool(공식 API)의 '연관 키워드'가 곧
  스마트블록 세부주제와 높게 겹친다(블록은 연관 검색의도 묶음이므로).
  → 연관 키워드 + 월검색량으로 '블록 세부주제 후보'를 근사하고, 의도 유형(추천/확인/후기/방법/비교)을
  기계 분류해 앵글 정렬에 쓴다. 크롤링 0.
"""
from __future__ import annotations

import re

from app.services import searchad

# 세부주제 의도 유형 → 글 앵글
_INTENT = [
    ("추천", re.compile(r"(추천|베스트|BEST|순위|인기)")),
    ("확인", re.compile(r"(확인|체크|보는\s?법|고르는|주의|사기|허위|피하)")),
    ("후기", re.compile(r"(후기|리뷰|실구매|내돈내산|타보)")),
    ("방법", re.compile(r"(방법|하는\s?법|절차|과정|어떻게)")),
    ("비교", re.compile(r"(비교|vs|차이|대비)")),
    ("가격", re.compile(r"(가격|비용|시세|얼마|견적)")),
]

_ANGLE_MAP = {"추천": "review", "확인": "howto", "후기": "review",
              "방법": "howto", "비교": "howto", "가격": "price"}


def intent_of(keyword: str) -> str:
    for name, pat in _INTENT:
        if pat.search(keyword or ""):
            return name
    return ""


def angle_for(keyword: str) -> str:
    """블록 세부주제 → 글 앵글(추천/확인/후기=review·howto, 가격=price). 기본 review."""
    return _ANGLE_MAP.get(intent_of(keyword), "review")


def subtopics(seed_keywords: list[str], min_volume: int = 100, limit: int = 12) -> list[dict]:
    """씨앗 키워드 → 블록 세부주제 후보 [{keyword, volume, intent}] (연관 키워드 근사, 월검색량 필터).
    검색광고 무키/실패 시 [](임의 생성 금지)."""
    seeds = [s for s in (seed_keywords or []) if s and s.strip()]
    if not (searchad.configured() and seeds):
        return []
    vols = searchad.keyword_volumes(seeds, limit=80)
    out, seen = [], set()
    for v in vols:
        kw = (v.get("keyword") or "").strip()
        vol = v.get("total", 0) or 0
        k = kw.replace(" ", "")
        if not kw or k in seen or vol < min_volume:
            continue
        # 의도 신호가 있는 것 = 블록 세부주제 성격(단순 대형 키워드 제외)
        it = intent_of(kw)
        if not it:
            continue
        seen.add(k)
        out.append({"keyword": kw, "volume": vol, "intent": it})
    out.sort(key=lambda x: -x["volume"])
    return out[:limit]
