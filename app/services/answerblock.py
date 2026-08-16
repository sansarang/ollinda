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
MAX_ATTRS = 3            # 한 글이 노리는 속성 축 상한(겹침 0.8~4.2% — 다중 타깃은 환상)

#: '답변 덩어리'로 인정할 최소 문단 길이(공백 제외).
#: 실측 근거(2026-08-16, 우리 글 10편 문단 262개):
#:   중간값 70자 · 상위25% 89자 · **최장 164자** · 200자 넘는 문단 **0개**.
#:   즉 우리 글은 1~2문장짜리 조각의 나열이라 네이버가 뽑아갈 덩어리가 애초에 없었다.
#:   섹션만 줄이면 글이 그냥 짧아진다(실측: 소제목 8→5로 줄이자 비교 축 4→2로 얇아짐).
#:   180자 = 3~4문장 = '그 문단만 읽어도 답이 되는' 최소치. 현재 최장(164)보다 조금 위다.
MIN_THICK_CHARS = 180
MIN_THICK_PARAS = 2      # 본문 소제목 2~3개에 대응 — 답변 덩어리도 최소 2개

#: 노리지 않는 축 — 답할 재료가 구조적으로 없는 축은 아예 겨냥하지 않는다.
#: '가격'은 2026-08-16 사장님 지시로 제외한다. 실제 단가를 받은 적이 없어 쓸 수 없고,
#: 소제목만 걸고 금액을 못 써서 두 번 연속 '약속미이행'으로 걸렸다.
#: 답할 수 없는 축을 노리면 게이트가 날조를 압박하게 된다(정직 게이트).
EXCLUDED_INTENTS = ("가격",)


def plan(core: str = "", keywords=None) -> dict:
    """노리는 질의를 [핵심 1개 + 속성 축 2~3개]로 나눈다.

    왜 나누나 (2026-08-16 실측):
      검색어마다 판이 분리돼 있다 — 두 검색어 이상에 걸친 글 비율이
      중고차 0.8% · 썬팅 4.2% · 이어폰 15.8%. **한 글이 여러 핵심 키워드를 먹는 일은 없다.**
      대신 같은 판 안의 속성 질의(가격·시간·과정·비교)는 전용 문단으로 함께 딸 수 있다
      (실측 사례: 한 글이 '부산 썬팅' 9위와 '썬팅 가격' 10위를 서로 다른 문단으로 동시 확보).

    핵심은 `seo.resolve_target_keyword()`가 이미 정한 값을 그대로 받는다(canonical 단일 관문).
    속성 축은 후보 키워드에 실제로 그 축 표지가 있을 때만 잡는다 — 축을 지어내지 않는다.
    """
    kws = [k for k in (keywords or []) if (k or "").strip()]
    core = (core or "").strip() or (kws[0] if kws else "")
    attrs, seen = [], set()
    for name, (qre, _sre) in INTENTS.items():
        if name in seen or name in EXCLUDED_INTENTS:
            continue
        hit = next((k for k in kws if k != core and qre.search(k)), "")
        if hit:
            seen.add(name)
            attrs.append({"intent": name, "query": hit})
    return {"core": core, "attrs": attrs[:MAX_ATTRS]}


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


def thickness(body: str) -> dict:
    """문단 두께 — 뽑아갈 덩어리가 실제로 있는가.

    ★ 축(가격·시간·과정·비교) 판정만으로는 '그냥 짧아진 글'을 못 잡는다(2026-08-16 실측).
      섹션을 줄였더니 내용이 같이 빠졌는데 축 판정은 통과했다. 두께는 따로 재야 한다.
    반환 {"n_thick", "longest", "ok"}
    """
    lens = [len(re.sub(r"\s", "", p)) for p in paragraphs(body)]
    n_thick = sum(1 for x in lens if x >= MIN_THICK_CHARS)
    return {"n_thick": n_thick, "longest": (max(lens) if lens else 0),
            "ok": n_thick >= MIN_THICK_PARAS}


_TOK_STOP = {"추천", "후기", "곳", "업체", "전문점", "가게", "매장"}


def _tokens(q: str) -> list:
    """질의를 대조용 토큰으로. 언어 규칙만 — 업종어를 박지 않는다."""
    ws = [w for w in re.split(r"\s+", (q or "").strip()) if len(w) >= 2]
    return [w for w in ws if w not in _TOK_STOP]


