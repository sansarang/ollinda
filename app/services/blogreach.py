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
    _is_local = (getattr(t, "biz_type", "local") or "local") != "seller"
    place: dict = {}
    if _is_local:
        try:
            place = place_audit(t)
        except Exception:
            place = {}
        if not _map:
            rx.append({"level": "mid", "area": "플레이스",
                       "msg": "네이버 플레이스(지도) 링크가 등록돼 있지 않습니다 — 지도→블로그 유입 경로가 끊깁니다."})
        if place.get("configured") and place.get("registered") is False:
            _clash = place.get("name_clash") or {}
            if _clash:                                    # 같은 상호의 다른 지역 업체가 먼저 잡히는 경우
                rx.append({"level": "high", "area": "플레이스 등록",
                           "msg": f"'{t.name}' 검색에 다른 지역 업체({_clash.get('address') or '?'})가 "
                                  "먼저 잡힙니다 — 우리 가게가 지역검색에서 밀리거나 아직 안 잡히는 "
                                  "상태입니다. 스마트플레이스에서 상호·주소·업종을 정확히 채우면 "
                                  "지역 검색어에서 우리 쪽이 잡힙니다."})
            else:
                rx.append({"level": "high", "area": "플레이스 등록",
                           "msg": f"'{t.name}'이(가) 네이버 지역검색에 안 잡힙니다 — 통합검색 첫 화면의 "
                                  "플레이스 자리를 통째로 놓치고 있습니다. 네이버 스마트플레이스에 "
                                  "업체 등록(무료)이 최우선입니다."})
        elif place.get("ambiguous"):                      # 지역 일부만 일치 — 단정하지 않고 확인 요청
            _amb = place.get("ambiguous") or {}
            rx.append({"level": "mid", "area": "플레이스 확인",
                       "msg": f"'{t.name}' 지역검색 결과가 등록 지역과 어긋납니다"
                              f"(잡힌 주소: {_amb.get('address') or '?'}). 같은 상호의 다른 업체이거나 "
                              "플레이스 주소가 옛 주소일 수 있어 진단을 보류했습니다 — 확인이 필요합니다."})
        elif place.get("registered"):
            # ★ '전화번호 비어 있음' 처방 삭제(2026-08-01 사장님 지적 — 실제로는 등록돼 있었다).
            #   지역검색 API의 telephone 필드는 네이버가 값을 내려주지 않는 사실상 폐기 필드라,
            #   빈 값 = 미등록이 아니다. API가 번호를 '줬는데 다를 때'만 경고한다(그건 실측이다).
            if place.get("tel_match") is False:
                rx.append({"level": "high", "area": "플레이스 정보",
                           "msg": f"플레이스 전화번호가 가게 정보와 다릅니다(등록: {place.get('listed',{}).get('tel')}) "
                                  "— 손님이 다른 번호로 겁니다. 즉시 수정하세요."})
            _rr = place.get("region_rank")
            if _rr == 0:
                rx.append({"level": "high", "area": "플레이스 순위",
                           "msg": f"'{place.get('region_kw')}' 지역검색 상위 5곳 안에 없습니다"
                                  f"(1위: {place.get('leader') or '?'}). 플레이스 사진·소개·영업정보·"
                                  "리뷰를 채우는 것이 블로그 글 한 편보다 유입 효과가 큽니다."})
            elif isinstance(_rr, int) and _rr >= 2 and place.get("rival"):
                rx.append({"level": "mid", "area": "플레이스 순위",
                           "msg": f"'{place.get('region_kw')}' {_rr}위 — 바로 위는 '{place.get('rival')}'입니다. "
                                  "사진·리뷰·정보 완성도로 추월 가능한 거리입니다."})
    return {"ok": True, "tenant": t.name, "profile": prof, "theme_fit": fit,
            "posts_per_week": per_week, "last_post_days": last_days,
            "external": ext, "place": place, "prescriptions": rx}


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


