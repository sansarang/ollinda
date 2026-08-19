"""
공용 Claude 호출 계층 — 모델 ID·호출·폴백을 한 곳에 모은다(리팩토링 #2).
기존 text_claude._call_llm 이 이 모듈로 위임하며, 동작(모델·adaptive thinking·무키 더미)은 그대로.
개선점: 요청 타임아웃 지정(무한 대기 방지)·비용 로깅 훅 1곳.
"""
from __future__ import annotations

import os
import threading as _thl

MODEL = "claude-opus-4-8"

_TL = _thl.local()          # 스레드별 stop_reason(병렬 채널 생성 오염 차단)


def last_finish() -> str:
    """이 스레드가 마지막으로 받은 stop_reason — 전역보다 우선."""
    return getattr(_TL, "finish", "") or last_finish_reason


last_finish_reason = ""   # 직전 호출의 stop_reason(생성 절단 검증 V1) — 생성기가 payload에 기록
LAST_SLOW_TS = 0.0        # 직전 재시도(느림) 시각 — 진행률이 사용자에게 사유 안내
LAST_SLOW_REASON = ""


def _dummy(prompt: str) -> str:
    """ANTHROPIC_API_KEY 없을 때 골격 검증용 더미(형식 유지) — 기존 동작 보존."""
    return ("[제목]\n[샘플] " + prompt[:30].replace("\n", " ")
            + "\n[메타설명]\n샘플 메타설명\n[본문]\n## 소제목\n샘플 본문 (이미지: 메인사진)\n"
            "[이미지배치]\n- 서론: 메인사진\n[키워드]\n샘플,키워드,지역")


# 💳 크레딧 소진 상태(2026-08-01 사장님 지시) — 감지되면 즉시 관리자에게 알리고 전부 중지한다.
#   계속 시도해봐야 매번 같은 400이 나고, 사장님 화면엔 원시 오류만 남는다.
CREDIT_OUT_TS = 0.0
_CREDIT_HOLD_SEC = 1800          # 감지 후 30분간 새 작업 차단(그 사이 충전하면 자동 해제)
CREDIT_MSG = ("AI 사용량(크레딧)이 모두 소진돼 지금은 만들 수 없어요 — "
              "운영자에게 충전을 요청해 주세요. 충전되면 바로 다시 만들 수 있어요.")
# 어느 키인지 운영자가 바로 알 수 있게(사장님 지시) — 실측 오류 원문이 제공사를 명시한다.
CREDIT_PROVIDER = "Anthropic(Claude) API — ANTHROPIC_API_KEY"


def _is_credit_error(e) -> bool:
    s = repr(e).lower()
    return ("credit balance is too low" in s) or ("credit" in s and "too low" in s)


def note_credit_out(e=None) -> None:
    """크레딧 소진 기록 + 관리자 1회 통보(중복 억제는 watchtower가 담당)."""
    global CREDIT_OUT_TS, _LAST_PROBE_TS
    import time as _tc
    first = (_tc.time() - CREDIT_OUT_TS) > _CREDIT_HOLD_SEC
    CREDIT_OUT_TS = _tc.time()
    _LAST_PROBE_TS = CREDIT_OUT_TS      # 막 실패한 걸 곧바로 다시 찌르지 않는다
    import logging as _lgc
    _lgc.getLogger("shopcast.llm").error("[llm] 크레딧 소진 — 신규 작업 차단: %s", repr(e)[:200])
    if first:
        try:
            from app.services import watchtower as _wt
            _wt.send(f"🚨 [올린다] {CREDIT_PROVIDER} 크레딧 소진\n"
                     "→ 글 생성·영상 대본이 전부 중지됐습니다(헛 시도 방지).\n"
                     "console.anthropic.com → Plans & Billing 에서 충전해 주세요.\n"
                     "충전하면 30분 내 자동 재개되며, 즉시 풀려면 /admin/credit-reset 을 호출하세요.\n"
                     "※ 네이버·ElevenLabs·Gemini 키와는 무관합니다.")
        except Exception:
            pass


_PROBE_EVERY_SEC = 60          # 회복 확인 주기 — 이보다 자주 찔러봐야 소용없다
_LAST_PROBE_TS = 0.0


def _probe_ok() -> bool:
    """크레딧이 돌아왔는지 가장 싼 호출로 확인 — 1토큰짜리 haiku 한 번.

    ★ 성공했을 때만 푼다. 키 없음·네트워크 오류를 '회복'으로 읽으면 크레딧이 없는데도
      작업을 재개해 전부 실패시킨다(골든이 잡았다) — 확인 못 한 것은 회복이 아니다.
    """
    try:
        import anthropic
        anthropic.Anthropic(timeout=20.0, max_retries=0).messages.create(
            model=HAIKU, max_tokens=1, messages=[{"role": "user", "content": "."}])
        return True
    except Exception:
        return False


