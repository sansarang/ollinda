"""
실패가 보이게 하는 계약 박제(2026-08-03 실사고).

사고: 크레딧 소진으로 전 채널이 400을 맞아 글이 0개 나왔는데, 진행률은 done/1.0
'콘텐츠 완성'이었다. 화면상 성공, 실제로는 0개 — 사장님도 나도 '끝났다'고 읽었다.
동기 실행으로 돌려서야 사유가 나왔다.

교훈: 실패는 실패라고 말해야 한다. 그리고 사장님 말로 말해야 한다.
"""
from __future__ import annotations

import inspect

from app.services import ingest


def test_zero_pieces_is_not_reported_as_done():
    """A. 아무것도 못 만들었으면 '완성'이라고 하지 않는다."""
    src = inspect.getsource(ingest.ingest_upload)
    i = src.find("if not pieces:")
    assert i > 0, "0건 분기가 없다 — 실패가 성공으로 보고된다"
    seg = src[i:i + 900]
    assert 'status="failed"' in seg, "진행률이 실패로 안 바뀐다"
    assert "error=" in seg, "사유가 진행률에 안 남는다"
    assert "raise" in seg, "호출부가 실패를 모른 채 지나간다"
    # done 기록보다 앞에 있어야 한다 — 뒤면 이미 '완성'으로 덮인다
    j = src.find('"done", "콘텐츠 완성"')
    assert 0 < i < j, "0건 판정이 완성 기록보다 뒤에 있다"


def test_failure_reason_is_in_owner_language():
    """B. 사장님이 읽는 문구다 — 기계 오류를 그대로 보여주면 아무 도움이 안 된다.
    무엇 때문인지와 무엇을 하면 되는지를 함께 적는다."""
    h = ingest._human_gen_error
    msg = h("BadRequestError('Your credit balance is too low to access the Anthropic API')")
    assert "충전" in msg, msg
    assert "BadRequest" not in msg and "400" not in msg, "기계 오류가 그대로 노출된다"
    assert "다시" in h("APITimeoutError: timeout")
    # 못 알아본 사유를 그럴듯하게 지어내지 않는다
    unknown = h("ZeroDivisionError")
    assert "확인 중" in unknown, unknown


def test_raw_reason_is_kept_for_diagnosis():
    """C. 사람 말로 바꾸되 원문도 남긴다 — 원인을 추적할 수 없으면 못 고친다."""
    src = inspect.getsource(ingest.ingest_upload)
    i = src.find("if not pieces:")
    seg = src[i:i + 900]
    assert "LAST_ERRORS" in seg, "채널별 실제 사유를 읽지 않는다"
    assert "_why[:400]" in seg, "원문을 버린다"


def test_credit_detection_covers_the_real_error_shape():
    """D. 실측한 에러 문자열을 실제로 잡는가 — 감지기가 있어도 모양이 다르면 못 잡는다.
    2026-08-03 프로덕션 실측: BadRequestError(400, 'Your credit balance is too low…')."""
    from app import llm
    real = ("BadRequestError(\"Error code: 400 - {'type': 'error', 'error': "
            "{'type': 'invalid_request_error', 'message': 'Your credit balance is too low "
            "to access the Anthropic API. Please go to Plans & Billing'}}\")")
    assert llm._is_credit_error(Exception(real)), "실측 에러 모양을 못 잡는다"
    assert not llm._is_credit_error(Exception("APITimeoutError")), "무관한 오류를 크레딧으로 오인"
