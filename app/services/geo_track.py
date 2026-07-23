"""
GEO 레이어 — 트랙 B(정보성 글) : 네이버 AI 브리핑 인용 최적화.

배경(2026.6.4 룰): 네이버가 'AI 브리핑 인용수'를 창작자 보상 기준으로 공식화.
- 브리핑은 정보형 질의(원인·방법·기준)에 뜨고 상업형(추천·비교·매물)엔 거의 안 뜬다 → 정보성 글이 입장권.
- Top10 밖도 인용, 도메인 권위 낮을수록 질문형·FAQ 형식 신호 효과 큼 → 신생 블로그(우리 고객)에 유리.
- AI는 '단락 단위'로 잘라 인용 → 단락 독립완결 + 첫 문장 결론 + 수치 근거가 인용 조건.

설계 원칙:
- 트랙 A(매물·시공 글)는 불변. 트랙 B는 별도 프롬프트·별도 게이트(추가만).
- 업종 어휘 하드코딩 0 — 주제는 가게 스키마(content_angles·honesty_hooks·attribute_axes) 유래(Haiku 1콜).
- 정보형 질의는 지역 결합 불필요 → trade_area 무관하게 비지역 질문형 키워드 허용(트랙 B 한정 예외).
- 날조 금지: 수치·경력은 실값(입력·프로필)만. 게이트를 날조로 통과시키지 않는다(정직 원칙 우선).
"""
from __future__ import annotations

import json
import logging
import re

_log = logging.getLogger("shopcast.geo")

# source_type은 claim_writing이 'source_type ASC'로 정렬 → 트랙 B는 P1~P4 뒤(R1)로 최하 우선순위.
# 매물·시공 글(트랙 A P1~P4)이 항상 먼저 소비되어 '매물 글이 밀리지 않음'을 구조적으로 보장.
INFO_SOURCE = "R1"          # writing_queue source_type(트랙 B 슬롯 식별, P4보다 뒤 정렬)
WEEKLY_INFO_CAP = 2         # 가게당 주 트랙 B 상한(매물 글 비율 유지)
MIN_INFO_VOLUME = 50        # 정보형 질의는 상업형보다 검색량 얇음 — 하한 완화(단, 0은 배제)


# ── PHASE 1-1 : 질문형 주제 도출(스키마 유래, Haiku 1콜) ──────────────
def _exp_angle(q: str) -> str:
    """경험 질문 → 앵글 추정(하드코딩 업종 어휘 없이 의도어로만)."""
    if any(k in q for k in ("가격", "비용", "얼마", "요금", "견적")):
        return "price"
    if any(k in q for k in ("차이", "vs", "비교", "어떤", "고르", "괜찮")):
        return "review"
    return "howto"


def experience_topics(experiences: list) -> list[dict]:
    """★ 1순위 주제 후보 — 사장 실경험 Q&A의 '질문'을 그대로 주제로(실제 받는 질문 = 검색 수요 최근접).
    스키마 유래 주제(2순위)보다 앞. 업종 하드코딩 0(질문 원문 사용)."""
    out = []
    for e in (experiences or []):
        q = " ".join((e.get("question") or "").split())
        if len(q) < 6:
            continue
        if "?" not in q and not any(k in q for k in ("나요", "까", "무엇", "어떻게", "왜", "얼마", "어디")):
            q = q.rstrip(".") + "?"                   # 질문형 보정(원문 의미 불변)
        out.append({"topic": q, "angle": _exp_angle(q), "source": "experience", "exp_id": e.get("id")})
    return out


