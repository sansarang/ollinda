"""렌더 백엔드 어댑터 (gorender 이관) — 분기는 '이 파일 안에만'.

RENDER_BACKEND = python | go | shadow (기본 python).
- python: 현행 render_storyboard(변경 0).
- go: build_render_job → 자산 R2 업로드 → gorender /render → 폴링 → 결과 R2 회수.
       실패(타임아웃·오류·health 불통) 시 python 자동 폴백(사용자는 실패를 못 봄).
- shadow: 실산출은 python(사용자 무변화) + 동일 잡을 gorender에도(best-effort) → 결과 별도 저장 +
          비교 로그(해상도·길이·씬수·크기·렌더시간). python 결과 반환.
게이트·생성·발행 흐름 무변경. render_storyboard·build_render_job 로직 수정 0(호출만).
"""
from __future__ import annotations

import json
import os
import time
import uuid
import urllib.request

from app import storage
from app.generators import video as _vid
from app.services import render_job as _rj

GORENDER_URL = os.environ.get("GORENDER_URL", "http://gorender.railway.internal:8080").rstrip("/")
_SHADOW_LOG = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), "shadow_log.jsonl")


def backend() -> str:
    return (os.environ.get("RENDER_BACKEND", "python") or "python").strip().lower()


def render(sb, img_by_id, kws, tenant, strat, title="", sale_price="", mileage="", mode_override=""):
    """단일 진입 어댑터. 반환 (vp, note, dur, cover, compare, meta). mode_override로 요청별 백엔드 지정 가능."""
    gen = _vid.ShortVideoGenerator()
    mode = (mode_override or "").strip().lower() or backend()

    def _python():
        with _vid.RENDER_SEM:
            return gen.render_storyboard(sb, img_by_id, kws, tenant, strat,
                                         title=title, sale_price=sale_price, mileage=mileage)

    if mode == "go":
        try:
            vp, note, dur, cover, compare = _go(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage)
            return vp, note, dur, cover, compare, {"backend": "go"}
        except Exception as e:
            import logging
            logging.warning("[render_backend] go 실패 → python 폴백: %r", e)
            vp, note, dur, cover, compare = _python()
            return vp, note, dur, cover, compare, {"backend": "python(fallback)", "go_error": repr(e)[:150]}

    if mode == "shadow":
        vp, note, dur, cover, compare = _python()               # 실결과(사용자 무변화)
        shadow = {"attempted": True}
        try:
            shadow = _go_shadow(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage,
                                py_dur=dur, py_scenes=len([c for c in compare if c.get("dur")]),
                                py_path=vp)
        except Exception as e:
            shadow = {"attempted": True, "error": repr(e)[:150]}
        return vp, note, dur, cover, compare, {"backend": "shadow", "shadow": shadow}

    # python (기본)
    vp, note, dur, cover, compare = _python()
    return vp, note, dur, cover, compare, {"backend": "python"}


# ── gorender 호출 파이프라인 ──
def _build_and_upload(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage):
    """build_render_job(로컬 자산) → R2 업로드 → 키 재작성한 job dict 반환. (job, jobid, work)."""
    import tempfile
    work = tempfile.mkdtemp(prefix="rjadapter_")
    job = _rj.build_render_job(sb, img_by_id, kws, tenant, strat, work,
                               sale_price=sale_price, mileage=mileage)
    if not job:
        raise RuntimeError("empty_job")
    jobid = uuid.uuid4().hex[:16]
    prefix = f"renderjobs/{jobid}"
    # 자산(로컬 basename) → R2 업로드 + 키 재작성
    def up(name):
        if not name:
            return name
        local = os.path.join(work, name)
        key = storage.put_key(local, f"{prefix}/{name}")
        if not key:
            raise RuntimeError(f"R2 업로드 실패: {name}")
        return key
    job["ass_r2_key"] = up(job["ass_r2_key"])
    if job["meta"].get("bgm_r2_key"):
        job["meta"]["bgm_r2_key"] = up(job["meta"]["bgm_r2_key"])
    for s in job["scenes"]:
        if s.get("photo"):
            s["photo"]["r2_key"] = up(s["photo"]["r2_key"])
        if s.get("card"):
            s["card"]["png_r2_key"] = up(s["card"]["png_r2_key"])
        if s.get("tts_audio_r2_key"):
            s["tts_audio_r2_key"] = up(s["tts_audio_r2_key"])
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    return job, jobid