def credit_out() -> bool:
    """지금 크레딧 소진 상태인가. 새 작업 진입점이 이걸 보고 즉시 중지한다.

    ★ 2026-08-04: 감지는 잘 됐는데 해제가 '30분 대기' 아니면 '사람이 admin을 누르기'였다.
      충전은 사장님이 언제 하실지 모른다 — 기다리게 하거나 부르게 하는 건 우리 일을
      사장님에게 미루는 것이다. 1분에 한 번 스스로 찔러보고 돌아왔으면 즉시 푼다.
    """
    global CREDIT_OUT_TS, _LAST_PROBE_TS
    import time as _tc
    if CREDIT_OUT_TS <= 0:
        return False
    now = _tc.time()
    if (now - CREDIT_OUT_TS) >= _CREDIT_HOLD_SEC:
        CREDIT_OUT_TS = 0.0
        return False
    if (now - _LAST_PROBE_TS) >= _PROBE_EVERY_SEC:
        _LAST_PROBE_TS = now
        if _probe_ok():
            CREDIT_OUT_TS = 0.0
            import logging as _lgr
            _lgr.getLogger("shopcast.llm").info("[llm] 크레딧 회복 확인 — 차단 자동 해제")
            return False
    return True


#: 생성 한 세트가 반드시 거치는 작업들 — 이 중 하나라도 anthropic이면 크레딧이 필요하다.
_CORE_TASKS = ("body", "caption", "spoken", "vision")


def anthropic_needed() -> bool:
    """지금 라우팅에서 Anthropic이 실제로 필요한가.

    ★ 2026-08-17 실사고 — 크레딧이 소진되자 **생성 자체가 전면 차단**됐다.
      그런데 그날 본문은 Solar, 사진 분석은 Gemini로 가고 있었다.
      Anthropic 없이도 만들 수 있는데 "Anthropic만 쓰던 시절"의 차단 로직이 전부를 막았다.
      실제로 프로덕션 로그에 크레딧 오류가 20건 넘게 쌓이는 동안 글이 한 건도 안 나왔다.
      → 차단 여부는 '크레딧이 없는가'가 아니라 **'그것이 필요한가'**로 판정한다.
    """
    try:
        return any(route(t)[0] == "anthropic" for t in _CORE_TASKS)
    except Exception:
        return True                    # 판정 못 하면 막는다(모르면 보수적으로)


def blocked() -> bool:
    """새 생성을 막아야 하는가 — 크레딧이 없고 **그것이 실제로 필요할 때만**."""
    return credit_out() and anthropic_needed()


def _retryable(e) -> bool:
    """재시도 가치 판정 — 429·5xx·연결오류·타임아웃만 True. 400·401·크레딧부족은 False(헛 재시도 금지)."""
    s = repr(e).lower()
    if "credit" in s or "invalid" in s or "authentication" in s or "permission" in s:
        return False
    code = getattr(e, "status_code", None)
    if code in (429, 500, 502, 503, 504, 529):
        return True
    return any(k in s for k in ("timeout", "timedout", "connection", "overloaded", "rate", "429", "503", "502", "504", "500"))


def _messages(prompt: str, cache_prefix: str = ""):
    """프롬프트 캐싱: 채널들이 공유하는 긴 프리픽스(cache_prefix — 브리프·사진분석)를 ephemeral 캐시로 표시 →
    2·3·4번째 채널 호출이 프리픽스 재계산 없이 히트(P50 지연↓·비용 41~80%↓). 프리픽스 없으면 기존 단일 문자열."""
    if cache_prefix and len(cache_prefix) > 400:           # 캐시 최소 토큰 미달이면 캐싱 이득 없음 → 그냥 합침
        return [{"role": "user", "content": [
            {"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": prompt}]}]
    return [{"role": "user", "content": (cache_prefix + prompt) if cache_prefix else prompt}]