def query_coverage(body: str, plan_d: dict) -> list:
    """★ 핵심 판정 — **노린 질의마다 그 질의에 답하는 문단이 실제로 있는가.**

    축(가격·시간·과정·비교) 신호를 세는 것은 대리 지표다. 진짜 질문은 이것이다:
    '부산 동구 썬팅업체 추천'을 노렸다면, **그 말이 들어 있고 충분히 두꺼운 문단**이
    글 안에 서 있는가? 없으면 네이버가 그 검색어로 뽑아갈 단위가 없다.

    반환 [{"query","role","hit_chars","covered"}]
      · covered = 질의 토큰이 과반 이상 들어 있고 MIN_THICK_CHARS를 넘는 문단이 하나라도 있음
    """
    paras = paragraphs(body)
    out = []
    items = []
    if (plan_d or {}).get("core"):
        items.append(("핵심", plan_d["core"]))
    for a in (plan_d or {}).get("attrs") or []:
        items.append(("속성", a.get("query") or ""))
    for role, q in items:
        toks = _tokens(q)
        need = max(1, (len(toks) + 1) // 2)          # 토큰 과반
        best = 0
        for p in paras:
            flat = re.sub(r"\s", "", p)
            if sum(1 for t in toks if t in p) >= need:
                best = max(best, len(flat))
        out.append({"query": q, "role": role, "hit_chars": best,
                    "covered": best >= MIN_THICK_CHARS})
    return out


def _wanted(intent_re, *texts) -> bool:
    """이 글이 그 의도를 노리고 있는가 — 타깃 키워드·제목·소제목에서 찾는다."""
    return any(intent_re.search(t or "") for t in texts)


def audit(body: str, keywords=None, title: str = "") -> dict:
    """질의별 답변 문단 점검.

    ★ 게이트는 '없는 값을 지어내라'고 요구하면 안 된다(정직 게이트, 2026-08-16 수정).
      `seo.target_keywords()`는 모든 가게에 "{업종} 가격"·"{업종} 추천"을 **항상** 붙인다.
      그래서 '노린 축'을 그대로 요구하면 가격을 공개하지 않는 가게는 **가격을 지어내야**
      통과하게 된다. 그건 게이트가 날조를 강요하는 것이다.
      → 판정을 셋으로 나눈다:

        scattered — 재료가 본문에 있는데 여러 문단에 흩어졌다      → **실패**(모으면 끝, 날조 불필요)
        missing   — 소제목으로 약속해놓고 덩어리가 없다             → **실패**(약속 위반)
        unfilled  — 노렸지만 그 축의 재료 자체가 없다               → **통과**(경고만. 빈칸이 날조보다 낫다)

    반환 {"intents", "missing", "scattered", "unfilled", "n_paras"}
    """
    body = body or ""
    heads = " ".join(re.findall(r"^#{2,3}\s*(.+)$", body, re.M))
    kw_txt = " ".join([k for k in (keywords or []) if k])
    paras = paragraphs(body)
    intents, missing, scattered, unfilled = {}, [], [], []
    for name, (qre, sre) in INTENTS.items():
        aimed = _wanted(qre, kw_txt, title)        # 노린 축(후보 키워드·제목)
        promised = _wanted(qre, heads)             # 소제목으로 약속한 축
        per = [len(sre.findall(p)) for p in paras]
        best = max(per) if per else 0
        total = sum(per)
        ok_ = best >= MIN_SIGNALS
        intents[name] = {"aimed": aimed, "promised": promised,
                         "best": best, "total": total, "ok": ok_}
        if name in EXCLUDED_INTENTS:
            continue                               # 노리지 않는 축은 요구도 하지 않는다
        if ok_ or not (aimed or promised):
            continue
        if total >= SCATTER_TOTAL:
            scattered.append(name)                 # 재료 있음 · 안 모임 → 고칠 수 있다
        elif promised:
            missing.append(name)                   # 약속했는데 덩어리 없음 → 약속 위반
        else:
            unfilled.append(name)                  # 재료 없음 → 지어내게 하지 않는다
    thick = thickness(body)
    return {"intents": intents, "missing": missing, "scattered": scattered,
            "unfilled": unfilled, "n_paras": len(paras), "thick": thick}


def ok(body: str, keywords=None, title: str = "") -> bool:
    r = audit(body, keywords, title)
    return not (r["missing"] or r["scattered"])


def detail(body: str, keywords=None, title: str = "") -> str:
    """게이트·로그용 한 줄 사유. 통과면 빈 문자열(unfilled는 사유가 아니라 참고)."""
    r = audit(body, keywords, title)
    parts = []
    if r["scattered"]:
        parts.append("흩어짐:" + ",".join(r["scattered"]))
    if r["missing"]:
        parts.append("약속미이행:" + ",".join(r["missing"]))
    if not r["thick"]["ok"]:
        parts.append(f"문단얇음:{MIN_THICK_CHARS}자이상 {r['thick']['n_thick']}개"
                     f"/최장{r['thick']['longest']}자")
    # 사유는 ' · '로 구분한다 — 공백으로 이으면 사유 하나가 여러 조각으로 잘려 읽힌다.
    return " · ".join(parts)


def note(body: str, keywords=None, title: str = "") -> str:
    """실패는 아니지만 남겨둘 관찰 — 노렸는데 재료가 없어 못 채운 축."""
    r = audit(body, keywords, title)
    return ("재료없음:" + ",".join(r["unfilled"])) if r["unfilled"] else ""


_AXIS_HOWTO = {
    "가격": "금액과 구간을 한 문단 안에 모아라(30만원대 / 50만원대 / 80만원대처럼 비교되게)",
    "시간": "소요 시간 수치를 한 문단에 모아라(항목별로 몇 분·몇 시간인지)",
    "과정": "순서를 한 문단 또는 연속된 목록으로 모아라(먼저 → 그다음 → 마지막)",
    "비교": "무엇과 무엇이 어떻게 다른지를 한 문단에서 마주 놓아라(반면·대신·차이)",
}


def prompt_rule(keywords=None, core: str = "") -> str:
    """생성 프롬프트에 넣을 규칙 문장(업종 무관 — 글 구조 규칙).

    ★ 프롬프트 지시는 확률이고 게이트가 보장이다. 둘 다 둔다(기존 제목·FAQ와 같은 패턴).
    ★ [핵심 1개 + 속성 축 2~3개] 구조를 명시한다 — 평평한 키워드 목록만 주면
      모델이 어느 것을 글 전체로 답하고 어느 것을 전용 문단으로 답할지 알 수 없다.
    """
    p = plan(core, keywords)
    lines = [
        "[질의별 답변 문단 — 필수]",
        "네이버는 글 전체가 아니라 **검색어에 맞는 문단 하나**를 뽑아 노출한다. "
        "실측: 같은 글이 검색어에 따라 다른 대목을 요약으로 받았다(3업종 84%).",
    ]
    if p["core"]:
        lines.append(f"· **핵심 질의 '{p['core']}'** — 이 글 전체가 답한다. 제목과 도입이 이 질의를 정면으로 받는다.")
    for a in p["attrs"]:
        lines.append(f"· **속성 질의 '{a['query']}'** — 이 질의만 답하는 **전용 문단 하나**를 두어라. "
                     f"{_AXIS_HOWTO.get(a['intent'], '관련 내용을 한 문단에 모아라')}.")
    lines += [
        # ★ 숫자로 못 박는다(2026-08-16). "두껍게"라고만 썼더니 섹션만 줄고 내용이 같이 빠졌다.
        #   실측: 우리 글 문단 262개 중 200자 넘는 것이 0개(중간값 70자·최장 164자)였다.
        f"· **답변 문단은 최소 {MIN_THICK_CHARS}자 이상**(공백 제외)으로, 그런 문단이 "
        f"**{MIN_THICK_PARAS}개 이상** 있어야 한다. 한두 문장 쓰고 줄바꿈하지 마라 — "
        "그 문단 하나가 그 질문의 답 전체가 되도록 근거·단계·수치를 이어서 써라.",
        # ★ 2026-08-16 실측: 두께만 요구했더니 **모든** 문단이 길어져 리듬이 죽었다
        #   (문장 길이 편차 54 → 45, 쉼표 문장 47% → 58%로 오히려 AI 쪽). 사람 글의 표식은
        #   두께가 아니라 들쭉날쭉함이다 — 두꺼운 문단은 '최소 2개'지 '전부'가 아니다.
        f"· 단, **모든 문단이 길 필요는 없다.** 두꺼운 문단은 {MIN_THICK_PARAS}개면 충분하고 "
        "나머지는 짧아도 된다. 한 줄로 끊고 가는 대목을 일부러 섞어라 — "
        "문단 길이가 다 비슷하면 그게 기계가 쓴 표식이다.",
        "· 각 문단은 그 문단만 읽어도 뜻이 통해야 한다 — 앞 문단을 가리키는 말('위에서 말한 그것') 금지.",
        "· 그 질의어가 해당 문단 **안에** 들어 있어야 한다(제목에만 있으면 안 된다).",
        # ★ 2026-08-16 사장님 지적("AI가 쓴다는 느낌이 안 들어야 한다") — 이 줄이 원인이었다.
        #   전에는 "소제목이 그 질의를 그대로 말하게 하라"고 시켰고, 그 결과
        #   '부산 동구 썬팅업체 시간 – 항목별로 얼마나 걸릴까' 같은 기계 조합 소제목이 나왔다.
        #   질의어는 **문단 안에** 있으면 되고, 소제목은 사람이 쓰는 말이어야 한다.
        "· 소제목에 검색어를 그대로 박지 마라. 소제목은 **사람이 말하듯** 쓰고"
        "(예: '얼마나 걸리는지부터 말씀드릴게요'), 검색어는 그 아래 문단 안에서 자연스럽게 쓴다.",
        "· 도입부에만 힘주지 마라 — 도입부가 항상 뽑히는 것이 아니다.",
        "※ **없는 값을 지어내서 채우지 마라.** 모르는 값은 그 문단을 통째로 빼고, "
        "아는 축으로만 써라. 빈칸이 날조보다 낫다.",
    ]
    return "\n".join(lines) + "\n"
