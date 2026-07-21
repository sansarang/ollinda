"""
업종(셀러형·중고차) 상위 글 패턴 학습 레이어.

원칙(절대 준수):
- 크롤링 금지. 네이버 공식 블로그검색 API의 '제목·요약(snippet)·발행일'만 사용.
  본문 수집·저장 절대 금지.
- 기존 생성 프롬프트는 한 글자도 변경하지 않는다. 패턴은 별도 [업종 상위 패턴 참고]
  블록으로 뒤에 추가 주입만 하며, 문장 복제·모방 지시가 아님을 블록에 명시(유사문서 방지).
- 키워드당 1회 캐시(7일) — 같은 키워드 재분석 안 함(비용).
"""
from __future__ import annotations

import logging
import re

from app import db
from app.services.blogrank import _search_blog, configured

_log = logging.getLogger("shopcast.kwpattern")

# 도입 유형 신호(요약 앞부분에서 읽히는 톤) — 정규식 신호(본문 아님, snippet만)
_INTRO_ANXIETY = re.compile(r"(사고차|침수|허위|속지|당하지|불안|걱정|낚이|바가지|후회)")
_INTRO_SPEC = re.compile(r"(\d+만\s?km|\d{2,3}마력|\d{4}년식|\bcc\b|배기량|연비|옵션)")
_INTRO_STORY = re.compile(r"(어느 날|오늘|지난|처음|사실|고민하다|다녀왔|후기)")
# 신뢰 요소 어휘(초반 제시 빈도)
_TRUST_LEX = ["성능점검", "보험이력", "실매물", "허위매물", "무사고", "정비이력",
              "주행거리", "사고이력", "직접", "당일출고", "탁송", "보증"]
# 제목 유형
_TITLE_REVIEW = re.compile(r"(후기|타보|리뷰|실구매|내돈내산)")
_TITLE_LISTING = re.compile(r"(매물|판매|출고|시세|가격)")
_TITLE_GUIDE = re.compile(r"(고르는|고를 때|보는 법|체크|주의|방법|팁|총정리|가이드)")


def _pct(n: int, total: int) -> int:
    return int(round(100 * n / total)) if total else 0


def analyze(keyword: str, use_cache: bool = True) -> dict | None:
    """타깃 키워드 상위 글(제목·요약·발행일) 구조 신호 추출.
    반환: {n, title_types{review,listing,guide}, intro{anxiety,spec,story}, trust_top[(어휘,빈도)],
           recency{days_median, fresh_ratio}, sample_titles[:5]} — 방향 참고용.
    조회 불가/무키/결과 0이면 None(임의 패턴 만들지 않음)."""
    keyword = (keyword or "").strip()
    if not keyword:
        return None
    if use_cache:
        cached = db.get_kw_pattern(keyword)
        if cached is not None:
            cached["_cached"] = True
            return cached
    if not configured():
        return None
    items = _search_blog(keyword, display=30)
    if not items:
        return None
    n = len(items)
    tt = {"review": 0, "listing": 0, "guide": 0}
    intro = {"anxiety": 0, "spec": 0, "story": 0}
    trust = {w: 0 for w in _TRUST_LEX}
    days = []
    from datetime import datetime
    for it in items:
        title = it.get("title", "")
        desc = it.get("description", "")
        head = (title + " " + desc[:80])          # 요약 앞부분만(초반 제시 판단)
        if _TITLE_REVIEW.search(title):
            tt["review"] += 1
        if _TITLE_LISTING.search(title):
            tt["listing"] += 1
        if _TITLE_GUIDE.search(title):
            tt["guide"] += 1
        if _INTRO_ANXIETY.search(head):
            intro["anxiety"] += 1
        if _INTRO_SPEC.search(head):
            intro["spec"] += 1
        if _INTRO_STORY.search(head):
            intro["story"] += 1
        for w in _TRUST_LEX:
            if w in head:
                trust[w] += 1
        pd = it.get("postdate", "")
        if re.fullmatch(r"\d{8}", pd or ""):
            try:
                days.append((datetime.utcnow() - datetime.strptime(pd, "%Y%m%d")).days)
            except Exception:
                pass
    days.sort()
    days_median = days[len(days) // 2] if days else None
    fresh_ratio = _pct(sum(1 for d in days if d <= 90), len(days)) if days else 0
    trust_top = sorted(((w, c) for w, c in trust.items() if c), key=lambda x: -x[1])[:5]
    out = {
        "n": n,
        "title_types": {k: _pct(v, n) for k, v in tt.items()},
        "intro": {k: _pct(v, n) for k, v in intro.items()},
        "trust_top": trust_top,
        "recency": {"days_median": days_median, "fresh_ratio": fresh_ratio},
        "sample_titles": [it.get("title", "") for it in items[:5]],
        "_cached": False,
    }
    db.save_kw_pattern(keyword, out)
    return out


def directive_block(pat: dict) -> str:
    """패턴 → 기존 프롬프트 뒤에 붙일 [업종 상위 패턴 참고] 블록.
    방향 참고용이며 문장 복제·모방 금지를 명시(유사문서 방지)."""
    if not pat or not pat.get("n"):
        return ""
    tt = pat.get("title_types", {})
    intro = pat.get("intro", {})
    dom_title = max(tt, key=tt.get) if tt else ""
    _tmap = {"review": "후기형(직접 타보고 확인)", "listing": "매물형(차량 스펙·가격 제시)",
             "guide": "가이드형(고르는 법·체크포인트)"}
    dom_intro = max(intro, key=intro.get) if intro else ""
    _imap = {"anxiety": "불안 공감형(사고차·허위매물 걱정에 먼저 공감)",
             "spec": "스펙 제시형(연식·주행거리·가격을 초반에)",
             "story": "경험 스토리형"}
    trust = [w for w, _ in (pat.get("trust_top") or [])][:4]
    rec = pat.get("recency", {})
    lines = ["[업종 상위 패턴 참고 — 방향 참고용, 문장·표현 복제나 모방 금지(유사문서 위험). 아래는 '경향'일 뿐 그대로 베끼지 말 것)]"]
    if dom_title:
        lines.append(f"- 이 키워드 상위 글은 제목이 '{_tmap.get(dom_title, dom_title)}' 형태가 우세({tt.get(dom_title)}%).")
    if dom_intro:
        lines.append(f"- 도입부는 '{_imap.get(dom_intro, dom_intro)}' 경향({intro.get(dom_intro)}%) — 초반에 이 방향으로 공감·정보 제시.")
    if trust:
        lines.append(f"- 신뢰 요소({', '.join(trust)})를 글 초반에 제시하는 글이 많다 — 네가 가진 '실제' 근거만 그 자리에 배치(없는 건 만들지 말 것).")
    if rec.get("fresh_ratio", 0) >= 50:
        lines.append(f"- 최신성 요구 높음(최근 90일 글 {rec['fresh_ratio']}%) — 오늘 기준 신선한 정보·날짜 감각 반영.")
    lines.append("- 위는 검색 경향 참고일 뿐이다. 제목·문장을 상위 글과 다르게(너의 실제 매물·경험으로) 써라. 같은 표현 반복 금지.")
    return "\n".join(lines)
