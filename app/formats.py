"""
바이럴 포맷 프리셋 — '이미 터진 영상'의 검증된 구조(훅·전개)를 우리 콘텐츠에 '접목'.
영상 자체가 아니라 포맷(아이디어)을 차용 → 저작권 안전. 사이클연구소/머니피커식 양산의 핵심.
SHORT 생성기 프롬프트에 주입되어, 생성물이 검증된 후킹 구조를 따르게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ViralFormat:
    key: str
    name: str
    hook: str                      # 0~3초 훅 패턴(예시)
    structure: str                 # 전개 구조(프롬프트 주입)
    best_for: list[str] = field(default_factory=list)  # local/seller/hybrid
    triggers: list[str] = field(default_factory=list)   # 메모에 이 단어 있으면 우선


FORMATS: dict[str, ViralFormat] = {
    "price_shock": ViralFormat(
        "price_shock", "가격충격",
        hook="업체에서 OO만원 부르던데, 저는 이렇게 했어요",
        structure="①고가 견적/가격 충격 제시 ②'근데 직접 해보니/이 방법은' 반전 ③실제 결과·증거 ④구매·방문 CTA",
        best_for=["seller", "local"], triggers=["가격", "비용", "만원", "견적", "할인"]),
    "before_after": ViralFormat(
        "before_after", "비포애프터",
        hook="시공/사용 전 vs 후, 이 차이 실화?",
        structure="①before(문제 상황) ②과정 빠르게 ③극적인 after 클로즈업 ④'이렇게 됩니다' CTA",
        best_for=["local", "seller"], triggers=["시공", "전후", "변화", "효과", "결과"]),
    "mistake": ViralFormat(
        "mistake", "이거_모르면_손해",
        hook="OO 살 때/고를 때 이거 모르면 호구됩니다",
        structure="①흔한 실수·함정 지적(공감) ②올바른 기준 1~2개 ③우리 솔루션이 그 기준 충족 ④CTA",
        best_for=["seller", "local"], triggers=["추천", "고르", "선택", "기준", "주의"]),
    "honest_review": ViralFormat(
        "honest_review", "내돈내산_후기",
        hook="광고 아니고요, 진짜 써본 솔직 후기입니다",
        structure="①의심/반신반의 ②직접 사용 장면 ③장점+단점도 솔직히 ④그래도 추천하는 이유 CTA",
        best_for=["seller"], triggers=["후기", "리뷰", "내돈내산", "사용"]),
    "quick_tip": ViralFormat(
        "quick_tip", "3초_꿀팁",
        hook="이거 하나면 OO 끝납니다 (저장각)",
        structure="①0~3초 강한 한 줄 훅 ②핵심 꿀팁 1개 또렷이 ③'저장·공유' 유도 ④가게/상품 CTA",
        best_for=["local", "seller", "hybrid"], triggers=["꿀팁", "방법", "팁", "노하우"]),
}

DEFAULT_BY_BIZ = {"seller": "price_shock", "local": "before_after", "hybrid": "quick_tip"}


def pick_format(biz_type: str, note: str = "") -> ViralFormat:
    """메모 키워드 우선 → 사업형태 기본값. 검증된 포맷 1개 선택."""
    text = (note or "").lower()
    best = None
    for f in FORMATS.values():
        if biz_type in f.best_for and any(t in text for t in f.triggers):
            best = f
            break
    if not best:
        best = FORMATS.get(DEFAULT_BY_BIZ.get(biz_type or "local", "quick_tip"), FORMATS["quick_tip"])
    return best


def format_directive(fmt: ViralFormat) -> str:
    """SHORT 프롬프트 주입용 — 검증된 바이럴 포맷 강제."""
    return (f"[바이럴 포맷: {fmt.name}(검증된 터진 영상 구조 접목)]\n"
            f"- 훅(0~3초)은 이 패턴으로: \"{fmt.hook}\"\n"
            f"- 전개 구조: {fmt.structure}\n"
            f"- 이 구조를 반드시 따르되, 가게/상품에 맞게 자연스럽게 변형하라.")
