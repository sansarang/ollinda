"""
X(트위터) 단문 생성기 — 280자 이내, 페르소나 반영, 해시태그 2~4개.
"""
from __future__ import annotations

import uuid

from app.domain.models import Asset, Channel, ContentKind, ContentPiece, ContentStatus, Tenant
from app.generators.base import Generator
from app.generators.text_claude import MODEL, _call_llm
from app.industries import resolve_industry
from app.strategies import resolve_strategy, buy_block
from app import seo


class XPostGenerator(Generator):
    kind = ContentKind.X_POST

    def __init__(self, model: str = MODEL):
        self.model = model

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        imgs = images or [asset.path]
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        kws = seo.target_keywords(prof.name, tenant.region, asset.note,
                                  axis=strat.keyword_axis, brand=tenant.brand_name)
        buy = buy_block(tenant)
        buy_line = f"\n[구매 안내] {buy}" if buy else ""
        prompt = (
            f"[가게] {tenant.name} ({prof.name}, {tenant.region})\n"
            f"[사업형태] {strat.label}\n[페르소나] {prof.persona}\n[입력 정보] {asset.note}\n"
            f"[CTA] {strat.cta}{buy_line}\n"
            f"{seo.keywords_line(kws)}\n\n{seo.X_DIRECTIVES}\n\n"
            "X(트위터)용 단문을 한국어로 작성하라. 한 덩어리 텍스트로만 출력."
        )
        text = _call_llm(prompt, self.model, 400)[:280]
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.X, kind=self.kind,
            payload={"text": text, "image_path": imgs[0], "image_paths": imgs[:4],
                     "target_keywords": kws},  # X 미디어 최대 4
            status=ContentStatus.DRAFT)
