"""
X(트위터) 단문 생성기 — 280자 이내, 페르소나 반영, 해시태그 2~4개.
"""
from __future__ import annotations

import uuid

from app.domain.models import Asset, Channel, ContentKind, ContentPiece, ContentStatus, Tenant
from app.generators.base import Generator
from app.generators.text_claude import MODEL, _call_llm, cache_prefix_for
from app.industries import resolve_industry, industry_brief
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
        _kw0x, kws = seo.resolve_target_keyword(   # 공유 관문(전 생성기 공통)
            industry=(getattr(tenant, "industry", "") or prof.name), region=tenant.region or "",
            note=asset.note or "", biz=(getattr(tenant, "biz_type", "local") or "local"),
            content_type=(getattr(asset, "content_type", "sell") or "sell"), brand=tenant.brand_name or "",
            keyword_axis=strat.keyword_axis, target_kw_override=(getattr(asset, "target_kw", "") or ""),
            tenant_id=tenant.id, prof_name=prof.name)
        buy = buy_block(tenant)
        # X는 외부 링크가 도달 50~90% 깎음(2026) → URL 제거하고 '검색/프로필' 유도만
        import re as _re
        buy_nolink = _re.sub(r"https?://\S+", "", buy or "").strip()
        buy_line = f"\n[구매 안내(링크 절대 넣지 말고 검색·프로필로 유도)] {buy_nolink}" if buy_nolink else ""
        prompt = (
            f"[가게] {tenant.name} ({prof.name}, {tenant.region})\n"
            f"[사업형태] {strat.label}\n[페르소나] {prof.persona}\n{industry_brief(prof)}"   # 입력정보는 캐시 프리픽스로
            f"[CTA] {strat.cta}{buy_line}\n"
            f"{seo.keywords_line(kws)}\n\n{seo.X_DIRECTIVES}\n{seo.HOOK_RULE}\n{seo.COPY_PSYCH}\n{seo.FACTS_RULE}\n{seo.HUMAN_TOUCH}\n\n"
            "X(트위터)용 단문을 한국어로 작성하라. 한 덩어리 텍스트로만 출력."
        )
        text = _call_llm(prompt, self.model, 400, cache_prefix=cache_prefix_for(asset))[:280]
        # 소재 정합 게이트(캐스퍼/토레스 실사고 재발 방지) — 불일치면 소재 고정 재작성 1회
        _subj_state = ""
        _subj = seo.subject_match(text, asset.note or "", (kws[0] if kws else ""))
        if _subj is False:
            _re2 = _call_llm(prompt + "\n[재작성 — 소재 고정] 직전 초안이 사진 분석에 없는 차종·제품을 "
                             "실물처럼 서술해 폐기됐다. 위 [사진N] 분석에서 확인되는 소재만 서술하고, 사진과 "
                             "다른 차종·모델명은 언급 자체를 하지 마라.",
                             self.model, 400, cache_prefix=cache_prefix_for(asset))[:280]
            if (_re2 or "").strip():
                text = _re2
            _subj = seo.subject_match(text, asset.note or "", (kws[0] if kws else ""))
            _subj_state = "retried_ok" if _subj is not False else "miss"
        elif _subj is True:
            _subj_state = "ok"
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.X, kind=self.kind,
            payload={"text": text, "image_path": imgs[0], "image_paths": imgs[:4],
                     "target_keywords": kws, "subject_check": _subj_state},  # X 미디어 최대 4
            status=ContentStatus.DRAFT)
