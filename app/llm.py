"""
공용 Claude 호출 계층 — 모델 ID·호출·폴백을 한 곳에 모은다(리팩토링 #2).
기존 text_claude._call_llm 이 이 모듈로 위임하며, 동작(모델·adaptive thinking·무키 더미)은 그대로.
개선점: 요청 타임아웃 지정(무한 대기 방지)·비용 로깅 훅 1곳.
"""
from __future__ import annotations

import os

MODEL = "claude-opus-4-8"

last_finish_reason = ""   # 직전 호출의 stop_reason(생성 절단 검증 V1) — 생성기가 payload에 기록
LAST_SLOW_TS = 0.0        # 직전 재시도(느림) 시각 — 진행률이 사용자에게 사유 안내
LAST_SLOW_REASON = ""


def _dummy(prompt: str) -> str:
    """ANTHROPIC_API_KEY 없을 때 골격 검증용 더미(형식 유지) — 기존 동작 보존."""
    return ("[제목]\n[샘플] " + prompt[:30].replace("\n", " ")
            + "\n[메타설명]\n샘플 메타설명\n[본문]\n## 소제목\n샘플 본문 (이미지: 메인사진)\n"
            "[이미지배치]\n- 서론: 메인사진\n[키워드]\n샘플,키워드,지역")


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
    client = anthropic.Anthropic(timeout=90.0, max_retries=0)  # SDK 자동재시도 끄고(중복 방지) 아래서 명시 제어
    _kw = {} if "haiku" in model else {"thinking": {"type": "adaptive"}}   # Haiku는 adaptive thinking 미지원(400)
    _msgs = _messages(prompt, cache_prefix)

    def _create(mt):
        last = None
        for _try in range(3):                              # 상한 3 — 무한 재시도 금지
            try:
                return client.messages.create(model=model, max_tokens=mt, messages=_msgs, **_kw)
            except Exception as e:
                last = e
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

    resp = _create(max_tokens)
    if getattr(resp, "stop_reason", "") == "max_tokens":   # thinking이 예산을 잠식해 본문이 잘림 → 2배로 1회 재시도
        resp = _create(max_tokens * 2)
    global last_finish_reason, LAST_USAGE
    last_finish_reason = getattr(resp, "stop_reason", "") or ""
    _u = getattr(resp, "usage", None)                     # 실측 토큰(원가 추적) — resp.usage
    if _u is not None:
        LAST_USAGE = {"in": getattr(_u, "input_tokens", 0) or 0,
                      "out": getattr(_u, "output_tokens", 0) or 0, "model": model}
        USAGE["anthropic"]["in"] = USAGE["anthropic"].get("in", 0) + LAST_USAGE["in"]
        USAGE["anthropic"]["out"] = USAGE["anthropic"].get("out", 0) + LAST_USAGE["out"]
    import logging
    logging.getLogger("shopcast.llm").info("[llm] stop_reason=%s max_tokens=%s", last_finish_reason, max_tokens)
    return next((b.text for b in resp.content if b.type == "text"), "")


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
USAGE = {"gemini": {"n": 0, "in": 0, "out": 0}, "anthropic": {"n": 0}}
LAST_ROUTE: dict = {}   # {task: {"provider","model","fallback","error"}} — payload 기록용(원가 추적)


HAIKU = "claude-haiku-4-5-20251001"
# 작업별 기본 라우팅(env LLM_<TASK>로 오버라이드 가능) — spoken(자막 구어 변환)은
# '빼기만·더하기 금지' 제약 준수 작업이라 Claude Haiku 지정(A/B 실측 Claude 우위 유형, 문장 수 적어 비용 미미).
TASK_DEFAULTS = {"spoken": ("anthropic", HAIKU)}


def route(task: str) -> tuple[str, str]:
    """작업 유형 → (provider, model). env → 작업별 기본값 → ('anthropic', 기본 모델)."""
    v = (os.environ.get(f"LLM_{task.upper()}") or "").strip()
    if ":" in v:
        p, m = v.split(":", 1)
        if p.strip().lower() in ("gemini", "anthropic") and m.strip():
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
    try:
        return d["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        raise RuntimeError(f"gemini 응답 파싱 실패: {str(d)[:160]}")


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
    if provider == "gemini":
        parts = ([{"inline_data": {"mime_type": mt, "data": b64}} for mt, b64 in (images or [])]
                 + [{"text": prompt}])
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
                    out = _gemini_generate([{"text": prompt}], "gemini-flash-latest", max_tokens)
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
    return call(prompt, am, max_tokens)
