"""
레지스트리 — 채널↔어댑터, 종류↔생성기 매핑을 한 곳에서 관리.
새 채널/생성기는 여기에만 등록하면 코어가 자동 사용.
"""
from __future__ import annotations

from app.adapters.base import Publisher
from app.adapters.instagram import InstagramPublisher
from app.adapters.kakao_alimtalk import KakaoAlimtalkPublisher
from app.adapters.naver_blog import NaverBlogPublisher
from app.adapters.x_twitter import XPublisher
from app.adapters.youtube import YouTubePublisher
from app.domain.models import Channel, ContentKind
from app.generators.base import Generator
from app.generators.text_claude import BlogDraftGenerator, CaptionGenerator, MarketplaceGenerator
from app.generators.video import ShortVideoGenerator
from app.generators.x_text import XPostGenerator

PUBLISHERS: dict[Channel, Publisher] = {
    Channel.INSTAGRAM: InstagramPublisher(),
    Channel.YOUTUBE: YouTubePublisher(),
    Channel.NAVER_BLOG: NaverBlogPublisher(),
    Channel.X: XPublisher(),
    Channel.KAKAO_ALIMTALK: KakaoAlimtalkPublisher(),
}

GENERATORS: dict[ContentKind, Generator] = {
    ContentKind.CAPTION: CaptionGenerator(),     # 인스타(피드/릴스)
    ContentKind.BLOG: BlogDraftGenerator(),      # 네이버
    ContentKind.SHORT: ShortVideoGenerator(),    # 유튜브 숏/인스타 릴스
    ContentKind.X_POST: XPostGenerator(),        # X(트위터)
    ContentKind.MARKETPLACE: MarketplaceGenerator(),  # 셀러 판매 플랫폼(상품명·상세페이지·태그)
    # ContentKind.ALIMTALK: AlimtalkGenerator(),   # Phase 3
}


def get_publisher(channel: Channel) -> Publisher:
    return PUBLISHERS[channel]


def get_generator(kind: ContentKind) -> Generator:
    return GENERATORS[kind]
