"""
SEO/성과 엔진 — 플랫폼별 '잘 팔리고 잘 노출되는' 콘텐츠 설계 규칙.
- target_keywords: 지역+업종+검색의도 기반 타겟 키워드(LLM 없이도 결정적 생성).
- *_DIRECTIVES: 각 플랫폼 성과/SEO 베스트프랙티스(프롬프트에 주입).
이 규칙이 곧 제품의 '성과 차별화'. 키워드는 검색량 있는 롱테일을 노린다.
"""
from __future__ import annotations

import re

# 검색 의도 수식어(구매 직전 키워드 = 전환율 높음)
_INTENTS = ["추천", "후기", "가격", "잘하는곳", "예약", "위치"]


# 온라인 셀러용 구매 직전 검색 의도(상품축)
_PRODUCT_INTENTS = ["추천", "후기", "내돈내산", "사용기", "단점", "비교", "가성비"]


def product_keywords(note: str = "", brand: str = "", limit: int = 10) -> list[str]:
    """상품/후기축 키워드 — 온라인 셀러용(지역 대신 상품명+구매의도)."""
    kws: list[str] = []
    nouns = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or "")
             if w not in ("추천", "이벤트", "할인", "후기") and len(w) <= 12]
    head = nouns[:2] or ([brand.strip()] if brand.strip() else [])
    for n in head:
        for it in _PRODUCT_INTENTS:
            kws.append(f"{n} {it}")
    if brand.strip():
        for n in nouns[:2]:
            kws.append(f"{brand.strip()} {n}")
    seen, out = set(), []
    for k in kws:
        if k and k not in seen:
            seen.add(k); out.append(k)
    return out[:limit]


def target_keywords(industry_name: str, region: str, note: str = "", limit: int = 10,
                    axis: str = "local", brand: str = "") -> list[str]:
    """키워드 세트. axis='product'면 상품/후기축(셀러), 'both'면 지역+상품 병합, 기본은 지역축."""
    if axis == "product":
        return product_keywords(note, brand, limit)
    if axis == "both":
        merged = product_keywords(note, brand, limit) + target_keywords(industry_name, region, note, limit)
        return list(dict.fromkeys(merged))[:limit]
    kws: list[str] = []
    reg = (region or "").strip()
    ind = (industry_name or "").strip()
    if reg and ind:
        kws.append(f"{reg} {ind}")
        for it in _INTENTS:
            kws.append(f"{reg} {ind} {it}")
    if ind:
        kws += [f"{ind} 추천", f"{ind} 가격"]
    # 메모에서 핵심 명사 추출(신메뉴/차종/시술명 등)
    for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", note or ""):
        if w not in ("추천", "이벤트", "할인") and len(w) <= 12:
            cand = f"{reg} {w}".strip()
            if cand and cand not in kws:
                kws.append(cand)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k); out.append(k)
    return out[:limit]


# ── 네이버 플레이스(지도) 노출 보조 ──
def place_keywords(industry_name: str, region: str, limit: int = 12) -> list[str]:
    """플레이스 업체명·소개·메뉴·리뷰에 녹일 키워드(지역+업종+의도). 검색 매칭↑."""
    reg, ind = (region or "").strip(), (industry_name or "").strip()
    out: list[str] = []
    if reg and ind:
        out += [f"{reg} {ind}", f"{reg} {ind} 추천", f"{reg} {ind} 잘하는곳",
                f"{reg} {ind} 예약", f"{reg} {ind} 후기", f"{reg} 가까운 {ind}"]
    if ind:
        out += [f"{ind} 추천", f"{ind} 잘하는곳", f"{ind} 가격"]
    seen, res = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k); res.append(k)
    return res[:limit]


def review_request(tenant_name: str, region: str = "", industry: str = "") -> str:
    """방문자 영수증 리뷰 요청 문구 — 플레이스 노출의 핵심 연료(리뷰 수·키워드)."""
    kw = (f"{region} {industry}").strip() or "후기"
    name = tenant_name or "저희 가게"
    return ("방문해 주셔서 감사합니다! 🙏\n"
            f"도움이 되셨다면 네이버에 '{name}' 검색 → 영수증 리뷰 한 줄만 남겨주세요.\n"
            f"리뷰에 '{kw}' 키워드를 자연스럽게 적어주시면 다른 분들도 더 쉽게 찾을 수 있어요. 💙")