def info_topics(industry: str, biz_type: str, schema: dict, region: str = "",
                desc: str = "", n: int = 3, experiences: list = None) -> list[dict]:
    """가게 스키마 → AI 브리핑이 받을 '질문형 정보 주제' n개. [{topic, angle}] (angle=howto|review|price).
    주제 소스 = content_angles·honesty_hooks·attribute_axes(업종 어휘 하드코딩 0). 실패 시 [].
    experiences 주어지면 사장 실경험 질문이 1순위(앞), 스키마 유래가 2순위. 둘 다 호출부 키워드 관문 경유."""
    exp_first = experience_topics(experiences)          # 1순위
    from app import llm as _llm
    axes = ", ".join((schema.get("attribute_axes") or [{}])[0].get("tokens", [])[:6]) if schema.get("attribute_axes") else ""
    hooks = ", ".join(schema.get("honesty_hooks") or [])
    angles = ", ".join(schema.get("content_angles") or [])
    prompt = (
        "너는 한국 검색 콘텐츠 전략가다. 아래 가게가 '네이버 AI 브리핑에 인용될' 정보성 글 주제를 JSON으로만 출력하라.\n"
        f"[업종] {industry}\n[사업형태] {biz_type}\n[핵심 속성축] {axes}\n[정직하게 밝힐 한계] {hooks}\n[가능한 앵글] {angles}\n[설명] {desc[:160]}\n\n"
        "규칙:\n"
        "- 상업형(추천·비교·매물·가격만) 금지. 정보형(원인·방법·기준·차이·주의점)만 — 브리핑은 정보 질의에만 뜬다.\n"
        "- 각 주제는 '실제 검색되는 질문형' 한 문장(의문형). 지역명 넣지 마라(정보형은 비지역).\n"
        f"- 정확히 {n}개. 서로 다른 검색 의도.\n"
        "- angle은 howto(방법·절차)|review(기준·판단)|price(비용 구조) 중.\n"
        '출력 형식: {"topics":[{"topic":"질문형 문장","angle":"howto"}, ...]}'
    )
    try:
        raw = _llm.call_task("spoken", prompt, max_tokens=500)
    except Exception as e:
        _log.warning("[geo] 주제 도출 실패: %r", repr(e)[:120])
        return []
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for t in (data.get("topics") or []):
        topic = " ".join((t.get("topic") or "").split())
        ang = (t.get("angle") or "howto").strip()
        if topic and "?" in topic + "?":
            out.append({"topic": topic, "angle": ang if ang in ("howto", "review", "price") else "howto",
                        "source": "schema"})
    # 1순위(경험) + 2순위(스키마), 중복 제거
    merged, seen = [], set()
    for tp in (exp_first + out):
        k = tp["topic"].replace(" ", "")
        if k and k not in seen:
            seen.add(k)
            merged.append(tp)
    return merged[:max(n, len(exp_first))]


# ── PHASE 1-2 : 트랙 B 키워드 관문(비지역 질문형 허용 예외) ────────────
def select_info_keyword(candidates: list, region: str, industry: str,
                        tenant_id: str = "", verify_volume: bool = True) -> str:
    """정보형 주제 키워드 관문 — 검색량 실측 + 기초지역 배제.
    ★ 트랙 B 한정 예외: 정보형 질의는 지역 결합 불필요 → trade_area 무관하게 '비지역 질문형' 허용
      (트랙 A의 지역 결합 강제·is_basic_region_kw 배제는 여기 미적용). 상업형 관문과 다름을 명시."""
    from app import seo as _seo
    cands = [" ".join((c or "").split()) for c in (candidates or []) if c and c.strip()]
    cands = list(dict.fromkeys(cands))
    # 기초지역(구·군)이 '붙은' 후보만 배제 — 순수 비지역 질문형은 그대로 통과(예외의 핵심)
    cands = [c for c in cands if not _seo.is_basic_region_kw(c, region, "seller")]
    if not cands:
        return ""
    if not verify_volume:
        return cands[0]
    try:
        from app.services import searchad as _sa
        if not _sa.configured():
            return cands[0]                          # 무키 → 검증 스킵(생성 계속, 날조 아님)
        vols = {(_v.get("keyword") or "").replace(" ", ""): (_v.get("total") or 0)
                for _v in _sa.keyword_volumes(cands, limit=40)}
        ranked = sorted(cands, key=lambda c: -vols.get(c.replace(" ", ""), 0))
        for c in ranked:
            if vols.get(c.replace(" ", ""), 0) >= MIN_INFO_VOLUME:
                return c
        return ranked[0] if ranked else ""           # 전부 미달이어도 최상위 1개는 시도(로그는 호출부)
    except Exception:
        return cands[0]


# ── PHASE 1-3 : GEO 구조 강제 프롬프트 ──────────────────────────────
def _author_trust(tenant, note: str) -> str:
    """저자 신뢰 신호 실값 — 입력/프로필에 '실제로 있는' 경력·실적만(날조 금지). 없으면 ''."""
    src = (note or "") + " " + (getattr(tenant, "name", "") or "")
    m = re.search(r"((?:경력|시공|운영|영업|업력)?\s*\d{1,2}\s*년(?:째|간|차|경력)?)", src)
    return m.group(1).strip() if m else ""


