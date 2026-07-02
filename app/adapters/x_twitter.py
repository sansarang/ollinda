"""
X(트위터) 발행 어댑터 — X API v2.
- 텍스트: POST https://api.x.com/2/tweets (OAuth2 Bearer, scope tweet.write)
- 미디어: POST https://api.x.com/2/media/upload 3단계(INIT/APPEND/FINALIZE) → media_ids 첨부
  (scope media.write 필요). 토큰 없으면 시뮬.
docs: https://docs.x.com/x-api/posts/create-post
"""
from __future__ import annotations

import os

from app.adapters.base import Publisher
from app.domain.models import ChannelAccount, ContentPiece, PublishResult

TWEET_URL = "https://api.x.com/2/tweets"
MEDIA_URL = "https://api.x.com/2/media/upload"
MAX_LEN = 280


class XPublisher(Publisher):
    supports_auto_publish = True

    def validate(self, content: ContentPiece) -> list[str]:
        errors: list[str] = []
        text = content.payload.get("text", "")
        if not text:
            errors.append("본문 없음")
        if len(text) > MAX_LEN:
            errors.append(f"280자 초과({len(text)})")
        return errors

    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        if not account.access_token_enc:
            import uuid
            return PublishResult(ok=True, external_id="SIM-X-" + uuid.uuid4().hex[:8],
                                 detail={"simulated": True,
                                         "note": "계정 미연결 → 시뮬. /admin/connect 에서 X 연결 시 실발행"})
        import requests
        token = account.access_token_enc
        body: dict = {"text": content.payload.get("text", "")}
        # 미디어(이미지/영상) 첨부 — 최대 4장(영상은 1개)
        media_ids = []
        media_path = content.payload.get("video_path")
        paths = [media_path] if media_path else (content.payload.get("image_paths") or [])
        for p in paths[:4]:
            mid = self._upload_media(token, p)
            if mid:
                media_ids.append(mid)
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        try:
            r = requests.post(TWEET_URL,
                              headers={"Authorization": f"Bearer {token}",
                                       "Content-Type": "application/json"},
                              json=body, timeout=30)
            r.raise_for_status()
            return PublishResult(ok=True, external_id=str(r.json().get("data", {}).get("id", "")),
                                 detail={"simulated": False, "media": len(media_ids)})
        except Exception as e:
            return PublishResult(ok=False, error=f"X 발행 실패: {str(e)[:120]}")

    def _upload_media(self, token: str, path: str | None) -> str | None:
        """3단계 청크 업로드. 실패 시 None(미디어 없이 텍스트만 발행)."""
        if not (path and os.path.exists(path)):
            return None
        import time
        import requests
        h = {"Authorization": f"Bearer {token}"}
        size = os.path.getsize(path)
        is_video = path.lower().endswith((".mp4", ".mov"))
        mtype = "video/mp4" if is_video else "image/jpeg"
        cat = "tweet_video" if is_video else "tweet_image"
        try:
            # INIT
            r = requests.post(MEDIA_URL, headers=h, data={
                "command": "INIT", "total_bytes": size,
                "media_type": mtype, "media_category": cat}, timeout=30)
            r.raise_for_status()
            mid = r.json().get("data", {}).get("id") or r.json().get("media_id_string")
            # APPEND (5MB 청크)
            with open(path, "rb") as f:
                seg = 0
                while True:
                    chunk = f.read(5 * 1024 * 1024)
                    if not chunk:
                        break
                    requests.post(MEDIA_URL, headers=h,
                                  data={"command": "APPEND", "media_id": mid, "segment_index": seg},
                                  files={"media": chunk}, timeout=60).raise_for_status()
                    seg += 1
            # FINALIZE
            fin = requests.post(MEDIA_URL, headers=h,
                                data={"command": "FINALIZE", "media_id": mid}, timeout=30).json()
            # 영상이면 처리 폴링
            info = fin.get("data", {}).get("processing_info") or fin.get("processing_info")
            for _ in range(20):
                if not info or info.get("state") == "succeeded":
                    break
                if info.get("state") == "failed":
                    return None
                time.sleep(info.get("check_after_secs", 3))
                st = requests.get(MEDIA_URL, headers=h,
                                  params={"command": "STATUS", "media_id": mid}, timeout=30).json()
                info = st.get("data", {}).get("processing_info")
            return mid
        except Exception:
            return None