def call(prompt: str, model: str = MODEL, max_tokens: int = 1200, cache_prefix: str = "") -> str:
    """공용 Claude 호출. 키 없으면 더미. ★ 명시적 바운드 재시도(429/5xx/타임아웃, 지수백오프, 상한 3) +
    타임아웃 + 프롬프트 캐싱(cache_prefix). 무한 행 방지 — 재시도 소진 시 예외 raise(호출부 except 처리)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _dummy(prompt)
    import time as _t
    import anthropic
    # 타임아웃은 출력 예산에 비례(2026-07-31 실사고: 90초 고정 → 6000토큰 재작성이 APITimeoutError로
    # 개선 라운드째 사망). 5000tk≈190초, 6000tk≈210초, 상한 600초.
    _to = min(600.0, 90.0 + max_tokens * 0.02)
    client = anthropic.Anthropic(timeout=_to, max_retries=0)  # SDK 자동재시도 끄고(중복 방지) 아래서 명시 제어
    # thinking은 대형 생성에만(2026-07-31 실사고: 700토큰 판단 호출까지 thinking이 예산을 잠식해
    # max_tokens 절단 유발). Haiku는 adaptive thinking 미지원(400).
    _kw = ({"thinking": {"type": "adaptive"}}
           if ("haiku" not in model and max_tokens >= 2000) else {})
    _msgs = _messages(prompt, cache_prefix)

    def _create(mt):
        last = None
        for _try in range(3):                              # 상한 3 — 무한 재시도 금지
            try:
                return client.messages.create(model=model, max_tokens=mt, messages=_msgs, **_kw)
            except Exception as e:
                last = e
                if _is_credit_error(e):
                    note_credit_out(e)
                    raise
                _rt = _retryable(e)
                import logging as _lg
                _lg.getLogger("shopcast.llm").warning("[llm] 콜 실패(try %d, retry=%s): %s",
                                                      _try + 1, _rt, repr(e)[:120])
                if not _rt or _try == 2:
                    raise
                global LAST_SLOW_TS, LAST_SLOW_REASON       # 느림 사유 노출(진행률이 사용자에게 안내)
                _es = repr(e).lower()
                LAST_SLOW_TS = _t.time()
                LAST_SLOW_REASON = ("AI 응답이 평소보다 느려요 — 요청이 몰려 잠시 기다리는 중이에요"
                                    if ("429" in _es or "rate" in _es or "overloaded" in _es) else
                                    "AI 응답을 기다리는 중이에요 — 잠시만요")
                _t.sleep(min(2 ** _try * 1.5, 12))         # 지수 백오프(상한 12초)
        raise last

    def _txt(r) -> str:
        return next((b.text for b in getattr(r, "content", []) or [] if b.type == "text"), "")

    resp = _create(max_tokens)
    _mt2 = max_tokens
    while getattr(resp, "stop_reason", "") == "max_tokens" and _mt2 < min(max_tokens * 4, 16000):
        # ★ 텍스트가 0바이트인 절단은 '예산 부족'이 아니라 thinking이 예산을 통째로 삼킨 것이다
        #   (2026-08-01 실사고). 이때 예산을 2배씩 늘리면 매번 같은 결과를 더 비싸고 더 느리게
        #   받는다 — 실측: 6000→12000→16000 확대에 재시도까지 겹쳐 한 콜이 10분 넘게 걸렸고
        #   최종 결과는 빈 문자열이었다. 예산이 아니라 thinking을 끄는 것이 답이다.
        if _kw and not _txt(resp).strip():
            _kw = {}
            resp = _create(max_tokens)
            continue
        _mt2 = min(_mt2 * 2, 16000)                        # 절단 → 예산 2배 확대 재시도(×2→×4, 상한 16k)
        resp = _create(_mt2)
    global last_finish_reason, LAST_USAGE
    last_finish_reason = getattr(resp, "stop_reason", "") or ""
    # ★ 채널 병렬 생성(2026-08-01 재활성)에서 전역 하나를 공유하면 X의 절단이 본문 결과로
    #   기록돼 멀쩡한 글이 -15점을 먹는다(검토 지적). 스레드별로도 따로 남긴다.
    _TL.finish = last_finish_reason
    _u = getattr(resp, "usage", None)                     # 실측 토큰(원가 추적) — resp.usage
    if _u is not None:
        LAST_USAGE = {"in": getattr(_u, "input_tokens", 0) or 0,
                      "out": getattr(_u, "output_tokens", 0) or 0, "model": model}
        _track_cost(model, LAST_USAGE.get("in", 0), LAST_USAGE.get("out", 0))
        USAGE["anthropic"]["in"] = USAGE["anthropic"].get("in", 0) + LAST_USAGE["in"]
        USAGE["anthropic"]["out"] = USAGE["anthropic"].get("out", 0) + LAST_USAGE["out"]
    import logging
    logging.getLogger("shopcast.llm").info("[llm] stop_reason=%s max_tokens=%s", last_finish_reason, max_tokens)
    _text = _txt(resp)
    # ★ 빈 응답을 조용히 돌려주지 않는다(2026-08-01 실사고: 주안모터스 재작성이 0바이트로 돌아와
    #   호출부가 '안전게이트 위반(len 0)'으로 오해하고 보정을 포기 → 77점 고착).
    #   위 루프에서 thinking을 이미 껐다면 여기선 진짜 실패다 → 예외로 올려 사유를 남긴다.
    if not _text.strip():
        if _kw:                                            # 절단이 아닌 사유로 비었어도 한 번은 더
            _kw = {}
            resp = _create(max_tokens)
            last_finish_reason = getattr(resp, "stop_reason", "") or ""
            _TL.finish = last_finish_reason
            _text = _txt(resp)
            logging.getLogger("shopcast.llm").warning("[llm] 빈 응답 → thinking 끄고 재시도: %d자",
                                                      len(_text))
        if not _text.strip():
            raise RuntimeError(f"빈 응답(stop_reason={last_finish_reason}, max_tokens={max_tokens})")
    return _text


LAST_USAGE: dict = {"in": 0, "out": 0, "model": ""}   # 마지막 Anthropic 콜 실측 토큰
# USD/1M 토큰(리스트가) — 실측 토큰 × 가격 = 콜 원가. 모델군별 (input, output).
_PRICE = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}


def usd_cost(model: str, tin: int, tout: int) -> float:
    """실측 토큰(입력·출력)에 모델군 리스트가를 적용한 콜 원가(USD)."""
    key = "opus" if "opus" in (model or "") else "sonnet" if "sonnet" in (model or "") else "haiku"
    pi, po = _PRICE[key]
    return round(tin / 1e6 * pi + tout / 1e6 * po, 6)


def ping() -> bool:
    """API 사용 가능 여부(크레딧 등) 초저가 확인 — 워치독이 헛 재시도로 1회 제한을 소진하지 않게.
    True=사용 가능/판단 불가(진행), False=크레딧 소진 확정."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic
        anthropic.Anthropic(timeout=15.0).messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1,
            messages=[{"role": "user", "content": "."}])
        return True
    except Exception as e:
        return "credit" not in repr(e).lower()


