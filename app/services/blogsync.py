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


# ── PHASE 2: 발행 확인(생성글 ↔ RSS 최근글 매칭) ─────────────────────
def _norm_text(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]+", "", (s or "")).lower()


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", (s or "").lower()) if t}


def match_score(piece_payload: dict, post_title: str) -> float:
    """올린다 생성글(제목·키워드) ↔ 블로그 글제목 유사도(0~1).
    제목 포함 관계면 강한 매칭, 아니면 토큰 자카드 + 타겟키워드 겹침 가점."""
    pt = _norm_text(post_title)
    if not pt:
        return 0.0
    title = piece_payload.get("title") or ""
    nt = _norm_text(title)
    if nt and len(nt) >= 8 and (nt in pt or pt in nt):
        return 1.0
    a, b = _tokens(title), _tokens(post_title)
    jac = (len(a & b) / len(a | b)) if (a and b) else 0.0
    kws = (piece_payload.get("target_keywords") or [])[:3]
    kw_hit = sum(1 for k in kws if _norm_text(k) and _norm_text(k) in pt)
    return min(1.0, jac + kw_hit * 0.2)


MATCH_THRESHOLD = 0.5   # 이 이상만 '발행됨' 자동 판정 — 애매하면 발행됨을 만들지 않는다(수동 확인 병행)


def find_published(pieces: list, posts: list[dict]) -> list[dict]:
    """생성 블로그 글(pieces) ↔ RSS 최근글(posts) 매칭. 임계 이상만 반환(1글=1포스트).
    반환: [{piece_id, url, published_at, score, post_title}]"""
    out = []
    used_links: set = set()
    for p in pieces:
        best, best_post = 0.0, None
        for post in posts:
            if not post.get("link") or post["link"] in used_links:
                continue
            s = match_score(p.payload, post["title"])
            if s > best:
                best, best_post = s, post
        if best_post and best >= MATCH_THRESHOLD:
            used_links.add(best_post["link"])
            out.append({"piece_id": p.id, "url": best_post["link"],
                        "published_at": best_post["published_at"],
                        "score": round(best, 2), "post_title": best_post["title"]})
    return out


def is_my_post_url(url: str, blog_id: str) -> bool:
    """붙여넣은 URL이 '등록된 내 블로그' 글인지 — blog_id 일치 검사."""
    return bool(blog_id) and normalize_blog_id(url) == blog_id


# ── PHASE 4: 발행 일관성(C-Rank '활동 지속성' 신호) ─────────────────
def posting_consistency(posts: list[dict], weekly_target: int = 3, weeks: int = 4) -> dict:
    """RSS 최근글 발행일 → 실제 발행 주기 측정. 올린다 안에서의 활동이 아니라
    '블로그의 진짜 발행'을 기준으로 재기 때문에 정직하다(수동 발행 포함).
    반환: {this_week, weekly_target, on_pace, last_post_at, days_since_last,
           week_counts:[오래된주→이번주], streak_weeks, avg_per_week}"""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    # 이번 주 시작(월요일 00:00 UTC 근사 — KST 엄밀 경계보다 단순함 우선, 지표는 추세용)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    dates = sorted([p["published_at"] for p in (posts or []) if p.get("published_at")], reverse=True)
    counts = [0] * weeks                      # [이번주, 지난주, ...] → 마지막에 뒤집음
    for d in dates:
        for i in range(weeks):
            lo = week_start - timedelta(weeks=i)
            hi = lo + timedelta(weeks=1)
            if lo <= d < hi:
                counts[i] += 1
                break
    streak = 0
    for i in range(weeks):                    # 이번 주 포함 연속으로 1회+ 발행한 주 수
        if counts[i] > 0:
            streak += 1
        elif i > 0:                           # 이번 주는 아직 진행 중 — 0이어도 스트릭 안 끊음
            break
    last = dates[0] if dates else None
    return {
        "this_week": counts[0],
        "weekly_target": max(1, weekly_target),
        "on_pace": counts[0] >= max(1, weekly_target),
        "last_post_at": (last.isoformat() if last else ""),
        "days_since_last": ((now - last).days if last else None),
        "week_counts": list(reversed(counts)),
        "streak_weeks": streak,
        "avg_per_week": round(sum(counts) / weeks, 1),
    }


