"""
인쇄물 자동 생성(신규기능②) — HTML 템플릿 → Playwright(Chromium) 렌더 → PNG/PDF → R2 저장.
Playwright/Chromium 미설치 시 graceful(에러 dict 반환, 앱은 계속 동작).
규격 프리셋: A4 전단지 / A5 / 정사각 POP / 배너.
"""
from __future__ import annotations

import os
import uuid

# 규격 프리셋 — 96dpi 기준 픽셀(w,h). scale=인쇄 선명도 배율(2배=고해상).
PRINT_PRESETS = {
    "flyer_a4": {"w": 794, "h": 1123, "label": "A4 전단지", "scale": 2},
    "a5":       {"w": 559, "h": 794,  "label": "A5 전단",   "scale": 2},
    "pop":      {"w": 800, "h": 800,  "label": "정사각 POP", "scale": 2},
    "banner":   {"w": 1200, "h": 628, "label": "가로 배너",  "scale": 2},
}
DEFAULT_PRESET = "flyer_a4"


def preset(name: str) -> dict:
    return PRINT_PRESETS.get(name, PRINT_PRESETS[DEFAULT_PRESET])


def _out_dir(tenant_id: str) -> str:
    d = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant_id, "print")
    os.makedirs(d, exist_ok=True)
    return d


def render(html: str, preset_name: str, tenant_id: str, fmt: str = "png") -> dict:
    """HTML → 이미지/PDF 렌더 후 저장. 반환 {ok, path, url, error}.
    Playwright/Chromium 없으면 {ok:False, error:...}."""
    p = preset(preset_name)
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {"ok": False, "error": "인쇄물 렌더 엔진(Playwright)이 아직 준비 중이에요. 잠시 후 다시 시도해 주세요."}

    out_dir = _out_dir(tenant_id)
    ext = "pdf" if fmt == "pdf" else "png"
    out = os.path.join(out_dir, f"print_{uuid.uuid4().hex}.{ext}")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": p["w"], "height": p["h"]},
                                    device_scale_factor=p.get("scale", 2))
            page.set_content(html, wait_until="networkidle")
            if fmt == "pdf":
                page.pdf(path=out, width=f"{p['w']}px", height=f"{p['h']}px", print_background=True)
            else:
                page.screenshot(path=out, full_page=False)
            browser.close()
    except Exception as e:
        import logging
        logging.exception("[printable] 렌더 실패")
        return {"ok": False, "error": f"인쇄물 생성 중 오류가 났어요. 다시 시도해 주세요. ({str(e)[:60]})"}

    if not os.path.exists(out):
        return {"ok": False, "error": "인쇄물 파일 생성 실패"}

    # R2 미러(있으면) — /print/{tenant}/{fname} 서빙에 사용
    url = None
    try:
        from app import storage
        storage.mirror_to_r2(out)
        url = storage.public_url_for(out)
    except Exception:
        pass
    return {"ok": True, "path": out, "url": url, "preset": preset_name, "fmt": fmt}