# ── 작업 유형별 provider 라우팅(비용 이원화) ────────────────────────
# env: LLM_VISION / LLM_CAPTION / LLM_BODY = "provider:model" (예: gemini:gemini-flash-latest)
# 미설정 시 기본값 = 현행 Anthropic 경로 그대로(변수 없어도 기존과 동일 동작 — 배포 안전).
USAGE = {"gemini": {"n": 0, "in": 0, "out": 0}, "anthropic": {"n": 0},
         "upstage": {"n": 0, "in": 0, "out": 0}}

# Upstage Solar — 추론 강도(2026-08-17 A/B 실측).
#   API 기본값은 "minimal"이고, 그대로 두면 다단계 지시를 최소 추론으로 처리한다.
#   같은 프롬프트로 실측: minimal → 노린 질의 커버 0/2 · medium → 2/2 · high → 1/2(사진 7곳 뭉침).
#   **medium이 최적점**이라 코드에서 고정한다. env로만 실험적으로 바꾼다.
SOLAR_EFFORT = os.environ.get("SOLAR_REASONING", "medium").strip() or "medium"

#: 작업별 추론 강도 — **짧은 출력에 medium을 주면 추론이 예산을 다 먹고 빈 응답이 나온다.**
#: 2026-08-17 실측: 영상 자막(spoken)을 medium으로 부르니 0자, low로 부르니 361자 정상.
#: 본문처럼 긴 글은 medium이 필요하다(minimal이면 노린 질의 커버가 0/2로 죽는다).
SOLAR_EFFORT_BY_TASK = {"spoken": "low", "caption": "low", "x": "low", "title": "low",
                        "aux": "low", "judge": "low"}


#: 이 토큰 예산 아래에서는 effort를 강제로 낮춘다.
#: Solar는 reasoning 토큰을 **출력 예산 안에서** 쓴다. 예산이 작은데 medium을 주면
#: 추론이 예산을 다 먹고 본문이 0자로 나온다(빈 응답).
SHORT_OUTPUT_TOKENS = 400


def solar_effort(task: str = "", max_tokens: int = 0) -> str:
    """작업별 추론 강도. **짧은 출력이면 task와 무관하게 low로 낮춘다.**

    ★ 2026-08-18 — 같은 결함을 두 번째로 만났다.
      처음엔 spoken·caption에서 났고 그때는 SOLAR_EFFORT_BY_TASK에 task 이름을 하나씩
      넣어 막았다(표면별 수정). 오늘 `analysis`를 추가하자 **같은 빈 응답이 또 났다** —
      교훈 추출은 max_tokens=100인데 medium이라 reasoning 3040토큰이 예산을 삼켰다.

      원인은 task 이름이 아니라 **출력 예산**이다. 이름으로 막으면 새 task마다 재발한다.
      그래서 예산 기준 공통 규칙으로 바꾼다(헌법: 같은 계열 결함 2회째는 전 표면 공통 규칙으로만).
    """
    base = SOLAR_EFFORT_BY_TASK.get(task, SOLAR_EFFORT)
    if 0 < max_tokens < SHORT_OUTPUT_TOKENS:
        return "low"
    return base

