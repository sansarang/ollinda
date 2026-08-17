"""블로그 서명 게이트 골든 (2026-08-17 사장님 지시: 완전 삭제).

무엇이 있었나 — 두 실계정(루마썬팅·주안모터스)의 `tenants.blog_signature`에
이 문장이 저장돼 발행 글 끝마다 붙고 있었다:

    "이 글은 사진 몇 장으로 AI가 25분 만에 완성했습니다 · 올린다 ollinda.kr"

세 겹으로 틀렸다:
  ① 실제 생성 시간은 3.8분인데 25분이라고 썼다 — 우리가 금지한 날조를 우리가 했다
  ② 사장님(고객) 블로그에 우리 도메인 광고를 붙였다 — 대행 상품에서는 계약 위반 소지
  ③ 'AI가 썼다'를 스스로 공표했다 — 무색무취한 AI 글을 누락시킨다는 우리 전략과 정면 충돌

왜 안 잡혔나 — `qualitycheck._SELF_PROMO`에 이걸 잡는 규칙이 **이미 있었다.**
그런데 서명은 **품질 검사가 끝난 뒤에** 붙는다. 게이트 없는 산출물 표면이었다.
모델을 오퍼스에서 Solar로 바꿔도 계속 나온 이유가 이것이다(모델과 무관).

여기서 막는 재발: 입구(저장)와 출구(삽입) 양쪽. 한쪽만 막으면 다른 쪽으로 들어온다.
"""
import os
import re

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services.qualitycheck import _self_promo_hits

BAD = "이 글은 사진 몇 장으로 AI가 25분 만에 완성했습니다 · 올린다 ollinda.kr"


def test_그_서명이_규칙에_걸린다():
    hits = _self_promo_hits(BAD)
    assert hits, "실제로 발행됐던 서명이 검출되지 않는다"


def test_변형도_걸린다():
    """숫자·표현을 바꾼 재시도를 막는다 — 표면이 아니라 계열로 잡아야 한다."""
    for s in ("AI가 3분 만에 완성했습니다",
              "이 글은 AI가 작성했습니다",
              "올린다 ollinda.kr",
              "10초 만에 작성"):
        assert _self_promo_hits(s), f"변형이 통과했다: {s}"


def test_정상_서명은_통과한다():
    """게이트가 과하면 쓸 수 있는 서명이 없어진다."""
    for s in ("사진과 실제 시공 기록을 바탕으로 작성했습니다",
              "문의는 매장으로 연락 주세요",
              "부산 동구 루마썬팅 현대상사"):
        assert not _self_promo_hits(s), f"정상 문구가 막혔다: {s}"


def test_삽입_지점에_게이트가_있다():
    """★ 핵심 — 검사 뒤에 붙는 표면이라 규칙이 있어도 통과했다."""
    import inspect

    from app.services import ingest
    src = inspect.getsource(ingest)
    i = src.find("blog_signature")
    assert i > 0
    seg = src[i:i + 1600]
    assert "_self_promo_hits" in seg or "_sph" in seg, \
        "서명 삽입 지점에 자기광고 검사가 없다(게이트 없는 표면)"
    assert "삽입 거부" in seg, "걸렸을 때 거부하고 로그를 남기지 않는다"


def test_저장_지점에도_게이트가_있다():
    """입구를 막지 않으면 다음 사람이 또 저장한다."""
    import inspect

    from app import main
    src = inspect.getsource(main.admin_set_signature)
    assert "_self_promo_hits" in src or "_sph" in src, "서명 저장에 검사가 없다"
    assert "400" in src or "status_code" in src, "걸린 서명을 거부하지 않는다"
