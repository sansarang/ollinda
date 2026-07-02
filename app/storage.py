"""
파일 스토리지 (MVP: 로컬). 추후 S3/Cloudflare R2로 교체.
"""
from __future__ import annotations

import os
import uuid

STORAGE_DIR = os.environ.get("SHOPCAST_STORAGE", "storage")


def save_upload(data: bytes, filename: str, tenant_id: str) -> str:
    """업로드 바이트를 tenant별 폴더에 저장하고 경로 반환."""
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    d = os.path.join(STORAGE_DIR, tenant_id)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, uuid.uuid4().hex + ext)
    with open(path, "wb") as f:
        f.write(data)
    return path
