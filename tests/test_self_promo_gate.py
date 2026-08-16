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


# ── 점수 이름 정직화 (2026-08-16 사장님 질문) ────────────────────────────

def test_score_label_does_not_claim_to_predict_ranking():
    """사장님 질문: "상위노출 66점은 어디서 측정한 거니? 실제로 근거가 있는 점수니?"

    실체: seo.quality_audit()이 100에서 항목별로 빼는 **구조 체크리스트**다.
      · 감점 폭(-15·-12·-6·-5·-4)은 손으로 정한 값이고 실측에서 나오지 않았다
      · **실제 순위와 대조된 적이 없다**(발행 글 6건뿐이라 지금은 대조도 불가)
      · 게다가 이 점수는 글 전체 구조만 본다 — 네이버는 문단을 뽑아 노출한다(2026-08-16 실측)
    그런데 이름이 '상위노출 점수'라 노출을 예측한다는 뜻으로 읽혔다. 검증 안 된 주장이다.
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "app", "main.py"), encoding="utf-8").read()
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "상위노출 {sc}점" not in body, "배지가 아직 노출을 예측한다고 주장한다"
    assert "상위노출 점검:" not in body, "점검 패널이 아직 노출을 주장한다"
    assert "글 구조 {sc}점" in body, "사실에 맞는 이름이 없다"
    assert "순위를 예측하는 점수가 아니에요" in body, "예측이 아니라는 고지가 없다"


# ── 검사만으로는 안 지워진다 — 기계 삭제 (2026-08-16 실물 재발) ──────────

def test_self_promo_is_actually_removed_not_just_detected():
    """실물 재발: 게이트가 잡았는데 글에는 그대로 남았다.
    자체 수정 대상(_FIXABLE_KEYS)이 '금지 클리셰·어미 다양성' 둘뿐이라
    새 항목은 **잡히기만 하고 지워지지 않았다.** 프롬프트 지시도 모델이 계속 어겼다.
    이건 문장 재작성이 아니라 한 줄 삭제라 기계가 해야 한다."""
    from app.services.qualitycheck import strip_self_promo as strip
    body = ("본문 마지막 문단입니다.\n\n함께 보면 좋은 글\n\n"
            "부산 썬팅 정리\nhttps://blog.naver.com/x/1\n\n---\n"
            "이 글은 사진 몇 장으로 AI가 25분 만에 완성했습니다 · 올린다 ollinda.kr")
    out = strip(body)
    assert not hits(out), f"삭제 후에도 신호가 남았다: {hits(out)}"


def test_removal_preserves_the_article():
    """서명만 지워야 한다 — 본문·링크가 함께 사라지면 그게 더 큰 사고다."""
    from app.services.qualitycheck import strip_self_promo as strip
    body = ("본문입니다.\n\n부산 썬팅 정리\nhttps://blog.naver.com/x/1\n\n---\n"
            "이 글은 AI가 25분 만에 완성했습니다 · ollinda.kr")
    out = strip(body)
    assert "부산 썬팅 정리" in out and "blog.naver.com/x/1" in out, "본문·링크가 지워졌다"
    assert not out.rstrip().endswith("---"), "구분선 꼬리가 남았다"


def test_clean_body_is_untouched():
    from app.services.qualitycheck import strip_self_promo as strip
    clean = "아무 문제 없는 본문입니다.\n\n두 번째 문단."
    assert strip(clean) == clean, "깨끗한 글을 건드렸다"


def test_removal_is_wired_into_the_mechanical_pass():
    """지시·검사만 있으면 또 새어 나간다 — 기계 수선 경로에 걸려 있어야 한다."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "app", "services", "qualitycheck.py"), encoding="utf-8").read()
    assert "strip_self_promo(fix_orphan_parens(" in src, "기계 수선 패스에 안 걸렸다"
