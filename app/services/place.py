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
    """내 상호 ↔ 네이버 업체명 매칭(정규화 후 양방향 부분일치, 짧은 오탐 방지)."""
    u, n = _norm_name(user_name), _norm_name(naver_name)
    if len(u) < 2 or not n:
        return False
    return u in n or (len(n) >= 3 and n in u)


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


def rank(keyword: str, store_name: str, limit: int = 5) -> int | None:
    """참고용 순위 — 네이버 지역검색 상위 limit 안에서 내 가게 위치(1~limit).
    상위 밖이면 0, 조회 불가(무키/실패)면 None."""
    items = search(keyword, limit)
    if not items:
        return None
    for i, it in enumerate(items, 1):
        if _name_match(store_name, it.get("name", "")):
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
