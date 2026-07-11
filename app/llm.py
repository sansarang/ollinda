"""
공용 Claude 호출 계층 — 모델 ID·호출·폴백을 한 곳에 모은다(리팩토링 #2).
기존 text_claude._call_llm 이 이 모듈로 위임하며, 동작(모델·adaptive thinking·무키 더미)은 그대로.
개선점: 요청 타임아웃 지정(무한 대기 방지)·비용 로깅 훅 1곳.
"""
from __future__ import annotations

import os

MODEL = "claude-opus-4-8"


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
    return next((b.text for b in resp.content if b.type == "text"), "")