# 💰 세트별 비용 계측(2026-07-29 실측 $4/세트 사고 후) — 모델 단가($/M tokens)로 근사 집계.
#   ingest가 세트 시작 시 reset, 종료 시 snapshot을 blog payload(api_cost)에 기록.
_PRICES = {"claude-opus": (15.0, 75.0), "claude-sonnet": (3.0, 15.0),
           "claude-haiku": (1.0, 5.0), "gemini": (0.30, 2.50),
           "solar": (0.30, 1.20)}    # Upstage Solar Pro 4(2026-08 공식가)
COST = {"usd": 0.0, "calls": 0}


def _track_cost(model: str, tin: int, tout: int) -> None:
    try:
        for k, (a, b) in _PRICES.items():
            if (model or "").startswith(k):
                COST["usd"] += tin / 1e6 * a + tout / 1e6 * b
                COST["calls"] += 1
                return
    except Exception:
        pass


def cost_reset() -> None:
    COST.update({"usd": 0.0, "calls": 0})


def cost_snapshot() -> dict:
    return {"usd": round(COST["usd"], 4), "calls": COST["calls"]}
LAST_ROUTE: dict = {}   # {task: {"provider","model","fallback","error"}} — payload 기록용(원가 추적)


HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
# 작업별 기본 라우팅(env LLM_<TASK>로 오버라이드 가능) — spoken(자막 구어 변환)은
# '빼기만·더하기 금지' 제약 준수 작업이라 Claude Haiku 지정(A/B 실측 Claude 우위 유형, 문장 수 적어 비용 미미).
#: 작업별 기본 라우팅(env LLM_<TASK>로 오버라이드 가능)
#: ★ 2026-08-17 — spoken·caption·x를 Solar로 옮겼다(사장님 승인·실측 근거).
#:   spoken은 '빼기만·더하기 금지' 제약 준수 작업이라 Claude Sonnet을 썼는데,
#:   Solar가 effort=low에서 같은 제약을 지켰다(8문장 → 361자, 중괄호 강조·22자 내외 준수).
#:   effort=medium은 추론이 예산을 다 먹어 **0자 빈 응답**이 났다 — 짧은 출력엔 low다.
#:   원가: Sonnet 대비 1/10 수준이고, 무엇보다 Anthropic 크레딧과 무관하게 돈다.
SOLAR = "solar-pro4"
#: 사진을 보는 작업(vision)의 모델. 모델명이 코드 두 곳에 흩어져 있어 상수로 묶었다 —
#: 같은 값이 두 곳에 살면 그게 결함이다(canonical 단일 관문의 모델명판).
GEMINI = "gemini-flash-latest"
#: aux — 제목 조각·YES/NO 판정 같은 **짧은 보조 호출**.
#:   2026-08-17 실사고: 본문을 Solar로 옮겨놓고도 생성이 계속 실패했다. 원인은 이 보조
#:   호출들이 anthropic 직행이라 크레딧 0에서 터진 것이다. 본문만 옮겨서는 소용이 없다 —
#:   한 세트가 완성되려면 그 세트의 **모든 호출**이 살아 있는 경로여야 한다.
#: ★ 2026-08-18 사장님 승인 — `body`를 코드 기본값으로 못 박았다.
#:   그전까지 본문 Solar는 Railway 환경변수(LLM_BODY) 하나에만 걸려 있었고,
#:   **코드 기본값은 Opus였다.** 변수가 지워지거나 서비스를 다시 만들면 아무 경고 없이
#:   Opus로 돌아가 비용이 40배가 된다(실측: 8/16 건당 $0.54~1.37 → 8/17 $0.011~0.023).
#:   침묵 폴백 금지 — 비용도 조용히 되돌아가면 안 된다. 이제 env는 안전장치일 뿐이다.
#: judge — YES/NO·유형 같은 **짧은 판정**. 사장님 설계: "초안은 한국 특화 api, 검수는 클로드".
#:   단 '사진에 없는 것을 썼는가'(seo.fact_check)만은 클로드에 남긴다 — 날조를 막는 마지막 관문이라
#:   여기서 실력이 떨어지면 정직 게이트 전체가 뚫린다.
TASK_DEFAULTS = {"spoken": ("upstage", SOLAR),
                 "caption": ("upstage", SOLAR),
                 "x": ("upstage", SOLAR),
                 "aux": ("upstage", SOLAR),
                 "body": ("upstage", SOLAR),
                 "judge": ("upstage", SOLAR),
                 "title": ("upstage", SOLAR),
                 #: analysis — 순위 격차 추론·교훈 추출. 짧은 판정(judge)과 달리 **추론**이 필요해
                 #: effort를 낮추지 않는다(SOLAR_EFFORT_BY_TASK에 넣지 않음 = medium).
                 #: 2026-08-18 사장님 지시로 클로드에서 옮겼다 — 이로써 글 파이프라인 전체가
                 #: Anthropic 크레딧과 무관해진다. 남는 anthropic 호출은 크레딧 확인 ping뿐이다.
                 "analysis": ("upstage", SOLAR),
                 #: vision은 사진을 봐야 하므로 Solar가 못 한다(멀티모달 아님) → Gemini.
                 #: body와 똑같은 위험이었다 — 프로덕션은 LLM_VISION env로 Gemini인데
                 #: 코드 기본값은 Opus라, 변수가 사라지면 사진 분석이 조용히 Opus로 갔다.
                 #: 실측으로 이미 Gemini가 돌고 있다(payload.vision_route = gemini-flash-latest).
                 "vision": ("gemini", GEMINI)}

