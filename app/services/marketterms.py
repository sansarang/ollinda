"""판의 언어 — 상위 글이 공통으로 쓰는 말 중 **손님 관심사만** 추린다.

왜(2026-08-17 사장님 제안: "상위 글과의 유사도 + 구조를 판다"):
  실측 커버율 — 우리 글이 상위글 공통어를 얼마나 다루는가:
    '부산 동구 썬팅업체' 50% · '차량 썬팅' 16% · '썬팅업체' 0~8%
  '투과율'·'가시광선'·'재시공'·'시인성'이 한 번도 안 나온다. 시공 과정은 자세히 쓰면서
  **손님이 검색창에 치는 말**이 비어 있다. 모델을 바꿔도 이 값은 안 변했다 —
  프롬프트에 이 정보를 준 적이 없기 때문이다.

★ 그대로 쓰면 안 되는 것이 섞여 있다:
  · '레이노·솔라가드·후퍼옵틱' — **경쟁사 필름 브랜드.** 안 쓰는 브랜드를 언급하면 날조다.
  · '깔끔하게·고객님·제대로' — 판의 언어가 아니라 그냥 흔한 말. 넣으면 글이 상투적이 된다.

★ 브랜드를 사전으로 거르지 않는다(업종 중립 조항 — 업체명을 코드에 박을 수 없다).
  ✗ 폐기한 방법 — '교차 출현'(여러 검색어에 두루 나오면 주제어): **실측이 반증했다.**
      레이노 4 · 솔라가드 4 (브랜드)  vs  열차단 3 · 자외선 3 · **투과율 1** (주제어)
      유명 브랜드일수록 여러 판에 나온다. 이 신호는 브랜드를 못 막고 진짜 주제어를 죽였다.
  ✓ 쓰는 방법 — **재료 대조.** 그 가게가 쓰는 브랜드는 재료(사진 묘사·사장님 입력)에 있다.
      재료에 없는 제품명은 우리 것이 아니므로 언급 자체를 금지한다.
      판별을 코드가 하지 않고 '재료에 있느냐'로 가른다 — 어떤 업종에서도 성립한다.

★ 이건 '넣으라'는 지시가 아니라 '손님이 이걸 궁금해한다'는 정보다.
  재료에 없으면 비운다 — 커버율을 올리려고 없는 수치를 지어내면 그게 날조이고,
  억지로 반복하면 키워드 스터핑이다. 둘 다 우리 금지선이다.
"""
from __future__ import annotations

import json
import logging
import re

_log = logging.getLogger("shopcast.marketterms")

#: 판의 언어가 아닌 말 — 어느 업종에서나 흔히 쓰이는 부사·형용사·상투어
STOP = {"그대로", "이렇게", "아니라", "있도록", "전체적", "다양한", "확인할", "진행해",
        "이야기", "편하게", "깔끔하게", "고객님", "제대로", "하나로", "합리적인", "만족스러운",
        "안전하게", "완벽한", "필요한", "투명하게", "자연스럽게", "명확하게", "미세한",
        "바라보는데", "추천해", "그리고", "하지만", "때문에", "경우에", "생각합니다"}

#: 어미·활용형 신호 — 명사가 아니라 서술어 조각이면 주제어가 아니다
_TAIL = re.compile(r"(하게|하고|해서|합니다|했습니다|이라|으로|에서|까지|부터|보다|처럼|"
                   r"인데|는데|지만|어요|아요|네요|시죠|드립니다|겠습니다)$")

MIN_BLOGS = 2       # 한 검색어 안에서 이만큼의 블로그가 함께 써야 함(수집 단계 기준과 동일)
TOP_N = 10


def _rows() -> list:
    """저장된 상위글 해부 전체(크롤 안 함)."""
    from app import db
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_anatomy("
                      "keyword TEXT PRIMARY KEY, captured_at TEXT, data TEXT)")
            return [dict(r) for r in c.execute("SELECT keyword, data FROM kw_anatomy").fetchall()]
    except Exception:
        _log.exception("[marketterms] kw_anatomy 조회 실패")
        return []


def _phrases(data_json: str) -> list:
    try:
        d = json.loads(data_json or "{}")
        return [(p.get("p") or "", int(p.get("blogs") or 0))
                for p in (d.get("common_phrases") or [])]
    except Exception:
        return []


#: 행정구역 신호 — 지역명은 주제어가 아니다(업종 중립: 특정 지명을 박지 않고 형태로 판정).
#: 2026-08-17 실측 결함: '부산광역시'가 판의 언어로 뽑혀 그걸로 취재했더니
#: '부산광역시 자동차매매사업조합'(중고차 조합)이 썬팅 글 재료로 들어왔다.
#: 2026-08-17 추가 — 도로명 주소가 새어 '중앙대로274번길'이 판의 언어로 잡혔다.
#:   주소는 재료(고정정보 블록)에 있으니 주제어로 다룰 이유가 없다.
_REGION = re.compile(r"(특별시|광역시|특별자치시|특별자치도|[가-힣]{1,3}시$|[가-힣]{1,3}군$|"
                     r"[가-힣]{1,3}구$|[가-힣]{2,4}동$|[가-힣]{2,4}읍$|[가-힣]{2,4}면$|"
                     r"\d+번길|[가-힣]+대로|[가-힣]{2,}로$|[가-힣]+\d+로$|\d+가$)")


