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
    """검증 안 된 provider 이름은 무시하고 **그 작업의 코드 기본값**으로 간다.

    ★ 2026-08-18 — 기대값을 anthropic에서 바꿨다. 테스트가 무뎌진 게 아니라
      계약이 바뀐 것이다: 이날 body의 코드 기본값이 Opus → Solar가 됐다
      (env 하나 지워지면 비용 40배로 되돌아가는 구멍을 막느라).
      지키려는 것은 그대로다 — **알 수 없는 provider가 그대로 통과하면 안 된다.**
    """
    monkeypatch.setenv("LLM_BODY", "openai:gpt-9")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    p, m = llm.route("body")
    assert p != "openai" and m != "gpt-9", "검증 안 된 provider가 통과했다"
    assert (p, m) == llm.TASK_DEFAULTS["body"], "기본값이 아닌 엉뚱한 곳으로 떨어졌다"


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


def test_본문_생성이_라우팅을_실제로_탄다(monkeypatch):
    """★ 2026-08-17 실사고 — text_claude._call_llm이 llm.call(anthropic 직행)만 불러
    LLM_BODY 라우팅을 통째로 우회했다. env를 넣고 '적용됐다'고 믿은 채 다른 원인을
    Solar 탓으로 오진했다. 설정이 조용히 무시되는 것이 가장 비싼 결함이다.

    여기서 막는 것: 본문 호출이 다시 call_task를 안 타게 되는 것.
    """
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert 'task="body"' in src, "본문 호출이 라우팅 태스크를 잃었다(LLM_BODY가 무시된다)"

    # _call_llm 자체가 task를 call_task로 넘기는지(task 없으면 기존 경로 — 하위호환)
    called = {}

    def _fake_task(t, *a, **k):
        called["task"] = t
        return "routed"

    monkeypatch.setattr(llm, "call_task", _fake_task)
    monkeypatch.setattr(llm, "call", lambda *a, **k: "direct")
    assert tc._call_llm("p", task="body") == "routed", "task를 줬는데 라우팅을 안 탔다"
    assert called["task"] == "body"
    assert tc._call_llm("p") == "direct", "task 없이 부르면 기존 경로여야 한다(하위호환)"


def test_짧은_보조호출은_추론을_낮춘다():
    """★ 이 골든은 원래 '보조 호출은 라우팅을 타지 않는다'였다 — **그 판단이 틀렸다.**

    당시 이유는 "추론 모델로 보내면 느려진다"였다. 속도는 맞는 걱정이었지만,
    2026-08-17 실사고가 더 큰 것을 드러냈다: 본문만 Solar로 옮기고 보조 호출을
    anthropic에 남겨두니 **크레딧 0에서 세트가 통째로 실패**했다.
    한 세트가 완성되려면 그 세트의 모든 호출이 살아 있는 경로여야 한다.

    속도 걱정은 라우팅을 막는 대신 **추론 강도를 낮춰서** 푼다(aux → low).
    """
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc)
    for line in src.splitlines():
        if "YES 또는 NO" in line:
            idx = src.splitlines().index(line)
            seg = "\n".join(src.splitlines()[idx:idx + 4])
            assert 'task="aux"' in seg, "짧은 판정이 라우팅을 안 탄다(크레딧 0에서 죽는다)"
    assert llm.solar_effort("aux") == "low", "보조 호출에 추론을 많이 주면 느려지고 빈 응답이 난다"


def test_본문_경로가_payload에_기록된다():
    """★ 라우팅을 바꿔놓고도 '실제로 그 경로로 갔는지'를 증명할 수 없으면 대조가 성립하지 않는다.
    2026-08-17 Solar 검증 중 발견 — vision_route만 남고 body_route가 없어
    어느 모델이 그 글을 썼는지 사후 확인이 불가능했다(헌법 2번: 사용 기준 대조).
    """
    import inspect

    from app.services import ingest
    src = inspect.getsource(ingest)
    assert 'payload["body_route"]' in src, "본문 경로가 payload에 안 남는다(사후 검증 불가)"
    assert 'LAST_ROUTE.get("body")' in src


