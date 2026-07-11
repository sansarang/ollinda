"""
shopcast 도메인 모델 — 시스템 전체가 공유하는 핵심 엔터티/열거형.
ORM 이전의 순수 도메인 표현(영속화는 db 레이어가 담당).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# ── 열거형 ──────────────────────────────────────────────
class Channel(str, enum.Enum):
    INSTAGRAM = "instagram"   # Meta Graph API (자동) — 피드/릴스
    YOUTUBE = "youtube"       # YouTube Data API (자동/예약) — 쇼츠
    NAVER_BLOG = "naver_blog"  # Playwright 자동 or 반자동(초안 export)
    X = "x"                   # X(Twitter) API v2
    KAKAO_ALIMTALK = "kakao_alimtalk"  # 비즈메시지(정보성 자동/광고성 동의)
    MARKETPLACE = "marketplace"  # 셀러 판매 플랫폼(쿠팡·스마트스토어·11번가)


class AssetType(str, enum.Enum):
    IMAGE = "image"
    VIDEO = "video"


class ContentKind(str, enum.Enum):
    CAPTION = "caption"        # 인스타 캡션+해시태그(+릴스 영상)
    SHORT = "short"            # 유튜브 숏(스크립트+자막영상)
    BLOG = "blog"              # 네이버 블로그 초안
    X_POST = "x_post"          # X(트위터) 단문
    ALIMTALK = "alimtalk"      # 카카오 메시지
    MARKETPLACE = "marketplace"  # 셀러 판매 플랫폼(상품명·상세페이지·태그)


class ContentStatus(str, enum.Enum):
    DRAFT = "draft"            # AI 생성 직후
    APPROVED = "approved"      # 사람 검수 통과
    REJECTED = "rejected"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"


class MessageClass(str, enum.Enum):
    INFO = "info"              # 정보성: 예약확인/리마인드 (수신동의 불필요)
    AD = "ad"                  # 광고성: 동의자/6개월내 기존고객만, 야간 별도동의


# ── 엔터티 ──────────────────────────────────────────────
@dataclass
class Tenant:
    """가게(멀티테넌트의 단위)."""
    id: str
    name: str
    industry: str             # 업종(미용실/치과/학원...)
    region: str = ""
    phone: str = ""           # 연락처(블로그 자동 삽입)
    address: str = ""         # 주소(장소 블록)
    hours: str = ""           # 영업시간
    parking: str = ""         # 주차 안내(블로그 고정정보 블록 재사용)
    map_url: str = ""         # 네이버 지도 링크
    autonomy: int = 0         # 0=수동검수 1=점수게이트 자동 2=완전자동
    # ── 사업형태(분류축) — strategies.py가 사용 ──
    biz_type: str = "local"   # local=동네매장 / seller=온라인셀러 / hybrid=둘다
    marketplace: str = ""     # seller: coupang/11st/smartstore/gmarket/self
    buy_url: str = ""         # seller: 상세페이지/스토어 URL(직링크 가능 시)
    search_kw: str = ""       # seller: "쿠팡에서 OO 검색" 유도용 키워드
    brand_name: str = ""      # seller: SNS 노출 브랜드/스토어명
    publish_schedule: int = 0  # 대행 운영: 주간 발행 목표 횟수(0=미설정)
    lat: Optional[float] = None  # 가게 위도(사진 GPS 지오태그)
    lon: Optional[float] = None  # 가게 경도
    topic_axis: str = ""      # 전문 주제 축 — 이 블로그가 밀 핵심 주제/키워드군(C-Rank)
    naver_blog_url: str = ""  # 사용자 네이버 블로그 URL(수동 발행 추적의 기준점)
    blog_id: str = ""         # 정규화된 네이버 블로그 아이디(RSS·검색결과 정확 식별)
    created_at: Optional[datetime] = None


@dataclass
class ChannelAccount:
    """가게별 채널 연동 계정. 토큰은 암호화 저장(여기선 평문 금지)."""
    id: str
    tenant_id: str
    channel: Channel
    access_token_enc: str = ""
    refresh_token_enc: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    status: str = "active"


@dataclass
class Asset:
    """사장님이 올린 원재료(사진/영상 + 한 줄 메모)."""
    id: str
    tenant_id: str
    type: AssetType
    path: str
    note: str = ""            # "오늘 신메뉴 ___ 출시" 같은 한 줄
    created_at: Optional[datetime] = None


@dataclass
class ContentPiece:
    """원재료 1개에서 파생된 채널별 생성물."""
    id: str
    tenant_id: str
    asset_id: str
    channel: Channel
    kind: ContentKind
    payload: dict[str, Any] = field(default_factory=dict)  # text/해시태그/영상경로/자막...
    status: ContentStatus = ContentStatus.DRAFT
    scheduled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class Publication:
    """실제 발행 이력(채널 외부 ID/결과)."""
    id: str
    content_id: str
    channel: Channel
    external_id: str = ""
    published_at: Optional[datetime] = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class PublishResult:
    """어댑터가 반환하는 발행 결과(성공/실패 표준형)."""
    ok: bool
    external_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    error: str = ""
