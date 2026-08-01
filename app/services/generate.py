"""
Generate 서비스 — 원재료 1개 → 여러 채널 콘텐츠(초안) 생성.
'1소스 → 멀티채널' 변환의 진입점. 결과는 DRAFT 상태로 Review 큐에 들어간다.
"""
from __future__ import annotations

from app.domain.models import Asset, ContentKind, ContentPiece, Tenant
from app.registry import get_generator


LAST_ERRORS: dict = {}   # {kind: 최근 실패 사유} — 비동기 잡 상태 기록용


def generate_for(tenant: Tenant, asset: Asset, kinds: list[ContentKind],
                 images: list[str] | None = None) -> list[ContentPiece]:
    """요청된 종류(kinds)별로 콘텐츠 초안을 생성한다. images=업로드된 사진 경로들(여러 장).
    ★ 채널 병렬 생성(순차→동시) — 채널마다 독립 LLM 호출이라 최대 채널 수만큼 빨라짐. asset은 읽기 공유."""
    import os
    # ★ 세트=한 소재=한 키워드: 타깃 키워드는 세트당 1회만 결정해 전 채널이 공유.
    #   채널별 재결정 시 큐 회전으로 캡션이 '다른 차종' 키워드를 받아 사진에 없는 차종·흠집을
    #   서술한 실사고(2026-07-27, 캐스퍼/토레스) — 날조 금지 위반의 구조적 원인.
    if not (getattr(asset, "target_kw", "") or "").strip():
        # ① 같은 세트에 이미 생성된 피스가 있으면 그 키워드를 승계 — asset.target_kw는 메모리 전용이라
        #   워치독 보완·온디맨드 영상(다른 시점/프로세스)이 재결정하면 또 어긋난다(영속화 구멍).
        try:
            from app import db as _db
            for _p in _db.get_set_pieces(asset.id):
                _tk = ((_p.payload.get("target_kw") or "")
                       or ((_p.payload.get("target_keywords") or [""])[0] or "")).strip()
                if _tk:
                    asset.target_kw = _tk
                    break
        except Exception:
            pass
    if not (getattr(asset, "target_kw", "") or "").strip():
        try:
            from app import seo as _seo
            from app.industries import resolve_industry as _ri
            from app.strategies import resolve_strategy as _rs
            _prof, _strat = _ri(tenant.industry), _rs(tenant)
            _kw0, _ = _seo.resolve_target_keyword(
                industry=(getattr(tenant, "industry", "") or _prof.name), region=tenant.region or "",
                note=asset.note or "", biz=(getattr(tenant, "biz_type", "local") or "local"),
                content_type=(getattr(asset, "content_type", "sell") or "sell"),
                brand=tenant.brand_name or "", keyword_axis=_strat.keyword_axis,
                target_kw_override="", tenant_id=tenant.id, prof_name=_prof.name)
            if _kw0:
                asset.target_kw = _kw0
        except Exception:
            pass
    # ★ 병렬화 재활성(2026-08-01) — 원래 껐던 이유는 '타임아웃 없는 LLM 호출이 블록'이었고,
    #   그 조건이 해소됐다: llm.call이 출력 예산에 비례한 클라이언트 타임아웃 + 재시도 상한 3을
    #   갖고, 빈 응답도 예외로 올린다(무한 대기 경로 없음). 실측 근거: 본문 68.6초 → 캡션 21.1초
    #   → X 5.3초를 줄줄이 기다려 95초. 서로 의존이 없으므로 동시에 돌리면 68초로 줄어든다.
    #   (캡션·X는 '현재 블로그 피스'를 읽지 않는다 — 참고하는 건 이전 세트의 도입부뿐.)
    #   되돌리려면 SHOPCAST_GEN_PARALLEL=0.
    if len(kinds) <= 1 or os.environ.get("SHOPCAST_GEN_PARALLEL", "1") == "0":
        return _generate_sequential(tenant, asset, kinds, images)

    from concurrent.futures import ThreadPoolExecutor

    def _one(kind):
        try:
            gen = get_generator(kind)   # 미등록이면 KeyError
            return (kind, gen.generate(tenant, asset, images), None)
        except Exception as e:          # 한 채널 실패해도 나머지는 진행
            import logging
            logging.exception("[generate] %s 생성 실패", kind)
            return (kind, None, repr(e)[:200])

    try:   # 병렬에선 채널별 라벨을 순차로 갱신할 수 없다 — 한 번에 정직하게 표시
        from app import db as _dbp
        if len(kinds) > 1:
            _dbp.set_gen_progress(tenant.id, "body", "글·캡션 동시에 쓰는 중", "", 0.6)
    except Exception:
        pass
    _cc = min(len(kinds), int(os.environ.get("SHOPCAST_GEN_CONCURRENCY", "4")))
    with ThreadPoolExecutor(max_workers=max(1, _cc)) as ex:
        results = list(ex.map(_one, kinds))            # 입력 순서 보존
    pieces: list[ContentPiece] = []
    for kind, piece, err in results:
        if err:
            LAST_ERRORS[str(kind)] = err               # 잡 상태 기록(영상 워치독) — 사유 포착
        elif piece is not None:
            pieces.append(piece)
    return pieces


# 채널별 세부 진행 라벨·퍼센트(정직한 표시 — "뭘 하는지 정확히"). 순차 생성이라 채널마다 갱신.
_KIND_PROGRESS = {
    ContentKind.BLOG: ("블로그 글 쓰는 중", 0.60),
    ContentKind.CAPTION: ("인스타 캡션 쓰는 중", 0.70),
    ContentKind.X_POST: ("X(트위터) 글 쓰는 중", 0.76),
    ContentKind.MARKETPLACE: ("판매 상세페이지 쓰는 중", 0.80),
    ContentKind.SHORT: ("영상 대본 짜는 중", 0.84),
}


def _generate_sequential(tenant: Tenant, asset: Asset, kinds: list[ContentKind],
                         images: list[str] | None = None) -> list[ContentPiece]:
    pieces: list[ContentPiece] = []
    # 단건 생성(온디맨드 영상·워치독 보완·autoqueue 단독 블로그)은 홈 생성 진행률을 건드리지 않는다 —
    # done 마킹은 업로드 플로우(ingest)만 하므로, 단건 경로가 running을 남기면 영구 잔상(실측 2회:
    # SHORT '영상 대본' 잔상, 2026-07-27 워치독 X 보완 'X 글 쓰는 중' 26분 잔상).
    _touch_progress = len(kinds) > 1
    for kind in kinds:
        try:
            lbl, pct = _KIND_PROGRESS.get(kind, ("콘텐츠 만드는 중", 0.65))
            if _touch_progress:
                try:  # 채널별 세부 진행(사용자가 지금 뭘 만드는지 정확히 — 가짜 60% 스톨 방지)
                    from app import db as _db
                    _db.set_gen_progress(tenant.id, "body", lbl, "", pct)
                except Exception:
                    pass
            gen = get_generator(kind)
            pieces.append(gen.generate(tenant, asset, images))
        except Exception as e:
            import logging
            logging.exception("[generate] %s 생성 실패", kind)
            LAST_ERRORS[str(kind)] = repr(e)[:200]
    return pieces