# 품질 표면 고정(2026-07-28 사장님 결정: 품질 우선, 비용 절감 라우팅 폐지) — env LLM_* 보다 우선.
# 캡션 제미나이 절감 라우팅은 사진 분석 미전달 실사고(캐스퍼 날조)의 온상이었음. 절감 실험은
# 품질 회귀 검사(/admin/quality-check) 정착 후에만 재개.
#: 품질 표면 고정 — env보다 우선한다.
#: ★ 2026-08-17 사장님 승인으로 caption 고정을 풀었다.
#:   2026-07-28에 고정한 이유는 '캡션 제미나이 절감 라우팅이 사진 분석 미전달 실사고(캐스퍼 날조)의
#:   온상'이어서였다. 그 사고의 원인은 **cache_prefix 누락**(사진 분석이 전달 안 됨)이었지
#:   모델 실력이 아니었고, 그 구멍은 이미 막혀 있다(call_task가 _full_prompt로 합쳐 보낸다).
#:   Solar 실측: 캡션 343자·X 193자 모두 재료 범위 안, 날조 0. 원가 $0.004.
#:   그리고 이날 Anthropic 크레딧이 소진돼 오퍼스 고정이 곧 '캡션 생성 불가'를 뜻하게 됐다.
#:   env(LLM_CAPTION)를 비우면 아래 TASK_DEFAULTS/기본 모델로 되돌아간다.
QUALITY_PIN: dict = {}


def route(task: str) -> tuple[str, str]:
    """작업 유형 → (provider, model). 품질 고정(QUALITY_PIN) → env → 작업별 기본값 → 기본 모델."""
    if task in QUALITY_PIN:
        return QUALITY_PIN[task]
    v = (os.environ.get(f"LLM_{task.upper()}") or "").strip()
    if ":" in v:
        p, m = v.split(":", 1)
        if p.strip().lower() in ("gemini", "anthropic", "upstage") and m.strip():
            return p.strip().lower(), m.strip()
    return TASK_DEFAULTS.get(task, ("anthropic", MODEL))


