"""Upstage Solar 라우팅 골든 (2026-08-17 사장님 승인).

도입 근거 — 같은 재료·같은 프롬프트로 잰 A/B 실측:
  | 경로                        | 커버 | 두꺼운문단 | 사진뭉침 | 날조 | 원가     |
  | 발행글(오퍼스+파이프라인)   | 2/2  | 3          | 1곳      | 1건  | $0.5435  |
  | 오퍼스 초안 1회             | 1/2  | 2          | 5곳      | 0    | $0.2635  |
  | Solar minimal(기본값)       | 0/2  | 2          | 0곳      | 0    | $0.0016  |
  | Solar medium                | 2/2  | 2          | 0곳      | 0    | $0.0030  |
  | Solar high                  | 1/2  | 3          | 7곳      | 0    | $0.0076  |

여기서 막는 재발:
  ① reasoning_effort가 기본값(minimal)으로 새는 것 — 그러면 커버리지가 0/2로 죽는다.
     이건 조용히 일어난다: 응답은 정상이고 글도 그럴듯한데 노린 질의에 답이 없다.
  ② Solar에 이미지를 태우는 것 — 텍스트 전용 모델이다.
  ③ 실패가 침묵 폴백되는 것 — 빈 응답을 성공으로 넘기면 그게 곧 빈 글이다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

import importlib

from app import llm


def test_라우팅이_upstage를_받는다(monkeypatch):
    monkeypatch.setenv("LLM_BODY", "upstage:solar-pro4")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    assert llm.route("body") == ("upstage", "solar-pro4")


def test_모르는_provider는_기본값으로_떨어진다(monkeypatch):
    monkeypatch.setenv("LLM_BODY", "openai:gpt-9")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    p, _ = llm.route("body")
    assert p == "anthropic", "검증 안 된 provider가 통과했다"


def test_추론강도가_medium이다():
    """★ 이 값이 minimal로 새면 노린 질의 커버가 0/2로 죽는다(실측).
    응답은 정상이고 글도 그럴듯해서 눈으로는 안 잡힌다 — 그래서 골든으로 막는다."""
    m = importlib.reload(llm)
    assert m.SOLAR_EFFORT == "medium", f"추론 강도가 {m.SOLAR_EFFORT}로 바뀌었다"


def test_solar_단가가_등록돼_원가집계에_잡힌다():
    assert "solar" in llm._PRICES
    llm.cost_reset()
    llm._track_cost("solar", 2_000_000, 1_000_000)
    # in 2M × $0.30 + out 1M × $1.20 = $1.80
    assert abs(llm.cost_snapshot()["usd"] - 1.80) < 0.01, "solar 원가가 집계되지 않는다"


def test_빈_응답은_실패로_올린다(monkeypatch):
    """추론만 하고 본문을 안 내는 경우가 있다. 빈 문자열을 성공으로 넘기면 빈 글이 나간다."""
    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "  "}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 900,
                              "completion_tokens_details": {"reasoning_tokens": 900}}}
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", lambda *a, **k: _R())
    try:
        llm._upstage_generate("x", "solar-pro4", 1000)
        raise AssertionError("빈 응답이 성공으로 통과했다")
    except RuntimeError as e:
        assert "빈 응답" in str(e)


def test_키_없으면_명시적으로_실패한다(monkeypatch):
    monkeypatch.delenv("UPSTAGE_API_KEY", raising=False)
    try:
        llm._upstage_generate("x", "solar-pro4", 100)
        raise AssertionError("키 없이 호출이 통과했다")
    except RuntimeError as e:
        assert "UPSTAGE_API_KEY" in str(e)


def test_이미지가_있으면_solar로_보내지_않는다(monkeypatch):
    """Solar는 텍스트 전용이다. 비전은 검증된 gemini 경로를 그대로 쓴다."""
    monkeypatch.setenv("LLM_BODY", "upstage:solar-pro4")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    called = []
    monkeypatch.setattr(llm, "_upstage_generate",
                        lambda *a, **k: called.append(1) or "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class _B:
        type = "text"
        text = "vision-text"

    class _M:
        content = [_B()]

    class _C:
        def __init__(self, *a, **k):
            self.messages = type("X", (), {"create": lambda *a, **k: _M()})()

    monkeypatch.setattr("anthropic.Anthropic", _C)
    out = llm.call_task("body", "p", images=[("image/jpeg", "AAA")])
    assert not called, "이미지를 Solar에 태웠다"
    assert out == "vision-text"


def test_solar_실패는_anthropic으로_폴백된다(monkeypatch):
    """새 provider가 파이프라인을 멈추면 안 된다 — 기존 폴백 사슬을 지킨다."""
    monkeypatch.setenv("LLM_BODY", "upstage:solar-pro4")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    monkeypatch.setattr(llm, "_upstage_generate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("upstage 500")))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(llm, "call", lambda *a, **k: "anthropic-text")
    assert llm.call_task("body", "p") == "anthropic-text"
    assert llm.LAST_ROUTE["body"]["fallback"] is True, "폴백이 기록되지 않았다(원가 추적 불가)"
