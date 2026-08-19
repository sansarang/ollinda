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
    m = re.search(r"se-main-container[^>]*>", html_txt)   # 클래스명 뒤(태그 닫힘)부터 — 클래스 문자열이
    seg = html_txt[m.end():] if m else html_txt          # 본문 텍스트로 새어 'container 안녕하세요'가 잡히던 실측 결함
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
            "heads": heads, "kw_hits": kw_hits,
            "phrases": _query_phrases(seg, text)}     # 검색 의도 구절(원문 아님 — 짧은 구절만)


_Q_STOP = ("합니다", "습니다", "했어요", "드립니다", "때문", "그리고", "하지만", "저희", "우리",
           "오늘", "이번", "여기", "정도", "경우", "생각")
# 에디터·템플릿 마크업 잔해(업종 무관 기술 토큰) — 본문 용어로 오인되면 안 됨
_MARKOUP_HINT = "se-|css|class|style|div|span|container|module|component|editor|blog"
_MARKUP_TOKENS = {"container", "div", "span", "class", "style", "css", "se", "module",
                  "component", "editor", "img", "src", "href", "http", "https", "www"}
_JOSA_TAIL = re.compile(r"(으로써|으로서|에서는|에서도|에게서|이라는|라는|으로|에서|에게|한테|"
                        r"까지|부터|보다|처럼|만큼|이나|나마|이며|이고|이라|은|는|이|가|을|를|의|"
                        r"에|와|과|도|만|랑|께|여|아|야)$")


def _norm_token(w: str) -> str:
    """조사·어미 제거 정규화 — 블로그마다 다른 활용형을 같은 용어로 모으기 위함(언어 규칙만)."""
    if len(w) <= 2 or not re.search(r"[가-힣]", w):
        return w
    cut = _JOSA_TAIL.sub("", w)
    return cut if len(cut) >= 2 else w
_Q_MARK = re.compile(r"(어떻게|어디|얼마|무엇|뭐가|왜|언제|추천|비교|후기|방법|가격|비용|차이|"
                     r"기준|확인|주의|고르|선택|필요|가능)")