def test_짧은_출력은_추론강도를_낮춘다():
    """★ 2026-08-17 실측 — 영상 자막(spoken)을 medium으로 부르니 **0자 빈 응답**,
    low로 부르니 361자 정상. 추론이 출력 예산을 다 먹기 때문이다.
    반대로 본문은 medium이 필요하다(minimal이면 노린 질의 커버가 0/2로 죽는다)."""
    assert llm.solar_effort("spoken") == "low"
    assert llm.solar_effort("caption") == "low"
    assert llm.solar_effort("body") == "medium", "본문이 low로 떨어지면 커버리지가 죽는다"


def test_캡션_영상자막이_solar로_간다(monkeypatch):
    """2026-08-17 사장님 승인 — Anthropic 크레딧 소진 시에도 만들 수 있어야 한다."""
    monkeypatch.delenv("LLM_CAPTION", raising=False)
    monkeypatch.delenv("LLM_SPOKEN", raising=False)
    assert llm.route("spoken")[0] == "upstage"
    assert llm.route("caption")[0] == "upstage"


def test_품질고정이_비었는지_확인한다():
    """caption 고정을 풀었다. 되살리면 크레딧 소진 시 캡션이 통째로 막힌다 —
    2026-07-28 고정 사유(캐스퍼 날조)의 진짜 원인은 cache_prefix 누락이었고 이미 막혔다."""
    assert "caption" not in llm.QUALITY_PIN, "caption이 다시 특정 모델에 고정됐다"


def test_크레딧이_없어도_필요없으면_막지_않는다(monkeypatch):
    """★ 2026-08-17 실사고 — 크레딧 소진에 생성이 **전면 차단**됐다.
    그때 본문은 Solar, 사진은 Gemini로 가고 있어서 Anthropic 없이도 만들 수 있었다.
    프로덕션 로그에 크레딧 오류가 20건 쌓이는 동안 글이 한 건도 안 나왔다.
    차단은 '크레딧이 없는가'가 아니라 '그것이 필요한가'로 판정해야 한다."""
    monkeypatch.setattr(llm, "credit_out", lambda: True)
    monkeypatch.setenv("LLM_BODY", "upstage:solar-pro4")
    monkeypatch.setenv("LLM_VISION", "gemini:gemini-flash-latest")
    monkeypatch.setenv("LLM_CAPTION", "upstage:solar-pro4")
    monkeypatch.setenv("LLM_SPOKEN", "upstage:solar-pro4")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    assert not llm.anthropic_needed(), "전부 비-Anthropic인데 필요하다고 판정했다"
    assert not llm.blocked(), "Anthropic이 필요 없는데 생성을 막았다"


def test_하나라도_anthropic이면_막는다(monkeypatch):
    """핵심 작업 중 하나만 anthropic이어도 크레딧 없이는 못 만든다 → 막아야 한다.

    ★ 2026-08-18 — 전에는 LLM_VISION을 지우기만 하면 vision이 기본값 anthropic으로
      떨어져서 이 상황이 만들어졌다. 이제 vision 기본값도 Gemini라 그 방법이 안 통한다.
      그래서 **명시적으로** anthropic을 하나 심어 검증한다 — 검사하려는 것은 그대로다.
    """
    monkeypatch.setattr(llm, "credit_out", lambda: True)
    monkeypatch.setenv("LLM_BODY", "upstage:solar-pro4")
    monkeypatch.setenv("LLM_VISION", "anthropic:claude-opus-4-8")
    monkeypatch.setattr(llm, "QUALITY_PIN", {})
    assert llm.anthropic_needed()
    assert llm.blocked(), "Anthropic이 필요한데 안 막았다"


