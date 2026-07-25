"""렌더-잡 직렬화 (gorender 이관 어댑터) — 콘티+해석 → render_job_v1(자산 사전생성).

★ 원칙: 기존 게이트/해석/렌더 자산 함수를 '호출만' 한다. render_storyboard 로직 수정 0.
   render_storyboard와 '동일한' 씬 해석(주행거리 단일화·VG3·발화 정규화·VG4 크롭·TTS 길이)을
   재현하되, 렌더 대신 자산(카드 PNG·씬 TTS·ASS)을 사전 생성해 파일로 남기고 render_job.json을 쓴다.
   → Go가 이 잡을 렌더하면 Python render_storyboard와 동일 결과(파리티 원천 보장).
계약: ~/Desktop/gorender/contracts/render_job_v1.json.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid

from app.generators import video as _v
from app.media import bgm as _bgm
from app.media import tts as _tts


def _stow(src: str, out_dir: str, name: str) -> str:
    """자산을 out_dir로 복사하고 basename 키 반환(자체완결 잡 — 이식·zip 가능)."""
    dst = os.path.join(out_dir, name)
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy(src, dst)
    return name


def _accent_hex(theme_key: str) -> str:
    rgb = _v._theme_rgb(theme_key)
    return "0x%02X%02X%02X" % rgb


def build_render_job(sb: dict, img_by_id: dict, kws, tenant, strat, out_dir: str,
                     sale_price: str = "", mileage: str = "") -> dict:
    """콘티(render_v1) + 세트값 → render_job_v1 + 자산 파일. out_dir에 자산·job.json 기록.
    반환 job dict. r2_key = out_dir 내 로컬 파일 경로(LocalProvider가 그대로 소비)."""
    os.makedirs(out_dir, exist_ok=True)
    gen = _v.ShortVideoGenerator()
    # 카드 디자인 토큰(업종 중립) — render_storyboard와 동일 경로
    try:
        from app.services import indschema as _isc
        _biz = getattr(tenant, "biz_type", "local") or "local"
        _sch = _isc.get_schema(getattr(tenant, "industry", "") or "", _biz)
        vtok = gen._visual_tokens(_sch.get("visual_preset") or "basic")
    except Exception:
        vtok = gen._visual_tokens("basic")

    scenes_out = []
    ass_scenes = []
    t = 0.0
    for i, s in enumerate(sb.get("scenes", [])):
        role = s.get("role", "")
        line = (s.get("line") or "").strip()
        if mileage:                                   # 주행거리 단일화(호출)
            line = _v._normalize_mileage(line, mileage)
        sh = s.get("shot") or {}
        # VG3 가격(호출) — 위반 씬 제외
        if _v._price_semantics_violation(line, sale_price):
            continue
        # 발화 정규화(호출) + 게이트
        speak = _v._speechify(line)
        if _v._speech_number_left(speak):
            continue
        # TTS 사전 합성(render_storyboard와 동일 호출) → 오디오 자산 + 길이
        seg_tts, word_times = _tts.synthesize_timed(speak, out_dir) if line else (None, [])
        if speak != line:
            word_times = []
        td = _v._probe_dur(seg_tts) if seg_tts else 0
        sdur = min(15.0, max(_v.MIN_SCENE, td + 0.4)) if td > 0.3 else \
            gen._clamp(len(line) * 0.14 + 1.0)

        tts_key = _stow(seg_tts, out_dir, f"tts_{i}.wav") if seg_tts else None
        scene = {"index": i, "role": role, "subtitle_text": line, "speech_text": speak,
                 "duration_sec": round(sdur, 3), "tts_audio_r2_key": tts_key}

        if "photo_id" in sh:
            pid = sh.get("photo_id")
            crop = sh.get("crop", "full")
            if crop == "closeup" and _v._EVIDENCE_REF.search(line):   # VG4(호출)
                crop = "full"
            img = img_by_id.get(pid)
            if not (img and os.path.exists(img)):
                continue
            _ext = os.path.splitext(img)[1] or ".jpg"
            scene["kind"] = "photo"
            scene["photo"] = {"r2_key": _stow(img, out_dir, f"photo_{i}{_ext}"), "crop": crop}
            scene["card"] = None
        elif "card" in sh:
            cv = str((sh["card"] or {}).get("value", "")).strip()
            cl = str((sh["card"] or {}).get("label", "")).strip()
            if mileage:
                cv = _v._normalize_mileage(cv, mileage)
            if not cv:
                continue
            if _v._PRICE_RE.search(cv) and _v._price_semantics_violation(f"{cl} {cv}", sale_price):
                continue
            # 카드 PNG 사전 렌더(B-2) — render_storyboard와 동일 _data_card_png
            cp = os.path.join(out_dir, f"card_{i}.png")
            gen._data_card_png(cp, cv, cl, vtok)
            scene["kind"] = "card"
            scene["card"] = {"png_r2_key": f"card_{i}.png", "value": cv, "label": cl}
            scene["photo"] = None
        else:
            continue

        _text, _emph = _v._parse_emphasis(line)
        ass_scenes.append((t, sdur, _text, word_times, _emph))
        t += sdur
        scenes_out.append(scene)

    if not scenes_out:
        return {}

    # ASS 자막 사전 생성(호출) — 카라오케·강조 baked
    from app.industries import subtitle_preset as _sp
    ass_path = os.path.join(out_dir, "cap.ass")
    _v._build_ass(ass_scenes, kws, strat.key, ass_path,
                  preset=_sp(getattr(tenant, "industry", "") or ""))

    # BGM 선택(호출) — 파일 확정, out_dir로 stow
    _bgm_src = _bgm.pick(_bgm.mood_for(getattr(tenant, "industry", "") or ""))
    bgm_key = _stow(_bgm_src, out_dir, "bgm" + (os.path.splitext(_bgm_src)[1] or ".mp3")) if _bgm_src else None
    meta = sb.get("meta", {}) or {}
    job = {
        "version": "render_job_v1",
        "meta": {
            "set_id": meta.get("set_id", "") or getattr(sb, "set_id", "") or "",
            "tenant_id": getattr(tenant, "id", ""),
            "channel": meta.get("channel", "naver"),
            "aspect": meta.get("aspect", "9:16"),
            "width": _v.W, "height": _v.H, "fps": _v.FPS, "xfade_sec": _v.XFADE,
            "canonical": meta.get("canonical", ""),
            "accent_hex": _accent_hex(strat.key),
            "bgm_r2_key": bgm_key,
            "sale_price": sale_price or "", "mileage": mileage or "",
        },
        "ass_r2_key": "cap.ass",
        "scenes": scenes_out,
    }
    with open(os.path.join(out_dir, "render_job.json"), "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=1)
    return job
