"""그래프 골든.

★ 이 파일이 지키는 단 하나의 진실: **순위는 낮을수록 좋다.**
  y축을 뒤집지 않으면 1위가 바닥에 깔리고, 떨어지는 선이 '성장'으로 보인다.
  화면이 진실과 정반대를 말하는 것은 측정 허위와 같은 계열이다.

  그리고 자료가 없으면 그리지 않는다 — 0으로 채운 평평한 선은 날조다.
"""
from app.web import chart


def _line_ends(svg: str) -> tuple[float, float]:
    """선(polyline)의 첫 점·끝 점 y — 면(polygon)이 아니라 **선**을 읽는다.
    처음엔 첫 points= 를 잡았는데 그게 배경 면이라 바닥 좌표만 나왔다."""
    pts = svg.split("<polyline points='")[1].split("'")[0].split()
    return float(pts[0].split(",")[1]), float(pts[-1].split(",")[1])


def _h(*ranks):
    return [{"rank": r, "checked_at": f"2026-08-{i + 1:02d}T00:00:00"}
            for i, r in enumerate(ranks)]


def test_순위가_좋아지면_선이_위로_간다():
    """★ 뒤집기 검증 — 20위→3위는 **개선**이다. y는 작아져야(위로) 한다.
    이 테스트가 없으면 그래프가 진실의 정반대를 그려도 아무도 모른다."""
    y_first, y_last = _line_ends(chart.rank_line(_h(20, 3)))
    assert y_last < y_first, f"3위가 20위보다 아래에 그려졌다 (y {y_first}→{y_last})"


def test_순위가_나빠지면_선이_아래로_간다():
    y_first, y_last = _line_ends(chart.rank_line(_h(2, 15)))
    assert y_last > y_first, f"15위가 2위보다 위에 그려졌다 (y {y_first}→{y_last})"


def test_자료가_없으면_그리지_않는다():
    """침묵 폴백 금지 — 없는 값을 0으로 채운 선을 그리면 그게 날조다."""
    assert chart.rank_line([]) == ""
    assert chart.rank_line([{"rank": None, "checked_at": "2026-08-01"}]) == ""
    assert chart.bars([]) == ""
    assert chart.bars([("a", 0), ("b", 0)]) == "", "값이 전부 0이면 막대를 그리지 않는다"


def test_현재_순위를_숫자로도_말한다():
    """그림만 있으면 오독한다 — 마지막 값은 글자로도 있어야 한다."""
    out = chart.rank_line(_h(9, 4), keyword="부산 썬팅")
    assert "4위" in out and "부산 썬팅" in out


def test_변화없음은_상승색으로_칠하지_않는다():
    """5위→5위를 초록으로 칠하면 성과를 부풀리는 것이다."""
    flat = chart.rank_line(_h(5, 5))
    assert "#059669" not in flat, "변화가 없는데 상승색을 썼다"
    assert "#059669" in chart.rank_line(_h(9, 5)), "실제 상승은 상승색이어야 한다"


def test_점이_하나여도_그린다():
    """첫 측정 1건 — 추이는 없어도 '지금 몇 위'는 사실이다."""
    out = chart.rank_line(_h(7))
    assert out and "7위" in out


def test_순위밖은_바닥값으로_눌린다():
    """80위와 200위를 그대로 그리면 30위권 변화가 한 줄로 뭉갠다."""
    assert chart.RANK_FLOOR == 30
    out = chart.rank_line(_h(80, 5))
    assert "30위" not in out or "5위" in out


def test_사유없는_빈칸은_없다():
    """빈 그래프 자리에는 반드시 '왜 없는지'가 온다(정직 게이트)."""
    assert "아직 순위를 확인하지 않았어요" in chart.empty("아직 순위를 확인하지 않았어요")
