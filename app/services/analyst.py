"""
AI 순위 분석가 — "왜 이 순위인가 · 왜 1위가 아닌가 · 어떻게 이기나"(분석가 P1·P2).

[절대 원칙] 크롤링 없음. 사용하는 소스는 전부 합법 공개 API/피드:
  ① 네이버 블로그검색 API — 상위 글 제목·요약(description=본문 앞부분)·발행일·블로그명
  ② 공개 RSS(rss.blog.naver.com) — 상위 블로그의 발행 빈도·주제 일관성(체급 추정)
  ③ 관련 키워드 상위 재등장 횟수 — 블로그 파워 추정 + 업종 승리 패턴 기계 집계
  ④ 내 글 실측 — quality_audit·글자수·이미지수·발행일·내 블로그 일관성
본문 전체는 못 본다 — 모든 출력에 '제목·요약·발행패턴 기반'임을 명시하고 추론은 '~로 보임'으로.

비용 가드: RSS·검색 결과는 일 단위 캐시, LLM 분석은 (글, 현재 순위)당 1회 캐시 — 순위가
변해야 재분석. 처방 버튼은 실측 격차 플래그에서 기계적으로 생성(LLM이 액션을 지어내지 않음).
"""
from __future__ import annotations

import json
import logging

from app import db

_log = logging.getLogger("shopcast.analyst")

HONEST_NOTE = ("이 분석은 상위 글의 제목·요약·발행 패턴 등 공개 데이터 기반 추정이에요 — "
               "본문 전체와 네이버 내부 가중치는 볼 수 없어요. 순위 보장이 아니라 "
               "확인 가능한 신호 기준의 가능성 제시입니다.")

_REVIEW_WORDS = ("후기", "내돈내산", "다녀왔", "시공기", "받아봤", "써봤", "방문기", "경험")


def _cache_get(key: str, ttl: int):
    from app import ratelimit
    return ratelimit.cache_get(key, ttl)


def _cache_set(key: str, val) -> None:
    from app import ratelimit
    ratelimit.cache_set(key, val)


def _blog_power(blog_id: str, industry: str) -> dict:
    """상위 블로그 체급 추정(공개 RSS) — {posts_4w, topic_ratio, days_since_last}. 일 캐시."""
    key = f"an:power:{blog_id}"
    hit = _cache_get(key, 86400)
    if hit is not None:
        return hit
    out = {"posts_4w": None, "topic_ratio": None, "days_since_last": None}
    try:
        from app.services import blogsync
        feed = blogsync.fetch_feed(blog_id)
        posts = feed.get("posts") or []
        if posts:
            pc = blogsync.posting_consistency(posts)
            out["posts_4w"] = sum(pc.get("week_counts") or [])
            out["days_since_last"] = pc.get("days_since_last")
            toks = [w for w in (industry or "").split() if len(w) >= 2]
            if toks:
                n = sum(1 for p in posts if any(w in (p.get("title") or "") for w in toks))
                out["topic_ratio"] = round(n / len(posts), 2)
    except Exception:
        pass
    _cache_set(key, out)
    return out


def _related_tops(kw: str, region: str, industry: str) -> dict:
    """관련 키워드 3종의 상위 10 검색 결과(일 캐시) — 노출 빈도·업종 패턴 집계 재료."""
    key = f"an:rel:{kw.replace(' ', '')}"
    hit = _cache_get(key, 86400)
    if hit is not None:
        return hit
    from app.services import blogrank
    variants = []
    base = (region or "").split(" ")[0]
    for cand in (f"{base} {industry}".strip(), f"{industry} 후기".strip(), f"{industry} 가격".strip()):
        cand = " ".join(cand.split())
        if cand and cand.replace(" ", "") != kw.replace(" ", "") and cand not in variants:
            variants.append(cand)
    out = {}
    for v in variants[:3]:
        out[v] = [{"blog_id": blogrank._item_blog_id(it), "title": it.get("title", ""),
                   "postdate": it.get("postdate", "")}
                  for it in blogrank._search_blog(v, 10)]
    _cache_set(key, out)
    return out


def _industry_pattern(top_items: list, related: dict, region: str) -> dict:
    """업종 승리 패턴 — 상위 글들의 제목 특징 기계 집계(LLM 아님 = 날조 불가)."""
    import datetime
    titles = [it.get("title", "") for it in top_items[:10]]
    for rows in related.values():
        titles += [r.get("title", "") for r in rows[:5]]
    titles = [t for t in titles if t]
    if not titles:
        return {}
    n = len(titles)
    base = (region or "").split(" ")[0]
    has_region = sum(1 for t in titles if base and base in t)
    has_price = sum(1 for t in titles if any(w in t for w in ("가격", "비용", "만원", "얼마")))
    is_review = sum(1 for t in titles if any(w in t for w in _REVIEW_WORDS))
    ages = []
    for it in top_items[:10]:
        pd = it.get("postdate") or ""
        if len(pd) == 8:
            try:
                d = datetime.date(int(pd[:4]), int(pd[4:6]), int(pd[6:8]))
                ages.append((datetime.date.today() - d).days)
            except Exception:
                pass
    return {"n": n, "region_pct": round(100 * has_region / n), "price_pct": round(100 * has_price / n),
            "review_pct": round(100 * is_review / n),
            "avg_age_days": (round(sum(ages) / len(ages)) if ages else None)}


