"""
스마트 입력 엔진(콘텐츠생성 개선 PHASE 1~4) — 무료(랜딩)·유료(대시보드) 공유.
상위노출(C-Rank·D.I.A.+)의 핵심은 1차 경험·구체 정보인데, 기존 입력은 업종+메모 50자뿐이라
재료가 없었다(SEO_CURRENT.md §5). 이 엔진이 그 병목을 푼다:

  ① vision 선추측 → 사용자 확인("이 사진 [X]로 보여요, 맞나요?")   — 틀린 전제 차단
  ② 업종별 스마트 질문 3~4개(industries.py trust_signals 활용)     — 신뢰 신호의 '실제 값' 수집
  ③ 경험 유도 1개("손님이 왜 만족했나요? 한 줄이면 충분")           — D.I.A.+ 경험서술 재료
  ④ 구조적 주입: 답변을 strategist·blog 프롬프트에 '실제 정보' 블록으로

간편함 유지: 전부 선택 입력("안 넣어도 되지만 넣으면 좋아져요"). 유료는 매장정보 프리필.
정직성: 안 준 정보는 날조 금지(FACTS_RULE 그대로) — 정보량에 따라 글 구체성이 정직하게 차등.
"""
from __future__ import annotations

import json
import re

from app.industries import resolve_industry

# ── 업종별 스마트 질문 뱅크(프리셋 6종) — trust_signals의 '실제 값'을 묻는다 ──
# type: choice(선택형) | text(짧은입력). 전부 선택 입력.
_QUESTION_BANK: dict[str, list[dict]] = {
    "tinting": [
        {"id": "film", "q": "어떤 필름인가요?", "type": "text", "ph": "예: 루마 세라믹 1등급 (브랜드·등급)"},
        {"id": "scope", "q": "시공 부위는요?", "type": "choice",
         "options": ["전면", "측후면", "전체", "기타"]},
        {"id": "warranty", "q": "보증 기간이 있나요?", "type": "text", "ph": "예: 5년 하자보증"},
        {"id": "duration", "q": "시공 시간은 얼마나 걸렸나요?", "type": "text", "ph": "예: 1시간 30분"},
    ],
    "usedcar": [
        {"id": "car", "q": "차종·연식은요?", "type": "text", "ph": "예: 2021 그랜저 IG"},
        {"id": "mileage", "q": "주행거리는요?", "type": "text", "ph": "예: 3만 2천km"},
        {"id": "history", "q": "사고 이력은요? (사실대로)", "type": "choice",
         "options": ["무사고", "단순교환 있음", "수리 이력 있음", "직접 안내"]},
        {"id": "perks", "q": "보증·할부 조건이 있나요?", "type": "text", "ph": "예: 성능보증 6개월, 할부 가능"},
    ],
    "clothing": [
        {"id": "item", "q": "어떤 옷인가요?", "type": "text", "ph": "예: 울 혼방 라운드 니트"},
        {"id": "fit", "q": "핏·사이즈 팁이 있나요?", "type": "text", "ph": "예: 168cm 55 기준 살짝 여유"},
        {"id": "price", "q": "가격대는요?", "type": "text", "ph": "예: 4만원대"},
        {"id": "season", "q": "언제 입기 좋나요?", "type": "choice",
         "options": ["봄가을", "여름", "겨울", "사계절"]},
    ],
    "hair": [
        {"id": "service", "q": "무슨 시술인가요?", "type": "text", "ph": "예: 레이어드컷 + 애쉬브라운"},
        {"id": "time_price", "q": "소요시간·가격대는요?", "type": "text", "ph": "예: 2시간 30분, 12만원대"},
        {"id": "fit_for", "q": "어떤 분께 어울리나요?", "type": "text", "ph": "예: 둥근 얼굴형, 손상모"},
        {"id": "care", "q": "홈케어 팁이 있나요?", "type": "text", "ph": "예: 첫 3일 낮은 온도로 드라이"},
    ],
    "restaurant": [
        {"id": "menu", "q": "메뉴·가격은요?", "type": "text", "ph": "예: 김치찌개정식 8,000원"},
        {"id": "point", "q": "이 메뉴만의 포인트는요?", "type": "text", "ph": "예: 3년 묵은지, 직접 뽑는 사리"},
        {"id": "info", "q": "매장 정보 중 해당되는 건요?", "type": "choice",
         "options": ["주차 가능", "예약 가능", "단체석", "포장·배달"]},
        {"id": "when", "q": "언제 먹기 좋나요?", "type": "text", "ph": "예: 평일 점심 특선 12~2시"},
    ],
    "cafe": [
        {"id": "menu", "q": "메뉴·가격은요?", "type": "text", "ph": "예: 흑임자 라떼 5,500원"},
        {"id": "point", "q": "맛·재료 포인트는요?", "type": "text", "ph": "예: 국산 흑임자 직접 갈아서"},
        {"id": "space", "q": "공간 특징 중 해당되는 건요?", "type": "choice",
         "options": ["콘센트·좌석 넉넉", "주차 가능", "테라스·뷰", "포장 할인"]},
        {"id": "event", "q": "이벤트가 있나요?", "type": "text", "ph": "예: 오픈 기념 10%"},
    ],
}