# ── 플랫폼별 성과/SEO 디렉티브(프롬프트 주입) ──
BLOG_DIRECTIVES = (
    "[네이버 상위노출 알고리즘 분석 → 반영 필수]\n"
    "네이버는 C-Rank(주제전문성40%·활동지속성30%·사용자반응20%·품질10%) + "
    "D.I.A.(실제 '경험·후기'를 높게 평가)로 순위를 매긴다. 그래서:\n"
    "- **1인칭 실제 경험·후기체**로 써라('직접 가봤더니', '먹어보니', '시공해보니') → D.I.A 가점.\n"
    "- 한 주제를 깊게(전문성), 곁가지 잡담 금지 → C-Rank 전문성.\n"
    "- 댓글·질문을 부르는 문장 1개(예: '○○ 더 궁금하면 댓글 주세요') → 사용자반응.\n"
    "- 제목: **핵심 키워드를 맨 앞**에(예: '지역+업종+추천/후기'), 25~35자 롱테일. 숫자·연도·혜택 넣으면 클릭↑.\n"
    "- **첫 문장에 핵심 키워드 1회**(검색 의도 즉시 충족, 2~3문장 인트로).\n"
    "- **연관 검색어**(같은 의도의 변형어 2~3개)를 자연스럽게 본문에 녹여라 → D.I.A+ 가점.\n"
    "- **'## 자주 묻는 질문' 섹션 1개**(Q&A 2~3쌍, '저장각' 정보) → 네이버 Q&A·체류 가점.\n"
    "- 분량 1200~1800자, 소제목(##) 3~5개, 타겟 키워드 4~6회(남발 금지).\n"
    "- 신뢰·체류↑: 가격대·찾아오는길·영업시간·주차·예약을 표/목록으로.\n"
    "- [사진N] 마커를 본문 곳곳(체류↑). 마지막 방문/예약 CTA+연락 안내.\n"
    "[저품질·스팸 회피(어기면 3페이지로 추락)]\n"
    "- 과장·낚시·광고성 표현 금지: 최고/최저가/100%/무조건/보장/완벽/대박/강력추천/유일/1위/공짜.\n"
    "- 같은 키워드 7회+ 남발 금지, 복사·짜깁기 금지, 실시간 이슈 억지 삽입 금지, 느낌표 남발 금지."
)

SHORT_DIRECTIVES = (
    "[릴스/쇼츠 알고리즘 분석 → 반영 필수]\n"
    "배포 1위 신호는 '시청 유지(watch time)'. 3초 홀드율 60%+면 도달이 5~10배. "
    "DM 공유(sends)·저장(saves)은 좋아요보다 3~5배 중요. 그래서:\n"
    "- 0~3초 훅: 첫 프레임부터 질문/충격/공감으로 스크롤을 멈춰라('○○ 이거 모르면 손해').\n"
    "- 길이 7~30초(완주율↑·바이럴). 빠른 전개, 지루한 구간 0.\n"
    "- '저장각' 정보(꿀팁) 1개 + '친구 태그/공유' 유도 → sends·saves↑.\n"
    "- 제목/설명에 검색 키워드(유튜브=검색엔진), 해시태그 3~5개.\n"
    "- 자막은 무음 시청 대비 큰 글씨. 마지막 1.5초 명확한 CTA."
)

CAPTION_DIRECTIVES = (
    "[인스타 알고리즘 분석 → 반영 필수]\n"
    "도달 핵심은 watch + sends(DM 공유)·saves. 그래서:\n"
    "- 첫 줄 훅('더보기' 전 노출)로 시선 잡기.\n"
    "- '저장각' 유용함(팁/정보) 1개 + 'DM/공유하고 싶은' 한 줄 → saves·sends↑.\n"
    "- 해시태그 대형1~2+중형3~4+지역 니치2~3 믹스(검색 키워드 자연 포함).\n"
    "- 마지막 방문/문의 CTA. 과장·낚시 표현 금지."
)

