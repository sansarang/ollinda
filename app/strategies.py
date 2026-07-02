"""
사업형태(biz_type) 전략 레이어 — '어디서·어떻게 파는가' 축.

업종(industries.py)이 "무엇을 파는가(톤/해시태그/페르소나)"를 결정한다면,
이 모듈은 "어디서 파는가(CTA·글 마무리·채널믹스·키워드축·링크정책)"를 결정한다.
  · local  = 동네 매장 소상공인 → 매장 방문/예약, 글 끝에 지도+연락처
  · seller = 온라인 셀러(쿠팡/11번가/스토어) → 구매 유도, 글 끝에 구매 링크/검색어
  · hybrid = 둘 다
최종 콘텐츠 = IndustryProfile(업종) × ChannelStrategy(사업형태).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChannelStrategy:
    key: str                       # 'local' | 'seller' | 'hybrid'
    label: str                     # 표시명
    goal: str                      # 한 줄 목표(설명/대시보드용)
    cta: str                       # 행동유도 지시문(생성 프롬프트 주입)
    closing: str                   # 글 마무리 블록: 'map' | 'buy' | 'both'
    keyword_axis: str              # 'local'(지역) | 'product'(상품/후기) | 'both'
    channel_priority: list[str]    # 채널 노출 우선순위(표시용)
    # 생성·발행 순서(ContentKind 값) — 셀러는 영상(short) 우선
    content_order: list[str] = field(default_factory=lambda: ["blog", "caption", "short", "x_post"])
    notes: list[str] = field(default_factory=list)  # 생성/운영 주의


LOCAL = ChannelStrategy(
    key="local", label="동네 매장(소상공인)",
    goal="동네 손님을 '매장 방문·전화·예약'으로 데려온다",
    cta="매장 방문·전화·예약을 자연스럽게 유도하라(가격대/영업시간/주차/찾아오는길 안내 포함).",
    closing="map", keyword_axis="local",
    channel_priority=["naver_blog", "instagram", "youtube", "x"],
    content_order=["blog", "caption", "short", "x_post"],   # 소상공인: 블로그(지역검색) 우선
    notes=["지역명 키워드 필수(예: '부산 초량 OO')", "글 끝에 지도+연락처 블록"],
)

SELLER = ChannelStrategy(
    key="seller", label="온라인 셀러(쿠팡·11번가·스토어)",
    goal="검색·SNS에서 만난 손님을 '상품 상세페이지'로 데려와 구매시킨다",
    cta=("상품 '구매'를 유도하라. 광고 티 내지 말고 실사용 후기처럼. "
         "마지막에 구매처(스토어/검색어)로 자연스럽게 연결하고 찜·후기를 권하라."),
    closing="buy", keyword_axis="product",
    channel_priority=["instagram", "youtube", "naver_blog", "x"],
    content_order=["short", "caption", "blog", "x_post"],   # 셀러: 영상(릴스/쇼츠) 우선
    notes=["지역명 대신 '상품명+추천/후기' 키워드", "글 끝에 구매 링크/검색어 블록",
           "쿠팡은 외부 직링크 정책상 '검색어 유도'가 안전"],
)

HYBRID = ChannelStrategy(
    key="hybrid", label="매장+온라인 동시",
    goal="동네 방문과 온라인 구매를 함께 유도한다",
    cta="매장 방문(예약)과 온라인 구매를 함께 안내하라. 가까운 손님은 방문, 먼 손님은 온라인 구매로.",
    closing="both", keyword_axis="both",
    channel_priority=["instagram", "naver_blog", "youtube", "x"],
    content_order=["short", "caption", "blog", "x_post"],   # 매장+온라인: 영상 우선
    notes=["지역 + 상품 키워드 병행", "글 끝에 지도+연락처 그리고 구매 링크 모두"],
)

_BY_KEY = {s.key: s for s in (LOCAL, SELLER, HYBRID)}

# 마켓플레이스별 외부 직링크 가능 여부(현실 정책 반영)
MARKETPLACES = {
    "coupang":     {"name": "쿠팡",        "direct_link": False},  # 외부 직링크 제약 큼 → 검색어 유도
    "11st":        {"name": "11번가",      "direct_link": True},
    "smartstore":  {"name": "스마트스토어", "direct_link": True},
    "gmarket":     {"name": "지마켓",      "direct_link": True},
    "self":        {"name": "자사몰",      "direct_link": True},
    "":            {"name": "온라인",      "direct_link": True},
}


def resolve_strategy(tenant) -> ChannelStrategy:
    """tenant.biz_type → 전략. 미지정/미상이면 LOCAL(기존 동작 보존)."""
    bt = (getattr(tenant, "biz_type", "") or "local").strip().lower()
    return _BY_KEY.get(bt, LOCAL)


def buy_block(tenant) -> str:
    """SELLER/HYBRID의 글 마무리 '구매 유도' 문구를 마켓 정책에 맞게 생성.
    쿠팡 등 직링크 불가면 '검색어 유도', 가능하면 링크. LOCAL이면 빈 문자열."""
    bt = (getattr(tenant, "biz_type", "") or "local").strip().lower()
    if bt not in ("seller", "hybrid"):
        return ""
    mk = (getattr(tenant, "marketplace", "") or "").strip().lower()
    info = MARKETPLACES.get(mk, MARKETPLACES[""])
    brand = (getattr(tenant, "brand_name", "") or getattr(tenant, "name", "") or "").strip()
    url = (getattr(tenant, "buy_url", "") or "").strip()
    kw = (getattr(tenant, "search_kw", "") or "").strip()
    if info["direct_link"] and url:
        return f"▶ 구매하기 ({info['name']}): {url}"
    if kw:
        return f"▶ 구매: {info['name']}에서 '{kw}' 검색"
    if brand:
        return f"▶ 구매: {info['name']}에서 '{brand}' 검색"
    return f"▶ 구매: {info['name']} 스토어에서 확인"


def ordered_kinds(strat: ChannelStrategy, kinds: list) -> list:
    """ContentKind 리스트를 전략의 content_order(생성·발행 우선순위)대로 정렬.
    셀러 → 영상(short) 우선, 소상공인 → 블로그(blog) 우선."""
    order = strat.content_order
    return sorted(kinds, key=lambda k: order.index(getattr(k, "value", k))
                  if getattr(k, "value", k) in order else 99)


def kind_rank(strat: ChannelStrategy, kind_value: str) -> int:
    """발행 정렬용 — content_order 내 위치(작을수록 먼저). 없는 종류는 뒤로."""
    return strat.content_order.index(kind_value) if kind_value in strat.content_order else 99


def classify_biz_type(industry: str = "", note: str = "", has_address: bool = False,
                      has_url: bool = False, marketplace: str = "") -> dict:
    """규칙 기반 사업형태 자동 추정(보조). 최종 확정은 사용자 몫.
    return {biz_type, confidence(0~1), reason}."""
    text = f"{industry} {note}".lower()
    seller_signals = ["쿠팡", "11번가", "스마트스토어", "지마켓", "옥션", "스토어", "택배",
                      "배송", "상세페이지", "셀러", "온라인 판매", "오픈마켓", "무료배송"]
    local_signals = ["방문", "예약", "매장", "오시는", "영업시간", "주차", "동네", "근처", "내점"]
    s_hit = [w for w in seller_signals if w in text]
    l_hit = [w for w in local_signals if w in text]
    if marketplace or has_url:
        return {"biz_type": "seller", "confidence": 0.9,
                "reason": "마켓플레이스/상세페이지 URL 입력됨"}
    if s_hit and not l_hit:
        return {"biz_type": "seller", "confidence": 0.75,
                "reason": f"온라인 판매 신호: {s_hit[:3]}"}
    if has_address or (l_hit and not s_hit):
        return {"biz_type": "local", "confidence": 0.75,
                "reason": "주소/방문 신호" + (f": {l_hit[:3]}" if l_hit else "")}
    if s_hit and l_hit:
        return {"biz_type": "hybrid", "confidence": 0.6, "reason": "온라인+매장 신호 혼재"}
    return {"biz_type": "local", "confidence": 0.4, "reason": "신호 약함 → 기본값(매장)"}