def place_audit(tenant) -> dict:
    """📍 플레이스 진단(2026-08-01) — 지역 업종의 실질 1지면. 공식 지역검색 API만 사용(크롤 0).
    확인: ①등록 여부(상호 검색으로 잡히는가) ②등록 정보 정합(업종·주소·전화) ③지역+업종 노출 순위.
    실측 배경: 통합검색 첫 화면을 플레이스가 차지하는 판이 많아 블로그보다 이쪽이 실효가 크다."""
    from app.services import place as _pl
    out: dict = {"configured": _pl.configured()}
    if not _pl.configured():
        return out
    name = (getattr(tenant, "name", "") or "").strip()
    if not name:
        return out
    # ★ 상호만으로 찾으면 남의 가게를 우리 가게로 단정한다(2026-08-01 실측: 기장 중고차 업체를
    #   같은 상호의 남구 광택전문 업체로 진단해 '업종을 고치라'는 틀린 처방을 냈다).
    #   → 지역을 붙여 검색하고, 잡힌 업체 주소에 우리 지역 토큰이 있는지까지 확인한다.
    #   전 업종·전 지역 공통 규칙(지명 하드코딩 0 — tenant.region에서 토큰을 뽑아 쓴다).
    region = " ".join((getattr(tenant, "region", "") or "").split())
    _rtoks = [w for w in re.split(r"[\s,/]+", region) if len(w) >= 2]

    def _addr_hits(h: dict) -> int:
        """주소에 우리 지역 토큰이 몇 개 맞는가. '부산'만 맞고 '기장'이 안 맞으면 남의 가게다
        (실측: 부산 토큰 하나로 남구 동명이 업체가 통과했다). 구·군·시 표기 흔들림은 흡수."""
        addr = (h.get("address") or "") + " " + (h.get("jibun") or "")
        n = 0
        for t in _rtoks:
            if t in addr:
                n += 1
                continue
            _short = t.rstrip("시군구")          # 표기 흔들림 흡수(부산광역시↔부산광역)
            # ★ 단, 2글자가 1글자로 줄면 쓰지 않는다 — '동구'→'동'은 '동대신동'에도 걸려
            #   다른 구의 동명이 업체를 우리 가게로 단정한다(검토 지적).
            if len(_short) >= 2 and _short in addr:
                n += 1
        return n

    hits, mine, near = [], None, None
    for q in ([f"{region} {name}", name] if region else [name]):
        hits = _pl.search(q, 5) or []
        cand = [h for h in hits if _pl._name_match(name, h.get("name", ""))]
        if not _rtoks:                                    # 지역 정보가 없으면 판정 보류(기존 동작)
            mine = cand[0] if cand else None
        else:
            scored = sorted(((_addr_hits(h), h) for h in cand), key=lambda x: -x[0])
            if scored and scored[0][0] == len(_rtoks):    # 지역 토큰 전부 일치 = 우리 가게
                mine = scored[0][1]
            elif scored and scored[0][0] >= 1:            # 일부만 일치 = 단정하지 않는다
                near = scored[0][1]
        if mine:
            break
    if mine:
        out["registered"] = True
    elif near is not None:
        out["registered"] = None                          # 판정 보류(미등록으로 몰지 않는다)
        out["ambiguous"] = {"name": near.get("name"), "address": near.get("address")}
    else:
        out["registered"] = False
        # 상호는 잡혔는데 지역이 전혀 다르면 '동명이 업체'다 — 미등록과 구분해서 알린다.
        _other = next((h for h in (hits or []) if _pl._name_match(name, h.get("name", ""))), None)
        if _other:
            out["name_clash"] = {"name": _other.get("name"), "address": _other.get("address")}
    if mine:
        out["listed"] = {k: mine.get(k) for k in ("name", "category", "address", "tel")}
        # 정보 정합 — 우리가 아는 값과 다르면 손님이 헷갈리고 신뢰 신호도 약해진다
        _tel = re.sub(r"[^0-9]", "", (getattr(tenant, "phone", "") or ""))
        _ltel = re.sub(r"[^0-9]", "", mine.get("tel") or "")
        out["tel_match"] = (not _tel) or (not _ltel) or (_tel[-8:] == _ltel[-8:])
        # has_tel은 판정하지 않는다(2026-08-01 사장님 지적) — 지역검색 API의 telephone은
        # 실제 등록 여부와 무관하게 빈 값으로 오는 폐기 필드다. '비어 있음' 오진의 원인이었다.
    # 지역+업종 노출 — canonical 지역 토큰으로(전 표면 단일 소스 재사용)
    try:
        from app import seo as _seo
        reg = _seo.canonical_region(getattr(tenant, "region", "") or "",
                                    getattr(tenant, "biz_type", "local") or "local",
                                    getattr(tenant, "industry", "") or "")
        ind = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
        kw = " ".join(x for x in (reg, ind) if x).strip()
        if kw:
            d = _pl.rank_detail(kw, name, 5)
            out["region_kw"] = kw
            out["region_rank"] = d.get("rank")            # 0=상위5 밖
            out["leader"] = d.get("leader")
            out["rival"] = d.get("rival")
    except Exception:
        pass
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
            # ★ 2026-08-06 회귀 복구 2건:
            #   ① VALUES(?,?,?,?,?,?)는 컬럼 순서에 기댄다. 진단용 컬럼 3개(suspect·fp_suspect·
            #      mine_legacy)를 추가하자 개수가 안 맞아 저장이 전부 실패했다(밤새 0건).
            #      컬럼을 명시한다 — 스키마가 늘어도 안 깨진다.
            #   ② INSERT OR REPLACE는 행을 통째로 갈아끼운다. 그러면 어제 보존한 mine_legacy가
            #      NULL로 덮인다. UPSERT로 바꿔 '이번에 잰 것만' 갱신하고 나머지는 보존한다.
            for col, ddl in (("suspect", "INTEGER DEFAULT 0"), ("fp_suspect", "INTEGER DEFAULT 0"),
                             ("mine_legacy", "INTEGER"), ("evidence", "TEXT"),
                             ("collect_note", "TEXT")):
                try:
                    c.execute(f"ALTER TABLE kw_blocks ADD COLUMN {col} {ddl}")
                except Exception:
                    pass
            import json as _js
            for r in (rows or [])[:40]:
                kw = (r.get("keyword") or "").strip()
                if not kw:
                    continue
                # 수집 실패는 지면 지도에 쓰지 않는다(게이트 원칙) — 사유만 남긴다
                if r.get("collect_failed"):
                    c.execute("UPDATE kw_blocks SET collect_note=? WHERE tenant_id=? AND keyword=?",
                              ("; ".join(r.get("reasons") or [])[:200], tenant_id, kw))
                    continue
                c.execute(
                    "INSERT INTO kw_blocks(tenant_id, keyword, blocks, blog_blocks, mine, "
                    "checked_at, evidence, collect_note) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(tenant_id, keyword) DO UPDATE SET "
                    "blocks=excluded.blocks, blog_blocks=excluded.blog_blocks, mine=excluded.mine, "
                    "checked_at=excluded.checked_at, evidence=excluded.evidence, collect_note=NULL",
                    (tenant_id, kw, "|".join(r.get("blocks") or [])[:400],
                     "|".join(r.get("blog_blocks") or [])[:200],
                     1 if r.get("my_visible") else 0, _d.utcnow().isoformat(),
                     _js.dumps(r.get("visible_evidence") or {}, ensure_ascii=False)[:600], None))
                saved += 1
    except Exception as e:
        _log.exception("[blogreach] 블록 저장 실패")
        # 조용한 실패 금지 — 사유를 응답에 담는다. 밤새 '저장 실패'만 찍히고 원인을 못 봤다.
        return {"ok": False, "error": f"저장 실패: {repr(e)[:160]}"}
    return {"ok": True, "saved": saved}


