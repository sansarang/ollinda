"""
📷 사진 묘사 단일 파서(2026-08-03 — 캡션 10회 재발 종결).

사고: gen_source의 `[사진N]` 줄을 캡션은 '첫 매치'로 읽었다. 그런데 vision 응답에는
묘사 대신 제목·라벨·마크다운 잔해가 섞여 오고("사진 분석 (썬팅 업종 관점)", "피사체/제품", "**"),
분석 배치가 여러 번 이어붙어 같은 번호가 10번까지 나온다.
첫 줄이 헤더인 번호는 그 헤더가 그대로 캡션이 됐다 — 20장 중 8장.

★ 같은 재료를 읽는 소비자가 둘 이상이면 파서를 하나로 만든다.
  영상 자막에는 이미 방어가 있었는데(video._lines_for_photos) 캡션에는 없었다.
  한쪽만 고쳐서 같은 결함이 10회 재발했다. 이 모듈이 유일한 파서다.

원칙(침묵 폴백 금지): 쓸 만한 묘사가 없으면 **빈 문자열**을 준다.
템플릿·업종명·키워드로 채우지 않는다 — 채우는 순간 사장님은 결함을 못 본다.
"""
from __future__ import annotations

import re

# 묘사가 아닌 줄 — vision이 제목·섹션명으로 뱉는 형태(언어 규칙, 업종어 하드코딩 0).
#   '마케팅'·'사진 분석'은 우리 프롬프트 어휘이기도 하다: 사진 내용이 아니라 분석 행위를 가리킨다.
META = re.compile(r"(사진 ?분석|분석 ?결과|분석입니다|마케팅|관점|촬영 ?팁|추천 ?활용|"
                  r"다음과 ?같|요약하면|아래와 ?같)")
# 라벨·머리표 — '피사체/제품', '* 피사체:' 처럼 값이 아니라 항목 이름만 온 줄
LABEL = re.compile(r"^\s*[*\-•#>]*\s*[가-힣A-Za-z/·]{2,14}\s*[:：]?\s*$")
_MD = re.compile(r"^[\s*_~`#>-]+$")               # '**', '---' 같은 마크다운 잔해


def is_description(line: str) -> bool:
    """이 줄이 '사진에 무엇이 보이는가'를 말하는가 — 아니면 버린다."""
    s = " ".join((line or "").split())
    if len(s) < 8 or _MD.match(s) or LABEL.match(s):
        return False
    if META.search(s):
        return False
    return bool(re.search(r"[가-힣]{2,}", s))


def clean(line: str) -> str:
    """묘사 줄에서 서식 잔해만 걷어낸다(내용은 건드리지 않는다)."""
    s = re.sub(r"^\s*[*\-•#>]+\s*", "", line or "")
    s = re.sub(r"^\s*[가-힣A-Za-z/·]{2,12}\s*[:：]\s*", "", s)   # '피사체/차종:' 류 머리표
    return " ".join(s.split()).strip(" .,·—-")


def best_line(gen_source: str, n: int) -> str:
    """사진 n번의 '가장 묘사다운' 줄. 없으면 빈 문자열(채우지 않는다).

    ★ 첫 매치를 쓰지 않는다 — 배치가 이어붙어 첫 줄이 헤더인 경우가 실측으로 확인됐다.
      후보 전체에서 묘사인 것만 남기고, 그중 가장 정보량이 많은(긴) 것을 고른다.
    """
    cands = [m.group(1) for m in
             re.finditer(rf"\[사진{n}\]\s*([^\n]+)", gen_source or "")]
    good = [clean(c) for c in cands if is_description(c)]
    good = [g for g in good if len(g) >= 8]
    return max(good, key=len) if good else ""


def alternates(gen_source: str, n: int) -> list:
    """사진 n번의 다른 묘사 후보들(긴 순). 중복 캡션을 '다른 실제 묘사'로 가르는 데 쓴다 —
    키워드를 덧붙여 구분하는 것은 침묵 폴백이다(조항)."""
    cands = [clean(m.group(1)) for m in re.finditer(rf"\[사진{n}\]\s*([^\n]+)", gen_source or "")
             if is_description(m.group(1))]
    seen, out = set(), []
    for c in sorted(cands, key=len, reverse=True):
        k = re.sub(r"\s", "", c)[:30]
        if k and k not in seen:
            seen.add(k)
            out.append(c)
    return out


