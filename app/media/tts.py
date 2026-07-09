"""
TTS — 내레이션 대본 → 자연스러운 음성(mp3).
기본: Google Gemini TTS(gemini-2.5-flash-preview-tts, 자연스러운 한국어) — GEMINI_API_KEY(결제 필요).
대안: ElevenLabs(유료 플랜) — ELEVENLABS_API_KEY.
둘 다 없으면 None(영상은 무음). 업로드당 1회 호출.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import uuid

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")   # 차분한 한국어 보이스
EL_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
LAST_ERR = ""   # 진단용 — 마지막 TTS 실패 원인


def configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("ELEVENLABS_API_KEY"))


def synthesize(text: str, out_dir: str) -> str | None:
    """text → mp3 경로. Gemini TTS 우선, 실패 시 ElevenLabs, 둘 다 안 되면 None."""
    if not text.strip():
        return None
    return _gemini(text, out_dir) or _elevenlabs(text, out_dir)


def _gemini(text: str, out_dir: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not (key and shutil.which("ffmpeg")):
        return None
    import requests
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}:generateContent",
            params={"key": key},
            json={"contents": [{"parts": [{"text": text}]}],
                  "generationConfig": {"responseModalities": ["AUDIO"],
                      "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}},
            timeout=120)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        d = next((p.get("inlineData") or p.get("inline_data") for p in parts
                  if p.get("inlineData") or p.get("inline_data")), None)
        if not d or not d.get("data"):
            return None
        os.makedirs(out_dir, exist_ok=True)
        pcm = os.path.join(out_dir, f"tts_{uuid.uuid4().hex}.pcm")
        mp3 = pcm.replace(".pcm", ".mp3")
        with open(pcm, "wb") as f:
            f.write(base64.b64decode(d["data"]))
        # Gemini TTS = L16 PCM 24kHz mono → mp3
        subprocess.run(["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
                        "-i", pcm, mp3], capture_output=True, timeout=60)
        os.remove(pcm)
        return mp3 if os.path.exists(mp3) else None
    except Exception as e:
        global LAST_ERR
        LAST_ERR = repr(e)[:200]
        return None


def _elevenlabs(text: str, out_dir: str) -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    import requests
    voice = os.environ.get("ELEVENLABS_VOICE_ID", EL_DEFAULT_VOICE)
    out = os.path.join(out_dir, f"tts_{uuid.uuid4().hex}.mp3")
    try:
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                          headers={"xi-api-key": key, "Content-Type": "application/json"},
                          json={"text": text, "model_id": "eleven_multilingual_v2"}, timeout=90)
        r.raise_for_status()
        with open(out, "wb") as f:
            f.write(r.content)
        return out
    except Exception:
        return None
