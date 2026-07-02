"""
업종 프로필 — 업종별 콘텐츠 특화 데이터.
캡션 톤/해시태그/콘텐츠 앵글/촬영 가이드/법적 주의를 한 곳에서 관리.
새 업종 추가 = PROFILES 에 항목 하나 추가하면 끝.
tenant.industry(자유 문자열)는 resolve_industry()로 별칭 매칭 → 프로필 결정.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IndustryProfile:
    key: str                       # 슬러그
    name: str                      # 표시명
    aliases: list[str]             # 매칭용 별칭/키워드
    tone: str                      # 캡션 톤 지시문
    hashtag_seeds: list[str]       # 기본 해시태그(지역/메뉴는 동적 추가)
    content_angles: list[str]      # "무엇을 찍어 보낼지" 소재 앵글
    photo_guide: list[str]         # 사장님용 촬영 가이드
    cta: str                       # 행동유도 스타일
    persona: str = ""              # 업종 페르소나(말투/표현) — 강하게 적용
    cautions: list[str] = field(default_factory=list)  # 법적/표현 주의


PROFILES: dict[str, IndustryProfile] = {
    "tinting": IndustryProfile(
        key="tinting", name="썬팅업체",
        aliases=["썬팅", "선팅", "틴팅", "자동차필름", "윈도우필름", "열차단"],
        tone="전문성과 시공 퀄리티를 강조하고, 열차단·자외선 차단 효과와 하자보증을 신뢰감 있게 전달.",
        hashtag_seeds=["#썬팅", "#자동차썬팅", "#썬팅필름", "#열차단필름", "#신차썬팅", "#틴팅"],
        content_angles=["시공 전/후 비교", "열차단 등급·데이터", "신차 패키지", "차종별 시공사례", "하자보증·애프터"],
        photo_guide=["시공 전/후 같은 각도·조명", "차량+필름 등급 표기", "디테일(엣지 마감) 클로즈업"],
        cta="견적·시공 예약 문의 유도",
        persona=("기술력과 시공 디테일로 신뢰를 준다. Before/After를 강조하고 보증기간·차종별 추천을 "
                 "구체적으로. 과장보다 '이렇게 시공했습니다' 식 팩트와 마감 디테일로 말한다."),
        cautions=["효과는 과장 없이(체감/등급 기준)", "타사 비방 금지"],
    ),
    "usedcar": IndustryProfile(
        key="usedcar", name="중고차판매",
        aliases=["중고차", "중고차매매", "중고자동차", "자동차매매", "카매니저"],
        tone="투명성과 신뢰를 최우선. 실매물·성능점검·사고이력을 솔직하게. 과장·허위 절대 금지.",
        hashtag_seeds=["#중고차", "#중고차매매", "#실매물", "#무사고", "#중고차시세"],
        content_angles=["신규 입고 매물", "성능점검·사고이력 공개", "시세·할부/리스 안내", "시승 후기"],
        photo_guide=["외관 4방향+실내+계기판(주행거리)", "번호판/연락처 개인정보 가림", "흠집도 솔직히"],
        cta="매물 문의·시승 예약 유도",
        persona=("솔직하고 담백하게. 과장은 절대 금지. '상태 솔직히 말씀드리면', '급하게 처분합니다', "
                 "'타이어 거의 새것' 같은 현장 말투. 성능점검·사고이력을 투명하게 공개하고 지역명을 자연스럽게."),
        cautions=["허위매물·과장광고 금지(자동차관리법·표시광고법)", "주행거리/사고이력 정확히"],
    ),
    "clothing": IndustryProfile(
        key="clothing", name="옷가게",
        aliases=["옷가게", "의류", "의류매장", "패션", "boutique", "편집샵"],
        tone="트렌디하고 감성적인 톤. 코디 제안과 착용감으로 구매욕을 자극.",
        hashtag_seeds=["#데일리룩", "#코디", "#신상", "#ootd", "#패션스타그램"],
        content_angles=["신상 입고", "코디 제안(상하의 매치)", "착용샷", "시즌 세일"],
        photo_guide=["착용샷+디테일컷", "자연광에서 색감 살리기", "전신+소재 클로즈업"],
        cta="방문·사이즈/재고 문의 유도",
        persona=("코디 제안형 말투. '이 옷 입으면 이런 느낌', 계절감, 체형·상황별 추천을 친한 패션 친구처럼. "
                 "스타일링 팁을 곁들여 구매 상상을 자극한다."),
        cautions=["원산지·소재 표기 정확", "타브랜드 이미지 무단사용 금지"],
    ),
    "hair": IndustryProfile(
        key="hair", name="미용실",
        aliases=["미용실", "헤어샵", "헤어", "미용", "살롱", "펌", "염색"],
        tone="친근하고 트렌디한 톤. 시술 전/후 변화로 신뢰를 주고 예약을 유도.",
        hashtag_seeds=["#헤어스타일", "#펌", "#염색", "#헤어", "#미용실추천"],
        content_angles=["시술 전/후", "신규 스타일·시술 메뉴", "이벤트·할인", "디자이너 소개"],
        photo_guide=["시술 전/후 같은 조명·각도", "정면+측면+뒷모습", "디테일(컬·컬러) 클로즈업"],
        cta="예약·상담 유도",
        persona=("트렌디하고 친근하게. 시술 전/후 변화를 또렷이 보여주고 시술명·홈케어 팁을 구체적으로. "
                 "'이런 분께 추천' 식으로 타겟을 콕 집는다."),
        cautions=["전후 사진은 동일 인물 동의", "효과 과장 금지"],
    ),
    "restaurant": IndustryProfile(
        key="restaurant", name="음식점",
        aliases=["음식점", "식당", "맛집", "레스토랑", "고깃집", "한식", "분식"],
        tone="식욕을 자극하는 생생한 묘사. 시그니처 메뉴와 분위기를 매력적으로.",
        hashtag_seeds=["#맛집", "#맛스타그램", "#먹스타그램", "#존맛탱", "#맛집추천"],
        content_angles=["시그니처 메뉴", "신메뉴·점심특선", "단체/예약 안내", "매장 분위기"],
        photo_guide=["음식 클로즈업+김/소스 강조", "자연광·접시 정돈", "메뉴+테이블 세팅"],
        cta="예약·방문·포장 문의 유도",
        persona=("감성과 실용의 균형. 맛 묘사는 생생하게(식감·향), 동시에 가성비·혼밥·단체·예약 같은 실질 정보를 "
                 "함께. 솔직한 '진짜 맛있어서 추천' 톤."),
        cautions=["원산지 표시", "위생/효능 과장 금지"],
    ),
    "cafe": IndustryProfile(
        key="cafe", name="카페",
        aliases=["카페", "커피", "디저트카페", "베이커리", "브런치"],
        tone="감성적이고 따뜻한 톤. 시그니처 음료·디저트와 공간 분위기를 강조.",
        hashtag_seeds=["#카페추천", "#감성카페", "#디저트", "#카페스타그램", "#커피맛집"],
        content_angles=["시그니처 음료", "신메뉴 디저트", "공간·인테리어", "이벤트·쿠폰"],
        photo_guide=["음료+공간 함께", "자연광 감성컷", "디저트 클로즈업+소품"],
        cta="방문·예약·신메뉴 안내 유도",
        persona=("감성과 실용의 균형. 분위기(데이트·혼카페·작업하기 좋은)와 시그니처 메뉴를 함께 전한다. "
                 "따뜻하고 진솔한 동네 단골 톤."),
        cautions=["타카페 메뉴/사진 도용 금지", "알레르기 정보 정확"],
    ),
}

# 기본(미매칭) 프로필
GENERIC = IndustryProfile(
    key="generic", name="일반 매장",
    aliases=[],
    tone="친근하고 신뢰감 있는 톤으로 매장 방문을 유도.",
    hashtag_seeds=["#동네맛집", "#소상공인", "#가게추천"],
    content_angles=["신규 소식", "이벤트·할인", "매장 분위기"],
    photo_guide=["밝은 자연광", "주제가 분명한 한 컷"],
    cta="방문·문의 유도",
    persona="친근하고 신뢰감 있게. 과장 없이 솔직하게 방문을 유도한다.",
)

# 시작 업종 (요청: 썬팅/중고차/옷가게/미용실/음식점/카페)
ACTIVE_INDUSTRIES = ["tinting", "usedcar", "clothing", "hair", "restaurant", "cafe"]


# 업종별 작성 예시(업로드 가이드 + '예시 채우기'용). purpose는 폼 select 값과 일치.
EXAMPLES: dict[str, dict] = {
    "tinting": {"note": "신차 제네시스 G80 전면유리 열차단 1등급 시공 완료",
                "purpose": "신상품 홍보", "target": "신차 구매 고객", "extra": "하자보증 5년, 1시간 시공"},
    "usedcar": {"note": "2021 그랜저 IG 무사고 흰색",
                "purpose": "판매 전환", "target": "30~40대", "extra": "급매, 주행 3만km, 보증가능"},
    "clothing": {"note": "가을 신상 니트 입고",
                 "purpose": "방문 유도", "target": "20~30대 여성", "extra": "주말 10% 세일"},
    "hair": {"note": "레이어드컷 + 애쉬브라운 염색 시술",
             "purpose": "방문 유도", "target": "20~30대", "extra": "신규 고객 첫 방문 20%"},
    "restaurant": {"note": "점심특선 김치찌개정식 출시",
                   "purpose": "방문 유도", "target": "직장인", "extra": "평일 12~2시, 8,000원"},
    "cafe": {"note": "신메뉴 흑임자 라떼 출시",
             "purpose": "신상품 홍보", "target": "20~30대", "extra": "오픈 이벤트 10%"},
}
GENERIC_EXAMPLE = {"note": "오늘의 소식 한 줄", "purpose": "방문 유도", "target": "", "extra": "이벤트 내용"}


def example_for(profile: IndustryProfile) -> dict:
    return EXAMPLES.get(profile.key, GENERIC_EXAMPLE)


def _slug(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_")[:40]


def _profile_from_dict(d: dict) -> IndustryProfile:
    return IndustryProfile(
        key=d.get("key", "custom"), name=d.get("name", "매장"),
        aliases=d.get("aliases", []), tone=d.get("tone", GENERIC.tone),
        hashtag_seeds=d.get("hashtag_seeds", GENERIC.hashtag_seeds),
        content_angles=d.get("content_angles", GENERIC.content_angles),
        photo_guide=d.get("photo_guide", GENERIC.photo_guide),
        cta=d.get("cta", GENERIC.cta), persona=d.get("persona", GENERIC.persona),
        cautions=d.get("cautions", []))


def _preset_match(industry: str) -> IndustryProfile | None:
    s = (industry or "").strip().lower()
    if not s:
        return None
    for p in PROFILES.values():
        if s == p.key or s == p.name.lower():
            return p
        if any(a.lower() in s or s in a.lower() for a in p.aliases):
            return p
    return None


def resolve_industry(industry: str) -> IndustryProfile:
    """업종 → 프로필. 프리셋 매칭 → DB(AI생성/수정) 캐시 → GENERIC. (LLM 호출 안 함, 빠름)"""
    p = _preset_match(industry)
    if p:
        return p
    if industry:
        from app import db
        d = db.get_industry_profile(_slug(industry))
        if d:
            return _profile_from_dict(d)
    return GENERIC


def _to_list(s: str) -> list[str]:
    import re
    items = re.split(r"[\n,·]|^-\s*", s or "", flags=re.M)
    return [x.strip(" -#") for x in items if x.strip(" -#")][:10]


def ensure_profile(industry: str) -> IndustryProfile:
    """가게 등록 시 호출 — 프리셋/캐시에 없으면 AI로 업종 프로필 생성·저장. 실패 시 GENERIC."""
    p = _preset_match(industry)
    if p:
        return p
    if not industry:
        return GENERIC
    from app import db
    key = _slug(industry)
    cached = db.get_industry_profile(key)
    if cached:
        return _profile_from_dict(cached)
    data = _generate_ai(industry, key)
    if data:
        db.save_industry_profile(key, industry.strip(), data, source="ai")
        return _profile_from_dict(data)
    return GENERIC


def _generate_ai(industry: str, key: str) -> dict | None:
    """Claude로 업종 맞춤 프로필 생성. 키 없거나 실패 시 None."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from app.generators.text_claude import _call_llm, _parse_sections
        prompt = (
            f"한국 소상공인 '{industry}' 업종의 SNS 마케팅 콘텐츠 프로필을 만들어라.\n"
            "아래 형식 그대로(대괄호 머리표 유지) 한국어로:\n"
            "[페르소나]\n(말투/톤 한 문장)\n[톤]\n(한 문장)\n"
            "[해시태그]\n(#로 시작, 쉼표로 5~7개)\n"
            "[콘텐츠앵글]\n(- 로 4개, 무엇을 찍어 올리면 좋은지)\n"
            "[촬영가이드]\n(- 로 3개)\n[CTA]\n(행동유도 한 구)\n"
            "[주의]\n(- 로 1~2개, 법적/표현 주의)"
        )
        raw = _call_llm(prompt, max_tokens=900)
        d = _parse_sections(raw, ["페르소나", "톤", "해시태그", "콘텐츠앵글", "촬영가이드", "CTA", "주의"])
        if not d.get("페르소나") and not d.get("톤"):
            return None
        tags = [("#" + t.lstrip("#")) for t in _to_list(d.get("해시태그", "")) if t]
        return {
            "key": key, "name": industry.strip(), "aliases": [industry.strip()],
            "persona": d.get("페르소나", GENERIC.persona).strip(),
            "tone": d.get("톤", GENERIC.tone).strip(),
            "hashtag_seeds": tags or GENERIC.hashtag_seeds,
            "content_angles": _to_list(d.get("콘텐츠앵글", "")) or GENERIC.content_angles,
            "photo_guide": _to_list(d.get("촬영가이드", "")) or GENERIC.photo_guide,
            "cta": (d.get("CTA", GENERIC.cta).strip() or GENERIC.cta),
            "cautions": _to_list(d.get("주의", "")),
        }
    except Exception:
        return None