def _experience_block(experiences: list) -> str:
    """생성 프롬프트용 사장 실경험 Q&A 블록 — 본문 핵심 단락에 답변 실무 내용을 반영시키는 원료."""
    exps = [e for e in (experiences or []) if (e.get("answer") or "").strip()]
    if not exps:
        return ""
    lines = ["[사장님 실경험 답변 — 이 글의 핵심 원료 · 반드시 본문에 반영]"]
    for i, e in enumerate(exps[:3], 1):
        lines.append(f"  Q{i}. {(e.get('question') or '').strip()}")
        lines.append(f"  A{i}. {(e.get('answer') or '').strip()[:600]}")
    lines.append("→ 위 사장님 답변의 '실무 내용·구체 판단·수치'를 본문 핵심 단락에 그대로 녹여라. "
                 "일반론·교과서 설명만으로 채우면 실패다(사장님만 아는 현장 디테일이 인용가치를 만든다).")
    return "\n".join(lines) + "\n\n"


def info_prompt(tenant, industry: str, region: str, kw: str, angle: str,
                note: str, n_imgs: int, trust: str = "", experiences: list = None) -> str:
    """트랙 B 전용 생성 프롬프트 — GEO 구조 강제(질문형 소제목·역피라미드·단락 독립·수치·표/리스트·FAQ·요약).
    트랙 A의 훅-후답 구조는 사용 금지(결론 먼저). 사실 게이트(수치 실값)는 유지.
    experiences: 사장 실경험 Q&A — 본문 핵심 단락에 반영 강제(G6 게이트가 검증)."""
    trust_line = (f"[저자 신뢰 신호] 본문 어딘가에 '{trust}' 같은 실제 경력·실적을 딱 1회 자연스럽게 명시"
                  "(프로필/입력에 있는 값만 — 없는 경력 지어내기 금지).\n" if trust else "")
    return (
        f"[가게] {getattr(tenant,'name','')} (업종: {industry}, 지역: {region})\n"
        f"[글 유형] 정보성(트랙 B — 네이버 AI 브리핑 인용 최적화). 이 글은 매물·시공 홍보가 아니라 '{kw}'에 답하는 정보 글이다.\n"
        + _experience_block(experiences)
        + f"[입력 정보(실제 사진 분석 포함)] {note}\n\n"
        "[GEO 구조 — 반드시 지켜라. AI 브리핑은 단락 단위로 잘라 인용한다]\n"
        f"a. 제목·모든 소제목(##)·소소제목(###)을 '질문형'으로. 평서문 소제목 금지. (예: '{kw}' 자체가 질문형)\n"
        "b. 각 ## 소제목 바로 다음 첫 문장 = 완결형 정답(역피라미드 — 결론 먼저, 배경·부연은 그 뒤). 훅·궁금증 유발로 답을 미루지 마라.\n"
        "c. 각 단락은 독립 완결 — 앞 단락 없이 그 단락만 읽어도 의미가 성립해야 한다(지시대명사로 앞 문장 참조 금지).\n"
        "d. 검증 가능한 수치·실값을 최소 3개 이상(연식·가격대·소요시간·횟수·비율 등). 단, 입력·사진분석·프로필에 근거한 실값만 — 날조 금지.\n"
        "e. 비교는 마크다운 표(모바일 대응 — 열 2개 이하 '| 항목 | 내용 |'), 절차는 번호 리스트(1. 2. 3.), 핵심 정리는 불릿(-)로.\n"
        "   [모바일] 문단은 3~4줄(공백 제외 90~130자)로 끊고, 한 문장 60자 내외. PC 장문단·3열+ 표 금지.\n"
        "f. 글 끝에 '## 자주 묻는 질문'(질문형 Q&A 정확히 3쌍) + '## 3줄 요약'(- 목록 3줄, 각 줄 완결 결론).\n"
        + trust_line +
        "g. 각 단락은 400자 이하로 짧게 끊어라(AI 추출 단위). 한 단락에 한 논점.\n\n"
        "[분량] 공백 제외 1,500자 이상(단락 추출형이라 얇으면 인용 재료가 부족하다).\n"
        f"[사진] {n_imgs}장 → 본문 문단 사이 [사진1]..[사진{n_imgs}]를 순서대로 한 번씩(한 줄 단독) 배치.\n\n"
        "아래 형식 그대로(대괄호 머리표 유지) 출력:\n"
        f"[제목후보]\n(3줄. 각 줄 질문형 + '{kw}'의 검색 의도, 22~40자)\n"
        "[메타설명]\n(150자 내외, 질문에 답하는 요약)\n"
        f"[본문]\n(## 질문형 소제목 3~5개, 각 소제목 첫 문장은 완결 정답, 표 1개 + 번호리스트 + '## 자주 묻는 질문'(3쌍) + '## 3줄 요약', [사진N] 배치)\n"
        "[키워드]\n(쉼표로 5~8개, 정보형 질의 우선)"
    )


