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
    # A: 검색 밖 통로 측정(구글 색인·이미지 검색) + 플레이스 연동 점검
    ext: dict = {}
    try:
        _pub = (db.list_blog_publishes(tenant_id, limit=1) or [{}])[0]
        _kw = (_pub.get("target_kw") or "").strip()
        if not _kw:
            _kws = db.tracked_keywords(tenant_id, 1)
            _kw = _kws[0] if _kws else ""
        ext = external_reach(bid, (_pub.get("published_url") or ""), _kw)
    except Exception:
        pass
    if ext.get("google_indexed") is False:
        rx.append({"level": "mid", "area": "구글 색인",
                   "msg": "발행 글이 구글에 아직 안 잡혔습니다 — 네이버 밖 유입이 0입니다. "
                          "서치어드바이저 색인 요청 + 시간이 필요합니다."})
    if ext.get("image_rank") == 0:
        rx.append({"level": "mid", "area": "이미지 검색",
                   "msg": "이미지탭에 우리 사진이 안 보입니다 — 사진 많은 업종의 숨은 유입 통로입니다. "
                          "사진 파일명·본문 설명이 검색어와 맞물리면 노출 확률이 올라갑니다."})
    # 🧱 블록 판정 — 블로그 지면이 없는 판이면 글만으로는 통합검색 진입이 어렵다(클립·플레이스 병행)
    try:
        _kws = db.tracked_keywords(tenant_id, 3)
        _no_surface = [k for k in _kws if (blocks_for(tenant_id, k) or {}).get("blog_surface") is False]
        if _no_surface:
            rx.append({"level": "high", "area": "통합검색 지면",
                       "msg": f"'{_no_surface[0]}' 등 {len(_no_surface)}개 키워드는 통합검색 첫 화면에 "
                              "블로그 지면 자체가 없습니다(플레이스·클립·숏텐츠가 차지). "
                              "블로그 글만으로는 노출이 어렵고 — 같은 영상을 네이버 클립에 올리고 "
                              "플레이스를 채우는 쪽이 실효가 큽니다."})
    except Exception:
        pass
    _map = (getattr(t, "map_url", "") or "").strip()
    if (getattr(t, "biz_type", "local") or "local") != "seller" and not _map:
        rx.append({"level": "mid", "area": "플레이스",
                   "msg": "네이버 플레이스(지도) 링크가 등록돼 있지 않습니다 — 지도→블로그 유입 경로가 끊깁니다."})
    return {"ok": True, "tenant": t.name, "profile": prof, "theme_fit": fit,
            "posts_per_week": per_week, "last_post_days": last_days,
            "external": ext, "prescriptions": rx}


def external_reach(blog_id: str, post_url: str = "", keyword: str = "") -> dict:
    """🔍 검색 밖 유입 통로 측정(A, 2026-08-01) — 공개 API만, 자격증명 0.
    ①구글 색인 여부(외부 검색 유입의 전제) ②네이버 이미지 검색 노출(사진 많은 글의 숨은 통로).
    각 항목은 실패 시 None(모름) — 0(없음)과 구분해 정직하게 보고한다."""
    out: dict = {"blog_id": blog_id}
    # ① 구글 색인 — 공개 검색 결과 페이지에서 우리 도메인 등장 여부만 확인(크롤 1회, 저속)
    if post_url:
        try:
            q = requests.utils.quote(f"site:{post_url.split('?')[0]}")
            h = requests.get(f"https://www.google.com/search?q={q}&hl=ko",
                             headers=_UA, timeout=10).text
            out["google_indexed"] = ("blog.naver.com" in h and "did not match any documents" not in h
                                     and "일치하는 문서가 없습니다" not in h)
        except Exception:
            out["google_indexed"] = None
    # ② 네이버 이미지 검색 — 우리 블로그 사진이 이미지탭에 잡히는지(공식 검색 API)
    if keyword:
        try:
            import os as _os
            r = requests.get("https://openapi.naver.com/v1/search/image",
                             params={"query": keyword, "display": 50},
                             headers={"X-Naver-Client-Id": _os.environ.get("NAVER_CLIENT_ID", ""),
                                      "X-Naver-Client-Secret": _os.environ.get("NAVER_CLIENT_SECRET", "")},
                             timeout=8)
            if r.status_code == 200:
                items = r.json().get("items", [])
                hit = next((i + 1 for i, it in enumerate(items)
                            if blog_id and blog_id in (it.get("link", "") + it.get("thumbnail", ""))), 0)
                out["image_rank"] = hit                  # 0=미노출, N=이미지탭 N번째
                out["image_checked"] = len(items)
            else:
                out["image_rank"] = None
        except Exception:
            out["image_rank"] = None
    return out


def blocks_ingest(tenant_id: str, rows: list) -> dict:
    """🧱 스마트블록 정찰 결과 반영(2026-08-01) — 맥 로컬 스캐너(insight/blocks.py)가 POST.
    '이 키워드의 통합검색에 블로그 지면이 있는가'를 가게 payload에 남겨 작전·진단이 참조한다.
    실측 배경: 블로그탭 8위여도 통합검색 첫 화면엔 플레이스·숏텐츠·클립만 있는 판이 있다."""
    t = db.get_tenant(tenant_id)
    if not t:
        return {"ok": False, "error": "tenant 없음"}
    saved = 0
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            from datetime import datetime as _d
            for r in (rows or [])[:40]:
                kw = (r.get("keyword") or "").strip()
                if not kw:
                    continue
                c.execute("INSERT OR REPLACE INTO kw_blocks VALUES(?,?,?,?,?,?)",
                          (tenant_id, kw, "|".join(r.get("blocks") or [])[:400],
                           "|".join(r.get("blog_blocks") or [])[:200],
                           1 if r.get("my_visible") else 0, _d.utcnow().isoformat()))
                saved += 1
    except Exception:
        _log.exception("[blogreach] 블록 저장 실패")
        return {"ok": False, "error": "저장 실패"}
    return {"ok": True, "saved": saved}


def blocks_for(tenant_id: str, keyword: str) -> dict:
    """저장된 블록 판정 조회 — 생성 작전이 '이 판에 블로그 지면이 있는가'를 참조."""
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            r = c.execute("SELECT * FROM kw_blocks WHERE tenant_id=? AND keyword=?",
                          (tenant_id, " ".join((keyword or "").split()))).fetchone()
        if not r:
            return {}
        d = dict(r)
        d["blocks"] = [x for x in (d.get("blocks") or "").split("|") if x]
        d["blog_blocks"] = [x for x in (d.get("blog_blocks") or "").split("|") if x]
        d["blog_surface"] = bool(d["blog_blocks"])       # 이 판에 블로그 지면이 있는가
        return d
    except Exception:
        return {}


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