# 범용(미정의 업종) — 어떤 업종이든 D.I.A.+ 재료가 되는 기본 2개(나머지는 프로필에서 변환 생성)
_GENERIC_QUESTIONS = [
    {"id": "price", "q": "가격대는요?", "type": "text", "ph": "예: 3만원대 (없으면 비워두세요)"},
    {"id": "duration", "q": "소요 시간·기간은요?", "type": "text", "ph": "예: 당일 1시간"},
]

# 경험 유도(공통 1개, 항상 포함) — '왜/과정' 질문(SEO_CURRENT §5: vision은 결과만 보고
# 과정·이유를 못 본다). D.I.A.+ 1차 경험의 핵심 재료라 모든 업종에서 필수.
EXPERIENCE_QUESTION = {
    "id": "experience",
    "q": "손님이 왜 만족했나요? 또는 작업하며 특별히 신경 쓴 점은요?",
    "type": "text", "ph": "한 줄이면 충분해요. 예: 기포 없애려고 유리 물세척만 20분 했어요",
}


def _questions_from_profile(prof) -> list[dict]:
    """업종 프로필 → '그 가게의 실제 값'을 묻는 질문 변환(SEO_CURRENT §2·5 반영).
    - trust_signals("필름 등급 데이터, 보증기간, …") → 항목별 실제 값 질문
      ("보증기간은 어떻게 되나요?") — 신뢰 신호를 추상 지시("녹여라")가 아닌 실측값으로.
    - pain_points(고객 고민) → PAS 재료 질문("이번 손님은 왜 오셨어요?")."""
    out: list[dict] = []
    if getattr(prof, "key", "generic") == "generic":
        # GENERIC(프로필 없음)의 모호한 신호("실제 사진, 후기")는 변환하지 않음 — 강점 질문으로 대체
        return [{"id": "strength", "q": "우리 가게만의 강점 하나는요?", "type": "text",
                 "ph": "예: 10년 경력, 정품만 사용"}]
    sig = [s.strip() for s in re.split(r"[,·/]", getattr(prof, "trust_signals", "") or "") if s.strip()]
    for i, item in enumerate(sig[:2]):
        short = item[:24]
        out.append({"id": f"sig{i}", "q": f"'{short}' — 우리 가게는 실제로 어떤가요?",
                    "type": "text", "ph": "예: 실제 값·기간·숫자로 (모르면 비워두세요)"})
    pains = [s.strip() for s in re.split(r"[,·/]", getattr(prof, "pain_points", "") or "") if s.strip()]
    if pains:
        out.append({"id": "pain", "q": f"이번 손님은 왜 오셨어요? (예: {pains[0][:20]})",
                    "type": "text", "ph": "손님이 겪던 문제 한 줄 — PAS 도입의 재료가 돼요"})
    return out


def questions_for(industry: str, biz_type: str = "local", purpose: str = "",
                  known: dict | None = None) -> dict:
    """업종·목적 맞춤 질문 3~4개 + 경험 유도 1개. 무료·유료 공용(JSON 직렬화 가능).
    프리셋 없는 업종은 범용 3문 + trust_signals 기반 선택형 1문(AI 프로필 활용).
    known: 유료 프리필(콘텐츠생성 PHASE 3) — 이미 아는 값(매장정보 등)은 질문에서 제외하고
    prefill로 되돌려 반복 입력을 없앤다."""
    known = {k: v for k, v in (known or {}).items() if (v or "").strip()}
    prof = resolve_industry(industry)
    qs = [q for q in _QUESTION_BANK.get(prof.key, []) if q["id"] not in known]
    if not qs:
        # 프리셋 뱅크가 없는 업종(AI 생성/GENERIC 프로필) — trust_signals·pain_points를
        # '실제 값 질문'으로 변환(SEO_CURRENT §5: 신뢰 신호가 추상 표현으로만 쓰이던 병목 해소)
        qs = _questions_from_profile(prof) + [q for q in _GENERIC_QUESTIONS if q["id"] not in known]
    qs = qs[:4]
    # 목적별 미세 조정 — 이벤트·할인 목적이면 이벤트 질문을 앞으로
    if "이벤트" in (purpose or "") or "할인" in (purpose or ""):
        qs.sort(key=lambda x: 0 if ("이벤트" in x["q"] or x["id"] in ("event", "perks")) else 1)
    return {"industry": prof.name, "questions": qs, "experience": EXPERIENCE_QUESTION,
            "prefill": known,
            "hint": "안 넣어도 되지만, 넣으면 글이 훨씬 구체적으로 좋아져요"}


# ── vision 선추측(PHASE 2) ─────────────────────────────
def guess_from_photos(paths: list[str], industry: str = "") -> dict:
    """사진 → {guess(확인용 한 줄), analysis(전체 분석)}. 무키/실패 시 guess=''.
    guess는 '[전체]' 요약 라인 우선, 없으면 첫 사진의 '무엇이 보이는가' 첫 줄."""
    from app import vision
    analysis = vision.analyze_all(paths, industry)
    if not analysis:
        return {"guess": "", "analysis": ""}
    guess = ""
    m = re.search(r"\[전체\]\s*(.+)", analysis)
    if m:
        guess = m.group(1).strip()
    else:
        for line in analysis.splitlines():
            line = re.sub(r"^\[사진\d+\]\s*", "", line).strip()
            line = re.sub(r"^1\)\s*", "", line).strip()
            if len(line) >= 5:
                guess = line
                break
    return {"guess": guess[:120], "analysis": analysis}


