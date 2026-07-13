"""
"왜 아직 안 뜨나요?" — 노출 진단 + 처방전.

발행한 글이 검색에 안 보일 때 원클릭으로:
① 색인 경과 ② 현재 순위 실측(지역/블로그탭/쇼핑) ③ 글 품질(quality_audit)
④ C-Rank 주제 집중(RSS 실측) ⑤ 키워드 경쟁도(searchad) ⑥ 발행 일관성
을 점검해 '안 뜨는 원인 → 뜨게 하는 처방(원클릭 액션)'을 만든다.

정직성: 네이버 랭킹 가중치는 비공개 — '무조건 뜬다' 금지, '유리해진다'까지만.
모든 판정은 실측(순위 API·RSS·audit·searchad)만 사용, 추측 수치 금지.
"""
from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from app import db

_log = logging.getLogger("shopcast.whynot")

HONEST_NOTE = ("네이버 랭킹 가중치는 비공개예요 — 아래 진단은 공개된 신호(C-Rank 꾸준함·D.I.A. 경험서술) "
               "기준이고, 처방은 노출에 '유리해지는' 방법이지 무조건 노출을 보장하진 않아요.")


def _days_since(published_at: str) -> int:
    try:
        d = datetime.fromisoformat((published_at or "")[:19])
        return max(0, (datetime.utcnow() - d).days)
    except Exception:
        return -1


def _topic_post_count(posts: list, topic: str) -> int:
    """RSS 최근글 중 주제(업종/주제축) 토큰이 제목에 들어간 글 수 — C-Rank 주제 집중 실측."""
    toks = [w for w in (topic or "").split() if len(w) >= 2]
    if not toks:
        return 0
    n = 0
    for p in posts or []:
        title = p.get("title") or ""
        if any(w in title for w in toks):
            n += 1
    return n


