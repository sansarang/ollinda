"""같은 것을 두 곳에서 말하지 않는다 — 중복 표면 골든.

2026-08-18 사장님:
  "최근 발행 확인건?? 이거는 각 컨텐츠 분석과 중복되지 않니?"

맞았다. 블로그 카드의 '최근 발행 확인' 목록이 보여주던 것은 전부 다른 화면에 있었다:
  제목·발행일 → 홈 목록(날짜별)  ·  순위/N일차 → 조사 카드 그래프
  '이 글, 왜 이렇게 썼냐면' → 조사 카드 조사 항목  ·  주간 리포트 → 순위 그래프

중복 표면은 단순히 지저분한 게 아니다 — **같은 사실을 두 곳에서 다르게 말하게 된다.**
한쪽만 고치면 다른 쪽이 옛 값을 계속 말한다(캡션 결함이 10회 재발한 것과 같은 계열).

그리고 표면을 걷어낼 때 **버튼만 옮기고 그 버튼을 움직이는 스크립트를 두고 오면**
눌러도 아무 일이 없다. 오늘 실제로 그럴 뻔했다(analystView).
"""
import inspect
import os
import re

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import main


def _code(fn) -> str:
    """주석을 뺀 코드만 — 주석에는 '왜 지웠는지'가 적혀 있어서 그대로 검사하면
    지운 이름이 주석에 남았다는 이유로 오탐이 난다(실제로 났다)."""
    out = []
    for line in inspect.getsource(fn).splitlines():
        t = line.strip()
        if t.startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def test_주간리포트는_사라졌다():
    """사장님 지시로 제거 — 발행글마다 순위 추이 그래프가 그 역할을 한다."""
    src = _code(main._blog_connect_card)
    assert "latest_weekly_report" not in src, "주간 리포트가 되살아났다"
    assert "주간 리포트 <span" not in src


def test_발행목록이_두_곳에_있지_않다():
    """'최근 발행 확인' 목록은 홈 목록·조사 카드와 겹쳐서 지웠다."""
    src = _code(main._blog_connect_card)
    assert "최근 발행 확인" not in src, "중복 목록이 되살아났다"
    assert "def _pub_row(" not in src


def test_진단_버튼이_조사카드에_있다():
    """겹치지 않던 셋([순위 추적]·[왜 안 뜨나요?]·발행글 링크)은 살려야 한다."""
    src = _code(main._research_card)
    assert "raceView(" in src, "순위 추적 버튼이 없다"
    assert "whyNot(" in src, "'왜 안 뜨나요? 진단' 버튼이 없다"
    assert "발행된 글 보기" in src, "발행된 글로 가는 링크가 없다"


def test_스크립트가_버튼과_같은_화면에_있다():
    """★ 오늘 실제로 당할 뻔했다.

    `analystView`는 /api/race 응답 HTML 안의 버튼이 부른다. 그 결과가 삽입되는 곳은
    조사 카드다. 그런데 함수 정의는 블로그 카드(홈)에 있었다 —
    버튼은 미리보기에, 정의는 홈에. 눌러도 아무 일이 없는 '죽은 자리'다.
    """
    card = _code(main._research_card)
    for fn in ("whyNot", "raceView", "analystView"):
        assert f"async function {fn}(" in card, \
            f"{fn} 정의가 조사 카드에 없다 — 버튼만 있고 동작이 없으면 죽은 자리다"


def test_스크립트가_두_번_정의되지_않는다():
    """같은 함수가 두 곳에 정의되면 나중 것이 이긴다 — 한쪽만 고치면 조용히 어긋난다."""
    src = inspect.getsource(main)
    for fn in ("whyNot", "raceView", "analystView"):
        n = src.count(f"async function {fn}(")
        assert n == 1, f"{fn}이 {n}번 정의됐다(중복 정의)"


def test_조사카드에_그려질_것이_없으면_아무것도_안_그린다():
    """빈 카드는 '조사했는데 결과가 없다'로 읽힌다."""
    class _Empty:
        id = ""
        tenant_id = ""
        payload = {}

    class _T:
        id = ""
        name = "x"
    assert main._research_card(_T(), _Empty()) == ""
