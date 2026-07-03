"""
Generate 서비스 — 원재료 1개 → 여러 채널 콘텐츠(초안) 생성.
'1소스 → 멀티채널' 변환의 진입점. 결과는 DRAFT 상태로 Review 큐에 들어간다.
"""
from __future__ import annotations

from app.domain.models import Asset, ContentKind, ContentPiece, Tenant
from app.registry import get_generator


def generate_for(tenant: Tenant, asset: Asset, kinds: list[ContentKind],
                 images: list[str] | None = None) -> list[ContentPiece]:
    """요청된 종류(kinds)별로 콘텐츠 초안을 생성한다. images=업로드된 사진 경로들(여러 장)."""
    pieces: list[ContentPiece] = []
    for kind in kinds:
        try:
            gen = get_generator(kind)   # 미등록이면 KeyError
            pieces.append(gen.generate(tenant, asset, images))
        except Exception:               # 한 채널 실패(예: AI 크레딧 부족)해도 나머지는 진행
            import logging
            logging.exception("[generate] %s 생성 실패", kind)
    return pieces
