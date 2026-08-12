"""
가게 '검색' → 정보 자동입력 (타이핑 최소화).
네이버 지역검색 API 사용. env: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET.
키 없으면 [] 반환 → UI는 수동입력으로 graceful.
docs: https://developers.naver.com/docs/serviceapi/search/local/local.md
"""
from __future__ import annotations

import logging
import os
import re

import requests

_log = logging.getLogger("shopcast.place")


def _norm_name(s: str) -> str:
    """상호명 정규화 — 공백·특수문자 제거 + 소문자. 지점명 차이로 인한 매칭 실패 완화."""
    return re.sub(r"[\s()\[\]{}·・.,\-–—_/&'\"]+", "", (s or "")).lower()


def _name_match(user_name: str, naver_name: str) -> bool:
    """내 상호 ↔ 네이버 업체명 매칭.

    ① 정규화 후 양방향 부분일치(기존)
    ② ①이 실패해도 '핵심 토큰'이 충분히 겹치면 같은 가게로 본다(2026-08-12 실사고).
       실측: 사장님이 '초량루마썬팅'(지역명 붙임)이라 입력하면 네이버의 '루마썬팅 현대상사'와
       포함관계가 안 되어 실제 5위인데 '미노출'로 오판했다. 지역명·지점 접미사를 떼고 비교한다.
    한 글자 토큰·너무 짧은 이름은 오탐 위험이 커서 제외한다(다른 가게를 내 가게로 보면 더 나쁘다).
    """
    u, n = _norm_name(user_name), _norm_name(naver_name)
    if len(u) < 2 or not n:
        return False
    # ① 겹치는 부분이 '고유 상호'라 부를 만큼 길어야 한다(4글자↑).
    #    짧은 조각(‘썬팅’ 같은 업종어)으로 남의 가게에 붙는 오탐이 실사고였다.
    if (u in n or n in u) and min(len(u), len(n)) >= 4:
        return True
    # ② 한쪽의 고유 토큰이 다른 쪽 안에 통째로 들어있는가(붙여쓴 입력·지점 표기 차이 흡수).
    #    '초량루마썬팅' ⊃ '루마썬팅', '스타벅스 서면점' ↔ '스타벅스 부산대점'.
    for t in (_core_tokens(naver_name) | _core_tokens(user_name)):
        tt = _norm_name(t)
        if len(tt) >= 4 and tt in u and tt in n:
            return True
    # ③ 지역·지점 수식어를 뗀 '고유 브랜드'가 완전히 같은가 — 짧은 브랜드(3글자)의 지점 차이 흡수.
    #    '지벤트 초량점' ↔ '지벤트 서면점'. 완전일치만 인정해 오탐을 막는다.
    ub, nb = _core_tokens(user_name), _core_tokens(naver_name)
    if ub and nb and ub == nb:
        return True
    return False


# 지역·지점 수식어(붙어 있으면 고유 상호가 아니다) — 업종 중립: 지명·형태만, 업종어 금지
_GENERIC = ("점", "본점", "지점", "직영점", "센터", "매장", "샵", "샾", "스토어",
            "주식회사", "㈜", "유한회사", "상사", "공업사", "자동차")


def _core_tokens(s: str) -> set:
    """상호에서 지점·법인 수식어를 뺀 고유 토큰 집합. '초량루마썬팅' → {'루마썬팅'} 지향.
    '○○점'(지점 표기)은 통째로 버린다 — 지명을 코드에 박지 않고 패턴으로 처리(업종·지역 중립)."""
    import re as _re
    raw = _re.split(r"[\s()\[\]{}·・.,\-–—_/&'\"]+", (s or "").strip())
    out = set()
    for w in raw:
        w = w.strip().lower()
        if not w:
            continue
        if len(w) <= 4 and w.endswith("점"):    # '초량점'·'서면점'·'본점' = 지점 표기 → 제외
            continue
        for g in _GENERIC:                      # 접미/접두 수식어 제거
            if w.endswith(g) and len(w) > len(g) + 1:
                w = w[: -len(g)]
            if w.startswith(g) and len(w) > len(g) + 1:
                w = w[len(g):]
        if len(w) >= 2:
            out.add(w)
    return out