def _query_phrases(seg: str, text: str) -> list:
    """상위 글에서 '검색 의도 구절'만 추출(2026-08-01 사장님 승인) — 원문 저장 금지 원칙 유지:
    문장을 담지 않고, 소제목·질문·의도어 포함 짧은 구절(2~6어절)만 남긴다.
    ★ 반드시 '평문'에서만 추출한다(실측 2026-08-01: HTML 원본 정규식은 네이버 에디터 구조와 안 맞아
      매칭 0 + '<div class=se-' 같은 태그 조각이 구절로 잡혔다).
    업종·지명 하드코딩 0 — 의도 표지어(어떻게·가격·비교…)라는 언어 신호만 사용."""
    import html as _html
    out: list = []
    # 블록 태그를 줄바꿈으로 바꿔 '줄 구조'를 살린 뒤 태그 제거(소제목·짧은 줄이 살아남는다)
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", seg or "", flags=re.S)
    t = re.sub(r"(?i)</(p|div|h[1-6]|li|td|tr|blockquote)>|<br[^>]*>", "\n", t)
    t = _html.unescape(re.sub(r"<[^>]+>", " ", t))

    def _push(s: str):
        s = " ".join((s or "").split())
        s = re.sub(r"^[#\-•\d.\)\s]+", "", s).strip(" ?!.·|…\"'“”")
        if not (3 <= len(s) <= 28):
            return
        w = s.split()
        if not (1 <= len(w) <= 6):                     # 단일 용어도 허용(도메인 명사)
            return
        if re.search(r"(습니다|합니다|했어요|해요|입니다|이에요|드려요|드립니다)$", s):
            return                                     # 완결 서술문 = 원문 조각 → 배제(구절만)
        if any(t0 in s for t0 in _Q_STOP):
            return
        if re.search(r"(http|www\.|blog\.naver|se-|css|class=)", s, re.I):
            return                                     # 마크업·링크 잔해 차단
        if not re.search(r"[가-힣]{2,}", s):
            return
        if s not in out:
            out.append(s)

    # ★ 문장이 아니라 '용어'를 뽑는다(2026-08-01 실측 교훈): 같은 문장이 여러 블로그에 똑같이
    #   나오는 일은 없다. 시장 공통 신호는 2~3어절 용어('성능점검기록부','가시광선 투과율')다.
    #   ★★ 조사·어미를 떼고 정규화한다 — '중고차를 구매'와 '중고차 구매'가 다른 용어로 갈려
    #      교차 집계가 안 되던 실측 결함(수확 2개) 보정.
    words = []
    for w in re.findall(r"[0-9A-Za-z가-힣]{2,}", t):
        if re.fullmatch(r"\d+", w) or w in _Q_STOP:
            continue
        if re.fullmatch(r"[A-Za-z]{2,}", w) and w.lower() in _MARKUP_TOKENS:
            continue                                   # 마크업 잔해(container·div·class…) 제외
        w = _norm_token(w)
        if len(w) >= 2:
            words.append(w)
    # 단일 도메인 용어('성능점검기록부','유리막코팅','주행거리') — 동사·부사 활용형은 제외.
    # 한국어 명사는 아래 어미로 끝나지 않는다는 언어 규칙만 사용(업종 어휘 하드코딩 0).
    # 활용형 꼬리 — 동사·형용사·부사가 '시장 공통 용어'로 잡히면 빈자리 판정이 무의미해진다.
    #   실측(2026-08-19 '부산 썬팅'): 상위 25개 중 15개가 '합리적인·쾌적한·깔끔하게·실제로·
    #   그대로·효과적'이었다. 이걸 '이미 덮인 항목'이라고 주면 차별 항목을 못 가른다.
    #   ★ 감수한 손실: '고속도로·외국인'처럼 이 꼬리로 끝나는 진짜 명사도 함께 빠진다.
    #     노이즈 15/25보다 명사 몇 개를 잃는 쪽이 낫다고 판단(업종 어휘 하드코딩 0은 유지).
    _VERB_TAIL = re.compile(r"(요|다|고|서|지|나|까|네|죠|히|며|면|든|랑|께|든지|니|든가"
                            r"|한|인|운|된|게|적|로|라|하)$")
    for w in words:
        if len(w) >= 3 and re.search(r"[가-힣]{3,}", w) and not _VERB_TAIL.search(w):
            _push(w)
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i:i + n])
            if not re.search(r"[가-힣]{2,}", gram):
                continue
            if 4 <= len(gram) <= 22:
                _push(gram)
    for line in t.split("\n")[:800]:                   # 질문형 줄은 그대로도 가치 있음(검색 의도)
        s = line.strip()
        if s.endswith("?") and 4 <= len(s) <= 28:
            _push(s)
    return out[:120]                                   # 교차 집계에서 걸러지므로 넉넉히


def _blog_vitals(blog_id: str) -> "str | None":
    """블로그 계정 활동성 판정(③ 상대 전력, 2026-08-01 사장님 승인) — 공개 RSS만 사용(크롤 아님).
    weak=방치(마지막 발행 120일+ 또는 월 1편 미만) / strong=활발(주 2편+·최근 3주 내) / mid=중간.
    판정 불가(비공개 RSS 등)는 None(중립)."""
    if not blog_id:
        return None
    try:
        import requests
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        r = requests.get(f"https://rss.blog.naver.com/{blog_id}.xml", headers=_UA, timeout=8)
        if r.status_code != 200:
            return None
        dates = []
        for m in re.finditer(r"<pubDate>([^<]+)</pubDate>", r.text):
            try:
                dates.append(parsedate_to_datetime(m.group(1)))
            except Exception:
                pass
        if not dates:
            return "weak"                              # 글이 안 잡히는 RSS = 사실상 방치
        now = datetime.now(timezone.utc)
        dates = [d if d.tzinfo else d.replace(tzinfo=timezone.utc) for d in dates]
        last_days = (now - max(dates)).days
        per_week = sum(1 for d in dates if (now - d).days <= 56) / 8.0
        if last_days > 120 or per_week < 0.25:
            return "weak"
        if per_week >= 2 and last_days <= 21:
            return "strong"
        return "mid"
    except Exception:
        return None


