"""💡 실수요 질문 수집 — 사람이 실제로 치는 말을 네이버가 알려준다.

★ 2026-08-06 방향 전환: 질문을 우리가 조립하면 'PV5 부산 얼마나 걸리나요' 같은
  아무도 안 치는 말이 나온다. 비어 있는 게 당연하고 써도 아무도 안 온다.
  검색량 API는 월 10회 미만을 안 알려준다 — 우리가 노리는 롱테일이 정확히 거기다.
  **잴 수 없는 것을 재려 한 것**이 문제였다.

  자동완성은 다르다. 여기 뜬다는 것은 **치는 사람이 있다**는 뜻이다.
  실측: '신차 썬팅' → 추천·시간·농도·기포·가격. 우리가 만든 '묻는 축' 목록보다 정확하다.

★ R1: 공개 자동완성만 읽는다(로그인·조작 없음). R2: 호출 간 간격을 둔다.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.parse
import urllib.request

_log = logging.getLogger("shopcast.vacantq.suggest")
AC_URL = ("https://ac.search.naver.com/nx/ac?q={q}&st=100&r_format=json&r_enc=UTF-8"
          "&r_unicode=0&t_koreng=1&ans=2")
GAP_MIN, GAP_MAX = 0.8, 1.8


def fetch(seed: str, timeout: int = 15) -> list:
    """한 씨앗의 자동완성. 실패는 빈 목록 + 로그(조용한 실패 금지)."""
    try:
        req = urllib.request.Request(
            AC_URL.format(q=urllib.parse.quote(seed)),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.search.naver.com/"})
        d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore"))
    except Exception as e:
        _log.warning("[suggest] 자동완성 실패 %s: %r", seed, repr(e)[:80])
        return []
    out = []
    for grp in (d.get("items") or []):
        for x in grp:
            if isinstance(x, list) and x and isinstance(x[0], str):
                t = x[0].strip()
                if t and t not in out:
                    out.append(t)
    return out


# ★ 2026-08-07 폐기: 'PLATFORM_SEEK'(사이트·앱·순위…)를 코드에 박았다가 뺐다.
#   이상한 글감이 나올 때마다 필터를 추가하면 업종이 100개일 때 100번 고쳐야 한다
#   (표면별 수정 금지 조항 위반 — 세 번 어겼다).
#   더 근본적으로, 내가 나쁘다고 판정한 것 중 절대적으로 나쁜 건 하나도 없었다:
#     '중고차할부' — 할부 전문 업체엔 최고의 글감
#     '중고차사이트추천' — 플랫폼 회사엔 핵심
#     '오늘 부산 날씨' — 캠핑장·펜션엔 진짜 손님 질문
#   질문을 바꿔야 한다: "이 질문이 나쁜가"가 아니라 "이 가게가 답할 수 있는가"다.
#   그건 사장님만 안다 — declined()가 그 답을 학습한다.


def relevant(rows: list, work_terms: list, declined: set = None) -> list:
    """★ 자동완성이 준 말이 **우리가 하는 일과 관련 있는가**.

    2026-08-06 사고: 씨앗에 '부산'이 섞여 '오늘 부산 날씨'가 글감 큐까지 갔다.
    씨앗을 아무리 걸러도 자동완성은 엉뚱한 데로 샌다 — 결과에서 한 번 더 막는다.
    판정: 질문에 '하는 일' 낱말이 하나라도 있어야 한다. 없으면 우리 글감이 아니다.
    """
    ws = [w for w in (work_terms or []) if w]
    if not ws:
        return []
    dc = {d for d in (declined or set()) if d}
    out = []
    for r in (rows or []):
        q = r.get("q") or ""
        if not any(w in q for w in ws):
            continue
        # 사장님이 '안 합니다'라고 한 주제 — 그 가게에서만 빠진다(다른 가게엔 영향 없음).
        # 계열 판정은 겹침으로 한다(우리가 계열어를 뽑지 않는다).
        if dc:
            from app.services.vacantq.domain import is_declined as _isd
            if _isd(q, dc):
                continue
        out.append(r)
    return out


def expand(seeds: list, depth: int = 1, per_seed: int = 10, max_total: int = 60) -> dict:
    """씨앗들을 자동완성으로 넓힌다. depth=2면 나온 말로 한 번 더 판다(더 깊은 롱테일).

    ★ 우리가 만든 말은 하나도 안 섞는다 — 전부 네이버가 준 것이다.
    """
    seen, rows, frontier = set(), [], [s for s in (seeds or []) if s]
    for d in range(max(1, depth)):
        nxt = []
        for s in frontier:
            if len(rows) >= max_total:
                break
            got = fetch(s)[:per_seed]
            for g in got:
                if g in seen or g == s:
                    continue
                seen.add(g)
                rows.append({"q": g, "seed": s, "depth": d + 1})
                nxt.append(g)
            time.sleep(random.uniform(GAP_MIN, GAP_MAX))
        frontier = nxt[:6]
        if not frontier:
            break
    return {"rows": rows[:max_total], "n": len(rows[:max_total]),
            "note": "자동완성에 뜬 말 = 치는 사람이 있는 말. 우리가 조립한 것은 없다."}


def seeds_for(work_terms: list, region: str = "", anchors: list = None) -> list:
    """씨앗 — [하는 일], [지역+하는 일], [실값+하는 일]. 조합은 씨앗까지만이고
    질문 자체는 자동완성이 만든다."""
    ws = [w for w in (work_terms or []) if w][:4]
    # ★ 2026-08-06 실측: 지역을 '동구'만 주면 자동완성이 '대구'로 보정한다.
    #   광역+기초를 함께 줘야 우리 지역 롱테일이 나온다.
    rt = [x for x in (region or "").split() if x]
    r_full = " ".join(rt[-2:]) if len(rt) >= 2 else (rt[0] if rt else "")
    out = []
    for w in ws:
        out.append(w)
        if r_full:
            out.append(f"{r_full} {w}")
    # ★ 수치 실값(216km·30만원)은 씨앗이 못 된다 — '216km 중고차 얼마나 걸리나요'는 헛질문이다.
    #   본문에서는 살아야 할 정보지만(주행거리) 검색어의 축은 아니다.
    import re as _re
    _num = _re.compile(r"^\d[\d,.]*\s*(km|KM|원|만원|천원|cc|kg|년|월|%)?$")
    for a in [x for x in (anchors or []) if x and not _num.match(str(x))][:2]:
        for w in ws[:2]:
            out.append(f"{a} {w}")
    return list(dict.fromkeys([x for x in out if x]))[:10]
