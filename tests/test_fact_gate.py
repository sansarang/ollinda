"""날조 방지 게이트(seo.subject_match) 골든.

이 게이트는 **정직 게이트의 최후 관문**이다. 글이 '지금 여기 있는 실물'처럼 서술하는 소재가
사진 분석에서 확인되는지 기계로 검증한다.
실사고(2026-07-27): 캡션이 키워드 '캐스퍼'에 끌려 토레스 사진 세트에 '오늘 들여온 캐스퍼'·
'휠 스크래치'를 날조했다. 프롬프트 지시로는 못 막아 이 게이트를 만들었다.

★ 2026-08-18 — 이 게이트가 **크레딧 때문에 죽어 있었다.**
  haiku를 부르는데 Anthropic 크레딧이 0이라 매번 400으로 실패했고(Sentry 79건),
  실패는 None을 돌려주므로 fail-open — 즉 날조 검사 없이 글이 나가고 있었다.
  Sentry를 켜기 전까지 종일 몰랐다(규율 11이 여기서 나왔다).

  → Solar로 옮겼다. 실측 10/10(인공 6 + 실전 4), 날조 놓침 0.
     실전은 프로덕션 글 2,765자에 차종·수치·없는차량 날조를 주입했고 전부 잡았다.
"""
import ast
import inspect
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import llm, seo  # noqa: E402


def _code(fn) -> str:
    """주석·독스트링을 뺀 코드만 — 거기엔 '왜 그렇게 했는지'가 적혀 있어 오탐을 낸다."""
    src = inspect.getsource(fn)
    tree = ast.parse(src.lstrip())
    f = tree.body[0]
    if f.body and isinstance(f.body[0], ast.Expr) and isinstance(f.body[0].value, ast.Constant):
        f.body = f.body[1:]
    return ast.unparse(f)


def test_게이트가_크레딧에_묶이지_않는다():
    """★ 이 파일의 존재 이유. 크레딧이 0이어도 날조 검사는 돌아야 한다.

    전에는 haiku를 직행으로 불렀다. 크레딧이 떨어지자 게이트가 통째로 죽었고,
    실패가 fail-open이라 **아무도 모르게** 검사 없이 글이 나갔다.
    """
    code = _code(seo.subject_match)
    assert "claude" not in code.lower(), "특정 모델을 직행으로 부른다 — 크레딧에 묶인다"
    assert "call_task" in code, "라우팅을 거치지 않는다"
    assert llm.route("judge")[0] != "anthropic", \
        "judge가 anthropic으로 간다 — 크레딧 0이면 게이트가 또 죽는다"


def test_사진분석이_없으면_판정하지_않는다():
    """재료가 없는데 판정하면 그게 날조다 — 없으면 없다고 한다(None)."""
    assert seo.subject_match("아무 글", "", "썬팅") is None
    assert seo.subject_match("아무 글", "사진 분석 없음", "썬팅") is None
    assert seo.subject_match("", "[사진1] 차량", "썬팅") is None


def test_판정_실패는_통과시킨다_그러나_조용하지_않다():
    """fail-open은 의도된 설계다(게이트 고장으로 생성 전체를 막지 않는다).
    ★ 다만 그래서 **게이트가 죽어도 화면상 아무 일이 없다** — 2026-08-18에 정확히 그랬다.
      죽었는지는 라우팅으로 보증한다(위 테스트). 이 테스트는 fail-open 자체를 고정한다."""
    import app.llm as _llm
    orig = _llm.call_task
    try:
        _llm.call_task = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("죽음"))
        r = seo.subject_match("글", "[사진1] 차량 후면", "썬팅")
        assert r is None, "게이트 고장이 글을 죽이면 안 된다(fail-open)"
    finally:
        _llm.call_task = orig


def test_YES_NO_해석이_뒤집히지_않는다():
    """★ 여기가 뒤집히면 날조를 통과시키고 정상을 막는다 — 최악의 결함이다."""
    import app.llm as _llm
    orig = _llm.call_task
    note, text = "[사진1] 현대 쏘나타 후면", "쏘나타 썬팅을 했습니다"
    try:
        for answer, expect in (("YES", True), ("NO", False), ("yes", True), ("no", False),
                               ("", None), ("잘 모르겠습니다", None)):
            _llm.call_task = lambda *a, _r=answer, **k: _r
            assert seo.subject_match(text, note, "썬팅") is expect, \
                f"{answer!r} → {expect} 여야 한다"
    finally:
        _llm.call_task = orig