def _http_json(method, url, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def _gorender_render(job, poll_timeout=540):
    """gorender에 잡 제출 + 완료 폴링 → result dict(video_r2_key 등)."""
    st, resp = _http_json("POST", f"{GORENDER_URL}/render", job, timeout=30)
    gjid = resp.get("id")
    if not gjid:
        raise RuntimeError(f"render 제출 실패: {st} {resp}")
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        _st, jr = _http_json("GET", f"{GORENDER_URL}/job/{gjid}", timeout=20)
        status = jr.get("status")
        if status == "done":
            return jr.get("result") or {}
        if status == "failed":
            raise RuntimeError(f"gorender 실패: {(jr.get('result') or {}).get('error')}")
        time.sleep(3)
    raise RuntimeError("gorender 폴링 타임아웃")


def _fetch_result(result, tenant, tag):
    """gorender 결과(R2 키)를 로컬(storage/tenant)로 회수 → (video_path, cover_path)."""
    vkey = result.get("video_r2_key")
    if not vkey:
        raise RuntimeError("결과 video_r2_key 없음")
    out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
    os.makedirs(out_dir, exist_ok=True)
    vp = os.path.join(out_dir, f"{tag}_{uuid.uuid4().hex}.mp4")
    if not storage.get_key(vkey, vp):
        raise RuntimeError("결과 회수 실패")
    storage.mirror_to_r2(vp)   # 로컬 캐시 만료 대비 tenant 키로도 미러(서빙 일관)
    cover = None
    if result.get("cover_r2_key"):
        cp = os.path.join(out_dir, f"{tag}cover_{uuid.uuid4().hex}.png")
        if storage.get_key(result["cover_r2_key"], cp):
            cover = cp
    return vp, cover


def _go(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage):
    job, _jid = _build_and_upload(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage)
    result = _gorender_render(job)
    vp, cover = _fetch_result(result, tenant, "goshort")
    dur = result.get("duration_sec") or 0
    compare = [{"role": s["role"], "shot": (s.get("photo") or s.get("card")), "dur": s["duration_sec"],
                "자막": s["subtitle_text"], "발화": s["speech_text"]} for s in job["scenes"]]
    note = f"gorender(Go) · 씬 {len(job['scenes'])}개 · R2"
    return vp, note, dur, cover, compare


def _go_shadow(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage, py_dur, py_scenes, py_path):
    """실산출은 python — Go는 병행(best-effort). 결과 별도 저장 + 비교 로그 append."""
    t0 = time.time()
    job, jid = _build_and_upload(sb, img_by_id, kws, tenant, strat, title, sale_price, mileage)
    result = _gorender_render(job)
    go_ms = int((time.time() - t0) * 1000)
    vp, _cover = _fetch_result(result, tenant, "shadow")
    # 비교 지표
    def _probe(path):
        try:
            return _vid._probe_dur(path), os.path.getsize(path)
        except Exception:
            return 0, 0
    go_dur = result.get("duration_sec") or 0
    py_size = os.path.getsize(py_path) if (py_path and os.path.exists(py_path)) else 0
    go_size = os.path.getsize(vp) if os.path.exists(vp) else 0
    _meta = sb.get("meta", {}) if isinstance(sb.get("meta"), dict) else {}
    rec = {"set_id": _meta.get("set_id", "") or _meta.get("canonical", ""),
           "tenant": tenant.id,
           "py": {"dur": py_dur, "scenes": py_scenes, "size": py_size, "path": os.path.basename(py_path or "")},
           "go": {"dur": go_dur, "scenes": len(job["scenes"]), "size": go_size, "render_ms": go_ms,
                  "path": os.path.basename(vp)},
           "match": {"scenes": py_scenes == len(job["scenes"]),
                     "dur_delta": round(abs((py_dur or 0) - (go_dur or 0)), 2)}}
    try:
        with open(_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return {"ok": True, "go_video": os.path.basename(vp), "record": rec}


def shadow_log(limit=50) -> list:
    if not os.path.exists(_SHADOW_LOG):
        return []
    out = []
    with open(_SHADOW_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out[-limit:]