def anatomize(keyword: str, top_n: int = 5) -> "dict | None":
    """상위 top_n 글 해부(동기·저속) — 집계만 저장. 캐시 있으면 즉시 반환."""
    kw = " ".join((keyword or "").split())
    hit = cached(kw)
    if hit is not None:
        return hit
    from app.services import blogrank
    items = blogrank._search_blog(kw, 10)              # 계정 판정은 상위 10개 기준(③)
    if not items:
        return None
    # 지배 각도 감지(작전 지시서용) — 제목의 '유형'만 집계(원문·제목 비저장 원칙 유지)
    _ANGLES = [("후기", re.compile(r"후기|시공기|다녀왔|사용기|해봤|받았")),
               ("가격", re.compile(r"가격|비용|얼마|만원|견적")),
               ("방법", re.compile(r"방법|하는 ?법|셀프|고르는|체크|주의")),
               ("추천", re.compile(r"추천|잘하는 ?곳|업체|비교|순위|BEST|톱|TOP", re.I))]
    angles = {a: 0 for a, _ in _ANGLES}
    angles["정보"] = 0
    for it in items[:10]:
        t_ = it.get("title") or ""
        hit_ = next((a for a, rx in _ANGLES if rx.search(t_)), None)
        angles[hit_ or "정보"] += 1
    # ③ 상위 10개 글의 '블로그 계정' 수준 — 약체가 섞여 있으면 비집고 들어갈 틈(상위 블로거 루틴)
    vitals = []
    seen_bid = set()
    for it in items[:10]:
        _m = re.search(r"blog\.naver\.com/([A-Za-z0-9_-]+)", it.get("bloggerlink") or it.get("link") or "")
        bid = _m.group(1) if _m else ""
        if not bid or bid in seen_bid:
            continue
        seen_bid.add(bid)
        v = _blog_vitals(bid)
        if v:
            vitals.append(v)
        time.sleep(1.0)                                # 저속 원칙 유지
    items = items[:top_n]
    rows, ages = [], []
    phrase_blogs: dict = {}                            # 구절 → 그 구절을 쓴 블로그 수(교차 등장 = 시장 공통 수요)
    from datetime import datetime
    for it in items:
        met = _post_metrics(_fetch_post_html(it.get("link", "")), kw)
        if met:
            rows.append(met)
            _bid = re.search(r"blog\.naver\.com/([A-Za-z0-9_-]+)", it.get("link") or "")
            _who = _bid.group(1) if _bid else (it.get("bloggername") or "?")
            for ph in met.pop("phrases", []) or []:     # 지표에서 분리(집계만 남기고 구절은 폐기)
                phrase_blogs.setdefault(ph, set()).add(_who)
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
           "age_days_median": (sorted(ages)[len(ages) // 2] if ages else None),
           # ③ 상대 전력(계정 수준) — weak=방치/저활동, strong=활발 운영(승산 스코어가 사용)
           "blogs_checked": len(vitals),
           "weak_blogs": sum(1 for v in vitals if v == "weak"),
           "strong_blogs": sum(1 for v in vitals if v == "strong"),
           "angles": angles,                           # 지배 각도 분포(작전 지시서 — 각도 전환용)
           # 🔎 시장 공통 검색 의도 구절 — 2개 이상 블로그가 함께 쓴 것만(한 명만 쓰면 개인 취향).
           #    검색어 정찰의 후보 공급원(원문 아님, 짧은 구절 + 등장 블로그 수만 보관).
           "common_phrases": [{"p": p, "blogs": len(b)}
                              for p, b in sorted(phrase_blogs.items(),
                                                 key=lambda x: (-len(x[1]), -len(x[0])))
                              if len(b) >= max(2, min(3, len(rows) // 2))][:25]}
    try:
        with db._conn() as c:
            _ensure(c)
            c.execute("INSERT OR REPLACE INTO kw_anatomy(keyword, captured_at, data) VALUES(?,?,?)",
                      (kw, __import__("datetime").datetime.utcnow().isoformat(), json.dumps(out, ensure_ascii=False)))
        _log.info("[anatomy] %r → %s", kw, out)
    except Exception:
        pass
    return out


def topic_gap(keyword: str, an: "dict | None" = None, materials: str = "") -> dict:
    """🕳 **상위 10개가 다룬 항목 vs 안 다룬 항목** — 내용 단위 빈자리(2026-08-19 사장님 지시).

    사장님: "이길자리는 1위부터 10위까지 글을 크롤링해서 정보성을 차별화 두는거는 어때?"

    왜 각도만으로는 부족한가:
      battle_plan은 상위글을 4각도(가격·방법·추천·후기)로만 갈랐다. 그래서 지시가
      "가격형으로 써라"까지밖에 못 갔고, **같은 각도 안에서 또 비슷한 글**이 나왔다.
      상위 10개가 '가격의 무엇'을 다뤘는지는 안 봤기 때문이다.

    무엇을 하는가:
      이미 뽑아둔 `common_phrases`(2개 이상 블로그가 함께 쓴 구절 = 시장이 이미 덮은 항목)와
      우리 재료(사진 분석·사장님 답변)를 대조해 **덮인 것 / 우리만 말할 수 있는 것**을 가른다.
      LLM 1콜(Solar). 크롤링·본문 저장은 하지 않는다 — anatomize가 이미 지표만 남긴다.

    ★ 업종 중립 — 항목 목록을 코드에 갖지 않는다. 재료는 그 판의 구절과 이 세트의 입력뿐이다.
    ★ 없는 것을 지어내지 않는다. 우리 재료에 근거가 없으면 '차별 항목 없음'으로 둔다(정직 게이트).
    """
    an = an or cached(keyword) or {}
    covered = [p["p"] for p in (an.get("common_phrases") or [])][:20]
    if not covered:
        return {"covered": [], "gaps": [], "why": "상위글 해부 데이터 없음"}
    if not (materials or "").strip():
        # 재료가 없으면 '무엇이 덮였는지'만 알려준다(빈자리 제안은 근거가 있어야 한다)
        return {"covered": covered, "gaps": [], "why": "이 세트 재료 없음 — 덮인 항목만 보고"}
    try:
        from app import llm
        v = llm.call_task(
            "analysis",
            "너는 검색 결과 분석가다. 아래 [이미 덮인 항목]은 그 검색어 상위 글들이 공통으로 다룬 것이다.\n"
            "[우리 재료]에서 **상위 글들이 다루지 않은** 항목만 골라라.\n"
            "규칙: ① 우리 재료에 실제로 있는 것만(없는 것을 지어내지 마라) "
            "② 덮인 항목과 같은 말·같은 뜻이면 제외 ③ 검색자가 궁금해할 만한 것만 "
            "④ 최대 3개, 각 12자 이내 명사구 ⑤ 없으면 '없음' 한 단어만.\n"
            "출력: 쉼표로 구분한 항목만(설명 금지).\n\n"
            f"[검색어] {keyword}\n"
            f"[이미 덮인 항목] {', '.join(covered)}\n"
            f"[우리 재료]\n{materials[:1800]}",
            max_tokens=120)
        raw = " ".join((v or "").split()).strip().strip("\"'")
        if not raw or raw.startswith("없음"):
            return {"covered": covered, "gaps": [], "why": "우리 재료에 차별 항목 없음"}
        gaps = [g.strip() for g in raw.replace("·", ",").split(",") if 1 < len(g.strip()) <= 16][:3]
        low = [c.replace(" ", "") for c in covered]
        gaps = [g for g in gaps if g.replace(" ", "") not in low]     # 덮인 것과 겹치면 버린다
        return {"covered": covered, "gaps": gaps,
                "why": ("상위글이 안 다룬 항목" if gaps else "제안이 덮인 항목과 겹쳐 폐기")}
    except Exception:
        _log.exception("[anatomy] 항목 빈자리 판정 실패 kw=%r", keyword)
        return {"covered": covered, "gaps": [], "why": "판정 실패"}


def battle_plan(keyword: str, tenant_id: str = "", materials: str = "") -> "tuple[str, dict]":
    """🗺 판 유형별 작전 지시서(2026-08-01 사장님 승인) — 4신호(공급·추세·상대전력·해부)를
    글쓰기 작전으로 변환해 프롬프트에 주입. 반환 (프롬프트 블록, 감사용 meta).
    신호가 없으면 빈 문자열(기존 글쓰기 그대로) — 파이프라인을 절대 막지 않는다.

    materials: 이 세트의 재료(사진 분석·사장님 답변). 있으면 **내용 단위 빈자리**까지 판정한다
               (2026-08-19 사장님 지시 — 상위 10개가 안 다룬 항목으로 치고 들어간다).
    """
    _materials_hint = materials or ""
    kw = " ".join((keyword or "").split())
    if not kw:
        return "", {}
    meta: dict = {"kw": kw}
    try:
        from app.services import blogrank as _br
        from app.services import datalab as _dl
        an = cached(kw)
        if an is None:
            ensure_async(kw)
        docs = _br.doc_count(kw)
        growth = (_dl.growth([kw]) or {}).get(kw)
        meta.update({"docs": docs, "trend": growth,
                     "weak": (an or {}).get("weak_blogs"), "strong": (an or {}).get("strong_blogs"),
                     "age_median": (an or {}).get("age_days_median"),
                     "angles": (an or {}).get("angles")})
        # ── 판 판정 ──
        wk = (an or {}).get("weak_blogs") or 0
        stg = (an or {}).get("strong_blogs") or 0
        checked = (an or {}).get("blogs_checked") or 0
        age = (an or {}).get("age_median") if an else None
        contested = bool(checked and (stg >= 6 or (wk <= 1 and (age is not None and age <= 60))))
        open_field = bool((checked and wk >= 3) or (age is not None and age >= 180))
        rising = growth is not None and growth >= 0.10
        falling = growth is not None and growth <= -0.10
        meta["plan"] = ("치열한 판" if contested else "열린 판" if open_field else "보통 판") + \
                       (" · 상승 추세" if rising else " · 하락 추세" if falling else "")
        lines = [f"[판 분석 — '{kw}' 전장 실측]"]
        sig = []
        if docs and docs > 0:
            sig.append(f"발행 문서 {docs:,}개")
        if growth is not None:
            sig.append(f"최근 3개월 검색 추세 {growth:+.0%}")
        if checked:
            sig.append(f"상위권 블로그: 활발 {stg}·약체 {wk}")
        if age is not None:
            sig.append(f"상위 글 나이 중앙값 {age}일")
        if sig:
            lines.append(" · ".join(sig))
        # ── 작전 ──
        if contested:
            ang = (an or {}).get("angles") or {}
            dom = max(ang, key=ang.get) if ang else ""
            gap = min((k for k in ("가격", "방법", "추천", "후기") if k != dom),
                      key=lambda k: ang.get(k, 0)) if ang else ""
            lines.append(f"[작전 — 치열한 판: 정면 승부 금지, 각도를 틀어라] 상위권은 활발한 블로그 위주"
                         + (f"이고 지배 각도는 '{dom}형'({ang.get(dom, 0)}개)" if dom else "") + ". "
                         + (f"이 글은 '{gap}형' 각도로 진입하라 — 제목·소제목·구성을 그 검색 의도에 맞춰라. " if gap else "")
                         + "정보 우위가 필수: 상위 기준선보다 실측 수치·표·FAQ를 한 단계 더 갖춰라"
                         "(단, 입력 사실 안에서만 — 날조 금지·허사로 분량 불리기 금지).")
        elif open_field:
            lines.append("[작전 — 열린 판: 정공법·속전속결] 상위권이 낡았거나 약체다. 기준선을 확실히 넘기되 "
                         "과투자하지 마라(기준선 +10% 정도면 충분). 최신성이 무기다 — 올해 기준·최근 작업임이 "
                         "드러나는 표현을 도입부에 자연스럽게 써라.")
        else:
            lines.append("[작전 — 보통 판] 기준선 충족 + 이 가게만의 실측·경험 디테일로 차별화하라.")
        # 🧱 통합검색 지면(2026-08-01 실측): 지면이 없는 판이면 글 품질과 무관하게 노출이 막힌다.
        #   그 경우 '검색 상단'이 아니라 '이웃 피드·재방문·전환'을 노리는 글로 목적을 바꾼다.
        if tenant_id:
            try:
                from app.services import blogreach as _brc0
                _b0 = _brc0.blocks_for(tenant_id, kw) or {}
                if _b0.get("blog_surface") is False:
                    meta["blog_surface"] = False
                    lines.append("[지면 경고 — 통합검색에 블로그 자리가 없는 판] 이 키워드의 검색 첫 화면은 "
                                 "플레이스·클립·쇼핑이 차지한다. 검색 상단 노출은 기대하기 어려우니 "
                                 "'검색용 나열'이 아니라 **읽고 바로 문의하게 만드는 글**로 써라: 결론을 앞에, "
                                 "가격·소요시간·연락 방법을 명확히, 마지막에 방문·문의 안내를 분명히.")
                elif _b0.get("blog_surface") is True:
                    meta["blog_surface"] = True
                    lines.append("[지면 확인 — 블로그가 실리는 판] 이 키워드는 통합검색에 블로그 지면이 있다. "
                                 "검색 의도에 정확히 답하는 구조(질문형 소제목·요약·FAQ)를 확실히 갖춰라.")
            except Exception:
                pass
        if rising:
            lines.append("[톤 — 상승 수요] 요즘 찾는 사람이 늘어난 주제다 — 도입부에 시의성(최근 문의 증가·시즌 "
                         "맥락)을 자연스럽게 한 문장 녹여라(과장·날조 금지, 사실 프레임만).")
        elif falling:
            lines.append("[톤 — 에버그린] 유행 표현을 피하고 오래 읽힐 기본기형으로 써라(시점 표현 최소화).")
        # 🕳 내용 단위 빈자리(2026-08-19) — 각도만 틀면 같은 각도 안에서 또 비슷한 글이 나온다.
        #   상위 10개가 이미 덮은 항목을 알려주고, 우리 재료에만 있는 것을 앞세우게 한다.
        try:
            _tg = topic_gap(kw, an, materials=_materials_hint)
            meta["topic_gap"] = {"covered": (_tg.get("covered") or [])[:10],
                                 "gaps": _tg.get("gaps") or [], "why": _tg.get("why")}
            _cov = ", ".join((_tg.get("covered") or [])[:8])
            _gap = ", ".join(_tg.get("gaps") or [])
            if _gap:
                lines.append(f"[내용 빈자리 — 여기로 치고 들어가라] 상위 글들이 이미 덮은 것: {_cov}. "
                             f"**이 글에서 앞세울 것: {_gap}** — 소제목 하나를 여기에 쓰고 "
                             "우리 재료의 실측·사진으로만 뒷받침하라(없는 내용 지어내기 금지). "
                             "덮인 항목은 짧게 스치고 지나가라(같은 말을 길게 반복하면 유사문서다).")
            elif _cov:
                lines.append(f"[내용 — 이미 덮인 것] {_cov}. 이 항목들은 짧게만 다루고, "
                             "우리 사진·기록에서만 나오는 구체 장면으로 분량을 채워라.")
        except Exception:
            pass
        return "\n".join(lines) + "\n", meta
    except Exception:
        return "", meta


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
