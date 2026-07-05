"""
Claude 기반 텍스트 생성기 — 인스타 캡션, 네이버 블로그 SEO 초안.
모델: claude-opus-4-8 (기본). 키: ANTHROPIC_API_KEY.
업종 페르소나(prof.persona)·메모(asset.note: 목적/타겟/추가정보 포함)를 강하게 반영.
"""
from __future__ import annotations

import uuid

from app.domain.models import Asset, Channel, ContentKind, ContentPiece, ContentStatus, Tenant
from app.generators.base import Generator
from app.industries import resolve_industry
from app.strategies import resolve_strategy, buy_block
from app import seo

MODEL = "claude-opus-4-8"


class CaptionGenerator(Generator):
    """인스타 캡션 + 해시태그 (페르소나 강하게)."""
    kind = ContentKind.CAPTION

    def __init__(self, model: str = MODEL):
        self.model = model

    def _prompt(self, tenant: Tenant, asset: Asset, n_imgs: int, kws: list[str]) -> str:
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        seeds = " ".join(prof.hashtag_seeds)
        cautions = ("\n[주의] " + "; ".join(prof.cautions)) if prof.cautions else ""
        carousel = f"\n[사진 {n_imgs}장 — 캐러셀]" if n_imgs > 1 else ""
        buy = buy_block(tenant)
        buy_line = f"\n[구매 안내(마지막에 자연스럽게)] {buy}" if buy else ""
        tag_hint = "상품·후기 키워드" if strat.keyword_axis == "product" else "지역명·타겟키워드"
        return (
            f"[가게] {tenant.name} (업종: {prof.name}, 지역: {tenant.region})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n[업종 톤] {prof.tone}\n"
            f"[입력 정보] {asset.note}{carousel}\n[CTA] {strat.cta}{buy_line}\n"
            f"[기본 해시태그] {seeds}{cautions}\n"
            f"{seo.keywords_line(kws)}\n\n"
            f"{seo.CAPTION_DIRECTIVES}\n\n"
            "위 페르소나 말투를 강하게 적용해 인스타그램 캡션을 한국어로 작성하라. "
            f"과장 없이 솔직하게, 이모지는 적당히. 해시태그 8~12개({tag_hint} 포함)."
        )

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        imgs = images or [asset.path]
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        kws = seo.target_keywords(prof.name, tenant.region, asset.note,
                                  axis=strat.keyword_axis, brand=tenant.brand_name)
        text = _call_llm(self._prompt(tenant, asset, len(imgs), kws), self.model, 1200)
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.INSTAGRAM, kind=self.kind,
            payload={"text": text, "image_path": imgs[0], "image_paths": imgs[:10],
                     "target_keywords": kws},
            status=ContentStatus.DRAFT)


