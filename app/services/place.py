"""
가게 '검색' → 정보 자동입력 (타이핑 최소화).
네이버 지역검색 API 사용. env: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET.
키 없으면 [] 반환 → UI는 수동입력으로 graceful.
docs: https://developers.naver.com/docs/serviceapi/search/local/local.md
"""
from __future__ import annotations

import os
import re

import requests


def configured() -> bool:
    return bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))


def search(query: str, limit: int = 5) -> list[dict]:
    """가게명/키워드 → [{name, category, address, tel}]. 실패/무키 시 []."""
    query = (query or "").strip()
    if not (configured() and query):
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/local.json",
            params={"query": query, "display": max(1, min(limit, 5))},
            headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                     "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]},
            timeout=8)
        if r.status_code != 200:
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
            })
        return out
    except Exception:
        return []


def rank(keyword: str, store_name: str, limit: int = 5) -> int | None:
    """참고용 순위 — 네이버 지역검색 상위 limit 안에서 내 가게 위치(1~limit).
    상위 밖이면 0, 조회 불가(무키/실패)면 None."""
    items = search(keyword, limit)
    if not items:
        return None
    key = re.sub(r"\s+", "", store_name or "")
    for i, it in enumerate(items, 1):
        if key and key in re.sub(r"\s+", "", it.get("name", "")):
            return i
    return 0
