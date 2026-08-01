"""
🌐 유입 경로 진단 — 검색(블로그탭) 밖의 유입 통로를 점검한다(2026-08-01 사장님 지시 B).

배경: 지금까지 최적화는 '검색 중 블로그탭' 한 갈래만 봤다. 네이버 블로그의 실제 유입은
①검색(통합·블로그탭·이미지·스마트블록) ②네이버 내부 순환(이웃 새글 피드·주제별 인기글·클립)
③외부 검색(구글) ④직접·공유 로 갈린다. 이 모듈은 '측정 가능하고 조치 가능한' 축부터 진단한다.

원칙: 로그인·자격증명 0(공개 페이지만) · 업종/지명 하드코딩 0 · 실패는 조용히(None).
진단만 하고 자동 변경은 하지 않는다 — 처방은 운영자·사장님이 실행(주제 변경 등은 계정 설정).
"""
from __future__ import annotations

import logging
import re

import requests

from app import db

_log = logging.getLogger("shopcast.blogreach")
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")}


def blog_profile(blog_id: str) -> dict:
    """공개 모바일 블로그 홈에서 주제·이웃수·블로그명 추출(로그인 0). 실패 시 빈 dict."""
    bid = (blog_id or "").strip()
    if not bid:
        return {}
    try:
        h = requests.get(f"https://m.blog.naver.com/{bid}", headers=_UA, timeout=10).text
    except Exception:
        return {}
    if not h:
        return {}
    _subj = re.search(r'class="subject[^"]*">([^<]{1,20})</span>', h)
    _bud = re.search(r'class="buddy[^"]*">([\d,]+)\s*명의 이웃', h)
    _nm = re.search(r'"blogName"\s*:\s*"([^"]{2,40})"', h)
    out = {"blog_id": bid}
    if _subj:
        out["theme"] = _subj.group(1).strip("ㆍ· ").strip()
    if _bud:
        try:
            out["neighbors"] = int(_bud.group(1).replace(",", ""))
        except ValueError:
            pass
    if _nm:
        out["blog_name"] = _nm.group(1)
    return out


def _theme_fits(theme: str, industry: str, biz: str) -> "bool | None":
    """블로그 주제 ↔ 가게 업종 정합 판정 — 주제별 인기글·C-Rank 주제 응집도의 전제.
    LLM YES/NO + 30일 캐시(주제는 잘 안 바뀜). 무키·실패 None(판정 보류 — 경고하지 않음).
    업종·주제 목록 하드코딩 0(네이버 주제 체계가 바뀌어도 코드 수정 불필요)."""
    theme = (theme or "").strip()
    ind = (industry or "").strip()
    if not (theme and ind):
        return None
    try:
        from app import ratelimit as _rl
        _ck = f"themefit:{theme}|{ind}"
        hit = _rl.cache_get(_ck, 30 * 86400)
        if hit is not None:
            return bool(hit.get("fit"))
    except Exception:
        _rl, _ck = None, ""
    try:
        from app import llm as _llm
        _bz = {"local": "동네 매장", "seller": "온라인 판매자", "hybrid": "매장+온라인"}.get(
            biz or "local", "매장")
        v = _llm.call(
            f"네이버 블로그 주제(카테고리): '{theme}'\n가게 업종: '{ind}' ({_bz})\n"
            "이 가게가 자기 업종 글을 쓸 때, 저 블로그 주제가 적절한 분류인가?\n"
            "적절하면 YES, 전혀 다른 분야라 주제별 노출에 불리하면 NO. 한 단어만.",
            max_tokens=10)
        fit = "NO" not in (v or "").strip().upper()[:8]
    except Exception:
        return None
    try:
        if _rl and _ck:
            _rl.cache_set(_ck, {"fit": fit})
    except Exception:
        pass
    return fit


