"""
채널 발행 어댑터 인터페이스.
모든 채널(IG/YouTube/Naver/Kakao)은 이 인터페이스 뒤에 격리된다.
→ 채널 정책이 바뀌거나 외부 라이브러리를 교체해도 코어는 영향 없음.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import ChannelAccount, ContentPiece, PublishResult


class Publisher(ABC):
    """채널 발행 어댑터의 공통 계약."""

    #: 이 어댑터가 실제 자동 발행을 지원하는지(네이버는 False = 반자동).
    supports_auto_publish: bool = True

    @abstractmethod
    def validate(self, content: ContentPiece) -> list[str]:
        """발행 전 채널 규칙 검증. 위반 사유 리스트 반환(빈 리스트=통과).
        예) IG 릴스 90초 초과, 알림톡 광고성 미동의 등."""
        raise NotImplementedError

    @abstractmethod
    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        """승인된 콘텐츠를 채널에 발행. 멱등성을 고려해 구현."""
        raise NotImplementedError


class ManualPublisher(Publisher):
    """반자동 채널(네이버 블로그 등): 자동 발행 대신 사람이 발행하도록 초안을 내보낸다."""

    supports_auto_publish = False

    @abstractmethod
    def export_draft(self, content: ContentPiece) -> dict:
        """사람이 그대로 붙여넣어 발행할 수 있는 초안(제목/본문/이미지 등) 반환."""
        raise NotImplementedError

    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:  # noqa: D102
        return PublishResult(ok=False, error="manual_channel: export_draft 사용(사람 발행)")