def test_판정_불가면_막는다(monkeypatch):
    """모르면 보수적으로 — 크레딧 없이 돌려 전부 실패시키는 것보다 낫다."""
    monkeypatch.setattr(llm, "credit_out", lambda: True)
    monkeypatch.setattr(llm, "route", lambda t: (_ for _ in ()).throw(RuntimeError("x")))
    assert llm.anthropic_needed() and llm.blocked()


def test_생성_진입점이_blocked를_쓴다():
    import inspect

    from app import main
    src = inspect.getsource(main)
    assert "_llmu.blocked()" in src, "생성 진입점이 여전히 credit_out으로 전면 차단한다"


def test_보조호출도_라우팅을_탄다():
    """★ 2026-08-17 실사고 — 본문을 Solar로 옮겨놓고도 생성이 계속 실패했다.
    제목 조각·YES/NO 판정 같은 짧은 보조 호출이 anthropic 직행이라 크레딧 0에서 터졌다.
    한 세트가 완성되려면 그 세트의 **모든 호출**이 살아 있는 경로여야 한다."""
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc)
    assert src.count('task="aux"') >= 3, "보조 호출이 여전히 anthropic 직행이다"
    assert llm.route("aux")[0] == "upstage"
    assert llm.solar_effort("aux") == "low", "짧은 호출에 추론을 많이 주면 빈 응답이 난다"


# ── 빈 응답 복구 (2026-08-19 실측) ─────────────────────────────────────
#   같은 계열 결함 **3회째**다. spoken·caption에서 나서 task 이름으로 막았고,
#   analysis에서 또 나서 '출력 예산이 작으면 low'라는 공통 규칙으로 바꿨다.
#   오늘은 **예산이 큰 쪽**에서 났다 — 본문 max_tokens=5000(실제 15,000 요청)인데
#   reasoning이 14,998을 먹고 본문 0자. 예산 크기로는 못 막는다.
#   판정을 **결과(빈 응답)**로 옮긴다: 빈 응답이면 effort를 낮춰 한 번 더 부른다.
#
#   ★ 이게 없으면 벤더 폴백(anthropic)으로 넘어가고, 크레딧이 없으면 생성이 멈춘다.
#     실제로 오늘 그렇게 2편이 죽었다.

def _resp(content, reasoning=14998):
    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 15000,
                              "completion_tokens_details": {"reasoning_tokens": reasoning}}}
    return _R()


def test_빈_응답이면_추론을_낮춰_다시_부른다(monkeypatch):
    """★ 이 골든의 존재 이유. 벤더 폴백보다 값싼 복구를 먼저 한다."""
    seen = []

    def _post(*a, **k):
        seen.append((k.get("json") or {}).get("reasoning_effort"))
        return _resp("" if len(seen) == 1 else "본문입니다")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    got = llm._upstage_generate("x", "solar-pro4", 5000, task="body")
    assert got == "본문입니다", f"재시도가 없다: {got!r}"
    assert seen == ["medium", "low"], f"재시도 강도가 틀렸다: {seen}"


def test_재시도도_비면_실패로_올린다(monkeypatch):
    """두 번 다 비면 조용히 넘기지 않는다(침묵 폴백 금지)."""
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", lambda *a, **k: _resp(""))
    try:
        llm._upstage_generate("x", "solar-pro4", 5000, task="body")
        raise AssertionError("빈 응답이 통과했다")
    except RuntimeError as e:
        assert "빈 응답" in str(e)


def test_성공하면_두_번_부르지_않는다(monkeypatch):
    """멀쩡한 호출에 재시도가 붙으면 원가·시간이 두 배가 된다."""
    n = []

    def _post(*a, **k):
        n.append(1)
        return _resp("정상 본문")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    assert llm._upstage_generate("x", "solar-pro4", 5000, task="body") == "정상 본문"
    assert len(n) == 1, f"불필요한 재호출 {len(n)}회"


