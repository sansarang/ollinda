"""
gowatch 적응 큐 소비(트랙2 PHASE2) — 본체 Python. gowatch가 감지한 변화를 '개선 제안 카드'로 변환.

원칙: 산출물은 알림이 아니라 글/키트. 자동 발행 0(제안만 — 사용자가 게이트 경유 후 복붙 발행).
업종 어휘 하드코딩 0 — 카드 문구·진단 지시문은 글 제목·키워드·측정 근거(순위 등)만 삽입.
빈도 상한 가게당 주 N건(트랙 A 우선 불변). gowatch 미배포/불통이면 무동작(파이프라인 무영향).

kind별:
- rank_drop   → 개선판 생성(revise_piece, '수정 발행용' 신규 조각, 원 발행이력 보존) + 카드
- briefing_lost → 트랙B 신규 글 제안 카드(경험 게이트는 실제 생성 시 경유)
- index_lost  → 재발행 안내 키트 카드
- cross_signal → 생성 파라미터 '제안' 리포트 카드만(자동 반영 금지·승인 후)
"""
from __future__ import annotations

import copy
import logging
import os
import uuid

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
        except Exception:
            _log.exception("[adapt] 소비 실패 id=%s kind=%s", aid, kind)
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


def _handle_rank_drop(a: dict) -> "tuple[dict | None, str]":
    from app.models import ContentKind
    from app.services import revise
    ev = a.get("evidence") or {}
    pub = a.get("publish_id") or ""
    piece = db.get_piece(pub) if pub else None
    rb, ra = ev.get("rank_before"), ev.get("rank_after")
    move = ""
    if isinstance(rb, int) and isinstance(ra, int):
        move = f"{rb}위→{ra}위"
    elif isinstance(rb, int):
        move = f"{rb}위→검색 밖"
    label = _post_label(a, piece)
    piece_id = ""
    if piece is not None and piece.kind == ContentKind.BLOG:
        instr = (
            f"이 글의 검색 순위가 하락했습니다({move or '하락'}). 검색 상위에 노출되는 같은 주제 글들과 "
            "비교해 제목의 구체성, 도입부 후킹, 정보 밀도(단계·수치·사례), 핵심 키워드의 자연스러운 배치를 "
            "보강해 다시 써라. 원문의 업종·맥락·사실은 그대로 유지하고 없는 정보를 지어내지 마라."
        )
        improved = revise.revise_piece(piece, instr)
        newp = copy.copy(improved)
        newp.id = "rev_" + uuid.uuid4().hex[:12]
        pl = dict(improved.payload or {})
        pl["revision_of"] = pub                 # 원 발행 조각 보존(이력 유지)
        pl["revision_reason"] = "search_rank_drop"
        pl["revision_label"] = "수정 발행용"
        newp.payload = pl
        db.save_piece(newp)
        piece_id = newp.id
    headline = f"'{label}' 글이 검색에서 밀렸어요" + (f" ({move})" if move else "")
    sub = "상위 글과 비교해 보강한 글을 준비했어요. 확인하고 발행하세요." if piece_id \
        else "이 글을 보강해 다시 올리면 회복에 도움이 돼요."
    card = {
        "kind": "rank_drop", "icon": "📉", "headline": headline, "sub": sub,
        "preview": _preview_of(piece_id),
        "action": {"label": "개선 글 보기", "href": f"/me/proposal/{a.get('id')}"} if piece_id
        else {"label": "글 보강 안내", "href": "/me"},
    }
    return card, piece_id


def _handle_briefing_lost(a: dict) -> "tuple[dict | None, str]":
    ev = a.get("evidence") or {}
    label = ev.get("keyword") or "이 주제"
    card = {
        "kind": "briefing_lost", "icon": "💬",
        "headline": f"'{label}' 관련 AI 브리핑 인용이 빠졌어요",
        "sub": "이 주제로 경험이 담긴 새 글을 올리면 다시 인용될 가능성이 높아져요.",
        "action": {"label": "새 글 제안 받기", "href": "/me"},
    }
    return card, ""


def _handle_index_lost(a: dict) -> "tuple[dict | None, str]":
    ev = a.get("evidence") or {}
    label = ev.get("keyword") or "발행하신 글"
    card = {
        "kind": "index_lost", "icon": "🔎",
        "headline": f"'{label}' 글이 검색에서 빠졌어요",
        "sub": "색인에서 사라졌어요. 재발행하면 다시 잡히는 경우가 많아요.",
        "action": {"label": "재발행 키트", "href": "/me"},
    }
    return card, ""


def _handle_cross_signal(a: dict) -> "tuple[dict | None, str]":
    ev = a.get("evidence") or {}
    kw = ev.get("keyword_group") or "여러 키워드"
    n = ev.get("n_tenants") or 0
    card = {
        "kind": "cross_signal", "icon": "📊",
        "headline": f"'{kw}' 주제가 최근 여러 곳에서 동시에 밀렸어요",
        "sub": f"검색 정책 변화 신호일 수 있어요(관측 {n}곳). 대응안을 제안으로만 준비했어요 — 확인 후 반영하세요.",
        "action": {"label": "제안 리포트 보기", "href": "/me"},
        "advisory_only": True,   # 자동 반영 금지 — 승인 후
    }
    return card, ""


def _preview_of(piece_id: str) -> dict:
    if not piece_id:
        return {}
    p = db.get_piece(piece_id)
    if not p:
        return {}
    pl = p.payload or {}
    body = (pl.get("body") or "").strip().replace("\n", " ")
    return {"title": pl.get("title") or "", "snippet": (body[:90] + "…") if len(body) > 90 else body}
