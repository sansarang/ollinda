"""
광고 소재팩 — 만든 숏폼에서 '유료 광고용' 소재를 파생.
  · 6초·15초 광고컷(완주율↑)  · 광고 카피 3세트(헤드라인/본문/CTA)  · zip 묶음
광고대행(수수료) 라인의 탄약. Claude 없으면 카피는 안전 폴백으로 생성.
"""
from __future__ import annotations

import os
import subprocess
import uuid
import zipfile


def build_cuts(video_path: str, out_dir: str) -> dict:
    """원본 세로영상 → 15초·6초 광고컷(앞부분=훅). 실패 시 해당 컷 생략."""
    cuts: dict = {}
    if not (video_path and os.path.exists(video_path)):
        return cuts
    os.makedirs(out_dir, exist_ok=True)
    for sec in (15, 6):
        out = os.path.join(out_dir, f"ad{sec}s_{uuid.uuid4().hex}.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-i", video_path, "-t", str(sec),
                            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", out],
                           capture_output=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out):
            cuts[f"{sec}s"] = out
    return cuts


def _fallback_copy(tenant, piece, cta: str) -> list[dict]:
    texts = (piece.payload.get("scene_texts") or [])
    title = piece.payload.get("title") or tenant.name
    hooks = [piece.payload.get("hook_strategy") or title,
             (texts[0] if texts else title),
             f"{tenant.name} — 지금 확인하세요"]
    body = (texts[1] if len(texts) > 1 else (piece.payload.get("narration") or "")[:60]) or title
    btn = "구매하기" if (getattr(tenant, "biz_type", "local") in ("seller", "hybrid")) else "방문·예약"
    return [{"headline": h[:24], "body": body[:80], "cta": btn} for h in hooks[:3]]


def _parse_copy(raw: str) -> list[dict]:
    import re
    sets = [s for s in re.split(r"===+", raw) if s.strip()]
    out = []
    for s in sets[:3]:
        def grab(tag):
            m = re.search(rf"\[{tag}\]\s*(.+)", s)
            return m.group(1).strip() if m else ""
        h, b, c = grab("헤드라인"), grab("본문"), grab("CTA버튼")
        if h or b:
            out.append({"headline": h[:24] or "지금 확인", "body": b[:90], "cta": (c or "자세히")[:8]})
    return out


def build_copy(tenant, piece) -> list[dict]:
    """메타/유튜브 광고용 카피 3세트. Claude 있으면 생성, 없으면 폴백."""
    from app.strategies import resolve_strategy
    strat = resolve_strategy(tenant)
    src = " ".join((piece.payload.get("scene_texts") or [])[:4]) or (piece.payload.get("title") or "")
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from app.generators.text_claude import _call_llm
            prompt = (
                f"제품/가게: {tenant.name} (업종: {tenant.industry})\n"
                f"목표 행동(CTA): {strat.cta}\n소재 내용: {src}\n\n"
                "메타/유튜브 '유료광고'용 광고문구 3세트를 만들어라. 과장·허위 금지, 구체적 이득 강조.\n"
                "각 세트 형식(그대로):\n[헤드라인]\n(20자 내외 강한 후킹)\n[본문]\n(1~2문장)\n"
                "[CTA버튼]\n(5자 내외)\n세트 사이는 === 로 구분(총 3세트)."
            )
            parsed = _parse_copy(_call_llm(prompt, max_tokens=800))
            if parsed:
                return parsed
        except Exception:
            pass
    return _fallback_copy(tenant, piece, strat.cta)


def copy_text(tenant, copies: list[dict]) -> str:
    lines = [f"[올린다 광고 소재팩] {tenant.name}", ""]
    for i, c in enumerate(copies, 1):
        lines += [f"■ 버전 {i}", f"헤드라인: {c['headline']}", f"본문: {c['body']}",
                  f"CTA버튼: {c['cta']}", ""]
    lines += ["※ 메타 광고관리자/유튜브 캠페인에 위 영상+문구를 넣어 집행하세요.",
              "※ 6초=인지형, 15초=전환형 권장."]
    return "\n".join(lines)


def build_zip(out_dir: str, files_map: dict, copy_txt: str) -> str:
    """영상컷·규격·이미지 + 광고카피.txt 를 zip으로."""
    os.makedirs(out_dir, exist_ok=True)
    zpath = os.path.join(out_dir, f"adpack_{uuid.uuid4().hex}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name, path in files_map.items():
            if path and os.path.exists(path):
                z.write(path, arcname=name)
        z.writestr("광고카피.txt", copy_txt)
    return zpath
