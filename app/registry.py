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

# ★ 모델 하이브리드(속도) — 짧은 채널(캡션·X·마켓)은 Haiku(빠름·저렴, 품질 충분),
#   블로그는 Sonnet(Opus 4.8→Sonnet 5: 훨씬 빠르고 SEO 품질 유지). 저점수 시 SEO 편집장(polish)이 마감.
#   env로 조정 가능(문제 시 복귀). 채널 병렬화(generate_for)와 결합해 체감 속도 대폭↑.
import os as _os
from app.llm import HAIKU as _HAIKU

# ★ 긴급 복구 — 하이브리드 기본 OFF. blog=Sonnet/short=Haiku 전환 후 4장 생성이 240s+ 지연/행 실측
#   (원인 조사 전까지 알려진 정상 모델=Opus로 복귀). env로 재시도 가능(SHOPCAST_SHORT_MODEL 등).
from app.generators.text_claude import MODEL as _OPUS
_SHORT_MODEL = _os.environ.get("SHOPCAST_SHORT_MODEL", _OPUS)
_BLOG_MODEL = _os.environ.get("SHOPCAST_BLOG_MODEL", _OPUS)

GENERATORS: dict[ContentKind, Generator] = {
    ContentKind.CAPTION: CaptionGenerator(model=_SHORT_MODEL),     # 인스타(피드/릴스) — Haiku
    ContentKind.BLOG: BlogDraftGenerator(model=_BLOG_MODEL),       # 네이버 — Sonnet(품질 보호)
    ContentKind.SHORT: ShortVideoGenerator(),    # 유튜브 숏/인스타 릴스
    ContentKind.X_POST: XPostGenerator(model=_SHORT_MODEL),        # X(트위터) — Haiku
    ContentKind.MARKETPLACE: MarketplaceGenerator(model=_SHORT_MODEL),  # 셀러 판매 플랫폼 — Haiku
    # ContentKind.ALIMTALK: AlimtalkGenerator(),   # Phase 3
}


def get_publisher(channel: Channel) -> Publisher:
    return PUBLISHERS[channel]


def get_generator(kind: ContentKind) -> Generator:
    return GENERATORS[kind]