def desc_map(gen_source: str, count: int) -> dict:
    """{번호: 최선 묘사} — 없는 번호는 키가 없다(빈 값으로 채우지 않는다)."""
    out = {}
    for i in range(1, max(0, count) + 1):
        b = best_line(gen_source, i)
        if b:
            out[i] = b
    return out


# ── 캡션 규격(2026-08-03 사장님 지시) ────────────────────────────
#   원리 하나: **아는 건 실값으로 말하고, 모르는 건 말하지 않는다.**
#   "ST1로 추정"은 둘 다 위반이었다 — 아는 것(세트 차종)을 안 쓰고, 모르는 것(추측)을 썼다.
CAPTION_MAX = 60
BATCH = 8                    # 한 콜에 쓰는 캡션 수 — 출력 잘림을 구조로 막는다

# 불확실 표현 — vision의 망설임이 손님 눈에 날것으로 나가면 안 된다(언어 규칙, 업종어 0)
_GUESS_PAREN = re.compile(r"\(([^)]*?(추정|보이는|보이며|또는|인 ?듯|가능성|계열)[^)]*)\)")
_GUESS_TAIL = re.compile(r"[가-힣A-Za-z0-9·\s]{1,14}(으)?로 ?추정(되[는던]|됨)?")
_GUESS_OR = re.compile(r"\s*(또는|혹은)\s*[^,.]{1,20}")
_GUESS_WORD = re.compile(r"(추정|로 보이는|인 ?듯|가능성|것으로 보임)")
# 촬영 환경·소품 — 손님이 사는 것과 무관하다(시계 시각 사고와 같은 계열)
_SCENE = re.compile(r"(배경|바닥|조명|반사|콘크리트|쇼룸|매장 ?내부|창밖|도로|건물|"
                    r"손목시계|스마트워치|시계 ?착용|착용한 손|픽셀화|모자이크)")
_LIST_NO = re.compile(r"^\s*\d{1,2}\s*[).]\s*")
_PROP_PHRASE = re.compile(r"(손목시계|스마트워치|시계)\s*(\([^)]*\))?[^,]{0,16}?"
                          r"(착용한|찬|착용)\s*(손이|손|사람이|사람)?")