def _usable(term: str, region: str = "") -> bool:
    if not term or len(term) < 2 or len(term) > 12:
        return False
    if term in STOP or _TAIL.search(term):
        return False
    if _REGION.search(term):                     # 지역명 — 주제어가 아니다
        return False
    if region:
        # 그 가게의 지역명이 섞인 말도 뺀다('부산썬팅'). 행정 풀네임과 구어형이 다르므로
        # (부산광역시 ↔ 부산) canonical 축약 함수를 거친다 — 규칙이 두 곳에 살면 안 된다.
        try:
            from app import seo as _seo
            toks = set(region.split()) | set(_seo._kw_shorten(region).split())
        except Exception:
            toks = set(region.split())
        if any(len(t) >= 2 and t in term for t in toks):
            return False
    return bool(re.fullmatch(r"[가-힣A-Za-z0-9 ]+", term))


def cross_counts(region: str = "") -> dict:
    """용어 → 그 용어가 등장한 **검색어 수**. 브랜드·특수어를 가르는 근거."""
    out: dict = {}
    for r in _rows():
        seen = {t for t, b in _phrases(r.get("data")) if b >= MIN_BLOGS and _usable(t, region)}
        for t in seen:
            out[t] = out.get(t, 0) + 1
    return out


def topic_terms(keyword: str, limit: int = TOP_N, region: str = "") -> list:
    """그 검색어의 '판의 언어' — 업종 전반에 두루 쓰이는 주제어만.

    한 검색어에만 나오는 말(브랜드·특수 상품명 가능성)은 뺀다. 사전 없이 데이터로 가른다.
    """
    kw = " ".join((keyword or "").split())
    if not kw:
        return []
    row = next((r for r in _rows() if r.get("keyword") == kw), None)
    if not row:
        return []
    cross = cross_counts(region)   # 정렬 가중치로만 쓴다(판별 근거 아님 — 실측이 반증)
    picked = []
    for t, blogs in _phrases(row.get("data")):
        if blogs < MIN_BLOGS or not _usable(t, region):
            continue
        picked.append((t, blogs, cross.get(t, 0)))
    picked.sort(key=lambda x: (-x[2], -x[1]))     # 두루 쓰이는 것 우선
    return [t for t, _, _ in picked[:limit]]


def outsider_mentions(body: str, terms: list, material: str) -> list:
    """판의 언어 중 **재료에 없는데 글에 등장한 말** — 남의 브랜드 언급 위험.

    ★ 이것이 브랜드 방어선이다. 코드가 '무엇이 브랜드인가'를 판별하지 않는다 —
      판별은 불가능함이 실측으로 드러났다(레이노 4 vs 투과율 1).
      대신 '우리 재료에 있느냐'로 가른다. 그 가게가 쓰는 제품은 사진 묘사·입력에 있고,
      없는 제품명이 글에 나왔다면 그건 모델이 어디선가 끌어온 것이다.
      업종을 몰라도, 브랜드 목록이 없어도 성립한다.
    """
    return [t for t in (terms or [])
            if t and t in (body or "") and t not in (material or "")]


def coverage(body: str, terms: list) -> dict:
    """글이 그 말들을 얼마나 다뤘는가. **판정만 한다 — 채우라고 강제하지 않는다.**"""
    ts = [t for t in (terms or []) if t]
    if not ts:
        return {"n": 0, "hit": 0, "pct": None, "missing": [], "covered": []}
    hit = [t for t in ts if t in (body or "")]
    return {"n": len(ts), "hit": len(hit), "pct": round(100 * len(hit) / len(ts)),
            "covered": hit, "missing": [t for t in ts if t not in (body or "")]}


def directive(keyword: str, limit: int = TOP_N, region: str = "") -> str:
    """프롬프트 한 조각 — '손님이 궁금해하는 것'. 없으면 빈 문자열(빈칸 원칙)."""
    terms = topic_terms(keyword, limit, region)
    if not terms:
        return ""
    return ("[손님이 이 검색어를 칠 때 궁금해하는 것들]\n"
            + " · ".join(terms) + "\n"
            "→ 이 중 **위 재료로 실제 답할 수 있는 것만** 다뤄라. "
            "재료에 없는 수치·성능은 지어내지 말고 그 항목은 건너뛴다. "
            "억지로 단어를 반복하지 마라 — 다루라는 것이지 넣으라는 것이 아니다.\n"
            "→ ★ 이 목록에는 **다른 업체가 쓰는 제품·브랜드 이름이 섞여 있다.** "
            "위 재료에 없는 제품명·브랜드는 언급 자체를 하지 마라 — "
            "우리가 쓰지 않는 제품을 쓴 것처럼 보이면 그 글은 폐기다.\n")
