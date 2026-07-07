"""
파일 스토리지 — 로컬 저장 + (설정 시) Cloudflare R2 미러링.

R2는 S3 호환. 아래 env 설정 시 자동 활성화(미설정이면 로컬만 사용):
  R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, R2_PUBLIC_URL
  (R2_PUBLIC_URL = 버킷 공개 도메인, 예: https://media.ollinda.kr 또는 r2.dev URL)

동작:
  - 사진 업로드/영상·이미지 생성물은 로컬에 저장(ffmpeg·비전이 로컬 파일 필요)하고, R2에도 미러 업로드.
  - 서빙(/dl, /d)은 로컬에 있으면 로컬, 없으면(자동정리로 삭제됨) R2 공개 URL로 리다이렉트.
  → 로컬 볼륨은 최근 것만 유지해 꽉 차지 않고, 오래된 것도 R2에서 영구 서빙.
"""
from __future__ import annotations

import os
import uuid

STORAGE_DIR = os.environ.get("SHOPCAST_STORAGE", "storage")

_client = None


def r2_configured() -> bool:
    return all(os.environ.get(k) for k in
               ("R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET", "R2_PUBLIC_URL"))


def _r2():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY"],
            aws_secret_access_key=os.environ["R2_SECRET_KEY"],
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 2}))
    return _client


def _content_type(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower()
    return {"mp4": "video/mp4", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp", "mp3": "audio/mpeg"}.get(ext, "application/octet-stream")


def _key_for(local_path: str) -> str:
    """로컬 경로 → R2 키. STORAGE_DIR/{tenant}/{fname} → {tenant}/{fname}."""
    rel = os.path.relpath(local_path, STORAGE_DIR)
    return rel.replace(os.sep, "/")


def mirror_to_r2(local_path: str) -> str | None:
    """로컬 파일을 R2에 미러 업로드. 성공 시 키, 아니면 None."""
    if not r2_configured() or not (local_path and os.path.exists(local_path)):
        return None
    key = _key_for(local_path)
    try:
        _r2().upload_file(local_path, os.environ["R2_BUCKET"], key,
                          ExtraArgs={"ContentType": _content_type(local_path)})
        return key
    except Exception:
        import logging
        logging.exception("[r2] 업로드 실패 %s", local_path)
        return None


def r2_media_url(tenant_id: str, fname: str) -> str | None:
    """서빙용 — 로컬에 없을 때 R2 공개 URL(리다이렉트 대상)."""
    if not r2_configured():
        return None
    return os.environ["R2_PUBLIC_URL"].rstrip("/") + f"/{tenant_id}/{fname}"


def save_upload(data: bytes, filename: str, tenant_id: str) -> str:
    """업로드 바이트를 tenant별 폴더에 저장(+R2 미러) 후 로컬 경로 반환."""
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    d = os.path.join(STORAGE_DIR, tenant_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, uuid.uuid4().hex + ext)
    with open(path, "wb") as f:
        f.write(data)
    mirror_to_r2(path)                 # R2에도 사본(설정 시)
    return path