def to_caption(desc: str, anchors=(), shop: str = "") -> str:
    """묘사 → 캡션. 1문장·핵심만·추측 0.

    anchors: 세트 실값(차종·등급명 등, seo.input_anchors 유래). 실값이 있으면 추측 명칭 대신 쓴다.
    반환이 빈 문자열이면 캡션을 만들지 못한 것이다 — 채우지 않는다(침묵 폴백 금지).
    """
    s = _LIST_NO.sub("", " ".join((desc or "").split()))
    if not s:
        return ""
    had_guess_subject = bool(_GUESS_PAREN.search(s) or _GUESS_TAIL.search(s))
    s = _GUESS_PAREN.sub(" ", s)                 # (현대 ST1로 추정) 통째 제거
    s = _GUESS_TAIL.sub(" ", s)                  # '…로 추정' 제거
    s = _GUESS_OR.sub(" ", s)                    # '또는 왁스' 같은 대안 나열 제거
    # 촬영 소품 상투구 — vision이 거의 모든 사진에 붙이는 말이라 절 제거로는 안 걸린다.
    #   '손목시계(카키색)를 착용한 손이' → '손이'. 소품 어휘일 뿐 업종 어휘가 아니다.
    # 절 맨 앞이면 '손이'로 바꾸고(주어 유지), 중간이면 통째로 지운다(치환 잔재 방지)
    def _prop(m):
        return "손이" if m.start() == 0 or s[max(0, m.start() - 2):m.start()].strip() in ("", ",") else ""
    s = _PROP_PHRASE.sub(_prop, s)
    s = re.sub(r"손\s*\(\s*손이\s*\)", "손", s)
    s = re.sub(r"(손에|손으로|손이)\s+[가-힣]{1,6}색?\s*(손이)", r"\1", s)
    s = re.sub(r"손이\s*(손이|사람이|손으로)", "손이", s)
    s = re.sub(r"\s{2,}", " ", s)
    # 절 단위로 배경·소품 제거(첫 절은 피사체라 보존)
    parts = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
    kept = [p for p in parts if not _SCENE.search(p)] or parts[:1]
    s = ", ".join(kept)
    s = re.split(r"(?<=[.!?])\s+", s)[0]         # 1문장
    s = _GUESS_WORD.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+(의|을|를|로|으로|에|이|가)(\s|$)", r"\1\2", s)   # 괄호 제거로 뜬 조사만
    s = re.sub(r"\(\s*\)", " ", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,·—-.")
    if not s or len(s) < 6:
        return ""
    # 아는 건 실값으로 — vision이 피사체를 추측했다면 그 자리를 세트 실값이 대신한다
    a = next((x for x in (anchors or []) if x and x not in s), "")
    if a and had_guess_subject:
        # 추측으로 부르던 자리에 실값을 앞세운다. 일반 명칭을 지우려면 업종 어휘 목록이
        #   필요한데(차량·밴·세단…) 그건 업종 중립 위반이다 — 지우지 않고 실값을 앞에 둔다.
        s = f"{a} {s}".strip()
    if len(s) > CAPTION_MAX:                     # 절 경계에서 자른다(어중간한 절단 금지)
        cut = s[:CAPTION_MAX]
        j = max(cut.rfind(","), cut.rfind(" "))
        s = (cut[:j] if j > 12 else cut).strip(" ,·—-")
    return _finish(s)


# 홀로 서지 못하는 꼬리 — 조사·연결어미·관형형으로 끝나면 그 어절까지 되감는다.
#   (영상 자막에서 쓰던 규칙과 같은 언어 규칙 — 캡션에도 같은 기준을 적용한다)
_TAIL_BAD = re.compile(r"(와|과|의|에|을|를|이|가|은|는|도|로|으로|랑|및|고|며|"
                       r"면서|하며|으며|에서|까지|부터|처럼|보다|"
                       r"[가-힣]{2,}(색|형|식|용|급|압)|관련|포함|기반|전용)$")


def _finish(s: str) -> str:
    """캡션이 그 자리에서 끝나도 말이 되게. 못 만들면 빈 문자열."""
    t = (s or "").strip(" ,·—-.")
    for _ in range(4):
        if not t or " " not in t:
            break
        if not _TAIL_BAD.search(t.rsplit(" ", 1)[-1].rstrip(")")):
            break
        t = t.rsplit(" ", 1)[0].strip(" ,·—-")
    if t.count("(") != t.count(")"):
        t = re.sub(r"\([^)]*$", "", t).strip(" ,·—-")
    return t if len(t) >= 8 else ""


# 한국어 종결 — 서술어로 끝나야 문장이다(어미 규칙만, 업종 무관)
#   ① 서술어 종결(…있다/…한다/…중이다) ② 명사 종결은 '모습/장면/전경'처럼 캡션이 되는 말만.
#   ★ 어절 경계를 요구한다 — 안 그러면 '차량 도장면'의 '장면'이 매치돼 끊긴 문장이 통과한다.
_CLOSED = re.compile(
    r"(다|요|음|임|중|함|됨|짐)[.!?]?$|"
    r"(?:^|\s)(모습|장면|전경|모습이다|현장)[.!?]?$")


# 주관 수식어 — 사진에서 관찰될 수 없는 말(2026-08-03 실물 판정: '정성껏·세심하게'가 나갔다).
#   관찰 기록에 없는 것을 넣은 것이므로 규격 위반이다. 어휘 규칙만(업종 무관).
_SUBJ = re.compile(
    r"(정성껏|세심하게|세심히|꼼꼼히|꼼꼼하게|정교하게|완벽하게|깔끔하게|능숙하게|"
    r"조심스럽게|정성스럽게|한 ?치의|최선을|열심히|신중하게)")


_COLOR = re.compile(r"(빨간색|붉은색|분홍색|초록색|파란색|노란색|검정색|검은색|흰색|하얀색|회색|은색|남색|보라색)")


def caption_ok(text: str) -> str:
    """캡션 게이트 — 통과면 빈 문자열, 아니면 사유. 게이트 없는 표면은 만들지 않는다(조항)."""
    t = " ".join((text or "").split())
    if not t:
        return "빈 캡션"
    if _GUESS_WORD.search(t) or _GUESS_TAIL.search(t):
        return "추측 표현"
    if _LIST_NO.match(t):
        return "분석 넘버링"
    if _SCENE.search(t):
        return "촬영 환경·소품 서술"
    if _SUBJ.search(t):
        return "주관 수식어"
    # 한 물건에 색이 둘일 수는 없다(실물: '초록색 빨간색 스퀴지'). 언어 규칙만.
    if len(set(_COLOR.findall(t))) >= 3:
        return "색상 나열 과다"
    if len(t) > CAPTION_MAX + 12:
        return f"너무 김({len(t)}자)"
    # ★ 완결성(2026-08-03 실물 판정): '차량 유리에 손으로 시공 도구를 대' 같은 잘린 문장이
    #   게이트를 그냥 통과해 사장님 화면에 나갔다. 끝나도 말이 되는 문장만 캡션이다.
    #   상호 꼬리(— 가게명)는 본문 뒤에 붙는 서명이라 떼고 본다.
    b = re.sub(r"\s*[—-]\s*[^—-]{2,20}$", "", t).rstrip()
    if not _CLOSED.search(b):
        return "끊긴 문장"
    return ""


def write_captions(descs: list, anchors=(), shop: str = "", kw: str = "",
                   _diag: list = None, context: str = "") -> list:
    """사진 묘사 → 캡션 N개를 한 번에 쓴다(1콜, 2026-08-03 사장님 승인 B안).

    왜 깎지 않고 다시 쓰는가: 긴 관찰문에서 소품·추측·배경을 도려내면 문장 뼈대가 부서진다
    ('군용 스타일 시계를 착용한 손이' → '군용 스타일'). 캡션은 깎아 만드는 게 아니라 쓰는 것이다.

    원리(사장님 구술): **아는 건 실값으로 말하고, 모르는 건 말하지 않는다.**
    반환은 descs와 같은 길이. 게이트를 통과 못 한 줄은 규격 깎기로, 그것도 안 되면 빈칸.
    """
    import logging
    n = len(descs or [])
    if not n:
        return []
    _fallback = [to_caption(d, anchors) for d in descs]
    # ★ 한 콜에 다 담지 않는다(2026-08-03 실물 판정: 20장 중 13줄에서 출력이 잘렸고
    #   나머지 7장은 옛 규격 깎기의 끊긴 문장이 그대로 사장님 화면에 나갔다).
    #   개수는 요구가 아니라 구조로 보장한다 — 배치로 쪼개고, 모자라면 그 번호만 다시 묻는다.
    if n > BATCH:
        out = []
        for s0 in range(0, n, BATCH):
            out += write_captions(descs[s0:s0 + BATCH], anchors, shop if s0 == 0 else "",
                                  kw, _diag, context)
        return out
    src = "\n".join(f"{i + 1}. {(d or '')[:160]}" for i, d in enumerate(descs))
    # ★ 실값을 '나열'로 주면 오용한다(2026-08-03, 2회 재발: '버텍스500 패드'·'루마썬팅 필름').
    #   PV5는 차종, 버텍스500은 필름 등급인데 우리는 문자열 3개를 한 줄에 던지고
    #   "제자리에 넣어라"고 요구했다. 역할을 모르는 값을 제자리에 넣는 건 불가능한 요구다.
    #   → 값이 아니라 **사장님이 쓰신 원문 문맥**을 준다. 문맥이 역할을 알려준다.
    ctx = " ".join((context or "").split())[:400]
    prompt = (
        f"사진 {n}장의 관찰 기록을 보고, 각 사진에 붙일 **캡션**을 한 줄씩 써라.\n\n"
        "[규격]\n"
        f"1. 정확히 {n}줄. '번호. 캡션' 형식. 순서는 사진 번호 그대로.\n"
        "2. 한 문장, 40~60자. 끝나도 말이 되는 완결 문장.\n"
        "3. 담을 것: [무엇을] + [어디에] + [무슨 작업/상태]. 그것만.\n"
        "4. 빼야 할 것: 배경·바닥·조명·반사·소품(손목시계 등)·촬영 각도. 손님과 무관하다.\n"
        "5. 추측 금지: '추정·로 보이는·또는 ~인 듯·가능성' 같은 말을 쓰지 마라. "
        "확실치 않은 세부(도구명·차종)는 상위어로 써라('시공 도구로', '차량 표면을').\n"
        "6. 고유명사(모델명·등급명·상호)는 아래 [사장님 메모]에 나온 것만, "
        "**그 메모가 쓴 문맥 그대로** 써라.\n"
        "   메모가 어떤 이름을 무엇에 붙였는지 모르겠으면 그 이름을 아예 쓰지 마라. "
        "일반어로 쓰면 된다('시공 도구', '차량 표면').\n"
        "   ★ 메모에 없는 자리에 이름을 옮겨 붙이는 것은 날조다.\n"
        "7. 관찰 기록에 없는 내용을 만들지 마라. 판별 안 되는 사진은 확실한 부분만 쓴다.\n"
        "   ★ 특히 '정성껏·꼼꼼히·세심하게·완벽하게' 같은 주관 수식어 금지 — 사진에 안 찍힌다.\n"
        "   ★ 어미를 다양하게: '…의 모습.'만 반복하지 마라.\n"
        "8. 번호 매기기('1)')·제목·머리말 금지. 줄만 출력.\n\n"
        + (f"\n[사장님 메모 — 이 세트가 무슨 작업인지]\n{ctx}\n" if ctx else "")
        + f"\n[관찰 기록]\n{src}")
    _log = logging.getLogger("shopcast.caption")

    def _ask(text: str) -> dict:
        from app import llm as _llm
        raw = _llm.call_task("spoken", text, max_tokens=180 * n + 200)
        got = {}
        for ln in (raw or "").splitlines():
            m = re.match(r"^\s*(\d{1,2})\s*[.)]\s*(.+)$", ln.strip())
            if m:
                i = int(m.group(1)) - 1
                if 0 <= i < n:
                    c = " ".join(m.group(2).strip().strip('"“”').split())
                    why = caption_ok(c) if c else "LLM 빈 줄"
                    if not why:
                        got[i] = c
                    elif _diag is not None:      # 조용한 실패 금지 — 왜 떨어졌는지 남긴다
                        _diag.append({"llm": c, "reject": why})
        return got

    try:
        got = _ask(prompt)
    except Exception as e:
        _log.warning("[caption] 일괄 작성 실패 — 규격 깎기로: %r", repr(e)[:100])
        got = {}
    miss = [i for i in range(n) if i not in got]
    if miss and got:                     # 모자란 번호만 다시 묻는다(폴백보다 재요청이 먼저다)
        try:
            got.update(_ask(prompt + "\n\n[다시] " + ", ".join(str(i + 1) for i in miss) +
                            "번이 빠졌거나 규격 미달이다. 그 번호만 규격대로 다시 써라."))
        except Exception as e:
            _log.warning("[caption] 재요청 실패: %r", repr(e)[:80])
    out, blanks = [], []
    for i in range(n):
        c = got.get(i) or ""
        if not c:
            # ★ 침묵 폴백 금지: 규격 깎기 결과도 게이트를 통과해야 나간다.
            #   못 쓰면 빈칸이다 — 잘린 문장을 내보내는 것보다 낫다.
            fb = _fallback[i]
            c = fb if (fb and not caption_ok(fb)) else ""
            if not c:
                blanks.append(i + 1)
                if _diag is not None:
                    _diag.append({"fallback": fb, "reject": caption_ok(fb) if fb else "묘사 없음"})
        out.append(c)
    if blanks:
        _log.warning("[caption] %d/%d 빈칸 — 사진 %s (관찰 기록이 캡션 규격을 못 채웠다)",
                     len(blanks), n, blanks)
    _log.info("[caption] 작성 %d/%d", n - len(blanks), n)
    return out