def diagnose(tenant_id: str) -> dict:
    """가게 하나의 '검색 밖 유입 통로' 진단 + 처방. 판단 근거를 함께 반환(정직성)."""
    t = db.get_tenant(tenant_id)
    if not t:
        return {"ok": False, "error": "tenant 없음"}
    bid = (getattr(t, "blog_id", "") or "").strip()
    if not bid:
        return {"ok": False, "error": "블로그 미연결"}

    prof = blog_profile(bid)
    ind = getattr(t, "industry", "") or ""
    biz = getattr(t, "biz_type", "local") or "local"
    fit = _theme_fits(prof.get("theme", ""), ind, biz)

    # 발행 리듬(공개 RSS) — 이웃 자산이 있어도 새 글이 없으면 피드 유입이 0이다
    posts, per_week, last_days = [], None, None
    try:
        from app.services import blogsync as _bs
        feed = _bs.fetch_feed(bid) or {}
        posts = feed.get("posts") or []
        if posts:
            from datetime import datetime, timezone
            ds = [p.get("published_at") for p in posts if p.get("published_at")]
            ds = [d if getattr(d, "tzinfo", None) else
                  (d.replace(tzinfo=timezone.utc) if hasattr(d, "replace") else None) for d in ds]
            ds = [d for d in ds if d]
            if ds:
                now = datetime.now(timezone.utc)
                last_days = (now - max(ds)).days
                per_week = round(sum(1 for d in ds if (now - d).days <= 28) / 4.0, 1)
    except Exception:
        pass

    nb = prof.get("neighbors")
    rx: list = []                                        # 처방(사람이 실행할 것)
    if fit is False:
        rx.append({"level": "high", "area": "주제 설정",
                   "msg": f"블로그 주제가 '{prof.get('theme')}'인데 업종은 '{ind}' — "
                          "주제별 인기글·주제 응집도에서 불리합니다. 블로그 관리에서 주제를 업종에 맞게 변경하세요."})
    if isinstance(nb, int):
        if nb < 100:
            rx.append({"level": "high", "area": "이웃 피드",
                       "msg": f"이웃 {nb}명 — 새 글이 피드로 퍼지지 않습니다. 서로이웃을 늘리세요. "
                              "※ 서이추는 두 몫을 합니다: 발행 즉시 도달하는 '유입 채널' + "
                              "사장님 리드에게 닿는 '영업 채널'(사장님 운영 방식)."})
        elif nb >= 500 and (per_week is not None and per_week < 1):
            rx.append({"level": "high", "area": "이웃 피드",
                       "msg": f"이웃 {nb:,}명인데 최근 4주 주당 {per_week}편 — 가장 큰 자산이 놀고 있습니다. "
                              "주 2편만 올려도 피드 유입이 즉시 생기고, 서이추로 모은 이웃에게 "
                              "'살아있는 블로그'로 보이는 영업 효과까지 함께 납니다."})
    if last_days is not None and last_days >= 14:
        rx.append({"level": "mid", "area": "발행 리듬",
                   "msg": f"마지막 발행 {last_days}일 전 — 최신성 신호가 약해집니다(검색·피드 동시 손해)."})
    if posts:
        _vid = sum(1 for p in posts[:10] if "clip" in (p.get("link") or ""))
        if not _vid:
            rx.append({"level": "mid", "area": "클립",
                       "msg": "만든 세로 영상을 네이버 클립에도 올리면 지면이 하나 더 생깁니다(비용 0)."})
    return {"ok": True, "tenant": t.name, "profile": prof, "theme_fit": fit,
            "posts_per_week": per_week, "last_post_days": last_days,
            "prescriptions": rx}


def sweep(limit: int = 30) -> list:
    """전 가게 진단 요약(크론·사령탑용) — 조치가 필요한 가게만."""
    out = []
    for t in db.list_tenants_with_blog()[:limit]:
        try:
            d = diagnose(t.id)
            if d.get("ok") and d.get("prescriptions"):
                out.append({"tenant": d["tenant"], "neighbors": (d.get("profile") or {}).get("neighbors"),
                            "theme": (d.get("profile") or {}).get("theme"),
                            "theme_fit": d.get("theme_fit"),
                            "per_week": d.get("posts_per_week"),
                            "rx": [r["msg"] for r in d["prescriptions"]]})
        except Exception:
            _log.exception("[blogreach] 진단 실패 t=%s", getattr(t, "id", "?"))
    return out
