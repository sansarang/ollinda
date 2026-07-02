"""
Publish 오케스트레이터 — 승인된 콘텐츠를 채널 어댑터로 분배.
- 발행 전 어댑터.validate()로 채널 규칙 검증
- 반자동 채널(supports_auto_publish=False)은 자동발행 대신 초안 export 안내
- 채널별 실패는 격리(한 채널 실패가 다른 채널을 막지 않음)
"""
from __future__ import annotations

from app import db
from app.adapters.base import ManualPublisher
from app.domain.models import (Channel, ChannelAccount, ContentPiece,
                               ContentStatus, PublishResult)
from app.registry import get_publisher


def publish_piece(account: ChannelAccount, content: ContentPiece) -> PublishResult:
    if content.status != ContentStatus.APPROVED:
        return PublishResult(ok=False, error=f"승인 안 됨(status={content.status.value})")

    pub = get_publisher(content.channel)

    errors = pub.validate(content)
    if errors:
        return PublishResult(ok=False, error="검증 실패: " + "; ".join(errors))

    if not pub.supports_auto_publish:
        # 네이버 등 반자동: 사람이 발행하도록 초안 제공(export_draft 보유 시)
        exporter = getattr(pub, "export_draft", None)
        draft = exporter(content) if callable(exporter) else {}
        return PublishResult(ok=True, detail={"manual": True, "draft": draft})

    return pub.publish(account, content)


def publish_and_record(content: ContentPiece) -> PublishResult:
    """검수 통과분을 발행하고 결과를 DB에 기록 + 상태 갱신(라우트에서 사용)."""
    account = db.get_channel_account(content.tenant_id, content.channel) \
        or ChannelAccount(id="", tenant_id=content.tenant_id, channel=content.channel)
    result = publish_piece(account, content)
    if result.ok and not result.detail.get("manual"):
        db.create_publication(content.id, content.channel, result.external_id, result.detail)
        db.set_piece_status(content.id, ContentStatus.PUBLISHED)
    elif not result.ok:
        db.set_piece_status(content.id, ContentStatus.FAILED)
    return result
