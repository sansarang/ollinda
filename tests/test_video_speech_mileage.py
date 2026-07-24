"""주행거리 단일화 + TTS 숫자 한국어 발화 박제."""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")
from app.generators import video as v  # noqa: E402


def test_num_to_kr():
    assert v._num_to_kr(12272) == "만 이천이백칠십이"
    assert v._num_to_kr(2900) == "이천구백"
    assert v._num_to_kr(2022) == "이천이십이"
    assert v._num_to_kr(830) == "팔백삼십"
    assert v._num_to_kr(10000) == "만"


def test_speechify_units():
    assert v._speechify("12,272km") == "만 이천이백칠십이 킬로미터"
    assert v._speechify("2,900만원") == "이천구백만 원"
    assert v._speechify("2022년식") == "이천이십이 년식"
    # 자막 원문은 낱자 숫자 유지 대상 아님 — 발화 텍스트만 변환(원문 인자는 불변)
    orig = "12,272km 그랜저"
    assert v._speechify(orig) != orig and "km" not in v._speechify(orig)


def test_speech_gate_catches_unconverted():
    # 정상 변환 후엔 4자리+ 숫자 없음
    assert v._speech_number_left(v._speechify("계기판 12,272km, 2,900만원")) == ""
    # 하이픈 숫자열(전화·VIN)은 예외(반려 아님)
    assert v._speech_number_left("문의 010-1234-5678") == ""
    # 미변환 4자리 수량 남으면 검출
    assert v._speech_number_left("코드 45210 확인") == "45210"


def test_mileage_single_value():
    """상이 주행거리(12,269 vs 12,272)를 canonical 하나로 단일화 → 오판독값 제거."""
    out = v._normalize_mileage("성능지 12,269km, 계기판 12,272km 일치", "12,272km")
    assert "12,269" not in out
    assert out.count("12,272km") == 2  # 둘 다 canonical로


def test_render_storyboard_has_mileage_param():
    import inspect
    assert "mileage" in inspect.signature(v.ShortVideoGenerator.render_storyboard).parameters
