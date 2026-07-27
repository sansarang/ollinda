"""
🔬 상위 글 해부 — 노출된 글의 '구조 지표'만 추출(원문은 즉시 폐기, 사장님 승인 2026-07-28).

원칙: ① 원문 저장·학습 금지(저작권·유사문서 차단) — 숫자만 남긴다 ② 소량·저속(키워드당 상위 5개,
요청 간 1초+) ③ 실패는 조용히(생성 파이프라인을 절대 막지 않음 — cached() 우선, 크롤은 백그라운드).

지표: 글자수·사진 수·동영상 유무·표 유무·소제목 수·키워드 등장·발행 나이 → 키워드별 집계 캐시(3일).
쓰임: 생성 프롬프트 기준선("상위권 평균 N자·사진 M장"), 교훈 루프 진단, 승산 스코어.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time

from app import db

_log = logging.getLogger("shopcast.anatomy")
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
TTL_HOURS = 72
_FETCHING: set = set()          # 키워드별 중복 백그라운드 크롤 방지


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS kw_anatomy("
              "keyword TEXT PRIMARY KEY, captured_at TEXT, data TEXT)")


def cached(keyword: str) -> "dict | None":
    """캐시된 해부 결과(3일 TTL) — 크롤 안 함(생성 경로 안전)."""
    kw = " ".join((keyword or "").split())
    if not kw:
        return None
    try:
        from datetime import datetime, timedelta
        with db._conn() as c:
            _ensure(c)
            r = c.execute("SELECT * FROM kw_anatomy WHERE keyword=?", (kw,)).fetchone()
        if not r:
            return None
        if datetime.utcnow() - datetime.fromisoformat((r["captured_at"] or "")[:19]) > timedelta(hours=TTL_HOURS):
            return None
        return json.loads(r["data"] or "null")
    except Exception:
        return None


def ensure_async(keyword: str) -> None:
    """캐시 없으면 백그라운드로 해부 시작(생성은 기다리지 않음) — 다음 생성부터 기준선 사용."""
    kw = " ".join((keyword or "").split())
    if not kw or cached(kw) is not None or kw in _FETCHING:
        return
    _FETCHING.add(kw)

    def _run():
        try:
            anatomize(kw)
        finally:
            _FETCHING.discard(kw)
    threading.Thread(target=_run, daemon=True).start()


def _fetch_post_html(url: str) -> str:
    import requests
    m = re.search(r"blog\.naver\.com/([A-Za-z0-9_-]+)/(\d+)", (url or "").split("?")[0])
    if not m:
        return ""
    u = f"https://blog.naver.com/PostView.naver?blogId={m.group(1)}&logNo={m.group(2)}&redirect=Dlog"
    try:
        r = requests.get(u, headers=_UA, timeout=8)
        return r.text[:800_000] if r.status_code == 200 else ""
    except Exception:
        return ""


def _post_metrics(html_txt: str, kw: str) -> "dict | None":
    """본문 HTML → 구조 지표(근사치). 원문 텍스트는 반환하지 않는다(즉시 폐기)."""
    if not html_txt:
        return None
    m = re.search(r"se-main-container", html_txt)
    seg = html_txt[m.start():] if m else html_txt
    _e = re.search(r"area_comment|u_cbox|CommentBox|naverBlog_footer|post_btn", seg)
    if _e:
        seg = seg[:_e.start()]                        # 본문 밖(댓글·버튼·푸터) UI 제외 — 지표 부풀림 방지
    imgs = (len(re.findall(r'class="se-module se-module-image', seg))
            or len(re.findall(r"se-image-resource", seg))
            or len(re.findall(r"<img", seg)))
    video = bool(re.search(r"se-video|se-oembed|__se_module_data[^>]*video", seg))
    table = bool(re.search(r"se-table|<table", seg))
    heads = len(re.findall(r"se-section-quotation|<h[23]", seg))
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", seg, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    chars = len(re.sub(r"\s", "", text))
    kw_hits = text.count(kw) + text.count(kw.replace(" ", ""))
    if chars < 200:                                   # 파싱 실패로 보이면 지표 무효
        return None
    return {"chars": chars, "imgs": imgs, "video": video, "table": table,
            "heads": heads, "kw_hits": kw_hits}


def anatomize(keyword: str, top_n: int = 5) -> "dict | None":
    """상위 top_n 글 해부(동기·저속) — 집계만 저장. 캐시 있으면 즉시 반환."""
    kw = " ".join((keyword or "").split())
    hit = cached(kw)
    if hit is not None:
        return hit
    from app.services import blogrank
    items = blogrank._search_blog(kw, max(top_n, 5))[:top_n]
    if not items:
        return None
    rows, ages = [], []
    from datetime import datetime
    for it in items:
        met = _post_metrics(_fetch_post_html(it.get("link", "")), kw)
        if met:
            rows.append(met)
        pd = (it.get("postdate") or "").strip()
        try:
            ages.append((datetime.utcnow() - datetime.strptime(pd, "%Y%m%d")).days)
        except Exception:
            pass
        time.sleep(1.2)                               # 저속 원칙(사람 브라우징 수준)
    if not rows:
        return None

    def _avg(k):
        return round(sum(r[k] for r in rows) / len(rows))
    out = {"keyword": kw, "n": len(rows),
           "avg_chars": _avg("chars"), "avg_imgs": _avg("imgs"), "avg_heads": _avg("heads"),
           "video_pct": round(100 * sum(1 for r in rows if r["video"]) / len(rows)),
           "table_pct": round(100 * sum(1 for r in rows if r["table"]) / len(rows)),
           "avg_kw_hits": _avg("kw_hits"),
           "age_days_median": (sorted(ages)[len(ages) // 2] if ages else None)}
    try:
        with db._conn() as c:
            _ensure(c)
            c.execute("INSERT OR REPLACE INTO kw_anatomy(keyword, captured_at, data) VALUES(?,?,?)",
                      (kw, __import__("datetime").datetime.utcnow().isoformat(), json.dumps(out, ensure_ascii=False)))
        _log.info("[anatomy] %r → %s", kw, out)
    except Exception:
        pass
    return out


def baseline_line(keyword: str) -> str:
    """생성 프롬프트 주입용 한 줄 — 캐시만 사용(크롤 0초). 없으면 백그라운드 예열 후 빈 문자열."""
    an = cached(keyword)
    if an is None:
        ensure_async(keyword)
        return ""
    return (f"[상위 글 실측 기준선] '{keyword}' 상위 {an['n']}개 평균: {an['avg_chars']}자·"
            f"사진 {an['avg_imgs']}장·소제목 {an['avg_heads']}개·표 있는 글 {an['table_pct']}%·"
            f"동영상 있는 글 {an['video_pct']}%. 이 기준을 '정보량'으로 넘어서라 — "
            "허사로 분량만 늘리는 것 금지(입력 사실이 부족하면 기준 미달이어도 정직하게 끝내라).\n")
