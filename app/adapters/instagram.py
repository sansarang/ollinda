"""
Instagram 발행 어댑터 — Meta Graph API (Business/Creator 계정).
발행 흐름: 미디어 컨테이너 생성 → 처리완료 폴링 → media_publish.
제약: 하루 100건, 릴스 90초/ MP4·H.264.  [Phase 1 구현 대상]
"""
from __future__ import annotations

from app.adapters.base import Publisher
from app.domain.models import ChannelAccount, ContentPiece, PublishResult

GRAPH_BASE = "https://graph.facebook.com/v21.0"
MAX_REELS_SECONDS = 90
DAILY_PUBLISH_LIMIT = 100


class InstagramPublisher(Publisher):
    supports_auto_publish = True

    def validate(self, content: ContentPiece) -> list[str]:
        errors: list[str] = []
        dur = content.payload.get("duration_sec")
        if dur and dur > MAX_REELS_SECONDS:
            errors.append(f"릴스 길이 {dur}s > {MAX_REELS_SECONDS}s 한도")
        # 실 Graph API는 공개 image_url/video_url 필요. MVP/시뮬은 로컬 image_path 허용.
        has_media = any(content.payload.get(k) for k in ("image_url", "video_url", "image_path"))
        if not has_media:
            errors.append("미디어 없음")
        return errors

    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        # 토큰 없으면 시뮬레이션(계정 연결 전까지 흐름 검증용) — govmatch sim 패턴
        if not account.access_token_enc:
            import uuid
            return PublishResult(ok=True, external_id="SIM-" + uuid.uuid4().hex[:8],
                                 detail={"simulated": True,
                                         "note": "계정 미연결 → 시뮬 발행. /admin/connect 에서 인스타 연결 시 실발행"})
        return self._publish_real(account, content)

    def _publish_real(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        """Instagram Graph API: 컨테이너 생성 → media_publish (이미지). 영상 폴링은 TODO."""
        import os
        import requests
        ig_id = account.meta.get("ig_user_id")
        token = account.access_token_enc
        if not ig_id:
            return PublishResult(ok=False, error="ig_user_id 없음(재연결 필요)")
        # 공개 미디어 URL 필요 — 없으면 서버의 /asset|/video 공개 URL 사용(배포 시 https)
        base = os.environ.get("SHOPCAST_BASE", "http://127.0.0.1:8000")
        caption = content.payload.get("text") or content.payload.get("title", "")
        # 영상이 있으면 릴스, 없으면 이미지
        if content.payload.get("video_path") or content.payload.get("video_url"):
            video_url = content.payload.get("video_url") or f"{base}/video/{content.id}"
            create = {"media_type": "REELS", "video_url": video_url,
                      "caption": caption, "access_token": token}
        else:
            image_url = content.payload.get("image_url") or f"{base}/asset/{content.id}"
            create = {"image_url": image_url, "caption": caption, "access_token": token}
        try:
            r1 = requests.post(f"https://graph.instagram.com/v21.0/{ig_id}/media",
                               data=create, timeout=30)
            r1.raise_for_status()
            creation_id = r1.json().get("id")
            r2 = requests.post(f"https://graph.instagram.com/v21.0/{ig_id}/media_publish",
                               data={"creation_id": creation_id, "access_token": token},
                               timeout=30)
            r2.raise_for_status()
            return PublishResult(ok=True, external_id=str(r2.json().get("id", "")),
                                 detail={"simulated": False})
        except Exception as e:
            return PublishResult(ok=False, error=f"instagram publish 실패: {str(e)[:120]}")