def collect(t, piece, publish: dict) -> dict:
    """P1 — 합법 다각도 수집. 반환 {kw, my, top, power, exposure, pattern}."""
    from app.services import blogrank, race, whynot
    kw = race._kw_for(piece, publish)
    items = blogrank._search_blog(kw, 10)
    my_url = blogrank._norm_post_url((publish or {}).get("published_url") or "")
    my_rank = next((i for i, it in enumerate(items, 1)
                    if blogrank._norm_post_url(it.get("link", "")) == my_url), 0) or None
    top = []
    for i, it in enumerate(items[:3], 1):
        bid = blogrank._item_blog_id(it)
        d = {"rank": i, "title": it.get("title", ""), "desc": (it.get("description", "") or "")[:180],
             "blogger": it.get("bloggername", ""), "blog_id": bid, "postdate": it.get("postdate", "")}
        d["power"] = _blog_power(bid, t.industry or "") if bid else {}
        top.append(d)
    related = _related_tops(kw, t.region or "", t.industry or "")
    # 노출 빈도(블로그 파워): 상위 1~3 블로그가 관련 키워드 상위에 몇 번 더 등장하나
    for d in top:
        d["exposure"] = sum(1 for rows in related.values()
                            for r in rows if r.get("blog_id") and r["blog_id"] == d["blog_id"])
    pattern = _industry_pattern(items, related, t.region or "")
    # 내 글 실측
    pl = (piece.payload or {}) if piece else {}
    body = pl.get("body") or ""
    my_power = _blog_power(getattr(t, "blog_id", "") or "", t.industry or "") if getattr(t, "blog_id", "") else {}
    my = {"rank": my_rank, "days": whynot._days_since((publish or {}).get("published_at") or ""),
          "title": (publish or {}).get("post_title") or pl.get("title") or "",
          "audit": (pl.get("ranking_audit") or {}).get("score"),
          "chars": (len(body) or None), "photos": (body.count("[사진") or None) if body else None,
          "angle": pl.get("angle") or "", "power": my_power}
    return {"kw": kw, "my": my, "top": top, "pattern": pattern}


def _gap_flags(data: dict) -> list[dict]:
    """실측 격차 → 처방 액션(기계 생성 — LLM이 버튼을 지어내지 않음)."""
    from urllib.parse import quote
    kw, my, top, pat = data["kw"], data["my"], data["top"], data.get("pattern") or {}
    rx = []
    t1 = top[0] if top else {}
    p1 = (t1.get("power") or {})
    myp = (my.get("power") or {})
    if p1.get("posts_4w") and (myp.get("posts_4w") is None or p1["posts_4w"] > (myp.get("posts_4w") or 0) * 2):
        rx.append({"why": f"1위 블로그는 최근 4주 {p1['posts_4w']}건 발행"
                          + (f"(주제 비율 {int(100 * p1['topic_ratio'])}%)" if p1.get("topic_ratio") else "")
                          + f" — 내 블로그({myp.get('posts_4w') or 0}건)와 체급 차",
                   "text": "이 주제 글을 주 2회씩 3주 쌓으면 체급이 따라붙어요.",
                   "label": "발행 캘린더", "href": "/me#calendar"})
    title_has_price = any(w in (my.get("title") or "") for w in ("가격", "비용", "만원", "얼마"))
    if pat.get("price_pct", 0) >= 50 and not title_has_price:
        rx.append({"why": f"이 업종 상위 글 {pat['price_pct']}%가 제목에 가격·비용 언급 — 내 제목엔 없음",
                   "text": "제목에 가격·지역을 넣은 글이 검색 의도를 더 정확히 잡아요 — 그 방향으로 한 편 더.",
                   "label": "가격형으로 만들기", "href": f"/me?target_kw={quote(kw)}&angle=price"})
    my_is_review = any(w in ((my.get("title") or "") + (my.get("angle") or "")) for w in _REVIEW_WORDS + ("review",))
    if pat.get("review_pct", 0) >= 60 and not my_is_review:
        rx.append({"why": f"상위 글 {pat['review_pct']}%가 후기형 — 내 글은 정보형으로 보임",
                   "text": "이 키워드는 후기형이 강해요 — 후기형 앵글로 다시 노려보세요.",
                   "label": "후기형으로 만들기", "href": f"/me?target_kw={quote(kw)}&angle=review"})
    if not rx:
        rx.append({"why": "뚜렷한 구조 격차는 안 보여요 — 남은 변수는 꾸준함과 시간",
                   "text": f"'{kw}' 축으로 주 2회 발행을 유지하는 게 지금 최선의 수예요.",
                   "label": "글 하나 더", "href": f"/me?target_kw={quote(kw)}"})
    return rx[:3]


