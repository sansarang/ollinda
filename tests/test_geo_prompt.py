"""GEO 프롬프트 자가 점검 골든 — 2026-08-07 실측 보강.

'지켜라'라고 적어둔 지시가 있었는데도 G1 5/6·G2·G3에서 떨어졌다.
출력 직전에 스스로 세어보게 하는 항목을 넣었다. 이 게이트는 전 업종 공통이다.
"""
import inspect

from app.services import geo_track as G


def _rendered_prompt() -> str:
    """문구 위치가 아니라 실제로 LLM에 가는 프롬프트를 문다(사용 기준)."""
    from types import SimpleNamespace
    t = SimpleNamespace(name="골든가게")
    return G.info_prompt(t, "테스트업", "테스트구", "골든 키워드", "review", "", 3)


def test_출력_전_자가_점검이_프롬프트에_있다():
    src = _rendered_prompt()
    assert "출력 전 자가 점검" in src, "자가 점검 항목이 없다"
    # 실제로 떨어진 세 항목을 각각 짚는다
    assert "모든** ##" in src or "모든" in src, "G1(소제목 전부 질문형) 점검이 없다"
    assert "평서문 정답" in src, "G2(첫 문장 완결 정답) 점검이 없다"
    assert "3개 이상" in src, "G3(수치 3개) 점검이 없다"
    assert "지어내지 말고" in src, "수치를 채우려 날조할 여지를 남긴다"


def test_재생성도_같은_구조_계약을_싣는다():
    """실측(2026-08-07): honesty 지시만 실은 재생성이 GEO 구조를 무너뜨렸다(최종 G1 1/7).
    생성과 재생성이 같은 자가 점검 한 덩이(SELF_CHECK)를 쓴다 — 같은 재료는 같은 소스."""
    from app.services import revise as R
    src = inspect.getsource(R.revise_piece)
    assert "SELF_CHECK" in src, "재생성이 트랙 B 구조 계약을 모른다(수정마다 구조 붕괴 재발)"
    assert "content_type" in src, "트랙 판별 없이 전 글에 계약을 싣거나 아예 안 싣는다"
    assert "출력 전 자가 점검" in G.SELF_CHECK


def test_체인_재생성은_실패한_전_게이트_지시를_싣는다():
    """첫 사유만 실으면 다른 게이트를 모른 채 다시 써서 그 게이트를 깨뜨린다."""
    from app.services import autoqueue as AQ
    src = inspect.getsource(AQ.consume)
    assert "_cf[0][1]" not in src, "재생성이 첫 실패 사유만 싣는다(다른 게이트 파괴 재발)"
    assert "for c in _cf" in src, "실패 전 게이트 지시를 모아 싣지 않는다"


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
