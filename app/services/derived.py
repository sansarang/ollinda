"""
🗂 파생본 경로 단일 소스(2026-08-03 조항: 생성 경로와 참조 경로는 같은 함수 하나를 쓴다).

사고 배경: 썸네일을 '화면이 요청할 때' 만들도록 했더니, 아직 안 본 옛 세트는 첫 로드가
그대로 느렸다. 일괄 데우기를 붙이려 보니 경로 규칙이 화면(라우트)에만 있었다 —
데우기가 자기 규칙으로 만들면 '만들었는데 화면은 못 찾는' 어긋남이 난다.
그래서 경로는 이 함수 하나만 쓴다(canonical 원칙의 경로판).

대조도 이 함수 기준으로 한다: '만든 수'가 아니라 '화면이 요청하는 그 경로에 있는가'.
"""
from __future__ import annotations

import os

THUMB_PX = 320          # 목록 썸네일 한 변(56px 표시 × 레티나 여유)
THUMB_DIR = ".thumbs"


def storage_root() -> str:
    return os.environ.get("SHOPCAST_STORAGE", "storage")


def original_path(tenant_id: str, fname: str) -> str:
    """원본 — 상세·다운로드에서만 쓴다."""
    return os.path.join(storage_root(), tenant_id, fname)


def thumb_path(tenant_id: str, fname: str) -> str:
    """썸네일 파생본. ★ 라우트도 데우기도 대조도 전부 이 함수를 쓴다."""
    return os.path.join(storage_root(), tenant_id, THUMB_DIR,
                        os.path.splitext(fname)[0] + ".jpg")


def has_thumb(tenant_id: str, fname: str) -> bool:
    """화면이 요청할 그 경로에 실제로 있는가 — 대조의 유일한 기준."""
    return os.path.exists(thumb_path(tenant_id, fname))


def make_thumb(tenant_id: str, fname: str) -> bool:
    """원본에서 썸네일 생성(이미 있으면 그대로 True). 실패는 False — 조용히 채우지 않는다."""
    tp = thumb_path(tenant_id, fname)
    if os.path.exists(tp):
        return True
    src = original_path(tenant_id, fname)
    if not os.path.exists(src):
        return False
    try:
        from PIL import Image
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        im = Image.open(src)
        im.thumbnail((THUMB_PX, THUMB_PX))
        im.convert("RGB").save(tp, "JPEG", quality=72, optimize=True)
        return True
    except Exception:
        import logging
        logging.getLogger("shopcast.derived").exception("[derived] 썸네일 생성 실패 %s", fname)
        return False
