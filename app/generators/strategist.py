"""
🎯 마케팅 전략가 (Expert #1)
사진 분석 + 업종/사업형태를 보고 '크리에이티브 브리프' 1장을 만든다.
5개 채널(카피·블로그·영상·X)이 이 브리프를 공유 → 콘텐츠가 따로 놀지 않고 한 전략으로 정렬.
비용: 생성당 Claude 1콜(공유). 키 없으면 기존 전략/포맷/SEO로 안전 폴백(품질 유지).
"""
from __future__ import annotations

import json
import os

from app import seo
from app.formats import pick_format, format_directive
from app.industries import resolve_industry
from app.strategies import resolve_strategy


def _fallback_brief(tenant, asset) -> dict:
    """Claude 없이도 기존 로직으로 일관된 브리프 구성."""
    prof = resolve_industry(tenant.industry)
    strat = resolve_strategy(tenant)
    _kw0b, kws = seo.resolve_target_keyword(   # 공유 관문(전 생성기 공통 — 브리프 core_keyword도 phantom 차단)
        industry=(getattr(tenant, "industry", "") or prof.name), region=tenant.region or "",
        note=asset.note or "", biz=(getattr(tenant, "biz_type", "local") or "local"),
        content_type=(getattr(asset, "content_type", "sell") or "sell"), brand=tenant.brand_name or "",
        keyword_axis=strat.keyword_axis, target_kw_override=(getattr(asset, "target_kw", "") or ""),
        tenant_id=tenant.id, prof_name=prof.name)
    fmt = pick_format(getattr(tenant, "biz_type", "local") or "local", asset.note)
    return {
        "angle": f"{prof.name}의 핵심 매력을 솔직하게",
        "hook": (kws[0] if kws else prof.name) + ", 이거 하나면 됩니다",
        "core_keyword": kws[0] if kws else prof.name,
        "sub_keywords": kws[1:6],
        "target_audience": strat.goal,
        "selling_points": [prof.tone] if getattr(prof, "tone", "") else [],
        "tone": getattr(prof, "tone", "친근하고 신뢰감 있게"),
        "viral_format": getattr(fmt, "key", "honest_review"),
        "format_directive": format_directive(fmt),
        "cta": strat.cta,
        "_source": "fallback",
    }


def build_brief(tenant, asset) -> dict:
    """전 채널이 공유할 크리에이티브 브리프(dict). 실패/무키 시 폴백."""
    fb = _fallback_brief(tenant, asset)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fb
    prof = resolve_industry(tenant.industry)
    strat = resolve_strategy(tenant)
    prompt = (
        "너는 소상공인·온라인셀러 콘텐츠를 수백 건 성공시킨 '마케팅 전략가'다. "
        "아래 정보를 보고, 5개 채널(인스타·네이버블로그·유튜브쇼츠·릴스·X)이 공유할 "
        "'크리에이티브 브리프'를 설계하라. 검색 상위노출과 전환(매출)을 동시에 노린다.\n\n"
        f"[가게/상품] {tenant.name} (업종: {prof.name}, 지역: {tenant.region or '온라인'})\n"
        f"[사업형태] {strat.label} — 목표: {strat.goal}\n"
        f"[페르소나/톤] {getattr(prof, 'persona', '')} / {getattr(prof, 'tone', '')}\n"
        f"[입력·사진분석] {asset.note}\n\n"
        "다음 JSON만 출력(설명 금지). 각 값은 구체적·실전적으로:\n"
        "{\n"
        '  "angle": "이 콘텐츠의 핵심 컨셉/앵글(한 줄)",\n'
        '  "hook": "스크롤을 멈추게 하는 첫 문장(후킹)",\n'
        '  "core_keyword": "검색 상위노출 노릴 핵심 키워드 1개",\n'
        '  "sub_keywords": ["보조 키워드 3~5개"],\n'
        '  "target_audience": "구체적 타겟 고객",\n'
        '  "selling_points": ["고객이 얻는 이득 2~3개"],\n'
        '  "tone": "톤앤매너",\n'
        '  "cta": "고객이 취할 행동(방문·예약·구매 등)"\n'
        "}"
    )
    try:
        from app.generators.text_claude import _call_llm
        raw = _call_llm(prompt, max_tokens=900)
        s, e = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[s:e + 1]) if s >= 0 and e > s else {}
        if not isinstance(data, dict) or not data.get("core_keyword"):
            return fb
        # 폴백값 위에 덮어써 누락 필드 보전
        merged = {**fb, **{k: v for k, v in data.items() if v}}
        merged["_source"] = "claude"
        return merged
    except Exception:
        return fb


def brief_to_directive(brief: dict) -> str:
    """브리프를 전 채널 생성 프롬프트에 붙일 강한 지시 블록으로."""
    sp = brief.get("selling_points") or []
    sub = brief.get("sub_keywords") or []
    return (
        "\n[🎯 마케팅 전략가 브리프 — 모든 채널이 반드시 따를 것]\n"
        f"- 앵글(컨셉): {brief.get('angle','')}\n"
        f"- 후킹(첫 문장/도입 반영): {brief.get('hook','')}\n"
        f"- 핵심 키워드(제목·첫문장에 필수 포함): {brief.get('core_keyword','')}\n"
        f"- 보조 키워드: {', '.join(sub)}\n"
        f"- 타겟 고객: {brief.get('target_audience','')}\n"
        f"- 핵심 셀링포인트: {'; '.join(sp)}\n"
        f"- 톤앤매너: {brief.get('tone','')}\n"
        f"- CTA: {brief.get('cta','')}\n"
    )