def configured() -> bool:
    return bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))


def search(query: str, limit: int = 5) -> list[dict]:
    """가게명/키워드 → [{name, category, address, tel}]. 실패/무키 시 []."""
    query = (query or "").strip()
    if not (configured() and query):
        _log.info("[place.search] 무키/빈쿼리 → 빈결과 (configured=%s, q=%r)", configured(), query)
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/local.json",
            params={"query": query, "display": max(1, min(limit, 5))},
            headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                     "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]},
            timeout=8)
        if r.status_code != 200:
            # 401/403=키 문제, 429=레이트리밋 — 원인 구분 위해 로깅
            _log.warning("[place.search] 네이버 지역검색 non-200: status=%s q=%r body=%.200s",
                         r.status_code, query, r.text)
            return []
        out = []
        for it in r.json().get("items", []):
            name = re.sub(r"<[^>]+>", "", it.get("title", "")).strip()
            cats = [c for c in (it.get("category", "") or "").split(">") if c.strip()]
            out.append({
                "name": name,
                "category": (cats[-1].strip() if cats else ""),
                "address": (it.get("roadAddress") or it.get("address") or "").strip(),
                "jibun": (it.get("address") or "").strip(),   # 지번(동 포함) — 짧은 지역 추출용
                "tel": (it.get("telephone") or "").strip(),
                "mapx": (it.get("mapx") or "").strip(),        # 경도*10^7 (WGS84)
                "mapy": (it.get("mapy") or "").strip(),        # 위도*10^7
            })
        return out
    except Exception:
        return []


def shop_search(query: str, limit: int = 5) -> list[dict]:
    """상품명 → 네이버 쇼핑검색 [{name, category, image, price, mall}]. 무키/실패 []."""
    query = (query or "").strip()
    if not (configured() and query):
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": query, "display": max(1, min(limit, 5))},
            headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                     "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]},
            timeout=8)
        if r.status_code != 200:
            return []
        out = []
        for it in r.json().get("items", []):
            cats = [it.get(k, "") for k in ("category4", "category3", "category2", "category1") if it.get(k)]
            out.append({
                "name": re.sub(r"<[^>]+>", "", it.get("title", "")).strip(),
                "category": (cats[0] if cats else ""),
                "image": it.get("image", ""),
                "price": it.get("lprice", ""),
                "mall": it.get("mallName", ""),
                "brand": (it.get("brand") or it.get("maker") or "").strip(),
                "link": (it.get("link") or "").strip(),      # 상품 상세 링크
            })
        return out
    except Exception:
        return []


SHOP_SCAN_DEPTH = 40   # 쇼핑검색 스캔 깊이(공식 API display 상한 100 내, 호출 1회)


def shop_rank(keyword: str, store_name: str, brand: str = "") -> "int | None":
    """네이버 쇼핑검색 상위 SHOP_SCAN_DEPTH 안에서 내 스토어/브랜드 상품 순위.
    상위 밖이면 0, 조회 불가(무키/실패)면 None. 크롤링 아님 — 공식 shop.json 1회."""
    keyword = (keyword or "").strip()
    if not (configured() and keyword and (store_name or brand)):
        return None
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            params={"query": keyword, "display": SHOP_SCAN_DEPTH},
            headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                     "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]},
            timeout=8)
        if r.status_code != 200:
            _log.warning("[place.shop_rank] non-200: status=%s kw=%r body=%.200s",
                         r.status_code, keyword, r.text)
            return None
        for i, it in enumerate(r.json().get("items", []), 1):
            mall = (it.get("mallName") or "").strip()
            ibrand = (it.get("brand") or it.get("maker") or "").strip()
            title = re.sub(r"<[^>]+>", "", it.get("title", "")).strip()
            if (store_name and (_name_match(store_name, mall) or _name_match(store_name, ibrand))) \
               or (brand and (_name_match(brand, mall) or _name_match(brand, ibrand) or _name_match(brand, title))):
                _log.info("[place.shop_rank] kw=%r store=%r → %d위", keyword, store_name, i)
                return i
        return 0
    except Exception:
        return None