def analyze(t, piece, publish: dict) -> dict:
    """P2 — AI 분석(원인·격차·처방). (글, 현재 순위)당 1회 캐시 — 순위 변동 시에만 재분석."""
    data = collect(t, piece, publish)
    pid = (publish or {}).get("piece_id") or (piece.id if piece else "")
    ck = f"an:done:{pid}:{data['my'].get('rank')}"
    hit = _cache_get(ck, 3 * 86400)
    if hit is not None:
        return hit
    gaps = _gap_flags(data)
    my, top, pat = data["my"], data["top"], data.get("pattern") or {}
    top_txt = "\n".join(
        f"{d['rank']}위: '{d['title']}' (블로그 {d['blogger']}, {d['postdate']}, "
        f"최근4주 {d['power'].get('posts_4w')}건·주제비율 {d['power'].get('topic_ratio')}, "
        f"관련 키워드 상위 재등장 {d.get('exposure', 0)}회)\n  요약: {d['desc']}"
        for d in top) or "(상위 글 조회 불가)"
    prompt = (
        "너는 네이버 블로그 검색 순위 분석가다. 소상공인 사장님에게 쉬운 말로 설명하라(전문용어 최소).\n"
        "아래는 전부 실측 데이터다. 여기 없는 사실을 지어내지 마라. 본문 전체는 못 봤으므로 "
        "글 성격 추론은 '~로 보임'으로 말하라. 순위 보장 표현 금지 — '가능성'까지만.\n\n"
        f"[타겟 키워드] {data['kw']}\n"
        f"[내 글] 순위 {my.get('rank') or '10위 밖'} · 발행 {my.get('days')}일차 · 제목 '{my.get('title')}' · "
        f"품질점수 {my.get('audit')} · {my.get('chars') or '?'}자 · 사진 {my.get('photos') or '?'}장 · "
        f"내 블로그 최근4주 {my.get('power', {}).get('posts_4w')}건 발행\n"
        f"[상위 글(제목·요약·발행일·블로그 체급)]\n{top_txt}\n"
        f"[업종 패턴(상위 {pat.get('n')}개 제목 집계)] 지역 포함 {pat.get('region_pct')}% · "
        f"가격 언급 {pat.get('price_pct')}% · 후기형 {pat.get('review_pct')}% · 평균 {pat.get('avg_age_days')}일 전 발행\n"
        f"[실측 격차(이미 계산됨)]\n" + "\n".join("- " + g["why"] for g in gaps) + "\n\n"
        "형식 그대로(대괄호 머리표 유지, 각 2~3문장):\n"
        "[왜 이 순위]\n(내 글이 이 순위까지 온 이유 — 실측 강점 인용)\n"
        "[왜 1위가 아닌가]\n(상위 글과의 격차 — 위 실측 격차를 쉬운 말로)\n"
        "[한 줄 요약]\n(브리핑용 한 문장: 현재 상태+격차 핵심+다음 수)")
    try:
        from app import llm
        raw = llm.call(prompt, max_tokens=800)
        from app.generators.text_claude import _parse_sections
        sec = _parse_sections(raw, ["왜 이 순위", "왜 1위가 아닌가", "한 줄 요약"])
    except Exception:
        _log.exception("[analyst] LLM 분석 실패")
        sec = {}
    out = {"kw": data["kw"], "rank": my.get("rank"),
           "why_here": (sec.get("왜 이 순위") or "").strip(),
           "why_not_first": (sec.get("왜 1위가 아닌가") or "").strip(),
           "brief_line": (sec.get("한 줄 요약") or "").strip().split("\n")[0][:120],
           "gaps": gaps, "pattern": pat, "top": [{k: d[k] for k in ("rank", "title", "blogger")} for d in top],
           "note": HONEST_NOTE}
    _cache_set(ck, out)
    _cache_set(f"an:brief:{pid}", {"line": out["brief_line"], "rank": my.get("rank")})
    return out


def cached_brief_line(piece_id: str) -> str:
    """브리핑용 — 캐시된 분석 한 줄(있을 때만, LLM 재호출 없음)."""
    hit = _cache_get(f"an:brief:{piece_id}", 3 * 86400)
    return (hit or {}).get("line") or ""
