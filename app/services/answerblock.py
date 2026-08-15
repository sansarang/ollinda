"""질의별 독립 답변 문단 판정 — 네이버는 글이 아니라 '문단'을 노출시킨다.

실측 근거 (2026-08-16, 남의 상위글 339개 / 3업종 12개 검색어):
  같은 글이 검색어에 따라 **다른 대목**을 요약문으로 받았다(썬팅 83% · 중고차 100% · 이어폰 83%).
  결정적 사례 — 한 글이
      '부산 썬팅'  9위 → 도입부 문단
      '썬팅 가격' 10위 → "중저가 30만원대 / 중고가 50만원대 / 프리미엄 80만원대"
  네이버는 '가격'을 물으면 **가격 문단을 찾아 뽑아온다.** 도입부를 자르는 게 아니다.
  → 노리는 질의마다 그 질의에 정면으로 답하는 **독립 문단**이 글 안에 서 있어야 한다.
    흩어놓으면 네이버가 뽑아갈 덩어리가 없다.

업종 중립 (헌법):
  업종명·지명·상품명을 쓰지 않는다. **언어 규칙만** 쓴다 — 의도 표지어(가격·시간·과정·비교)와
  구체 신호(숫자+단위, 순서 표지, 대조 표지)는 어느 업종에서나 같은 한국어 규칙이다.

판정 대상은 '무엇을 썼나'가 아니라 '**한 문단 안에 모여 있나**'이다.
같은 정보라도 다섯 문단에 흩어지면 뽑히지 않는다.
"""
from __future__ import annotations

import re

#: 의도별 (질의 표지, 구체 신호). 질의 표지는 타깃 키워드·제목·소제목에서 찾고,
#: 구체 신호는 본문 '한 문단 안'에서 센다.
INTENTS: dict = {
    "가격": (
        re.compile(r"가격|비용|얼마|요금|견적|시세|단가"),
        re.compile(r"\d[\d,]*\s*(?:원|만원|천원|만)"),
    ),
    "시간": (
        re.compile(r"시간|기간|얼마나\s*걸|소요|당일|며칠|몇\s*일"),
        re.compile(r"\d+\s*(?:분|시간|일|주|개월|년)"),
    ),
    "과정": (
        re.compile(r"과정|방법|절차|순서|어떻게|하는\s*법|준비물|체크"),
        re.compile(r"먼저|그다음|다음으로|마지막으로|이어서|[①②③④⑤]|(?:^|\s)\d\.\s"),
    ),
    "비교": (
        re.compile(r"비교|차이|추천|고르|선택|어떤\s*게|장단점"),
        re.compile(r"반면|대신|보다|차이|장점|단점|각각|둘\s*다"),
    ),
}

MIN_SIGNALS = 2          # 한 문단이 '답변 덩어리'로 인정받는 최소 구체 신호 수
SCATTER_TOTAL = 3        # 이 이상 신호가 있는데 한 문단에 모이지 않으면 '흩어짐'


def paragraphs(body: str) -> list:
    """본문 → 문단 목록. 소제목·표·사진 마커는 문단으로 세지 않는다.

    ★ 표는 그 자체로 덩어리라 별도 취급한다 — 여기서 세는 것은 '산문 문단'이다.
    """
    txt = re.sub(r"\[사진\d+\]", " ", body or "")
    out = []
    for blk in re.split(r"\n\s*\n+", txt):
        # ★ 소제목은 '줄' 단위로 떼어낸다. 블록 통째로 버리면 소제목 바로 아래 붙은 본문이
        #   같이 사라진다 — 실제로 그래서 '가격을 한 문단에 모은 글'이 0문단으로 잡혔다
        #   (2026-08-16 극단값 검증에서 발견. 규율 4: 계측기부터 검증한다).
        lines = [ln for ln in blk.splitlines()
                 if not ln.lstrip().startswith("#")          # 소제목 줄
                 and not ln.lstrip().startswith("|")]        # 표 줄 — 별도 덩어리
        s = "\n".join(lines).strip()
        if s:
            out.append(s)
    return out


