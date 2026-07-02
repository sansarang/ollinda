"""
콘텐츠 생성기 인터페이스 — 원재료(Asset) → 채널별 ContentPiece.
텍스트/이미지/영상 생성기를 동일 계약 뒤에 둔다(교체 가능).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import Asset, ContentKind, ContentPiece, Tenant


class Generator(ABC):
    """하나의 원재료에서 특정 종류(kind)의 콘텐츠를 생성."""

    kind: ContentKind

    @abstractmethod
    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        """업종(tenant.industry) 톤에 맞춰 채널 콘텐츠 초안 생성.
        images: 업로드된 사진 경로들(여러 장). None이면 [asset.path] 사용.
        텍스트 생성기는 첫 장을 대표로, 숏 영상 생성기는 전부를 슬라이드쇼로."""
        raise NotImplementedError
