"""🎯 대조군 정의 — 같은 채널·같은 주제인데 뽑히지 않은 글.

★ 2026-08-06 실물 판정으로 축을 두 번 바꿨다:
  ① '동시노출 화면 vs 아닌 화면' → **폐기**. 상업성 질의에선 항상 동시노출이라 대조군이 없다.
  ② '같은 질의 2페이지 밖' → **불가**. 모바일 통합검색은 where=m_blog·start=31을 무시한다
     (세 URL이 완전히 같은 9건을 냈다).
  ③ **채택**: 같은 채널의 같은 주제 글 중 그 질의 결과에 없는 것.
     채널 파워·발행 이력이 상수로 통제되므로 남는 차이가 구조다 —
     발행 이력을 인자로 넣지 않고도 구조를 볼 수 있는 유일한 설계다(프레임 보호).

실측 예('부산 동구 썬팅업체'):
  뽑힘   ksmrnd1/224361495243  '부산 동구 썬팅업체 기아 EV6 신차썬팅·유리막코팅'
  안뽑힘 ksmrnd1/224347149338  '부산광역시 동구 썬팅업체 추천, 썬팅+유리막코팅 원스톱'
  제목에 질의어가 거의 그대로 있는데 하나만 떴다.
"""
from __future__ import annotations

import re
import urllib.request

_TOK = re.compile(r"[가-힣A-Za-z0-9]+")
MIN_TOPIC_HIT = 2          # 질의 토큰이 이만큼 겹쳐야 '같은 주제'로 본다


def _tokens(s: str) -> set:
    return {t for t in _TOK.findall(s or "") if len(t) >= 2}


def rss_items(blog: str, timeout: int = 20) -> list:
    """채널의 최근 글 목록 — 공개 RSS만(R1)."""
    try:
        req = urllib.request.Request(f"https://rss.blog.naver.com/{blog}.xml",
                                     headers={"User-Agent": "Mozilla/5.0"})
        x = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for it in re.findall(r"<item>(.*?)</item>", x, re.S):
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        l = re.search(r"<link>(.*?)</link>", it, re.S)
        if not (t and l):
            continue
        m = re.search(r"/(\d{6,})", l.group(1))
        if m:
            out.append({"title": t.group(1).strip(), "post": m.group(1), "blog": blog})
    return out


def build(query: str, ranked_posts: list, limit_per_channel: int = 6) -> dict:
    """뽑힌 글 / 안 뽑힌 글을 가른다.

    ranked_posts: 그 질의 결과에 실제로 등장한 글 [{blog, post, title}]
    반환 {picked, control} — control은 같은 채널·같은 주제인데 결과에 없는 글.
    """
    qt = _tokens(query)
    picked = [p for p in (ranked_posts or []) if p.get("blog") and p.get("post")]
    ranked_key = {(p["blog"], p["post"]) for p in picked}
    channels = []
    for p in picked:
        if p["blog"] not in channels:
            channels.append(p["blog"])
    control, skipped = [], []
    for ch in channels:
        items = rss_items(ch)
        if not items:
            skipped.append({"blog": ch, "why": "RSS 없음/실패"})
            continue
        same = [i for i in items if len(qt & _tokens(i["title"])) >= MIN_TOPIC_HIT]
        cand = [i for i in same if (i["blog"], i["post"]) not in ranked_key]
        control += cand[:limit_per_channel]
    return {"query": query, "picked": picked, "control": control,
            "channels": channels, "skipped": skipped,
            "note": ("대조군은 같은 채널·같은 주제 글이다 — 채널 파워와 발행 이력이 "
                     "상수로 통제되므로 남는 차이가 구조다")}


def pairs_for(rows: list, per_industry: int = 1, min_control: int = 1) -> dict:
    """업종별 ②쌍 확보 — 같은 채널에서 뽑힘/안뽑힘이 **둘 다** 나오는 경우만.

    ★ 확보 안 되는 업종은 억지로 다른 채널을 섞지 않는다 — 채널 통제가 깨지면 대조가 무의미하다.
      '쌍 확보 실패'로 정직하게 남기고 그 업종은 뺀다.
    """
    from app.services.coexpose import scope as _sc
    out, failed = [], []
    for r in (rows or []):
        ind = r.get("industry") or ""
        if _sc.is_excluded(r.get("q"), ind):
            failed.append({"industry": ind, "q": r.get("q"), "why": "실운영 업종 제외"})
            continue
        picked = [p for p in (r.get("posts") or []) if p.get("kind") == "blog"][:5]
        if not picked:
            failed.append({"industry": ind, "q": r.get("q"), "why": "블로그 상위 글 없음"})
            continue
        c = build(r["q"], picked)
        got = 0
        for ch in c["channels"]:
            hi = [p for p in c["picked"] if p["blog"] == ch]
            lo = [p for p in c["control"] if p["blog"] == ch]
            if not hi or len(lo) < min_control:
                continue
            out.append({"industry": ind, "region": r.get("region"), "q": r["q"],
                        "channel": ch, "picked": hi[:2], "control": lo[:3]})
            got += 1
            if got >= per_industry:
                break
        if not got:
            failed.append({"industry": ind, "q": r.get("q"),
                           "why": "같은 채널에서 뽑힘/안뽑힘 쌍이 안 나옴"})
    return {"pairs": out, "failed": failed,
            "industries": sorted({p["industry"] for p in out if p["industry"]}),
            "note": "각 쌍은 같은 채널이라 채널 파워·발행 이력이 상수로 통제된다"}
