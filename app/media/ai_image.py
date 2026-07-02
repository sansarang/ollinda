"""
AI 이미지 생성 — Google Gemini 이미지 모델(gemini-2.5-flash-image, generateContent).
사장님 사진이 부족할 때 보조 이미지 생성. env: GEMINI_API_KEY.
키 없으면/실패 시 None(업로드 사진만 사용). ※ 결제(billing) 활성 필요.
docs: https://ai.google.dev/
"""
from __future__ import annotations

import base64
import os
import uuid

MODEL = "gemini-2.5-flash-image"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def generate(prompt: str, out_dir: str) -> str | None:
    """프롬프트 → 이미지 파일 경로. 미설정/실패 시 None."""
    if not (configured() and prompt.strip()):
        return None
    import requests
    try:
        r = requests.post(
            ENDPOINT.format(model=MODEL),
            params={"key": os.environ["GEMINI_API_KEY"]},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=90)
        r.raise_for_status()
        parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
        for p in parts:
            d = p.get("inlineData") or p.get("inline_data")
            if d and d.get("data"):
                out = os.path.join(out_dir, f"aiimg_{uuid.uuid4().hex}.png")
                with open(out, "wb") as f:
                    f.write(base64.b64decode(d["data"]))
                return out
        return None
    except Exception:
        return None
