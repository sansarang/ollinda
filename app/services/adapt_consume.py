"""
gowatch 적응 큐 소비(트랙2) — gowatch가 감지한 변화를 **무음 교훈**으로 적재한다.

★ 2026-08-16 사장님 지시로 방향이 바뀌었다: **주방은 공개하지 않는다.**
  전에는 이 모듈이 사장님 홈에 '개선 제안 카드'를 띄웠다 —
  "'부산광역시 동구 썬팅' 글이 검색에서 밀렸어요 (1위→검색 밖)".
  순위·키워드는 사장님이 보실 것이 아니고(헌법: 사장님 화면에 주방 용어 금지),
  밀렸으면 **우리가 알아서 다음 글에 반영**하면 된다.
  사장님이 하시는 것은 사진 올리기다. 노출되게 만드는 것은 우리 역할이다.

kind별 (전부 화면 0):
- rank_drop     → 교훈 적재(다음 글 생성 브리프에 자동 반영). 미리 재작성하지 않는다.
- briefing_lost → 교훈 적재
- index_lost    → 교훈 적재
- cross_signal  → **운영자** 공지만(가게 무관 정책 신호)

같이 고친 계측 결함 2종:
- 관측 키워드 표기를 `seo._kw_shorten` 단일 관문으로 통일(행정 풀네임 ↔ 구어형 갈라짐 사고)
- `rank_after=None`은 조회 실패다 — '검색 밖'이라 단정하지 않는다(race.py와 같은 규칙)

업종 어휘 하드코딩 0. gowatch 미배포/불통이면 무동작(파이프라인 무영향).
"""
from __future__ import annotations

import logging
import os

from app import db
from app.services import gowatch_client

_log = logging.getLogger("shopcast.adapt")


def _cap() -> int:
    try:
        return max(1, int(os.environ.get("GOWATCH_WEEKLY_CAP", "3")))
    except Exception:
        return 3


def consume_all(limit: int = 50) -> dict:
    """큐 소비 1회(스케줄러/수동). 반환 {fetched, proposed, skipped_cap, error}."""
    out = {"fetched": 0, "proposed": 0, "skipped_cap": 0}
    if not gowatch_client.configured():
        out["error"] = "gowatch 미구성(GOWATCH_URL/TOKEN)"
        return out
    items = gowatch_client.list_adaptations(status="queued", limit=limit)
    out["fetched"] = len(items)
    week_count: dict[str, int] = {}
    for a in items:
        tid = a.get("tenant_id") or ""
        kind = a.get("kind") or ""
        aid = a.get("id") or ""
        if not aid or not kind:
            continue
        # 이미 제안된 이벤트면 skip(멱등) — gowatch 상태만 정합화
        if db.get_proposal(aid):
            gowatch_client.set_status(aid, "proposed")
            continue
        # 주 N건 상한(cross_signal은 tenant 없음 → 상한 무관)
        if tid and kind != "cross_signal":
            used = week_count.get(tid)
            if used is None:
                used = db.proposals_this_week(tid)
            if used >= _cap():
                out["skipped_cap"] += 1
                continue
        try:
            card, piece_id = _dispatch(a)
        except Exception as _e:
            import traceback
            _log.exception("[adapt] 소비 실패 id=%s kind=%s", aid, kind)
            out.setdefault("errors", []).append({"id": aid, "kind": kind,
                                                  "err": traceback.format_exc()[-300:]})
            continue
        if not card:
            continue
        if db.save_proposal(aid, tid, kind, a.get("publish_id") or "", piece_id, card):
            out["proposed"] += 1
            if tid:
                week_count[tid] = week_count.get(tid, db.proposals_this_week(tid)) + 1
        gowatch_client.set_status(aid, "proposed")   # gowatch가 자기 테이블에 상태 기록
    return out


def _dispatch(a: dict) -> "tuple[dict | None, str]":
    kind = a.get("kind")
    if kind == "rank_drop":
        return _handle_rank_drop(a)
    if kind == "briefing_lost":
        return _handle_briefing_lost(a)
    if kind == "index_lost":
        return _handle_index_lost(a)
    if kind == "cross_signal":
        return _handle_cross_signal(a)
    return None, ""


# ── 업종 중립 카드 문구 뼈대 — 제목/키워드/측정값만 삽입, 업종 어휘 없음 ──
def _post_label(a: dict, piece) -> str:
    """카드에 쓸 글 이름 — 조각 제목 우선, 없으면 키워드. 업종 어휘 하드코딩 아님(데이터 유래)."""
    if piece is not None:
        t = (piece.payload or {}).get("title")
        if t:
            return t
    ev = a.get("evidence") or {}
    return ev.get("keyword") or "발행하신 글"


