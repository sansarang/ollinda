"""GEO 프롬프트 자가 점검 골든 — 2026-08-07 실측 보강.

'지켜라'라고 적어둔 지시가 있었는데도 G1 5/6·G2·G3에서 떨어졌다.
출력 직전에 스스로 세어보게 하는 항목을 넣었다. 이 게이트는 전 업종 공통이다.
"""
import inspect

from app.services import geo_track as G


def test_출력_전_자가_점검이_프롬프트에_있다():
    src = inspect.getsource(G.info_prompt)
    assert "출력 전 자가 점검" in src, "자가 점검 항목이 없다"
    # 실제로 떨어진 세 항목을 각각 짚는다
    assert "모든** ##" in src or "모든" in src, "G1(소제목 전부 질문형) 점검이 없다"
    assert "평서문 정답" in src, "G2(첫 문장 완결 정답) 점검이 없다"
    assert "3개 이상" in src, "G3(수치 3개) 점검이 없다"
    assert "지어내지 말고" in src, "수치를 채우려 날조할 여지를 남긴다"


def test_게이트와_점검항목이_어긋나지_않는다():
    """프롬프트가 요구하는 것과 게이트가 검사하는 것이 다르면 영영 통과 못 한다."""
    gate = inspect.getsource(G.geo_gate)
    prompt = inspect.getsource(G.info_prompt)
    for key in ("자주 묻는 질문", "요약"):
        assert key in gate and key in prompt, f"게이트/프롬프트 불일치: {key}"


def test_업종어를_박지_않는다():
    """전 업종 공통 게이트다 — 특정 업종 낱말이 들어가면 그 업종에서만 통한다."""
    src = inspect.getsource(G)
    for w in ("썬팅", "중고차", "미용실", "치과", "필라테스", "카페"):
        assert w not in src, f"업종어가 박혔다: {w}"