X_DIRECTIVES = (
    "[X 알고리즘] 초반 인게이지먼트 속도가 노출을 좌우. 첫 문장 훅, 한 가지 핵심 메시지, "
    "리트윗/답글 부르는 한 줄, 해시태그 1~2개, 방문/문의 유도. 280자 이내. 과장 금지."
)

# ── 저품질/스팸 위험 표현(휴리스틱, 공식 목록 아님) ──
RISKY_EXPRESSIONS = [
    "최고", "최저가", "100%", "무조건", "보장", "완벽", "대박", "강력추천",
    "절대", "유일", "1위", "공짜", "무료나눔", "지금당장", "한정특가", "폭탄세일",
    "초대박", "역대급", "클릭", "꼭 사세요",
]


def keywords_line(kws: list[str]) -> str:
    return "[타겟 키워드(이 키워드로 검색 상위·전환을 노림)] " + ", ".join(kws) if kws else ""


# 경험/후기 신호(D.I.A 가점)
_EXPERIENCE_WORDS = ["후기", "직접", "경험", "먹어보", "써보", "방문", "가봤", "시공해", "느꼈"]


def quality_audit(channel: str, kind: str, payload: dict) -> dict:
    """분석된 랭킹 요인 기준으로 콘텐츠를 채점(0~100) + 개선 경고.
    휴리스틱(공식 알고리즘 비공개) — 상위노출 확률을 높이는 방향 점검."""
    text = (payload.get("body") or payload.get("text") or "")
    warnings: list[str] = []
    score = 100

    # 공통: 저품질/과장 표현
    hits = [w for w in RISKY_EXPRESSIONS if w in text]
    if hits:
        warnings.append(f"과장·광고성 표현 {hits[:5]} → 저품질/스팸 위험")
        score -= min(25, 6 * len(hits))
    if text.count("!") >= 5 or "!!!" in text:
        warnings.append("느낌표 남발 → 스팸 신호")
        score -= 5
    # 키워드 남발(스터핑)
    for kw in (payload.get("target_keywords") or [])[:3]:
        if kw and text.count(kw) > 6:
            warnings.append(f"'{kw}' {text.count(kw)}회 과다반복(남발)")
            score -= 10

    if kind == "blog":
        title = payload.get("title", "")
        main_kw = (payload.get("target_keywords") or [""])[0]
        if main_kw and main_kw not in title:
            warnings.append(f"제목에 핵심키워드 '{main_kw}' 없음 → 상위노출 크게 불리")
            score -= 12
        if main_kw and main_kw not in text[:120]:
            warnings.append("첫 문단에 핵심키워드 없음 → 검색의도 매칭 약함")
            score -= 6
        if main_kw and text.count(main_kw) < 2:
            warnings.append(f"핵심키워드 '{main_kw}' 본문 노출 부족(2회↓)")
            score -= 6
        if not any(s in text for s in ("자주 묻는", "Q&A", "Q.", "Q1")):
            warnings.append("FAQ(자주 묻는 질문) 없음 → Q&A·체류 가점 놓침")
            score -= 4
        if len(text) < 1000:
            warnings.append(f"본문 {len(text)}자 < 1000 (체류시간↓ → C-Rank 불리)")
            score -= 15
        if "##" not in text:
            warnings.append("소제목(##) 없음 → 구조/가독성 약함")
            score -= 5
        if "[사진1]" not in text:
            warnings.append("사진 마커 없음 → 체류시간↓")
            score -= 5
        if not any(w in text for w in _EXPERIENCE_WORDS):
            warnings.append("실제 경험·후기 표현 약함 → D.I.A 불리")
            score -= 12
    elif kind in ("short",):
        if not payload.get("hook_strategy"):
            warnings.append("0~3초 훅 없음 → 시청유지↓")
            score -= 15
        d = payload.get("duration_sec", 0)
        if d and d > 35:
            warnings.append(f"{d}s > 35 → 완주율↓(7~30초 권장)")
            score -= 6
    elif kind == "caption":
        if text.count("#") < 5:
            warnings.append("해시태그 부족(<5)")
            score -= 5
    elif kind == "x_post":
        if len(text) > 280:
            warnings.append("280자 초과")
            score -= 10

    score = max(0, min(100, score))
    grade = "우수" if score >= 85 else ("양호" if score >= 70 else "개선필요")
    return {"score": score, "grade": grade, "warnings": warnings}