def _gemini_generate(parts: list, model: str, max_tokens: int) -> str:
    """Gemini REST 호출 — parts는 [{text}|{inline_data}] 목록. 실패 시 예외(상위에서 폴백)."""
    import requests as _rq
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 미설정")
    r = _rq.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                 params={"key": key},
                 json={"contents": [{"parts": parts}],
                       "generationConfig": {"maxOutputTokens": max(max_tokens * 3, 6000)}},   # thinking 잠식 절단 방지
                 timeout=90)
    d = r.json()
    if r.status_code != 200:
        raise RuntimeError(f"gemini {r.status_code}: {str(d)[:160]}")
    u = d.get("usageMetadata", {})
    USAGE["gemini"]["n"] += 1
    USAGE["gemini"]["in"] += u.get("promptTokenCount", 0)
    USAGE["gemini"]["out"] += u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    _track_cost("gemini", u.get("promptTokenCount", 0),
                u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0))
    try:
        return d["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        raise RuntimeError(f"gemini 응답 파싱 실패: {str(d)[:160]}")


def _upstage_generate(prompt: str, model: str, max_tokens: int, task: str = "") -> str:
    """Upstage Solar REST 호출(OpenAI 호환). 실패 시 예외 — 상위에서 anthropic 폴백.

    2026-08-17 도입 근거(A/B 실측, 같은 재료·같은 프롬프트):
      본문 품질이 오퍼스 발행글과 동급 이상이면서 원가가 1/180이었다.
        · 노린 질의 커버 2/2(오퍼스 초안 1회는 1/2) · 사진 뭉침 0곳(오퍼스 초안 5곳)
        · 두꺼운 문단 5개(발행글 3개) · 날조 0건(발행글엔 '25분' 1건)
        · 세트 원가 $0.5435 → $0.0030
      ★ 텍스트 전용이다. 이미지는 이 경로로 보내지 않는다(비전은 gemini 유지).
    """
    import requests as _rq
    key = os.environ.get("UPSTAGE_API_KEY", "")
    if not key:
        raise RuntimeError("UPSTAGE_API_KEY 미설정")
    _budget = max(max_tokens * 3, 6000)
    # ⏱ 대기 시간을 예산에 맞춘다 — 본문(예산 15,000)은 300초로 모자란다.
    #   2026-08-19 실측: 같은 본문 호출이 한 번은 514초에 성공, 두 번은 300초에서 끊겼다.
    #   끊기면 anthropic 폴백으로 넘어가고, 크레딧이 없으면 글이 아예 안 나온다.
    _timeout = 300 if _budget <= 6000 else 600

    def _once(effort: str) -> tuple:
        r = _rq.post("https://api.upstage.ai/v1/chat/completions",
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                     json={"model": model, "max_tokens": _budget,  # 추론 토큰이 출력에 포함
                           "reasoning_effort": effort,
                           "messages": [{"role": "user", "content": prompt}]},
                     timeout=_timeout)  # 추론 모드는 느리다(실측 60~514초)
        d = r.json()
        if r.status_code != 200:
            raise RuntimeError(f"upstage {r.status_code}: {str(d)[:160]}")
        u = d.get("usage") or {}
        USAGE["upstage"]["n"] += 1
        USAGE["upstage"]["in"] += u.get("prompt_tokens", 0)
        USAGE["upstage"]["out"] += u.get("completion_tokens", 0)
        _track_cost("solar", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        try:
            return (d["choices"][0]["message"].get("content") or "").strip(), u
        except Exception:
            raise RuntimeError(f"upstage 응답 파싱 실패: {str(d)[:160]}")

    _eff = solar_effort(task, max_tokens)
    try:
        txt, u = _once(_eff)
    except (_rq.exceptions.Timeout, _rq.exceptions.ConnectionError) as _e:
        # 시간이 모자라거나 끊긴 경우도 **낮은 추론으로 한 번 더** — 벤더 폴백보다 값싸다.
        #   low는 추론 토큰을 거의 안 써서 눈에 띄게 빠르다(실측).
        if _eff == "low":
            raise
        import logging as _lgt
        _lgt.getLogger("shopcast.llm").warning(
            "[llm] upstage %s %s → low로 재시도", task or "?", type(_e).__name__)
        txt, u = _once("low")
    # ★ 빈 응답 = 추론이 출력 예산을 다 먹은 것. **effort를 낮춰 같은 호출을 한 번 더 한다.**
    #   2026-08-19 — 같은 계열 결함 3회째다(spoken·caption → analysis → body).
    #   앞선 두 번은 '예산이 작으면 low'라는 규칙으로 막았는데, 이번엔 예산이 큰 쪽에서 났다:
    #     본문 max_tokens=5000 → 실제 15,000 요청 → reasoning 14,998 → 본문 0자.
    #   예산 크기로는 못 막는다. 판정 기준을 **결과(빈 응답)**로 옮긴다 —
    #   벤더 폴백(=크레딧 소진 시 생성 중단)보다 먼저 시도해야 할 값싼 복구다.
    if not txt and _eff != "low":
        import logging as _lgu
        _lgu.getLogger("shopcast.llm").warning(
            "[llm] upstage %s 빈 응답(effort=%s reasoning=%s) → low로 재시도",
            task or "?", _eff, (u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
        txt, u = _once("low")
    if not txt:                        # 추론만 하고 본문을 안 낸 경우 — 침묵 폴백 금지
        raise RuntimeError(f"upstage 빈 응답(effort={_eff}→low max_tokens={max_tokens} "
                           f"reasoning={(u.get('completion_tokens_details') or {}).get('reasoning_tokens')})")
    return txt


def call_task(task: str, prompt: str, max_tokens: int = 1200,
              default_model: str | None = None,
              images: list | None = None, cache_prefix: str = "") -> str:
    """작업 유형별 라우팅 호출. images=[(media_type, b64), ...]면 멀티모달.
    Gemini 실패(429 포함) → 1회 재시도 → Anthropic 폴백(LAST_ROUTE에 기록).
    Anthropic도 불가면 예외 → 호출부의 기존 실패 처리(산출물 생략)로 — 글 파이프라인 안 막음."""
    import logging
    import time
    log = logging.getLogger("shopcast.llm")
    provider, model = route(task)
    info = {"provider": provider, "model": model, "fallback": False}
    # ★ cache_prefix(브리프·사진 분석)는 Gemini 경로에도 반드시 포함 — 누락 시 캡션이 사진을 전혀
    #   못 보고 소재를 지어냄(실사고 2026-07-27: 토레스 세트 캡션이 '오늘 들여온 캐스퍼' 날조의 진짜 원인).
    _full_prompt = (cache_prefix + prompt) if cache_prefix else prompt
    # Upstage Solar — 텍스트 전용. 이미지가 있으면 태우지 않고 기존 경로로 넘긴다
    # (비전은 gemini가 이미 싸고 검증됐다 — 여기서 바꾸지 않는다).
    if provider == "upstage" and not images:
        for attempt in (1, 2):                        # 1회 재시도(gemini 경로와 같은 규율)
            try:
                out = _upstage_generate(_full_prompt, model, max_tokens, task)
                LAST_ROUTE[task] = info
                return out
            except Exception as e:
                log.warning("[llm] upstage %s 실패(%d/2): %s", task, attempt, repr(e)[:120])
                info["error"] = repr(e)[:150]
                if attempt == 1:
                    time.sleep(2)
        info["fallback"] = True                       # → Anthropic 폴백(원가 추적용 기록)
        LAST_ROUTE[task] = info
        log.warning("[llm] upstage %s → anthropic 폴백", task)
    elif provider == "upstage":                       # 이미지 동반 — Solar는 텍스트 전용
        info["fallback"] = True
        info["fallback_to"] = "anthropic(images)"
        LAST_ROUTE[task] = info
        log.info("[llm] upstage %s: 이미지 동반이라 기존 경로 사용", task)
    elif provider == "gemini":
        parts = ([{"inline_data": {"mime_type": mt, "data": b64}} for mt, b64 in (images or [])]
                 + [{"text": _full_prompt}])
        for attempt in (1, 2):                        # 1회 재시도(rate limit 폭주 금지)
            try:
                out = _gemini_generate(parts, model, max_tokens)
                LAST_ROUTE[task] = info
                return out
            except Exception as e:
                log.warning("[llm] gemini %s 실패(%d/2): %s", task, attempt, repr(e)[:120])
                info["error"] = repr(e)[:150]
                if attempt == 1:
                    time.sleep(2)
        info["fallback"] = True                       # → Anthropic 폴백(원가 추적용 기록)
        LAST_ROUTE[task] = info
        log.warning("[llm] gemini %s → anthropic 폴백", task)
    else:
        LAST_ROUTE[task] = info
    # Anthropic 경로(기본/폴백)
    USAGE["anthropic"]["n"] += 1
    am = default_model or (model if provider == "anthropic" else MODEL)
    if provider == "anthropic" and not info.get("fallback"):
        try:
            out = call(prompt, am, max_tokens, cache_prefix=cache_prefix) if not images else None
            if out is not None:
                LAST_ROUTE[task] = info
                return out
        except Exception as e:
            log.warning("[llm] anthropic %s 실패: %s", task, repr(e)[:120])
            info["error"] = repr(e)[:150]
            if os.environ.get("GEMINI_API_KEY"):      # 역방향 폴백: anthropic → gemini
                try:
                    out = _gemini_generate([{"text": _full_prompt}], GEMINI, max_tokens)
                    info["fallback"] = True
                    info["fallback_to"] = "gemini"
                    LAST_ROUTE[task] = info
                    log.warning("[llm] anthropic %s → gemini 폴백", task)
                    return out
                except Exception as e2:
                    log.warning("[llm] gemini 역폴백도 실패: %s", repr(e2)[:120])
            raise
    if images:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("anthropic 키 없음(비전 폴백 불가)")
        import anthropic
        content = ([{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}
                    for mt, b64 in images] + [{"type": "text", "text": prompt}])
        resp = anthropic.Anthropic(timeout=120.0).messages.create(   # 이미지 배치(6장)는 60초로 빠듯 — 실측 상향
            model=am, max_tokens=max_tokens, messages=[{"role": "user", "content": content}])
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    return call(prompt, am, max_tokens, cache_prefix=cache_prefix)

# 한 번에 뱉게 할 상한. 8000으로 묶었다가 되돌렸다(2026-08-04) —
# 그때 본 400은 모델 상한 초과가 아니라 크레딧 소진이었다. 원인을 잘못 읽고 예산을 깎으면
# '글을 다 못 쓰는 요청'이 된다. thinking이 예산을 나눠 쓰므로 본문 분량에 여유를 둔다.
MAX_OUT = int(os.environ.get("SHOPCAST_MAX_OUT", "16000"))


def tokens_for(text: str, ratio: float = 2.4, extra: int = 800,
               floor: int = 1500, cap: int = 0) -> int:
    """이 글을 '통째로 다시 쓰게' 할 때 필요한 출력 토큰 — 계산은 여기 하나뿐이다.

    ★ 2026-08-04 실물 사고: 표면 수선이 max_tokens=2691로 요청했다가
      stop_reason=max_tokens로 빈 응답을 받고 실패했다. 본문 2,990자짜리였다.
      한글은 1자가 대략 1.5~2.4 토큰이라 len(body)*0.9는 애초에 완성될 수 없는 요청이다.
      같은 실수를 캡션에서도 냈다(20줄을 900토큰에 요구) — 그래서 계산을 한 곳으로 모은다.
      요청이 구조적으로 완성 불가능하면 그건 모델 탓이 아니라 우리 탓이다.
    """
    n = len(text or "")
    return max(floor, min(cap or MAX_OUT, int(n * ratio) + extra))
