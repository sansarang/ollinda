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
    # ★ 병렬화 기본 OFF(긴급 롤백) — 채널 병렬 생성이 행(hang) 유발 실측(타임아웃 없는 LLM 호출이 블록).
    #   순차로 복귀(모델 하이브리드 Haiku/Sonnet는 유지 → 속도 이득 대부분 보존). 타임아웃 보강 후 재활성.
    if len(kinds) <= 1 or os.environ.get("SHOPCAST_GEN_PARALLEL", "0") == "0":
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
    # 온디맨드 영상(SHORT 단독)은 홈 생성 진행률을 건드리지 않는다 — 영상 진행은 video_job(stage)이
    # 담당. 안 그러면 '영상 대본 짜는 중 running' 잔상이 영구히 남음(실측).
    _touch_progress = kinds != [ContentKind.SHORT]
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