class BlogDraftGenerator(Generator):
    """네이버 블로그 SEO 구조화 초안(제목/메타/본문/이미지배치/키워드). 반자동(사람 발행)."""
    kind = ContentKind.BLOG

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
        kw0 = kws[0] if kws else prof.name
        if strat.closing == "buy":
            closing = ("[마무리] 글 끝은 '구매 유도'로. 상세페이지/스토어로 자연스럽게 연결하고 찜·후기를 권하라."
                       + (f" 구매 안내 문구: {buy}" if buy else ""))
        elif strat.closing == "both":
            place = (f" 네이버 지도: {tenant.map_url}" if getattr(tenant, "map_url", "") else "")
            closing = ("[마무리] 가까운 손님은 매장 방문(찾아오는길·연락처) + "
                       f"'네이버에서 \"{tenant.name}\" 검색 → 플레이스 찜·예약', 먼 손님은 온라인 구매로 안내."
                       + (f" 구매 안내: {buy}" if buy else "") + place)
        else:
            place = (f" 네이버 지도 링크: {tenant.map_url}" if getattr(tenant, "map_url", "") else "")
            closing = ("[마무리] 글 끝에 '찾아오는길(지도)·영업시간·연락처' + "
                       f"'네이버에서 \"{tenant.name}\" 검색 → 플레이스에서 찜·예약·길찾기' 행동 유도."
                       + place)
        prompt = (
            f"[가게] {tenant.name} (업종: {prof.name}, 지역: {tenant.region})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n[업종 톤] {prof.tone}\n"
            f"[입력 정보] {asset.note}\n[사진 {len(imgs)}장]\n"
            f"{seo.keywords_line(kws)}\n{closing}\n\n"
            f"{seo.BLOG_DIRECTIVES}\n"
            f"사진 {len(imgs)}장 → 본문 문단 사이에 [사진1]..[사진{len(imgs)}]를 순서대로 한 번씩(한 줄 단독) 배치.\n\n"
            "아래 형식 그대로(대괄호 머리표 유지) 출력:\n"
            f"[제목]\n('{kw0}'를 맨 앞에 넣어 25~35자 롱테일, 숫자/혜택으로 클릭 유도)\n"
            "[메타설명]\n(150자 내외, 클릭 유도)\n"
            f"[본문]\n(첫 문장에 '{kw0}' 포함, ## 소제목 3~5개 + '## 자주 묻는 질문'(Q&A 2~3쌍), "
            "1200~1800자, [사진N] 마커 배치)\n"
            "[이미지배치]\n(- 각 사진을 어디에 왜)\n"
            "[키워드]\n(쉼표로 5~8개, 타겟 키워드 우선)"
        )
        raw = _call_llm(prompt, self.model, 3000)
        d = _parse_sections(raw, ["제목", "메타설명", "본문", "이미지배치", "키워드"])
        parsed = [k.strip().lstrip("#") for k in (d.get("키워드", "")).replace("\n", ",").split(",") if k.strip()]
        # 파싱된 키워드 + 타겟 키워드 병합(중복 제거)
        tags = list(dict.fromkeys(parsed + kws))[:10]
        body = _ensure_photo_markers(d.get("본문") or raw, len(imgs))
        # 셀러: 본문 끝에 구매 블록 보강(누락 대비)
        if strat.closing in ("buy", "both") and buy and buy not in body:
            body = body.rstrip() + "\n\n" + buy
        # 매장(local/hybrid): 글 끝에 '지도·연락처·플레이스' 블록 항상 보장
        if (getattr(tenant, "biz_type", "local") or "local") in ("local", "hybrid") and "찾아오는 길" not in body:
            cb = ["📍 찾아오는 길 · 문의"]
            if getattr(tenant, "address", ""):
                cb.append(tenant.address)
            if getattr(tenant, "phone", ""):
                cb.append(f"📞 {tenant.phone}")
            if getattr(tenant, "hours", ""):
                cb.append(f"🕒 {tenant.hours}")
            cb.append(f"네이버에서 '{tenant.name}' 검색 → 플레이스에서 찜·예약·길찾기 ⭐")
            if getattr(tenant, "map_url", ""):
                cb.append(f"🗺 {tenant.map_url}")
            body = body.rstrip() + "\n\n" + "\n".join(cb)
        markers = [{"marker": f"[사진{i+1}]", "image_index": i, "image_path": p}
                   for i, p in enumerate(imgs)]
        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.NAVER_BLOG, kind=self.kind,
            payload={"title": d.get("제목") or "제목 [기입필요]",
                     "meta_description": d.get("메타설명", ""),
                     "body": body, "photo_markers": markers,
                     "recommended_image_placement": d.get("이미지배치", ""),
                     "tags": tags, "seo_keywords": tags, "target_keywords": kws,
                     "biz_type": strat.key, "closing": strat.closing, "buy_block": buy,
                     "raw": raw, "image_path": imgs[0], "image_paths": imgs},
            status=ContentStatus.DRAFT)


def _ensure_photo_markers(body: str, n: int) -> str:
    """본문에 [사진1]..[사진n] 마커가 다 있는지 보장. 없으면 문단 사이에 고르게 삽입."""
    if n <= 0:
        return body
    present = [i for i in range(1, n + 1) if f"[사진{i}]" in body]
    if len(present) >= n:
        return body
    # 마커가 부족하면 기존 마커 제거 후 재배치(순서·중복 보장)
    import re
    clean = re.sub(r"\[사진\d+\]", "", body)
    paras = [p.strip() for p in clean.split("\n\n") if p.strip()]
    if not paras:
        return "\n\n".join(f"[사진{i+1}]" for i in range(n))
    out = [f"[사진1]"]                       # 첫 사진은 맨 위
    remaining = n - 1
    # 남은 마커를 문단들 사이에 고르게
    slots = len(paras)
    step = max(1, slots // max(remaining, 1)) if remaining else slots + 1
    mi = 2
    for idx, p in enumerate(paras):
        out.append(p)
        if mi <= n and (idx + 1) % step == 0:
            out.append(f"[사진{mi}]")
            mi += 1
    while mi <= n:                          # 남으면 끝에
        out.append(f"[사진{mi}]")
        mi += 1
    return "\n\n".join(out)


def _parse_sections(raw: str, headers: list[str]) -> dict:
    """[머리표] 기준으로 섹션 분리. 머리표 없으면 빈 dict(상위에서 raw 폴백)."""
    import re
    out: dict[str, str] = {}
    # 각 [헤더] 위치 찾기
    positions = []
    for h in headers:
        m = re.search(rf"\[{re.escape(h)}\]", raw)
        if m:
            positions.append((m.start(), m.end(), h))
    positions.sort()
    for i, (s, e, h) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(raw)
        out[h] = raw[e:end].strip()
    return out


def _call_llm(prompt: str, model: str = MODEL, max_tokens: int = 1200) -> str:
    """공용 Claude 호출. 키 없으면 골격 검증용 더미(형식 유지)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return ("[제목]\n[샘플] " + prompt[:30].replace("\n", " ")
                + "\n[메타설명]\n샘플 메타설명\n[본문]\n## 소제목\n샘플 본문 (이미지: 메인사진)\n"
                "[이미지배치]\n- 서론: 메인사진\n[키워드]\n샘플,키워드,지역")
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "")
