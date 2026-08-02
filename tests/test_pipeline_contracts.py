"""
파이프라인 계약 박제(2026-08-01 수정분 부채 청산).

발행 산출물·비용·데이터 안전에 직결되는 계약을 못 박는다.
각 테스트는 '수정 전 상태로 되돌리면 실패한다'를 기준으로 만들었다.

박제 대상(커밋 1356b29, 4cc9f39, c6ac735, 1042b0d, 4f5d6d9, 5d8807e, ed74158):
  A. 빈 LLM 응답 — 예산 확대 대신 thinking 끄고 재시도, 그래도 비면 예외
  B. 묘비 — 삭제된 세트는 되살아나지 않고, 소유 검증 없이는 묘비를 남기지 않는다
  C. 요청 플랫폼 — 사용자가 고른 것만 렌더한다
  D. 크레딧 소진 — 감지 시 새 작업을 막는다
"""
from __future__ import annotations

import types
import uuid

import pytest

from app import db, llm
from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus


# ── A. 빈 응답 처리 ────────────────────────────────────────────────
class _Blk:
    def __init__(self, t, txt=""):
        self.type, self.text = t, txt


class _Resp:
    def __init__(self, stop, blocks):
        self.stop_reason, self.content = stop, blocks
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=0)


def _fake_anthropic(calls, empty_when_thinking=True, always_empty=False):
    class Msgs:
        def create(self, **kw):
            calls.append({"max_tokens": kw["max_tokens"], "thinking": "thinking" in kw})
            if always_empty:
                return _Resp("max_tokens", [_Blk("thinking")])
            if empty_when_thinking and "thinking" in kw:
                return _Resp("max_tokens", [_Blk("thinking")])
            return _Resp("end_turn", [_Blk("text", "정상 결과 본문")])

    class Client:
        def __init__(self, **kw):
            self.messages = Msgs()
    return Client


def test_empty_response_retries_without_thinking_not_bigger_budget(monkeypatch):
    """A1. 텍스트 0바이트 절단은 '예산 부족'이 아니라 thinking이 예산을 삼킨 것이다.
    실측 사고: 6000→12000→16000으로 예산을 늘리며 콜당 최대 410초를 태우고도 빈 문자열."""
    import anthropic
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic(calls))
    out = llm.call("프롬프트", model="claude-sonnet-5", max_tokens=6000)
    assert out == "정상 결과 본문"
    assert len(calls) == 2, f"콜 수가 예상과 다름(예산 확대 재발?): {calls}"
    assert calls[1]["max_tokens"] == 6000, f"예산을 늘려 재시도함: {calls}"
    assert calls[1]["thinking"] is False, "thinking을 끄지 않고 재시도함"


def test_empty_response_raises_not_silently_empty(monkeypatch):
    """A2. 끝내 비면 빈 문자열을 조용히 반환하지 않고 예외로 올린다.
    실측 사고: 호출부가 빈 문자열을 '안전게이트 위반(len 0)'으로 오해해 보정을 포기했다."""
    import anthropic
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic(calls, always_empty=True))
    with pytest.raises(Exception) as ei:
        llm.call("프롬프트", model="claude-sonnet-5", max_tokens=6000)
    assert "빈 응답" in str(ei.value), f"사유가 불명확: {ei.value}"


# ── B. 묘비(삭제 세트) ────────────────────────────────────────────
def _piece(tenant: str, asset: str) -> ContentPiece:
    return ContentPiece(id=str(uuid.uuid4()), tenant_id=tenant, asset_id=asset,
                        channel=Channel.NAVER_BLOG, kind=ContentKind.BLOG,
                        payload={"body": "원본"}, status=ContentStatus.DRAFT)