# ── PHASE 2 : GEO 구조 게이트(G1~G5) — 트랙 B 전용 ──────────────────
_NUM_TOKEN = re.compile(r"\d+\s*(?:년|년식|%|퍼센트|만원|원|분|시간|초|회|개|장|배|일|주|개월|명|km|㎞|cc|㏄|도)")


def _h2_lines(body: str) -> list[str]:
    return [ln.strip() for ln in (body or "").splitlines() if ln.strip().startswith("## ")]


def _is_struct_head(h: str) -> bool:
    """FAQ·요약 등 구조 블록 소제목(질문형·역피라미드 검사 제외 대상)."""
    return any(k in h for k in ("자주 묻는 질문", "요약", "FAQ", "함께 보면"))


def _first_sentence_after_h2(body: str, content_only: bool = True) -> list[str]:
    """각 ## 소제목 직후 첫 '서술 문장'. content_only=True면 FAQ·요약 구조 블록은 제외."""
    out, lines = [], (body or "").splitlines()
    for i, ln in enumerate(lines):
        s0 = ln.strip()
        if s0.startswith("## ") and not (content_only and _is_struct_head(s0)):
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s and not s.startswith(("#", "[사진", "|", "-", "Q.", "A.", "1.", "2.", "3.")):
                    out.append(s)
                    break
    return out


_STOPWORDS = {"그리고", "하지만", "그래서", "때문", "경우", "가장", "정도", "이때", "이렇게", "저희",
              "고객", "손님", "사장", "가게", "매장", "제품", "상품", "사용", "확인", "가능", "추천"}


def _content_words(text: str) -> list:
    """내용어(2자+ 한글/영숫자 토큰, 불용어 제외) — 실경험 반영 검증용."""
    import re as _r
    toks = _r.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
    return [t for t in toks if t not in _STOPWORDS and not t.isdigit()]


def geo_gate(payload: dict) -> dict:
    """트랙 B 산출물 GEO 구조 게이트. {passed, checks{G1..G6}, fails[]}.
    G1 소제목 질문형 100% / G2 각 H2 첫 문장 완결 평서문 / G3 수치 3개+ / G4 FAQ·요약 존재 /
    G5 단락 400자 이하 / G6 실경험 반영(owner_experience 유래 내용이 본문에 존재 — payload에 경험 있을 때만)."""
    body = payload.get("body") or ""
    title = payload.get("title") or ""
    checks, fails = {}, []
    _QMARK = ("?", "나요", "까", "무엇", "어떻게", "왜", "차이", "얼마", "어디", "언제", "어느")
    # G1: 콘텐츠 소제목(H2/H3) 질문형 비율 100% (FAQ·요약 구조 블록은 제외)
    heads = [h for h in _h2_lines(body) if not _is_struct_head(h)]
    _q_heads = [h for h in heads if any(k in h for k in _QMARK)]
    g1 = bool(heads) and len(_q_heads) == len(heads) and any(k in title for k in _QMARK)
    checks["G1_소제목질문형"] = g1
    if not g1:
        fails.append(f"G1 소제목 질문형 미달({len(_q_heads)}/{len(heads)}) 또는 제목 비질문형")
    # G2: 각 콘텐츠 H2 첫 문장이 '완결 평서문'(의문문·미완결 종결 아님) — 역피라미드
    firsts = _first_sentence_after_h2(body, content_only=True)
    def _declarative(s):
        s = re.sub(r'["\'”’.\s]+$', "", s.rstrip())
        if s.endswith("?") or s.endswith("까요") or s.endswith("나요") or s.endswith("까"):
            return False                              # 의문문 = 답을 미룸(역피라미드 위반)
        return bool(re.search(r"(다|요|됩니다|입니다|습니다|한다|된다|이다|니다)$", s)) or len(s) >= 15
    g2 = bool(firsts) and all(_declarative(s) for s in firsts)
    checks["G2_첫문장완결정답"] = g2
    if not g2:
        fails.append("G2 일부 H2 첫 문장이 완결 정답 아님(의문문/미완결)")
    # G3: 검증가능 수치 토큰 3개+ (사실 게이트 통과 본문 내)
    nums = _NUM_TOKEN.findall(body)
    g3 = len(nums) >= 3
    checks["G3_수치3개+"] = g3
    if not g3:
        fails.append(f"G3 수치 토큰 부족({len(nums)}/3)")
    # G4: FAQ + 요약 블록 존재
    g4 = ("자주 묻는 질문" in body or "FAQ" in body) and ("요약" in body)
    checks["G4_FAQ요약블록"] = g4
    if not g4:
        fails.append("G4 FAQ 또는 요약 블록 없음")
    # G5: 단락당 400자 이하(추출 단위 과대 방지) — 빈 줄 기준 분할, 소제목·표·리스트·사진 제외
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    _over = [p for p in paras if not p.startswith(("#", "|", "-", "1.", "2.", "3.", "[사진"))
             and len(re.sub(r"\s", "", p)) > 400]
    g5 = not _over
    checks["G5_단락400자이하"] = g5
    if not g5:
        fails.append(f"G5 과대 단락 {len(_over)}개(400자 초과)")
    # G6: 실경험 반영 — owner_experience 답변의 내용어가 본문에 충분히 등장(일반론만이면 실패).
    #     payload에 경험이 없으면(트랙 A·진단) N/A로 통과(추가만, 기존 트랙 불변).
    _exps = payload.get("owner_experience") or []
    if _exps:
        body_words = set(_content_words(body))
        ans_words = []
        for e in _exps:
            ans_words += _content_words(e.get("answer") or "")
        ans_uniq = list(dict.fromkeys(ans_words))
        hit = [w for w in ans_uniq if w in body_words]
        # 답변 고유 내용어의 최소 3개 이상(또는 30%+)이 본문에 반영돼야 실경험 유래로 인정
        need = max(3, int(len(ans_uniq) * 0.3)) if ans_uniq else 3
        g6 = len(hit) >= min(need, 3) and len(hit) >= 3
        checks["G6_실경험반영"] = g6
        if not g6:
            fails.append(f"G6 실경험 반영 부족(답변 내용어 {len(hit)}개만 본문 등장, 3+ 필요) — 일반론만 채워짐")
    passed = all(checks.values())
    return {"passed": passed, "checks": checks, "fails": fails,
            "n_heads": len(heads), "n_nums": len(nums)}