def shop_top(keyword: str, limit: int = 3) -> list[dict]:
    """쇼핑검색 상위 상품 [{name, mall, price}] — 브리핑 '지금 1위는 ○○(N원)' 실측용. 무키/실패 []."""
    out = []
    for it in shop_search(keyword, limit):
        try:
            price = int(it.get("price") or 0)
        except Exception:
            price = 0
        out.append({"name": it.get("name", ""), "mall": it.get("mall", ""), "price": price})
    return out


def find_candidates(store_name: str, region: str = "", limit: int = 5) -> list[dict]:
    """상호로 '내 가게 후보'를 주소와 함께 찾는다(2026-08-12 사장님 지시).

    동명 가게가 여럿이면 순위 판정이 남의 가게를 볼 수 있다 — 사용자가 주소를 보고
    자기 가게를 직접 고르게 한다(헌법: 자사 식별자 매칭만, 허위 양성 금지).
    반환: [{name, address, category, tel, id}] — id는 이후 정확 매칭용 고유 키.
    """
    q = " ".join(x for x in [(region or "").strip(), (store_name or "").strip()] if x)
    if not q.strip():
        return []
    items = search(q, max(limit, 5)) or []
    # 상호가 실제로 걸리는 것만(지역+업종 검색 결과가 섞여 오는 것 방지)
    hits = [it for it in items if _name_match(store_name, it.get("name", ""))]
    if not hits:                                  # 상호 단독 재시도(지역명이 방해했을 수 있음)
        hits = [it for it in (search(store_name, max(limit, 5)) or [])
                if _name_match(store_name, it.get("name", ""))]
    out = []
    for it in hits[:limit]:
        out.append({"name": it.get("name", ""), "address": it.get("address", ""),
                    "category": it.get("category", ""), "tel": it.get("tel", ""),
                    "id": _norm_name(it.get("name", "")) + "|" + _norm_name(it.get("address", ""))[:24]})
    return out


def rank(keyword: str, store_name: str, limit: int = 5, addr: str = "") -> int | None:
    """참고용 순위 — 네이버 지역검색 상위 limit 안에서 내 가게 위치(1~limit).
    상위 밖이면 0, 조회 불가(무키/실패)면 None.
    addr: 사용자가 후보 목록에서 고른 주소 — 주면 동명 가게와 확실히 구분한다(2026-08-12)."""
    items = search(keyword, limit)
    if not items:
        return None
    a_want = _norm_name(addr)[:24] if addr else ""
    for i, it in enumerate(items, 1):
        if not _name_match(store_name, it.get("name", "")):
            continue
        if a_want:                                  # 주소가 주어지면 주소까지 일치해야 내 가게
            if _norm_name(it.get("address", ""))[:24] != a_want:
                continue
        return i
    return 0


def rank_detail(keyword: str, store_name: str, limit: int = 5) -> dict:
    """순위 + 경쟁사 + '추월 대상'(내 바로 위 가게). 성과 가시화·경쟁 추월용.
    반환: {rank, rival, leader, competitors:[{name, mine}]}. 무키/실패 시 rank=None."""
    items = search(keyword, limit)
    if not items:
        return {"rank": None, "rival": "", "leader": "", "competitors": []}
    my_i = 0
    comps = []
    for i, it in enumerate(items, 1):
        mine = _name_match(store_name, it.get("name", ""))
        if mine:
            my_i = i
        comps.append({"name": it.get("name", ""), "mine": mine})
    rival = (comps[my_i - 2]["name"] if my_i >= 2 else "")     # 내 바로 위 = 추월 대상
    leader = comps[0]["name"] if comps else ""
    _log.info("[place.rank_detail] kw=%r store=%r → rank=%s (top%d)", keyword, store_name, my_i, len(items))
    return {"rank": my_i, "rival": rival, "leader": leader, "competitors": comps}
