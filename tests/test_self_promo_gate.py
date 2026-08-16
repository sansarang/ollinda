"""자기 광고·제작시간 주장 차단 골든 (2026-08-16 실물 사고).

사고: 모델이 사장님 글 끝에 스스로 서명을 붙였다 —
      "이 글은 사진 몇 장으로 AI가 25분 만에 완성했습니다 · 올린다 ollinda.kr"
      실제 생성은 3.8분(06:50:03→06:53:51). 6배 부풀린 날조다.
      게다가 **사장님 블로그에 우리 광고**가 붙는다.

우리 코드 어디에도 그 문장이 없었다 — 모델이 지어냈고, 게이트가 못 잡았다.
'3초 만에 결과'(실측 126초)로 지적받은 것과 같은 계열이다(정직 게이트).
"""
from app.services.qualitycheck import _self_promo_hits as hits


def test_the_actual_incident_line_is_caught():
    bad = "이 글은 사진 몇 장으로 AI가 25분 만에 완성했습니다 · 올린다 ollinda.kr"
    assert hits(bad), "실제 사고 문장을 못 잡는다"


def test_fabricated_duration_is_caught():
    """걸린 시간은 모델이 알 수 없다 — 쓰면 무조건 지어낸 것."""
    for t in ("3분 만에 완성했습니다", "AI가 10분 만에 작성", "30초 만에 만들어 드립니다"):
        assert hits(t), f"제작 시간 주장을 못 잡는다: {t}"


def test_our_product_name_is_caught():
    """사장님 블로그에 우리 광고가 붙으면 안 된다."""
    assert hits("자세한 건 ollinda.kr 에서")
    assert hits("올린다 ollinda 로 만들었어요")


def test_normal_korean_verb_is_not_a_false_positive():
    """'올린다'는 흔한 동사다 — 통째로 막으면 정상 문장이 다 걸린다(오탐 = 새 사고)."""
    for ok in ("사진을 올린다", "블로그에 글을 올린다고 하셨죠",
               "매주 새 글을 올린다는 계획입니다", "리프트에 차를 올린다"):
        assert not hits(ok), f"정상 문장을 잡았다(오탐): {ok}"


def test_clean_body_passes():
    assert not hits("신차 유리막코팅을 부위별로 도포했습니다. 마감 상태를 다시 확인했습니다.")


def test_gate_is_wired_into_self_check():
    """지시는 확률, 게이트가 보장 — 프롬프트만으로는 또 새어 나온다."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "app", "services", "qualitycheck.py"), encoding="utf-8").read()
    assert "자기 광고·제작시간 주장 없음" in src, "검사 항목이 없다"
    gen = open(os.path.join(root, "app", "generators", "text_claude.py"), encoding="utf-8").read()
    assert "글 끝에 서명·출처를 붙이지 마라" in gen, "생성 지시가 없다"
    assert "너는 그 시간을 모른다" in gen, "시간 주장 금지 근거가 없다"
