"""
네이버 블로그 등록·발행확인(블로그등록 PHASE 1·2) — 공식 RSS 기반(크롤링 아님).
네이버 블로그는 발행 API가 없어 사용자가 수동 발행 → 등록된 blog_id의 공개 RSS
(https://rss.blog.naver.com/{id}.xml)로 '실제 발행'을 확인한다.

정직성: RSS에 없는 글을 '발행됨'으로 만들지 않는다. 자동 매칭이 불확실하면
사용자 수동 확인(URL 붙여넣기)을 병행하고, 매칭 근거(matched_by)를 함께 저장한다.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

_log = logging.getLogger("shopcast.blogsync")

RSS_URL = "https://rss.blog.naver.com/{blog_id}.xml"

# blog.naver.com/{id}, m.blog.naver.com/{id}, blog.naver.com/PostList.naver?blogId={id},
# blog.naver.com/{id}/223... 글 링크, 아이디만 입력 — 전부 흡수
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,30}$")


def normalize_blog_id(raw: str) -> str:
    """유연한 입력(전체 URL/모바일 URL/글 링크/아이디만)에서 blog_id 추출. 실패 시 ''."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.split("#")[0]
    # 쿼리형: PostList.naver?blogId=xxx / PostView.naver?blogId=xxx
    m = re.search(r"[?&]blogId=([A-Za-z0-9_-]{3,30})", s)
    if m:
        return m.group(1)
    # URL형: (m.)blog.naver.com/{id}[/...]
    m = re.search(r"(?:m\.)?blog\.naver\.com/([A-Za-z0-9_-]{3,30})", s)
    if m:
        cand = m.group(1)
        if cand.lower() not in ("postview", "postlist", "guestbook"):
            return cand
    # 아이디만 입력(공백·URL 문자가 없을 때)
    if "/" not in s and "." not in s and _ID_RE.match(s):
        return s
    return ""


def blog_url(blog_id: str) -> str:
    return f"https://blog.naver.com/{blog_id}" if blog_id else ""


def _parse_pubdate(s: str):
    """RSS pubDate(RFC822) → datetime(UTC, naive). 실패 시 None."""
    s = (s or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            continue
    return None


def fetch_feed(blog_id: str, timeout: int = 8) -> dict:
    """공식 RSS 조회 → {ok, exists, title, posts:[{title, link, published_at}], error}.
    exists=False는 '블로그 없음/비공개', ok=False는 네트워크 등 조회 실패(존재 판정 불가)."""
    blog_id = normalize_blog_id(blog_id) or (blog_id or "").strip()
    if not blog_id:
        return {"ok": False, "exists": False, "title": "", "posts": [], "error": "blog_id 없음"}
    try:
        r = requests.get(RSS_URL.format(blog_id=blog_id), timeout=timeout,
                         headers={"User-Agent": "ollinda-rss/1.0 (+https://ollinda.kr)"})
    except Exception as e:
        return {"ok": False, "exists": False, "title": "", "posts": [], "error": f"조회 실패: {e}"}
    if r.status_code != 200 or b"<rss" not in r.content[:2000]:
        # 네이버는 없는 아이디에 200이 아닌 에러 페이지/리다이렉트를 준다 → 존재하지 않음으로 판정
        _log.info("[blogsync] RSS 미확인 blog_id=%s status=%s", blog_id, r.status_code)
        return {"ok": True, "exists": False, "title": "", "posts": [],
                "error": "블로그를 찾지 못했어요(비공개이거나 주소가 다를 수 있어요)"}
    try:
        root = ET.fromstring(r.content)
        ch = root.find("channel")
        title = (ch.findtext("title") or "").strip() if ch is not None else ""
        link = (ch.findtext("link") or "").strip() if ch is not None else ""
        posts = []
        for it in (ch.findall("item") if ch is not None else []):
            posts.append({
                "title": (it.findtext("title") or "").strip(),
                "link": (it.findtext("link") or "").strip(),
                "published_at": _parse_pubdate(it.findtext("pubDate") or ""),
            })
        # 네이버는 '없는 아이디'에도 200 + 빈 채널(<title/>)을 준다 → title/link 비면 미존재 판정
        if not (title or link):
            return {"ok": True, "exists": False, "title": "", "posts": [],
                    "error": "블로그를 찾지 못했어요(아이디를 확인해 주세요)"}
        return {"ok": True, "exists": True, "title": title, "posts": posts, "error": ""}
    except Exception as e:
        return {"ok": False, "exists": False, "title": "", "posts": [], "error": f"RSS 파싱 실패: {e}"}


def verify_blog(raw_input: str) -> dict:
    """등록 시 유효성 검증 — 정규화 + RSS 실존 확인.
    반환: {ok, blog_id, url, title, post_count, error}."""
    bid = normalize_blog_id(raw_input)
    if not bid:
        return {"ok": False, "blog_id": "", "url": "", "title": "", "post_count": 0,
                "error": "블로그 주소를 인식하지 못했어요. 예: https://blog.naver.com/내아이디 또는 아이디만"}
    feed = fetch_feed(bid)
    if not feed["ok"]:
        # 네트워크 실패 — 존재 판정 불가. 정직하게 실패 안내(가짜 성공 금지).
        return {"ok": False, "blog_id": bid, "url": blog_url(bid), "title": "", "post_count": 0,
                "error": "지금 블로그 확인이 어려워요. 잠시 후 다시 시도해 주세요."}
    if not feed["exists"]:
        return {"ok": False, "blog_id": bid, "url": blog_url(bid), "title": "", "post_count": 0,
                "error": feed["error"] or "블로그를 찾지 못했어요."}
    return {"ok": True, "blog_id": bid, "url": blog_url(bid), "title": feed["title"],
            "post_count": len(feed["posts"]), "error": ""}