def read_citation_capture(image_path: str) -> dict:
    """PHASE 4 — 크리에이터 어드바이저 통계 캡처 → 'AI 브리핑 인용수' 판독(vision 재사용).
    API가 없어 사용자가 캡처 업로드 → Gemini vision 판독. 화면에 명확히 보이는 숫자만(날조 금지).
    반환 {citation_count:int|None, keyword:str, raw:str}."""
    import json as _j
    import os as _os
    import re as _re
    from app import vision as _vz
    if not (_vz.configured() and image_path and _os.path.exists(image_path)):
        return {"citation_count": None, "keyword": "", "raw": ""}
    try:
        mt, data = _vz._b64_for_vision(image_path)
        prompt = (
            "이 이미지는 네이버 '크리에이터 어드바이저' 통계 화면 캡처다. 화면에서 'AI 브리핑 인용수'"
            "(또는 '인용', '브리핑 인용') 지표의 숫자를 찾아라. 화면에 명확히 보이는 숫자만 읽어라 — 추측·계산 금지.\n"
            "관련 글 제목/키워드가 보이면 함께 적어라. 인용수가 안 보이면 null.\n"
            'JSON만: {"citation_count": <정수 또는 null>, "keyword": "<글제목/키워드 또는 빈칸>"}'
        )
        from app import llm
        raw = llm.call_task("vision", prompt, 300, default_model=_vz.MODEL, images=[(mt, data)]) or ""
        m = _re.search(r"\{.*\}", raw, _re.S)
        if not m:
            return {"citation_count": None, "keyword": "", "raw": raw[:200]}
        d = _j.loads(m.group(0))
        cc = d.get("citation_count")
        cc = int(cc) if isinstance(cc, (int, float)) or (isinstance(cc, str) and cc.isdigit()) else None
        return {"citation_count": cc, "keyword": (d.get("keyword") or "").strip()[:60], "raw": raw[:200]}
    except Exception as e:
        _log.warning("[geo] 인용수 판독 실패: %r", repr(e)[:120])
        return {"citation_count": None, "keyword": "", "raw": ""}


def regen_instruction(fails: list) -> str:
    """GEO 게이트 재생성 지시 — 미달 항목을 구조 규칙으로 변환(날조 유도 금지, 구조만 교정)."""
    base = ("아래 글을 '네이버 AI 브리핑 인용' 구조로 다시 써라(내용·사실은 유지, 구조만 교정):\n"
            "- 모든 ## 소제목을 질문형으로. 각 소제목 첫 문장은 '완결 정답'(결론 먼저, 궁금증 유발 금지).\n"
            "- 각 단락 400자 이하·독립 완결. 검증 가능한 실값 수치 3개+(입력·프로필 근거만 — 없는 수치 지어내지 마라).\n"
            "- '## 자주 묻는 질문'(3쌍) + '## 3줄 요약'(- 3줄) 블록 포함.")
    if fails:
        base += "\n[특히 이 항목 교정] " + " / ".join(fails[:5])
    return base
