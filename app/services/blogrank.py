"""
blog_id 기반 '정확한' 블로그 순위 매칭(블로그등록 PHASE 3).
place.py의 상호명 매칭(플레이스용)과 달리, 네이버 블로그검색 API 결과에서
등록된 blog_id를 URL로 직접 대조 → 동명 가게·유사 상호 오탐이 없다.

공식 검색 API(developers.naver.com) 범위 내에서만 조회(크롤링 아님).
env: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET (place.py와 동일 키 재사용).
정직성: 상위 N 밖이면 0(미노출), 조회 불가면 None — 임의 순위를 만들지 않는다.
"""
from __future__ import annotations

import logging
import os
import re

import requests

from app.services.blogsync import normalize_blog_id

_log = logging.getLogger("shopcast.blogrank")

TOP_N = 30   # 검색결과 상위 N위까지 확인(블로그탭 1~3페이지 체감 범위)


def configured() -> bool:
    return bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))


def _search_blog(keyword: str, display: int = TOP_N) -> list[dict]:
    """네이버 블로그검색 → [{title, link, bloggerlink}]. 무키/실패 []."""
    keyword = (keyword or "").strip()
    if not (configured() and keyword):
        return []
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/blog.json",
            params={"query": keyword, "display": max(1, min(display, 100))},
            headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                     "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]},
            timeout=8)
        if r.status_code != 200:
            _log.warning("[blogrank] 블로그검색 non-200: status=%s kw=%r", r.status_code, keyword)
            return []
        out = []
        for it in r.json().get("items", []):
            out.append({
                "title": re.sub(r"<[^>]+>", "", it.get("title", "")).strip(),
                "link": (it.get("link") or "").strip(),
                "bloggerlink": (it.get("bloggerlink") or "").strip(),
            })
        return out
    except Exception:
        return []


def _item_blog_id(item: dict) -> str:
    """검색결과 항목의 블로그 아이디 — link/bloggerlink 어느 쪽이든 추출."""
    return (normalize_blog_id(item.get("link", ""))
            or normalize_blog_id(item.get("bloggerlink", "")))


def blog_rank(keyword: str, blog_id: str, limit: int = TOP_N) -> dict:
    """키워드 블로그검색 상위 limit에서 내 blog_id 위치.
    반환: {rank(1~limit | 0=미노출 | None=조회불가), url, post_title, checked}."""
    blog_id = (blog_id or "").strip()
    items = _search_blog(keyword, limit)
    if not (items and blog_id):
        return {"rank": None, "url": "", "post_title": "", "checked": 0}
    for i, it in enumerate(items, 1):
        if _item_blog_id(it) == blog_id:
            _log.info("[blogrank] kw=%r blog=%s → %d위 (top%d)", keyword, blog_id, i, len(items))
            return {"rank": i, "url": it.get("link", ""), "post_title": it.get("title", ""),
                    "checked": len(items)}
    return {"rank": 0, "url": "", "post_title": "", "checked": len(items)}


def rank_many(keywords: list[str], blog_id: str, limit: int = TOP_N) -> list[dict]:
    """여러 키워드 일괄 조회 → [{keyword, rank, url, post_title}]."""
    out = []
    for kw in [k for k in (keywords or []) if k and k.strip()]:
        r = blog_rank(kw, blog_id, limit)
        r["keyword"] = kw
        out.append(r)
    return out