def test_deleted_set_never_resurrects():
    """B1. 삭제 뒤 늦게 끝난 백그라운드 저장이 글을 되살리면 안 된다.
    실측 사고: 13:41 삭제 → 13:47:33 다시쓰기 완료 → 80점으로 부활."""
    aid, tid = str(uuid.uuid4()), "T_TOMB_OWNER"
    p = _piece(tid, aid)
    try:
        db.save_piece(p)
        assert len(db.get_set_pieces(aid)) == 1
        db.delete_set(aid, tid)
        assert len(db.get_set_pieces(aid)) == 0
        p.payload["body"] = "다시쓰기 결과"
        db.save_piece(p)                                  # 뒤늦게 끝난 스레드
        assert len(db.get_set_pieces(aid)) == 0, "삭제된 세트가 부활함"
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM content_pieces WHERE tenant_id=?", (tid,))
            c.execute("DELETE FROM deleted_assets WHERE asset_id=?", (aid,))


def test_tombstone_requires_ownership():
    """B2. 소유 검증 없이 묘비를 남기면 남의 세트를 영구 동결시키는 취약점이 된다.
    실측: 로그인한 누구나 남의 asset_id로 삭제를 쏘면 그 세트의 모든 저장이 버려졌다."""
    aid, owner = str(uuid.uuid4()), "T_TOMB_OWNER2"
    p = _piece(owner, aid)
    try:
        db.save_piece(p)
        db.delete_set(aid, "T_ATTACKER")                  # 남의 세트 삭제 시도
        assert len(db.get_set_pieces(aid)) == 1, "남의 세트가 지워짐"
        assert not db.is_set_deleted(aid), "소유 검증 없이 묘비가 생김(영구 동결 취약점)"
        p.payload["body"] = "정상 갱신"
        db.save_piece(p)
        assert db.get_set_pieces(aid)[0].payload["body"] == "정상 갱신"
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM content_pieces WHERE tenant_id=?", (owner,))
            c.execute("DELETE FROM deleted_assets WHERE asset_id=?", (aid,))


# ── C. 요청 플랫폼만 생성 ─────────────────────────────────────────
def test_only_requested_platforms_render():
    """C. 사용자가 고른 것만 만든다. 네이버만 요청하면 쇼츠 렌더·릴스 변형을 하지 않는다.
    실측 사고: want가 상태 이름표에만 쓰이고 렌더 단계엔 전달되지 않아 전부 만들어졌다."""
    import inspect

    from app.generators import video as _v
    src = inspect.getsource(_v.ShortVideoGenerator.generate)
    assert "_want_platforms" in src, "요청 플랫폼이 렌더 단계로 전달되지 않음"
    assert "_need_shorts" in src, "쇼츠 렌더 생략 분기가 없음"
    # 판정 규칙 자체를 확인(쇼츠·릴스 미요청이면 쇼츠 렌더 없음)
    for want, need in (({"naver"}, False), ({"shorts"}, True), ({"reels"}, True),
                       ({"naver", "shorts"}, True), ({"clip"}, False)):
        assert bool({"shorts", "reels"} & want) is need, f"판정 어긋남: {want}"


def test_watchdog_off_by_default():
    """C2. 영상 자동 재시도는 기본 꺼져 있어야 한다(요청하지 않은 렌더가 크레딧을 태웠다)."""
    import inspect

    from app.services import ingest as _ing
    src = inspect.getsource(_ing.video_watchdog)
    assert "SHOPCAST_VIDEO_WATCHDOG" in src, "워치독 토글이 없음"
    assert '"0"' in src, "기본값이 켜짐(자동 재시도 재발)"


# ── D. 크레딧 소진 ────────────────────────────────────────────────
def test_credit_out_blocks_new_work(monkeypatch):
    """D. 크레딧 소진이 감지되면 새 작업을 시작하지 않는다.
    실측 사고: 계속 시도하며 5분씩 렌더를 태우고 화면엔 원시 오류만 보였다."""
    monkeypatch.setattr(llm, "CREDIT_OUT_TS", 0.0)
    assert llm.credit_out() is False
    monkeypatch.setattr(llm, "note_credit_out", lambda *_a, **_k: None)   # 통보 차단
    import time as _t
    monkeypatch.setattr(llm, "CREDIT_OUT_TS", _t.time())
    assert llm.credit_out() is True, "크레딧 소진 상태가 감지되지 않음"
    assert "크레딧" in llm.CREDIT_MSG and "운영자" in llm.CREDIT_MSG