def test_이미_low면_같은_호출을_반복하지_않는다(monkeypatch):
    """low에서도 비면 낮출 곳이 없다 — 같은 값으로 두 번 부르는 것은 낭비다."""
    n = []

    def _post(*a, **k):
        n.append((k.get("json") or {}).get("reasoning_effort"))
        return _resp("")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    try:
        llm._upstage_generate("x", "solar-pro4", 100, task="analysis")   # 짧은 출력 → low
    except RuntimeError:
        pass
    assert n == ["low"], f"low인데 재시도했다: {n}"


def test_본문은_더_오래_기다린다(monkeypatch):
    """★ 2026-08-19 실측 — 같은 본문 호출이 514초에 성공하고, 두 번은 300초에서 끊겼다.
    끊기면 anthropic 폴백으로 넘어가고 크레딧이 없으면 글이 아예 안 나온다."""
    seen = {}

    def _post(*a, **k):
        seen["timeout"] = k.get("timeout")
        seen["budget"] = (k.get("json") or {}).get("max_tokens")
        return _resp("본문")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    llm._upstage_generate("x", "solar-pro4", 5000, task="body")
    assert seen["timeout"] >= 600, f"본문 대기 {seen['timeout']}초 — 실측 514초를 못 기다린다"
    # 짧은 보조 호출까지 10분을 기다리면 파이프라인이 멈춘다
    #   (2026-08-19 실측으로 300 → 120으로 더 줄였다 — 60토큰 콜이 5분을 세웠다)
    llm._upstage_generate("x", "solar-pro4", 100, task="analysis")
    assert seen["timeout"] <= 120


def test_타임아웃도_low로_한_번_더_시도한다(monkeypatch):
    """벤더 폴백(=크레딧 소진 시 생성 중단)보다 값싼 복구를 먼저 한다."""
    import requests
    seen = []

    def _post(*a, **k):
        seen.append((k.get("json") or {}).get("reasoning_effort"))
        if len(seen) == 1:
            raise requests.exceptions.ReadTimeout("timeout")
        return _resp("본문")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    assert llm._upstage_generate("x", "solar-pro4", 5000, task="body") == "본문"
    assert seen == ["medium", "low"], f"재시도 강도가 틀렸다: {seen}"


def test_low에서_끊기면_그대로_올린다(monkeypatch):
    """더 낮출 곳이 없다 — 같은 호출을 반복하면 시간만 두 배로 버린다."""
    import requests
    n = []

    def _post(*a, **k):
        n.append(1)
        raise requests.exceptions.ReadTimeout("timeout")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    try:
        llm._upstage_generate("x", "solar-pro4", 100, task="analysis")   # 짧은 출력 → low
        raise AssertionError("실패가 통과했다")
    except requests.exceptions.ReadTimeout:
        pass
    assert len(n) == 1, f"low인데 {len(n)}회 시도했다"


def test_짧은_호출은_짧게_기다린다(monkeypatch):
    """★ 2026-08-19 실측 — 60토큰짜리 앵커 추출이 300초를 기다리며 파이프라인을 세웠다.

    예산 하한(max_tokens*3, 최소 6,000) 때문에 짧은 호출도 전부 300초로 묶여 있었다.
    low 추론이면 정상 응답은 수 초다 — 120초를 넘기면 그 콜은 죽은 것이다.
    """
    seen = {}

    def _post(*a, **k):
        seen["t"] = k.get("timeout")
        return _resp("답")
    monkeypatch.setenv("UPSTAGE_API_KEY", "up_test")
    monkeypatch.setattr("requests.post", _post)
    llm._upstage_generate("x", "solar-pro4", 60, task="analysis")
    assert seen["t"] <= 120, f"짧은 호출이 {seen['t']}초를 기다린다"
    llm._upstage_generate("x", "solar-pro4", 5000, task="body")
    assert seen["t"] >= 600, "본문은 오래 기다려야 한다(실측 514초 성공)"
