"""
AI 영상 생성 — 이미지→영상(Runway Gen-4.5 / Veo). 비동기 작업 → 폴링.
env: RUNWAY_API_KEY. host: api.dev.runwayml.com.
키 없으면 None → 상위에서 ffmpeg 슬라이드쇼로 폴백.
docs: https://docs.dev.runwayml.com/  (모델/버전은 도입 시 확인)
"""
from __future__ import annotations

import os
import time
import uuid

BASE = "https://api.dev.runwayml.com/v1"
MODEL = "gen4.5"          # 또는 veo (도입 시 확인)


def configured() -> bool:
    return bool(os.environ.get("RUNWAY_API_KEY"))


def image_to_video(image_url: str, prompt: str, out_dir: str,
                   duration: int = 5) -> str | None:
    """공개 image_url + 프롬프트 → 영상 파일 경로. 미설정/실패 시 None.
    ※ image_url은 공개 https 여야 함(배포된 /asset/... 사용)."""
    if not (configured() and image_url):
        return None
    import requests
    h = {"Authorization": f"Bearer {os.environ['RUNWAY_API_KEY']}",
         "X-Runway-Version": "2024-11-06", "Content-Type": "application/json"}
    try:
        # 1) 작업 생성
        r = requests.post(f"{BASE}/image_to_video", headers=h, json={
            "model": MODEL, "promptImage": image_url,
            "promptText": prompt, "duration": duration, "ratio": "720:1280"}, timeout=30)
        r.raise_for_status()
        task_id = r.json().get("id")
        # 2) 폴링
        for _ in range(60):
            time.sleep(5)
            s = requests.get(f"{BASE}/tasks/{task_id}", headers=h, timeout=30).json()
            if s.get("status") == "SUCCEEDED":
                url = (s.get("output") or [None])[0]
                if not url:
                    return None
                out = os.path.join(out_dir, f"aiv_{uuid.uuid4().hex}.mp4")
                with open(out, "wb") as f:
                    f.write(requests.get(url, timeout=120).content)
                return out
            if s.get("status") in ("FAILED", "CANCELED"):
                return None
        return None
    except Exception:
        return None