def _kw(ev: dict) -> str:
    """관측 키워드 표기 단일 관문 — 행정 풀네임을 구어형으로 축약한다.

    ★ 2026-08-16 실물 사고: gowatch는 '부산광역시 동구 썬팅'(행정 풀네임)으로 관측하고
      우리 추적·표시 층은 `_kw_shorten`으로 '부산 동구 썬팅'을 쓴다. 사장님 화면에
      같은 키워드가 위에서는 "검색 밖", 아래에서는 "첫 화면에 보이는 중"으로 동시에 떴다.
      (실측: 구어형 blog_search 08-15 = 6위, 즉 '검색 밖'이 아니었다)
      표기가 두 갈래면 판정도 두 갈래가 된다 — 관문을 하나로 둔다.
    """
    from app import seo
    return " ".join(seo._kw_shorten((ev or {}).get("keyword") or "").split())


def _move_text(rb, ra) -> str:
    """순위 이동 문구.

    ★ `rank_after`가 None인 것은 **조회 실패**이지 '검색 밖'이 아니다(2026-08-16 수정).
      전에는 None을 그대로 "검색 밖"이라 단정해, 못 잰 것을 밀린 것으로 보고했다.
      race.py에서 고친 것과 같은 계열이라 여기서도 같은 규칙을 쓴다 — 미측정과 미노출을 섞지 않는다.
    """
    if isinstance(rb, int) and isinstance(ra, int):
        return f"{rb}위→{ra}위" if ra >= 1 else f"{rb}위→상위 밖"
    if isinstance(rb, int):
        return f"{rb}위→확인 못 함"
    return ""


def _handle_rank_drop(a: dict) -> "tuple[dict | None, str]":
    """순위 하락 — **사장님 화면에 아무것도 만들지 않는다**(2026-08-16 사장님 지시).

    왜 카드를 없앴나: 카드 문구가 전부 주방이었다("'…썬팅' 글이 검색에서 밀렸어요(1위→검색 밖)").
      순위·키워드는 사장님이 보실 것이 아니다. 사장님은 사진만 올리시고,
      **노출되게 만드는 것은 우리 역할**이다.
    왜 재작성도 없앴나: 아무도 안 보는 개선판을 LLM으로 미리 만드는 것은
      '사용자가 고른 것만 만든다'(헌법) 위반이고 비용만 든다.
    대신 무엇을 하나: 교훈으로 적재한다 → 다음 글 생성 브리프에 자동 반영된다(UI 0).
    """
    ev = a.get("evidence") or {}
    kw = _kw(ev)
    move = _move_text(ev.get("rank_before"), ev.get("rank_after"))
    if kw:
        db.add_lesson(
            a.get("tenant_id") or "",
            f"'{kw}' 순위 하락({move or '하락'}) — 같은 주제 글은 제목 구체성과 "
            "질의별 답변 문단(수치·단계를 한 문단에 모으기)을 더 강하게 잡을 것.",
            source_kw=kw, source_piece_id=a.get("publish_id") or "",
            cause="search_rank_drop")
    return None, ""                       # 카드 0 · 산출물 0


def _handle_briefing_lost(a: dict) -> "tuple[dict | None, str]":
    """AI 브리핑 인용 이탈 — 무음 교훈만(주방 비공개)."""
    kw = _kw(a.get("evidence") or {})
    if kw:
        db.add_lesson(a.get("tenant_id") or "",
                      f"'{kw}' 브리핑 인용 이탈 — 이 주제는 질문형 소제목·표·수치를 "
                      "한 문단에 모아 인용 가능한 덩어리로 만들 것.",
                      source_kw=kw, cause="briefing_lost")
    return None, ""


def _handle_index_lost(a: dict) -> "tuple[dict | None, str]":
    """색인 이탈 — 무음 교훈만. 재발행이 필요하면 글감 큐가 사장님 언어로 안내한다."""
    kw = _kw(a.get("evidence") or {})
    if kw:
        db.add_lesson(a.get("tenant_id") or "",
                      f"'{kw}' 색인 이탈 — 같은 주제로 다시 쓸 때 원문 반복을 피하고 "
                      "새 실값(사진·수치)을 넣어 유사문서 판정을 피할 것.",
                      source_kw=kw, source_piece_id=a.get("publish_id") or "",
                      cause="index_lost")
    return None, ""


def _handle_cross_signal(a: dict) -> "tuple[dict | None, str]":
    """여러 가게 동시 하락 = 검색 정책 변화 신호 — **운영자**에게만 알린다(사장님 화면 0)."""
    ev = a.get("evidence") or {}
    kw = ev.get("keyword_group") or "여러 키워드"
    n = ev.get("n_tenants") or 0
    try:
        db.add_notice("", "adapt",              # tenant_id="" = 운영자 공지
                      f"교차 신호 — '{kw}' 주제가 여러 곳에서 동시에 밀렸다(관측 {n}곳). "
                      "검색 정책 변화 가능성. 자동 반영 없음 — 확인 후 판단할 것.")
    except Exception:
        _log.exception("[adapt] 교차 신호 운영자 공지 실패")
    return None, ""


# (제거됨 2026-08-16) _preview_of — 카드 미리보기용이었다. 카드가 없어져 부르는 곳이 없다.
#   옮기면 원위치를 비운다(규율 1). 필요해지면 git 이력에 있다.
