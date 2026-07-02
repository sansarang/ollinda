"""
카카오 알림톡 어댑터 — 비즈메시지(공식 딜러 경유).
정보성(예약확인/리마인드/노쇼방지)은 자동 발송.
광고성(할인/이벤트/재방문)은 수신동의자 또는 거래후 6개월내 기존고객만 + 야간(21~08) 별도동의.
참고: references/MIT/node-kakao-alimtalk-bizmsg (MIT).  [Phase 3]
"""
from __future__ import annotations

from app.adapters.base import Publisher
from app.domain.models import ChannelAccount, ContentPiece, MessageClass, PublishResult


class KakaoAlimtalkPublisher(Publisher):
    supports_auto_publish = True

    def validate(self, content: ContentPiece) -> list[str]:
        errors: list[str] = []
        if not content.payload.get("template_code"):
            errors.append("승인된 템플릿 코드 없음(사전심사 필요)")
        # 정보통신망법: 광고성은 동의 확인 필수
        if content.payload.get("message_class") == MessageClass.AD.value:
            if not content.payload.get("consent_verified"):
                errors.append("광고성 메시지: 수신동의/6개월내 기존고객 미확인")
            if content.payload.get("night_send") and not content.payload.get("night_consent"):
                errors.append("야간(21~08) 광고: 별도 동의 없음")
        return errors

    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        # TODO(Phase3): 비즈메시지 딜러 API 호출(템플릿+변수)
        return PublishResult(ok=False, error="not_implemented: kakao_alimtalk.publish")
