"""
재작성·보정 계약 박제(부채 청산 4차 — 80점 미달 실사고 계열).

2026-07-31 루마 71점 실사고와 그 후속 수정을 못 박는다. 되돌리면 실패한다.

박제 대상(커밋 0fde111, 9918e05, 9194843, 1356b29):
  A. 타임아웃은 출력 예산에 비례 — 고정 90초는 큰 재작성을 개선 라운드째 죽였다
  B. thinking은 대형 생성에만 — 작은 판단 호출에서 예산을 잠식해 절단을 만들었다
  C. stop_reason은 본문 호출 직후 기록 — 뒤의 소형 호출이 덮어써 오탐을 냈다
  D. 보정은 [사진N] 마커를 늘리지 않는다 — 날조로 개선 전체가 폐기됐다
  E. 헤드 키워드는 자연 프레임으로 — 동사 직결('부산 썬팅 맡기실 때')은 어색하다
"""
from __future__ import annotations

import inspect

from app import llm


# ── A. 타임아웃 비례 ──────────────────────────────────────────────
def test_timeout_scales_with_output_budget():
    """A. 90초 고정이면 6000토큰 재작성이 APITimeoutError로 죽는다 —
    개선 라운드가 통째로 사라져 71점 글이 그대로 나갔다."""
    src = inspect.getsource(llm.call)
    assert "max_tokens" in src and "timeout" in src, "타임아웃이 예산과 무관하다"
    assert "min(600" in src or "600.0" in src, "상한이 없다(무한정 매달릴 수 있다)"
    # 규칙 자체를 검증: 예산이 커지면 타임아웃도 커지고, 상한을 넘지 않는다
    def _to(mt):
        return min(600.0, 90.0 + mt * 0.02)
    assert _to(1200) < _to(6000) < _to(30000) == 600.0
    assert _to(6000) >= 190, "6000토큰에 200초도 못 준다"


# ── B. thinking 적용 범위 ─────────────────────────────────────────
def test_thinking_only_for_large_generations():
    """B. 700토큰짜리 판단 호출에까지 thinking을 붙이면 예산을 잠식해 텍스트가 0바이트로 잘린다.
    대형 생성(2000토큰 이상)에만 붙인다. Haiku는 adaptive thinking을 아예 못 받는다(400)."""
    src = inspect.getsource(llm.call)
    assert "max_tokens >= 2000" in src, "thinking 적용 하한이 없음"
    assert "haiku" in src, "Haiku 예외가 없음(400 발생)"

    def _uses_thinking(model, mt):
        return "haiku" not in model and mt >= 2000
    assert not _uses_thinking("claude-sonnet-5", 700)
    assert _uses_thinking("claude-sonnet-5", 6000)
    assert not _uses_thinking("claude-haiku-4-5-20251001", 6000)


# ── C. stop_reason 귀속 ───────────────────────────────────────────
def test_finish_reason_is_per_thread():
    """C. 마지막 stop_reason이 모듈 전역이면 ①뒤따르는 소형 호출이 본문 결과를 덮어써
    '본문 미완결' 오탐을 내고 ②병렬 생성끼리 서로의 값을 오염시킨다. 스레드별로 둔다."""
    import threading as _th
    assert isinstance(llm._TL, _th.local), "스레드 지역 저장이 아님(병렬 생성 오염)"
    assert callable(getattr(llm, "last_finish", None)), "last_finish() 조회 경로가 없음"
    src = inspect.getsource(llm)
    assert src.count("_TL.finish =") >= 2, "본문 호출 직후 기록 지점이 빠졌다"

    import threading
    seen = {}

    def _worker(name, val):
        llm._TL.finish = val
        seen[name] = llm.last_finish()

    ts = [threading.Thread(target=_worker, args=(f"t{i}", f"stop{i}")) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert seen == {f"t{i}": f"stop{i}" for i in range(4)}, f"스레드 간 오염: {seen}"


# ── D. 보정이 마커를 날조하지 않는다 ──────────────────────────────
def test_polish_prompt_pins_photo_marker_count():
    """D. 실사고: 보정 LLM이 '시각요소 부족' 감점을 없애려고 [사진N] 마커 수십 개를 날조했다.
    안전게이트가 잡아 개선 전체가 폐기됐다 — 비용만 태우고 39·68점 그대로.
    검사만 하고 지시를 안 한 구멍이었다. 프롬프트가 개수를 못 박아야 한다."""
    from app.services import qualitycheck as _q
    src = inspect.getsource(_q)
    assert "_n_mk" in src, "마커 개수를 세어 프롬프트에 넣지 않음"
    i = src.find("_n_mk}개")
    assert i > 0, "프롬프트에 '지금 정확히 N개' 고정이 없음"
    seg = src[max(0, i - 200):i + 300]
    assert "추가 절대 금지" in seg or "추가 금지" in seg, "마커 추가 금지 지시가 없음"
    assert "재배치" in src, "'추가 대신 재배치'라는 대안을 주지 않으면 지시가 실행 불가능하다"


def test_polish_safety_gate_still_checks_markers():
    """D2. 지시를 넣었다고 검사를 빼면 안 된다 — 지시는 확률, 게이트는 보장이다."""
    from app.services import qualitycheck as _q
    src = inspect.getsource(_q)
    assert src.count("사진") >= 3, "마커 관련 방어가 지나치게 줄었다"
    assert "안전게이트" in src or "마커 불변" in src, "마커 불변 검사 흔적이 없음"


# ── E. 헤드 키워드 자연 프레임 ────────────────────────────────────
def test_head_keyword_natural_frame_required():
    """E. 실측 어색 표현: '부산 썬팅 맡기실 때' — 헤드 키워드 정확 구문 뒤에 동사를 바로 붙이면
    한국어가 깨진다. 조사를 넣은 자연형을 쓰고, 정확 구문은 요약줄에서 채운다."""
    from app.generators import text_claude as _tc
    src = inspect.getsource(_tc)
    assert "동사 직결 금지" in src, "동사 직결 금지 지시가 없음"
    assert "자연 프레임" in src, "자연 프레임 지시가 없음"
    i = src.find("동사 직결 금지")
    seg = src[max(0, i - 300):i + 300]
    assert "요약줄" in seg or "정확 구문" in seg, "정확 구문 충족 대안 경로가 없음"