_MIN_SCAN_VOLUME = 100     # 월검색량 하한 — 이 아래는 지면을 훑어도 유입이 없다(정찰 기준과 동일)


def scout_plan(tenant_id: str, limit: int = 30, ttl_days: int = 7) -> list:
    """🗺 지면 정찰 계획(2026-08-01 사장님 승인 ①) — 이 가게에서 '지면을 확인해야 할 키워드' 목록.

    왜: 지면 데이터가 몇 개뿐이라 키워드 선정이 사실상 장님이다. 매일 밤 20~30개씩 훑어
    '어느 판에 블로그 자리가 있는가' 지도를 만든다. 지도가 있어야 _surface_first가 일한다.

    후보 공급원(전부 이미 있는 데이터 — 새 API 호출 0):
      ① 순위 추적 중인 키워드(검색어 정찰이 발견한 실유입어 포함)
      ② 발행한 글들의 타깃 키워드
      ③ 아직 안 쓴 글감 큐의 키워드(미리 정찰해두면 쓸 때 바로 판단)
    이미 최근에 훑은 키워드는 제외(ttl_days) — 오래된 것부터 다시 본다.
    업종·지명 하드코딩 0."""
    t = db.get_tenant(tenant_id)
    if not t:
        return []
    cands: list = []
    from app import seo as _seo

    def _worthy(k: str) -> bool:
        """훑을 값어치가 있는 '검색어'인가 — 글 문장 조각·과잉 어절을 배제(언어 규칙만).
        실측 2026-08-01: 타깃 키워드 데이터에 '신차라면 이것도 같이 보세요', '부산 기장에서
        전국으로' 같은 제목 조각이 섞여 있었다. 사람은 이렇게 검색하지 않는다."""
        if not (2 <= len(k) <= 22) or len(k.split()) > 4:
            return False
        if re.search(r"[,·—\-–…?!]|\.\.\.", k):
            return False
        if re.search(r"(요|다|죠|까|네|고|만)$", k) and len(k.split()) >= 2:
            return False        # 서술형 종결 = 문장 조각
        if re.search(r"(에서|으로|까지|부터|처럼|보다|에게|한테|이나|라면|하면)$", k):
            return False        # 조사·연결어미로 끝남 = 검색어가 아니라 문장 일부
        return bool(re.search(r"[가-힣]{2,}", k))

    def _add(k):
        # 행정구역 풀네임은 구어형으로(실측: '부산광역시 썬팅' 검색량 0) — 전 표면 단일 소스 재사용
        k = " ".join((_seo._kw_shorten(k or "")).split())
        if _worthy(k) and k not in cands:
            cands.append(k)

    try:
        for k in db.tracked_keywords(tenant_id, 40):
            _add(k)
    except Exception:
        pass
    try:
        for pub in db.list_blog_publishes(tenant_id, limit=20):
            _add(pub.get("target_kw") or "")
            try:
                p = db.get_piece(pub.get("piece_id") or "")
                for k in ((p.payload or {}).get("target_keywords") or [])[:4] if p else []:
                    _add(k)
            except Exception:
                pass
    except Exception:
        pass
    try:
        for q in db.writing_queue_rows(tenant_id, limit=20) or []:
            _add(q.get("keyword") or "")
    except Exception:
        pass
    if not cands:
        return []
    # 최근에 훑은 것은 건너뛰고, 오래된 것·미측정부터 — 매일 조금씩 지도를 넓힌다
    from datetime import datetime, timedelta
    fresh = datetime.utcnow() - timedelta(days=max(1, ttl_days))
    seen: dict = {}
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            for r in c.execute("SELECT keyword, checked_at FROM kw_blocks WHERE tenant_id=?",
                               (tenant_id,)).fetchall():
                seen[r["keyword"]] = (r["checked_at"] or "")[:19]
    except Exception:
        pass

    def _age(k):
        ts = seen.get(k)
        if not ts:
            return (0, "")                     # 미측정이 최우선
        try:
            return (0, ts) if datetime.fromisoformat(ts) < fresh else (1, ts)
        except Exception:
            return (0, "")
    todo = [k for k in cands if _age(k)[0] == 0]
    todo.sort(key=lambda k: _age(k)[1])        # 오래된 것부터
    todo = todo[:max(1, limit) * 2]            # 검색량 관문 통과분을 채우기 위해 여유를 둔다
    # 🔎 검색량 관문 — 아무도 안 치는 말의 지면을 훑는 건 시간 낭비다(queryscout와 동일 기준).
    #   키가 없거나 조회 실패면 통과(막지 않는다 — 기존 동작).
    try:
        from app.services import searchad as _sa
        if _sa.configured() and todo:
            vols = _sa.volume_map(todo) or {}
            _kept = [k for k in todo if int(vols.get(k.replace(" ", "")) or 0) >= _MIN_SCAN_VOLUME]
            if _kept:
                todo = _kept
    except Exception:
        pass
    return todo[:max(1, limit)]


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
        # 이 판에 블로그 지면이 있는가 — 블록 귀속이 빗나가도 '내 블로그가 실제로 보였다'면
        # 지면은 있는 것이다(2026-08-01 실측: 인기글 링크가 리다이렉트라 귀속이 0으로 잡혔다).
        d["blog_surface"] = bool(d["blog_blocks"]) or bool(d.get("mine"))
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
