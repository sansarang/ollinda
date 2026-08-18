"""LLM 라우팅 골든 — 비용이 조용히 되돌아가는 것을 막는다.

★ 2026-08-18 사장님 승인으로 만들었다.
  그전까지 본문 Solar는 **Railway 환경변수 하나**(LLM_BODY)에만 걸려 있었고
  코드 기본값은 Opus였다. 변수가 지워지거나 서비스를 다시 만들면
  아무 경고 없이 Opus로 돌아간다 — 실측 차이가 40배다.

    8/16 (Opus)  건당 $0.5435 · $0.7059 · $1.3680
    8/17 (Solar) 건당 $0.0110 · $0.0119 · $0.0147 · $0.0225

  침묵 폴백 금지는 산출물에만이 아니라 **비용에도** 적용된다.
  그래서 '환경변수가 하나도 없는 상태'에서 무엇으로 가는지를 여기서 못 박는다.
"""
import os

import pytest

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import llm

#: env가 전혀 없을 때 반드시 이 provider로 가야 하는 작업들.
#: 값은 **문자열 리터럴**로 적는다 — llm.SOLAR 같은 상수를 참조하면
#: 상수가 바뀔 때 테스트가 같이 따라가서 아무것도 못 잡는다(2026-08-17 자기기만 골든 4건).
MUST_BE_SOLAR = {
    "body": "solar-pro4",
    "caption": "solar-pro4",
    "spoken": "solar-pro4",
    "x": "solar-pro4",
    "aux": "solar-pro4",
    "judge": "solar-pro4",
    "title": "solar-pro4",
}


@pytest.fixture
def no_env(monkeypatch):
    """LLM_* 환경변수를 전부 지운 상태 — 코드 기본값만 남는다."""
    for k in list(os.environ):
        if k.startswith("LLM_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "QUALITY_PIN", {}, raising=False)
    return True


@pytest.mark.parametrize("task,model", sorted(MUST_BE_SOLAR.items()))
def test_환경변수가_없어도_솔라로_간다(no_env, task, model):
    """★ 이 테스트가 실패하면 그 작업의 비용이 40배가 된다."""
    provider, m = llm.route(task)
    assert provider == "upstage", f"{task}가 {provider}로 간다 — env 없이 Opus면 비용 40배"
    assert m == model, f"{task} 모델이 {m} (기대: {model})"


def test_본문은_크레딧이_0이어도_생성된다(no_env):
    """핵심 작업이 전부 non-anthropic이면 Anthropic 크레딧 없이도 글이 나와야 한다.
    2026-08-17 실사고: 본문만 Solar로 옮기고 보조 호출이 anthropic이라 생성이 계속 죽었다."""
    for t in llm._CORE_TASKS:
        assert llm.route(t)[0] != "anthropic", f"핵심 작업 {t}가 anthropic이다"
    assert llm.anthropic_needed() is False


def test_클로드에_남긴_자리는_의도적이다():
    """라우팅을 우회하는 `llm.call()` 직행은 **허용 목록**에만 있어야 한다.

    새 호출이 무심코 직행으로 추가되면 그 작업만 조용히 Opus로 간다 —
    2026-08-17에 정확히 그렇게 당했다(본문은 Solar인데 보조 호출이 anthropic).
    남긴 자리는 전부 '검수 계열'이고, 코드에 🔒 주석으로 이유가 붙어 있다.
    """
    import pathlib
    import re
    allowed = {
        ("app/seo.py", "사진에 없는 것을 실물처럼 썼는지 — 날조 방지 최후 관문"),
        ("app/main.py", "Anthropic 크레딧 생존 확인 ping"),
        ("app/services/lessons.py", "순위 하락 원인 추론 → 다음 글 교훈"),
        ("app/services/analyst.py", "상위 글과의 격차 분석"),
        ("app/generators/text_claude.py", "task 없이 부르는 마지막 폴백"),
    }
    allowed_files = {f for f, _ in allowed}
    root = pathlib.Path(__file__).resolve().parent.parent
    pat = re.compile(r"(?<![\w.])_?llm\.call\(")
    found = {}
    for py in (root / "app").rglob("*.py"):
        if py.name == "llm.py":
            continue
        rel = str(py.relative_to(root))
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if pat.search(line):
                found.setdefault(rel, []).append(i)
    unexpected = {f: ls for f, ls in found.items() if f not in allowed_files}
    assert not unexpected, (
        "라우팅을 우회하는 llm.call() 직행이 새로 생겼다 — 이 작업만 Opus로 간다.\n"
        "  Solar로 보낼 것이면 llm.call_task('judge'|'aux'|'title', ...)를 쓰고,\n"
        "  일부러 클로드에 남길 것이면 🔒 주석으로 이유를 적고 이 골든의 허용 목록에 넣어라.\n"
        f"  새로 생긴 것: {unexpected}")


def test_환경변수로_되돌릴_수는_있다(monkeypatch):
    """기본값을 고정해도 긴급 시 env로 바꿀 길은 남아야 한다(고정 ≠ 봉인)."""
    monkeypatch.setattr(llm, "QUALITY_PIN", {}, raising=False)
    monkeypatch.setenv("LLM_BODY", "anthropic:claude-opus-4-8")
    assert llm.route("body") == ("anthropic", "claude-opus-4-8")
