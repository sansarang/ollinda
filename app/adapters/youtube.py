"""
YouTube 숏 발행 어댑터 — YouTube Data API v3.
업로드 시 privacyStatus=private + status.publishAt 으로 예약발행.
세로영상 <60s 는 자동 Shorts 분류. 쿼터: 1만/일, 업로드 1,600 → ~6개/일.  [Phase 2]
"""
from __future__ import annotations

from app.adapters.base import Publisher
from app.domain.models import ChannelAccount, ContentPiece, PublishResult

UPLOAD_QUOTA_COST = 1600
DAILY_QUOTA = 10000


class YouTubePublisher(Publisher):
    supports_auto_publish = True

    def validate(self, content: ContentPiece) -> list[str]:
        errors: list[str] = []
        if not content.payload.get("video_path"):
            errors.append("영상 파일 없음(자막조립 실패 시 수동)")
        if not content.payload.get("title"):
            errors.append("제목 없음")
        return errors

    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        # 토큰 없으면 시뮬레이션(계정 연결 전까지 흐름 검증)
        if not account.access_token_enc:
            import uuid
            return PublishResult(ok=True, external_id="SIM-YT-" + uuid.uuid4().hex[:8],
                                 detail={"simulated": True,
                                         "note": "계정 미연결 → 시뮬. /admin/connect 에서 유튜브 연결 시 실업로드"})
        return self._publish_real(account, content)

    def _publish_real(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        """YouTube Data API v3 resumable 업로드. 세로<60s + #Shorts → 자동 Shorts 분류."""
        import json
        import os
        import requests
        from app import oauth

        token = oauth.refresh_youtube_token(account.refresh_token_enc) or account.access_token_enc
        video_path = content.payload.get("video_path")
        if not video_path or not os.path.exists(video_path):
            return PublishResult(ok=False, error="업로드할 영상 파일 없음")
        title = (content.payload.get("title") or "")[:95]
        desc = content.payload.get("script") or content.payload.get("text") or ""
        if "#shorts" not in (title + desc).lower():
            desc += "\n#Shorts"
        metadata = {"snippet": {"title": title, "description": desc,
                                "tags": content.payload.get("tags", []), "categoryId": "22"},
                    "status": {"privacyStatus": content.payload.get("privacy", "public"),
                               "selfDeclaredMadeForKids": False}}
        sched = content.payload.get("publish_at")
        if sched:
            metadata["status"]["privacyStatus"] = "private"
            metadata["status"]["publishAt"] = sched
        try:
            init = requests.post(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json; charset=UTF-8",
                         "X-Upload-Content-Type": "video/*"},
                data=json.dumps(metadata), timeout=30)
            init.raise_for_status()
            upload_url = init.headers.get("Location")
            if not upload_url:
                return PublishResult(ok=False, error="resumable 업로드 URL 없음")
            with open(video_path, "rb") as f:
                up = requests.put(upload_url, headers={"Authorization": f"Bearer {token}",
                                                       "Content-Type": "video/*"}, data=f, timeout=300)
            up.raise_for_status()
            return PublishResult(ok=True, external_id=str(up.json().get("id", "")),
                                 detail={"simulated": False})
        except Exception as e:
            return PublishResult(ok=False, error=f"youtube upload 실패: {str(e)[:120]}")