def diagnose(t, piece, publish: dict | None = None) -> dict:
    """진단 실행 → {kw, days, exposed, checks:[{status(ok|warn|fail|info), title, detail}],
    prescriptions:[{text, label, href}], note}. 모든 항목 실측 실패 시 'unknown'으로 정직 표기."""
    pl = piece.payload or {}
    kw = ((pl.get("target_keywords") or [""])[0] or "").strip() or \
        f"{(t.region or '').split(' ')[0]} {t.industry}".strip()
    biz = (getattr(t, "biz_type", "local") or "local")
    checks: list[dict] = []
    rx: list[dict] = []
    make = lambda k, ang="review": f"/me?target_kw={quote(k)}&angle={ang}"

    # ① 색인 경과일
    days = _days_since((publish or {}).get("published_at") or "")
    if 0 <= days < 4:
        checks.append({"status": "info", "title": f"발행 {days}일차 — 아직 네이버 수집 중",
                       "detail": "새 글이 검색에 잡히는 데 보통 3~7일 걸려요. 지금 안 보이는 건 정상이에요."})
        rx.append({"text": "3일쯤 더 기다리세요 — 그동안 같은 주제 글을 하나 더 올리면 수집·신뢰에 모두 유리해요.",
                   "label": "다른 글 하나 더 만들기", "href": make(kw)})
    elif days >= 0:
        checks.append({"status": "ok", "title": f"발행 {days}일차 — 수집 기간은 지났어요",
                       "detail": "색인 대기 문제는 아니에요. 아래 원인을 보세요."})

    # ② 현재 순위 실측
    exposed = False
    try:
        from app.services import place
        if biz == "seller":
            r = place.shop_rank(kw, getattr(t, "brand_name", "") or t.name)
            lab, depth = "쇼핑검색", place.SHOP_SCAN_DEPTH
        else:
            r = place.rank(kw, t.name)
            lab, depth = "네이버 지역검색", 5
        if r is None:
            checks.append({"status": "info", "title": f"'{kw}' 순위 — 조회 불가",
                           "detail": "네이버 API 응답이 없어 순위를 못 쟀어요. 잠시 후 다시 진단해보세요."})
        elif r >= 1:
            exposed = True
            checks.append({"status": "ok", "title": f"'{kw}' {lab} {r}위 — 노출 중!",
                           "detail": "이미 검색에 잡히고 있어요. 같은 키워드 글을 더하면 상위 안착이 빨라져요."})
            rx.append({"text": f"오르는 중엔 굳히기가 제일 효율 좋아요 — '{kw}' 글 1편 더.",
                       "label": "굳히기 글 만들기", "href": make(kw, "howto")})
        else:
            checks.append({"status": "fail", "title": f"'{kw}' {lab} {depth}위 밖 — 미노출",
                           "detail": "아직 검색 상위에 없어요. 아래 원인 항목에서 이유를 찾아 처방대로 해보세요."})
    except Exception:
        checks.append({"status": "info", "title": "순위 조회 실패", "detail": "네트워크/키 문제로 실측을 못 했어요."})
    # 블로그탭(블로그 연결 시)
    try:
        bid = getattr(t, "blog_id", "") or ""
        if bid and biz != "seller":
            from app.services import blogrank
            br = blogrank.blog_rank(kw, bid)
            if br.get("rank"):
                exposed = True
                checks.append({"status": "ok", "title": f"블로그탭 {br['rank']}위 — 내 글이 잡혀요",
                               "detail": "블로그 검색에는 이미 노출 중이에요."})
            elif br.get("rank") == 0:
                checks.append({"status": "warn", "title": "블로그탭 30위 밖",
                               "detail": "블로그 검색에서도 아직 상위가 아니에요 — 주제 꾸준함이 핵심이에요."})
    except Exception:
        pass

    # ③ 글 품질(quality_audit — 저장본 우선)
    au = pl.get("ranking_audit") or {}
    score = au.get("score")
    if score is None:
        try:
            from app import seo
            au = seo.quality_audit(piece.channel.value, piece.kind.value, pl)
            score = au.get("score")
        except Exception:
            score = None
    if score is not None:
        warns = [w for w in (au.get("warnings") or [])][:3]
        if score < 70:
            checks.append({"status": "fail", "title": f"글 품질 {score}점 — 보강 필요",
                           "detail": "부족한 신호: " + ("; ".join(warns) if warns else "경험 서술·분량·이미지 확인")})
            rx.append({"text": "이 키워드로 경험 문장·사진을 더 담아 보강 글을 새로 올리는 게 빨라요.",
                       "label": "보강 글 만들기", "href": make(kw)})
        elif score < 85:
            checks.append({"status": "warn", "title": f"글 품질 {score}점 — 나쁘지 않지만 아쉬워요",
                           "detail": ("; ".join(warns) if warns else "세부 경고 없음")})
        else:
            checks.append({"status": "ok", "title": f"글 품질 {score}점 — 글 자체는 좋아요",
                           "detail": "품질 문제라기보다 아래 신뢰도·경쟁 요인일 가능성이 커요."})

    # ④ C-Rank 주제 집중(RSS 실측)
    posts = []
    try:
        bid = getattr(t, "blog_id", "") or ""
        if bid:
            from app.services import blogsync
            feed = blogsync.fetch_feed(bid)
            posts = feed.get("posts") or []
            topic = (getattr(t, "topic_axis", "") or t.industry or "").strip()
            n_topic = _topic_post_count(posts, topic)
            if n_topic < 5:
                checks.append({"status": "warn",
                               "title": f"'{topic}' 주제 글이 최근 {n_topic}개뿐 — 전문성 신호 부족",
                               "detail": "네이버 C-Rank는 '한 주제를 꾸준히 쓰는 블로그'를 신뢰해요. "
                                         "주제 글이 쌓여야 새 글도 상위에 올라가요."})
                rx.append({"text": f"'{topic}' 주제 글을 주 2회씩 3주만 — 그럼 전문성 신뢰가 쌓여요.",
                           "label": "발행 캘린더 보기", "href": "/me#calendar"})
            else:
                checks.append({"status": "ok", "title": f"'{topic}' 주제 글 {n_topic}개 — 주제 집중 좋아요",
                               "detail": "전문성 신호는 쌓이고 있어요."})
        else:
            checks.append({"status": "info", "title": "블로그 미연결 — C-Rank 실측 불가",
                           "detail": "내 블로그를 연결하면 주제 집중·발행 주기를 실측해드려요."})
    except Exception:
        pass

    # ⑤ 키워드 경쟁도(searchad 실측)
    try:
        from app.services import searchad
        rows = searchad.keyword_volumes([kw])
        me = next((r for r in rows if (r.get("keyword") or "").replace(" ", "") == kw.replace(" ", "")), None)
        if me:
            comp, vol = (me.get("comp") or ""), (me.get("total") or 0)
            if comp == "높음" or vol >= 10000:
                alt = ""
                try:
                    cands = searchad.sweet_spot_keywords([kw], limit=3)
                    alt = next((c for c in cands if c.replace(" ", "") != kw.replace(" ", "")), "")
                except Exception:
                    pass
                checks.append({"status": "warn",
                               "title": f"'{kw}' 경쟁 치열 (월 {vol:,}회 · 경쟁도 {comp or '높음'})",
                               "detail": "대형 키워드는 신생 블로그가 바로 잡기 어려워요 — 롱테일부터 잡고 올라가는 게 정석이에요."})
                if alt:
                    rx.append({"text": f"'{alt}'(롱테일)부터 잡으세요 — 경쟁이 덜해 먼저 노출되고, 그 신뢰가 '{kw}'에도 쌓여요.",
                               "label": f"'{alt}' 글 만들기", "href": make(alt)})
            else:
                checks.append({"status": "ok",
                               "title": f"'{kw}' 경쟁 무난 (월 {vol:,}회 · 경쟁도 {comp or '보통'})",
                               "detail": "키워드 난이도 문제는 아니에요."})
    except Exception:
        pass

    # ⑥ 발행 일관성
    try:
        act = db.publish_activity(t.id)
        gap = act.get("gap_days")
        if posts:
            from app.services import blogsync
            pc = blogsync.posting_consistency(posts)
            gap = pc.get("days_since_last", gap)
        if gap is not None and gap >= 5:
            checks.append({"status": "warn", "title": f"마지막 발행 후 {gap}일 — 꾸준함 신호가 식고 있어요",
                           "detail": "발행이 띄엄띄엄이면 C-Rank '지속성' 점수가 안 쌓여요."})
            rx.append({"text": f"지난 {gap}일 발행이 없었어요 — 오늘 하나 올리면 페이스가 돌아와요.",
                       "label": "오늘 하나 올리기", "href": make(kw)})
        elif gap is not None:
            checks.append({"status": "ok", "title": f"마지막 발행 {gap}일 전 — 발행 페이스 좋아요",
                           "detail": "꾸준함 신호는 유지되고 있어요."})
    except Exception:
        pass

    if not rx:
        rx.append({"text": f"핵심은 꾸준함 — '{kw}' 축으로 주 2~3회 발행이 상위노출의 정석이에요.",
                   "label": "글 하나 더 만들기", "href": make(kw)})
    return {"kw": kw, "days": days, "exposed": exposed,
            "checks": checks, "prescriptions": rx[:3], "note": HONEST_NOTE}
