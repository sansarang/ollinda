"""웹 취재 — 판의 언어 중 **재료에 없는 것**을 공신력 있는 출처에서만 가져온다.

왜(2026-08-17 사장님 지적: "그냥 검색 기능을 넣어도 다 나온다"):
  실측 커버율에서 '투과율'·'재시공'·'가시광선'이 우리 글에 한 번도 없었다.
  사장님께 물어볼 일이 아니다 — 제품 스펙과 법령은 **검색하면 나온다.**
  실제로 네이버 API로 조회하니 국가법령정보센터 도로교통법 시행령 제28조
  (자동차 창유리 가시광선 투과율의 기준)가 그대로 나왔다. 손님이 가장 많이 묻는 것이고
  지어낼 필요가 없는 확정 사실이다.

헌법이 허용하는 범위 안이다:
  "검색은 취재다. 금지는 인칭 위조뿐이다."
  허용 — 확인된 사실·지식을 **3인칭 사실 서술**로 쓰는 것
  금지 — 그것을 1인칭 경험("저희가 해보니")으로 바꾸는 것

★ 출처를 가리는 것이 이 모듈의 존재 이유다.
  같은 조회에서 이런 것도 같이 나왔다:
    "gv80 썬팅 재시공 이유? 레인보우 최상위 등급 VS200으로! … 비교견적 받아보세요"
  **경쟁 업체 블로그·홍보글이다.** 이걸 재료로 쓰면 남의 글을 베끼는 것이고
  금지선(내용 복제 변주)에 정면으로 걸린다. 법령·백과·기관만 통과시킨다.

★ 업종 중립 — 업체명·업종어를 코드에 박지 않는다. 출처의 **형식**으로만 판정한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request

_log = logging.getLogger("shopcast.research")

#: 통과시키는 출처 — 공적 기관·사전. 도메인 형태로만 판정한다(업체명 없음).
_TRUST_HOST = re.compile(
    r"(\.go\.kr|\.or\.kr|\.re\.kr|\.ac\.kr|law\.go\.kr|terms\.naver\.com|"
    r"ko\.wikipedia\.org|\.gov|standard\.go\.kr)", re.I)

#: 막는 출처 — 블로그·카페·상업 플랫폼. 남의 경험담·홍보글이 재료로 들어오면 안 된다.
_BLOCK_HOST = re.compile(
    r"(blog\.naver|blog\.me|cafe\.naver|tistory|brunch\.co|post\.naver|"
    r"instagram|youtube|facebook|smartstore|coupang|11st|gmarket)", re.I)

#: 홍보 문구 신호 — 도메인이 깨끗해도 본문이 광고면 버린다.
_AD = re.compile(r"(비교견적|견적\s*받아|문의\s*주세요|상담\s*신청|이벤트|할인|최저가|"
                 r"시공\s*사례\s*소개|후기\s*확인)")

MAX_ITEMS = 3          # 주제어당 채택 상한 — 재료가 넘치면 글이 백과사전이 된다
MIN_CHARS = 40         # 너무 짧은 조각은 사실로 못 쓴다


def _api(kind: str, query: str, n: int = 5) -> list:
    cid = os.environ.get("NAVER_CLIENT_ID", "")
    cs = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not (cid and cs):
        raise RuntimeError("NAVER 검색 키 미설정")
    url = (f"https://openapi.naver.com/v1/search/{kind}.json?"
           + urllib.parse.urlencode({"query": query, "display": n}))
    req = urllib.request.Request(url, headers={"X-Naver-Client-Id": cid,
                                               "X-Naver-Client-Secret": cs})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r).get("items", []) or []


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def trusted(link: str, text: str) -> bool:
    """이 조각을 사실 재료로 써도 되는가. **막는 쪽이 먼저다**(의심스러우면 버린다)."""
    if _BLOCK_HOST.search(link or ""):
        return False
    if _AD.search(text or ""):
        return False
    return bool(_TRUST_HOST.search(link or ""))


def facts_for(term: str, context: str = "", limit: int = MAX_ITEMS) -> list:
    """주제어 하나에 대한 확인된 사실 조각. 출처를 함께 돌려준다(출처 없는 사실은 안 쓴다)."""
    q = " ".join(x for x in (context, term) if x).strip()
    out, seen = [], set()
    for kind in ("encyc", "webkr"):
        try:
            items = _api(kind, q, 5)
        except Exception as e:
            _log.warning("[research] %s 조회 실패 q=%s: %s", kind, q, repr(e)[:90])
            continue
        for it in items:
            link = it.get("link") or ""
            desc = _clean(it.get("description"))
            title = _clean(it.get("title"))
            if len(desc) < MIN_CHARS or desc in seen:
                continue
            if not trusted(link, desc + " " + title):
                continue
            seen.add(desc)
            out.append({"term": term, "title": title[:80], "text": desc[:300],
                        "source": link, "kind": kind})
            if len(out) >= limit:
                return out
    return out


def gather(terms: list, context: str = "", material: str = "", per_term: int = 2) -> dict:
    """재료에 없는 주제어만 취재한다. 이미 재료에 있으면 굳이 밖에서 찾지 않는다."""
    picked, skipped, seen = [], [], set()
    for t in [x for x in (terms or []) if x][:6]:      # 상한 — 조회는 네트워크다
        if t in (material or ""):
            skipped.append(t)
            continue
        for f in facts_for(t, context, per_term):
            # ★ 같은 백과 항목이 여러 주제어에 걸린다(실측: '투과율'·'재시공'·'자외선'이
            #   전부 같은 틴팅 항목을 물어왔다). 중복을 그대로 넣으면 재료가 한 말로 채워지고
            #   모델이 그 말만 반복한다 — 출처 기준으로 한 번만 쓴다.
            key = f["source"]
            if key in seen:
                continue
            seen.add(key)
            picked.append(f)
    return {"facts": picked, "already_had": skipped,
            "terms_tried": [t for t in (terms or [])[:6] if t not in (material or "")]}


def as_material(res: dict) -> str:
    """프롬프트에 붙일 재료 블록. **인칭 위조 금지를 같은 자리에 못 박는다.**

    이 블록이 1인칭으로 새면 그 순간 정직 게이트가 무너진다 —
    지시는 재료 옆에 있어야 지켜진다(멀리 두면 모델이 잊는다).
    """
    facts = (res or {}).get("facts") or []
    if not facts:
        return ""
    lines = []
    for f in facts:
        lines.append(f"· ({f['term']}) {f['text']}\n  출처: {f['source']}")
    return ("[확인된 사실 — 웹에서 취재한 공개 정보]\n"
            + "\n".join(lines) + "\n"
            "→ 이건 **우리 경험이 아니다.** 3인칭 사실로만 써라"
            "('법으로 정해져 있습니다', '일반적으로 ~라고 합니다').\n"
            "→ '저희가 해보니', '우리 손님이' 같은 1인칭으로 바꾸면 그 글은 폐기다.\n"
            "→ 이 중 이번 글의 소재와 관계있는 것만 골라 쓴다. 전부 넣지 마라.\n")