def _wanted(intent_re, *texts) -> bool:
    """이 글이 그 의도를 노리고 있는가 — 타깃 키워드·제목·소제목에서 찾는다."""
    return any(intent_re.search(t or "") for t in texts)


def audit(body: str, keywords=None, title: str = "") -> dict:
    """질의별 답변 문단 점검.

    반환 {"intents": {의도: {"wanted","best","total","ok"}}, "missing": [...], "scattered": [...]}
      · missing   — 노리는 의도인데 답변 덩어리가 없다(뽑아갈 문단이 없음)
      · scattered — 재료는 충분한데 여러 문단에 흩어져 있다(모으면 뽑힌다)
    """
    body = body or ""
    heads = " ".join(re.findall(r"^#{2,3}\s*(.+)$", body, re.M))
    kw_txt = " ".join([k for k in (keywords or []) if k])
    paras = paragraphs(body)
    intents, missing, scattered = {}, [], []
    for name, (qre, sre) in INTENTS.items():
        wanted = _wanted(qre, kw_txt, title, heads)
        per = [len(sre.findall(p)) for p in paras]
        best = max(per) if per else 0
        total = sum(per)
        ok = best >= MIN_SIGNALS
        intents[name] = {"wanted": wanted, "best": best, "total": total, "ok": ok}
        if not wanted:
            continue
        if ok:
            continue
        if total >= SCATTER_TOTAL:
            scattered.append(name)          # 재료는 있는데 안 모였다 — 고치기 쉬운 쪽
        else:
            missing.append(name)
    return {"intents": intents, "missing": missing, "scattered": scattered,
            "n_paras": len(paras)}


def ok(body: str, keywords=None, title: str = "") -> bool:
    r = audit(body, keywords, title)
    return not (r["missing"] or r["scattered"])


def detail(body: str, keywords=None, title: str = "") -> str:
    """게이트·로그용 한 줄 사유. 통과면 빈 문자열."""
    r = audit(body, keywords, title)
    parts = []
    if r["scattered"]:
        parts.append("흩어짐:" + ",".join(r["scattered"]))
    if r["missing"]:
        parts.append("없음:" + ",".join(r["missing"]))
    return " ".join(parts)


def prompt_rule(keywords=None) -> str:
    """생성 프롬프트에 넣을 규칙 문장(업종 무관 — 글 구조 규칙).

    ★ 프롬프트 지시는 확률이고 게이트가 보장이다. 둘 다 둔다(기존 제목·FAQ와 같은 패턴).
    """
    kws = [k for k in (keywords or []) if k][:3]
    aim = f"(노리는 검색어: {', '.join(kws)})" if kws else ""
    return (
        "[질의별 답변 문단 — 필수]\n"
        f"네이버는 글 전체가 아니라 **검색어에 맞는 문단 하나**를 뽑아 노출한다{aim}. "
        "따라서 노리는 질의마다 그 질의에 정면으로 답하는 문단이 **한 덩어리로** 서 있어야 한다.\n"
        "· 가격을 노리면 → 금액과 구간을 **한 문단 안에** 모아라(여러 문단에 흩뿌리지 마라).\n"
        "· 과정을 노리면 → 순서를 한 문단(또는 연속된 목록)에 모아라.\n"
        "· 시간을 노리면 → 소요 시간 수치를 한 문단에 모아라.\n"
        "· 각 문단은 '이 질문에 대한 답'처럼 그 문단만 읽어도 뜻이 통해야 한다.\n"
        "· 도입부에만 힘주지 마라 — 도입부가 항상 뽑히는 것이 아니다.\n"
        "※ 없는 값을 지어내서 채우지 마라. 모르는 값은 빈칸으로 두고 아는 것만 모아라.\n"
    )
