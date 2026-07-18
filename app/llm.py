"""
공용 Claude 호출 계층 — 모델 ID·호출·폴백을 한 곳에 모은다(리팩토링 #2).
기존 text_claude._call_llm 이 이 모듈로 위임하며, 동작(모델·adaptive thinking·무키 더미)은 그대로.
개선점: 요청 타임아웃 지정(무한 대기 방지)·비용 로깅 훅 1곳.
"""
from __future__ import annotations

import os

MODEL = "claude-opus-4-8"

last_finish_reason = ""   # 직전 호출의 stop_reason(생성 절단 검증 V1) — 생성기가 payload에 기록


def _dummy(prompt: str) -> str:
    """ANTHROPIC_API_KEY 없을 때 골격 검증용 더미(형식 유지) — 기존 동작 보존."""
    return ("[제목]\n[샘플] " + prompt[:30].replace("\n", " ")
            + "\n[메타설명]\n샘플 메타설명\n[본문]\n## 소제목\n샘플 본문 (이미지: 메인사진)\n"
            "[이미지배치]\n- 서론: 메인사진\n[키워드]\n샘플,키워드,지역")


def call(prompt: str, model: str = MODEL, max_tokens: int = 1200) -> str:
    """공용 Claude 호출. 키 없으면 더미. SDK 기본 재시도(429/5xx) + 타임아웃."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _dummy(prompt)
    import anthropic
    client = anthropic.Anthropic(timeout=60.0)   # 무한 대기 방지(SDK 기본 재시도 유지)
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    if getattr(resp, "stop_reason", "") == "max_tokens":   # thinking이 예산을 잠식해 본문이 잘림 → 2배로 1회 재시도
        resp = client.messages.create(
            model=model, max_tokens=max_tokens * 2,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
    global last_finish_reason
    last_finish_reason = getattr(resp, "stop_reason", "") or ""
    import logging
    logging.getLogger("shopcast.llm").info("[llm] stop_reason=%s max_tokens=%s", last_finish_reason, max_tokens)
    return next((b.text for b in resp.content if b.type == "text"), "")


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


def route(task: str) -> tuple[str, str]:
    """작업 유형 → (provider, model). 미설정이면 ('anthropic', 기본 모델)."""
    v = (os.environ.get(f"LLM_{task.upper()}") or "").strip()
    if ":" in v:
        p, m = v.split(":", 1)
        if p.strip().lower() in ("gemini", "anthropic") and m.strip():
            return p.strip().lower(), m.strip()
    return "anthropic", MODEL


def _gemini_generate(parts: list, model: str, max_tokens: int) -> str:
    """Gemini REST 호출 — parts는 [{text}|{inline_data}] 목록. 실패 시 예외(상위에서 폴백)."""
    import requests as _rq
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 미설정")
    r = _rq.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                 params={"key": key},
                 json={"contents": [{"parts": parts}],
                       "generationConfig": {"maxOutputTokens": max(max_tokens, 2000)}},
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
              images: list | None = None) -> str:
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
    am = default_model or MODEL
    if images:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("anthropic 키 없음(비전 폴백 불가)")
        import anthropic
        content = ([{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}
                    for mt, b64 in images] + [{"type": "text", "text": prompt}])
        resp = anthropic.Anthropic(timeout=60.0).messages.create(
            model=am, max_tokens=max_tokens, messages=[{"role": "user", "content": content}])
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    return call(prompt, am, max_tokens)
