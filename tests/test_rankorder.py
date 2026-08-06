"""📶 순위 서열 골든 — 오늘 밟은 함정 전부 반영."""
import inspect

from app.services.rankorder import collector as C
from app.services.rankorder import monotone as M


def test_R6_자부터_검증한다():
    """오늘 answer_fit이 1·3만 뱉어 자를 의심했다. 극단값에서 벌어지는지 먼저 본다."""
    rho, p = M.spearman(list(range(1, 11)), list(range(10, 0, -1)))
    assert rho == -1.0 and p == 0.0, "완벽 단조를 못 잡는다"
    rho2, p2 = M.spearman(list(range(1, 11)), [3, 9, 1, 7, 5, 2, 8, 4, 10, 6])
    assert abs(rho2) < 0.6 and p2 > 0.05, "무작위를 단조라고 한다"
    # 동점 처리(평균 순위)
    assert M._ranks([5, 5, 1]) == [2.5, 2.5, 1.0]


def test_R2_업종교차가_아니면_후보가_아니다():
    """한 업종의 상관은 그 업종 특성일 수 있다(오늘 tables 함정)."""
    one = [{"rank": i, "x": 11 - i, "industry": "A"} for i in range(1, 11)]
    assert M.analyze(one)["candidates"] == [], "한 업종만으로 후보를 만든다"
    two = one + [{"rank": i, "x": 11 - i, "industry": "B"} for i in range(1, 11)]
    assert len(M.analyze(two)["candidates"]) == 1
    # 방향이 엇갈리면 인자가 아니다
    opp = one + [{"rank": i, "x": i, "industry": "B"} for i in range(1, 11)]
    assert M.analyze(opp)["candidates"] == [], "방향 엇갈림을 후보로 올린다"


def test_표본_부족은_미확정이다():
    few = [{"rank": i, "x": 11 - i, "industry": "A"} for i in range(1, 4)]
    r = M.analyze(few)
    assert r["industries"]["A"]["note"] == "표본 부족(미확정)"
    assert r["candidates"] == []


def test_R5_인과를_말하지_않는다():
    assert "상관이지 인과가 아니다" in inspect.getsource(M), "인과 구분이 없다"
    assert "서열 인자 후보" in inspect.getsource(M), "확정 인자로 부른다"


def test_R1_R3_실운영업종과_우리채널을_뺀다():
    """썬팅·중고차 영구 제외 + 우리 글 습관 오염 방지(오늘 FAQ 함정)."""
    src = inspect.getsource(C.collect)
    assert "_sc.filter_queries" in src, "실운영 업종을 안 거른다"
    assert "exclude_blogs" in src, "우리 채널을 뺄 수 없다"
    assert "exclude_blogs" in str(inspect.signature(C.collect))


def test_R4_순위는_등장_순서_실측이다():
    src = inspect.getsource(C.collect)
    assert "enumerate(posts, 1)" in src, "순위를 실제 등장 순서로 안 매긴다"
    assert "PLACE_JS" in src, "공통 파서를 안 쓴다(사본 금지)"


def test_새_축이_기록된다():
    """공감·댓글·발행경과는 오늘 안 본 축이다 — 공개 표면에서 잴 수 있다."""
    src = inspect.getsource(C.collect)
    for k in ("like", "comment", "age_days"):
        assert f'"{k}"' in src, f"새 축 누락: {k}"
    assert C._days_since("2026. 7. 14") is not None
    assert C._days_since("") is None


def test_R8_원본_보존():
    assert '"a"' in inspect.getsource(C.collect), "원본을 덮어쓴다"