# ── 답변 → 생성 주입 블록(PHASE 4) ─────────────────────
def parse_answers(raw: str) -> dict:
    """폼에서 넘어온 answers JSON({질문id 또는 질문텍스트: 답}) → dict. 실패 시 {}."""
    try:
        d = json.loads(raw or "{}")
        return {str(k)[:60]: str(v)[:200] for k, v in d.items() if str(v).strip()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def build_intake_note(industry: str, confirmed: str = "", answers: dict | None = None,
                      experience: str = "") -> str:
    """확인된 사진내용 + 질문답 + 경험 → 프롬프트 주입 블록.
    정보가 있으면 'D.I.A.+ 재료로 최우선 사용' 지시, 없으면 빈 문자열(기존 흐름 그대로 = 정직)."""
    answers = answers or {}
    qmap = {}
    for q in (_QUESTION_BANK.get(resolve_industry(industry).key) or _GENERIC_QUESTIONS) + [EXPERIENCE_QUESTION]:
        qmap[q["id"]] = q["q"]
    lines = []
    if (confirmed or "").strip():
        lines.append(f"- 사진 내용(사장님 확인·수정 완료 = 사실): {confirmed.strip()[:120]}")
    for k, v in answers.items():
        v = (v or "").strip()
        if v:
            lines.append(f"- {qmap.get(k, k)}: {v}")
    exp = (experience or "").strip()
    if exp:
        lines.append(f"- 사장님 경험담(1차 경험 — 가장 중요한 재료): {exp[:200]}")
    if not lines:
        return ""
    return (
        "\n[✅ 사장님 제공 실제 정보 — D.I.A.+ 경험서술의 재료(최우선 사용)]\n"
        + "\n".join(lines) +
        "\n[반영 규칙] 위 정보는 사장님이 직접 확인·입력한 사실이다. 본문의 구체 수치·경험 문장은 "
        "반드시 여기서 가져와 1인칭으로 생생하게 서술하라(예: '기포 없애려고 물세척만 20분 했습니다'). "
        "위에 없는 가격·수치·스펙은 지어내지 마라 — 없으면 그 항목은 생략하고 '문의' 유도로.\n")


def analysis_block(analysis: str, confirmed: str = "") -> str:
    """vision 분석 → note 삽입 블록(SEO_CURRENT §5-3: 추측이 '사실'로 각인되던 단일경로 차단).
    사용자가 사진 내용을 확인(confirmed)했으면 '확인됨', 아니면 'AI 추측(미확인)' 라벨 +
    단정 금지 지시를 붙인다 — 오인 시 브리프·본문까지 틀린 전제로 가는 것을 막는다."""
    analysis = (analysis or "").strip()
    if not analysis:
        return ""
    if (confirmed or "").strip():
        return ("\n\n[사진 분석 — 사장님이 사진 내용을 확인·수정함(사실로 사용 가능)]\n" + analysis)
    return ("\n\n[사진 분석 — AI 추측(사장님 미확인)]\n" + analysis +
            "\n[⚠️ 추측 주의] 위 분석은 확인되지 않은 추측이다. 차종·모델명·메뉴명 등 구체 대상을 "
            "단정하지 마라 — 확실치 않으면 일반 표현(예: 'SUV 차량', '시그니처 메뉴')으로 쓰고, "
            "사진 속 글자를 그대로 읽은 것만 구체적으로 써라.")


def record_insight(industry: str, answers: dict | None = None, experience: str = "") -> None:
    """스마트질문 답변 축적(SEO_CURRENT §2 — AI 생성 업종 프로필엔 viral_hooks가 없음).
    ※ 스텁: 지금은 저장만 한다. TODO(viral_hooks): 같은 업종 답변·경험담이 N건 쌓이면
    이를 근거로 해당 업종의 viral_hooks(잘 터지는 오프닝 앵글)를 생성해
    industry_profiles에 보강한다 — 실제 사장님 데이터 기반이라 날조 없는 훅이 된다."""
    answers = {k: v for k, v in (answers or {}).items() if (v or "").strip()}
    if not (answers or (experience or "").strip()):
        return
    try:
        from app import db
        db.save_intake_insight(industry, answers, (experience or "").strip()[:200])
    except Exception:
        pass


def enrichment_level(confirmed: str = "", answers: dict | None = None, experience: str = "") -> str:
    """정보량 등급 — rich(경험 or 답 2개+) / some(1개+) / bare(없음). 품질 차등·재생성 유도용."""
    n = sum(1 for v in (answers or {}).values() if (v or "").strip())
    if (experience or "").strip() or n >= 2:
        return "rich"
    if n >= 1 or (confirmed or "").strip():
        return "some"
    return "bare"
