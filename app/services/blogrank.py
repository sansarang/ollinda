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
                "bloggername": (it.get("bloggername") or "").strip(),
                "description": re.sub(r"<[^>]+>", "", it.get("description", "")).strip(),  # 본문 앞부분 요약(분석가 P1)
                "postdate": (it.get("postdate") or "").strip(),   # YYYYMMDD — 경쟁 정찰(생존신고 P4)
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


def _norm_post_url(u: str) -> str:
    """포스트 URL 정규화 — blog.naver.com/{id}/{글번호} 꼴로(모바일 m. / 쿼리 제거)."""
    u = (u or "").strip().split("?")[0].rstrip("/")
    u = u.replace("://m.blog.naver.com", "://blog.naver.com")
    m = re.search(r"blog\.naver\.com/([A-Za-z0-9_-]+)/(\d+)", u)
    return f"blog.naver.com/{m.group(1)}/{m.group(2)}" if m else u.replace("https://", "").replace("http://", "")


def post_rank(keyword: str, post_url: str, limit: int = TOP_N) -> dict:
    """'그 글'의 정확한 순위(생존신고 P1) — 검색결과 link를 포스트 URL로 직접 대조.
    반환: {rank(1~limit | 0=limit 밖 | None=조회불가), checked}. 블로그 단위가 아닌 포스트 단위."""
    target = _norm_post_url(post_url)
    items = _search_blog(keyword, limit)
    if not (items and target):
        return {"rank": None, "checked": 0}
    for i, it in enumerate(items, 1):
        if _norm_post_url(it.get("link", "")) == target:
            return {"rank": i, "checked": len(items)}
    return {"rank": 0, "checked": len(items)}


def check_indexed(post_title: str, post_url: str, limit: int = 30) -> "bool | None":
    """색인 확인(생존신고 P2) — 글 제목으로 블로그검색해 그 글 URL이 잡히는지.
    True=색인됨 / False=아직 미검출 / None=조회 불가. 실측만(추측 금지)."""
    title = (post_title or "").strip()
    target = _norm_post_url(post_url)
    if not (title and target):
        return None
    items = _search_blog(title[:40], limit)
    if not items:
        return None if not configured() else False
    return any(_norm_post_url(it.get("link", "")) == target for it in items)


def scout_top(keyword: str, top: int = 5) -> list[dict]:
    """경쟁 정찰(생존신고 P4) — 상위 N 글의 발행일·블로그명(공식 API 필드만, 크롤링 아님).
    반환: [{rank, title, blogger, postdate(YYYYMMDD), age_days}]."""
    import datetime
    out = []
    for i, it in enumerate(_search_blog(keyword, top)[:top], 1):
        pd, age = it.get("postdate") or "", None
        if len(pd) == 8:
            try:
                d = datetime.date(int(pd[:4]), int(pd[4:6]), int(pd[6:8]))
                age = max(0, (datetime.date.today() - d).days)
            except Exception:
                age = None
        out.append({"rank": i, "title": it.get("title", ""), "blogger": it.get("bloggername", ""),
                    "postdate": pd, "age_days": age})
    return out


def rank_many(keywords: list[str], blog_id: str, limit: int = TOP_N) -> list[dict]:
    """여러 키워드 일괄 조회 → [{keyword, rank, url, post_title}]."""
    out = []
    for kw in [k for k in (keywords or []) if k and k.strip()]:
        r = blog_rank(kw, blog_id, limit)
        r["keyword"] = kw
        out.append(r)
    return out
