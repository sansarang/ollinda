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


# 인쇄물 타입 — 타입별 기본 규격 매핑
PRINT_TYPES = {
    "menu":  {"label": "메뉴판", "preset": "flyer_a4"},
    "price": {"label": "가격표", "preset": "a5"},
    "event": {"label": "이벤트 전단", "preset": "flyer_a4"},
    "pop":   {"label": "신메뉴 POP", "preset": "pop"},
    "store": {"label": "매장 안내", "preset": "banner"},
}


def _photo_data_uri(path: str) -> str:
    """로컬 사진 → base64 data URI(headless 렌더에서 자체 포함). 실패 시 빈 문자열."""
    try:
        import base64
        if not (path and os.path.exists(path)):
            return ""
        ext = path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
        with open(path, "rb") as f:
            return f"data:image/{mime};base64," + base64.b64encode(f.read()).decode()
    except Exception:
        return ""


def generate_copy(ptype: str, industry: str, name: str, items: list, note: str = "") -> dict:
    """인쇄물 문구 생성 — 헤드라인·태그라인만 AI(app.llm), 항목·가격은 사장님 입력 그대로(날조 금지).
    items: [{'name':..,'price':..}] (사장님 입력=사실). LLM은 가격/항목을 만들거나 바꾸지 않는다."""
    items = [{"name": (it.get("name") or "").strip(), "price": (str(it.get("price") or "")).strip()}
             for it in (items or []) if (it.get("name") or "").strip()]
    label = PRINT_TYPES.get(ptype, {}).get("label", "인쇄물")
    headline, tagline = name or label, ""
    try:
        from app import llm, seo
        item_txt = ", ".join(f"{it['name']}" + (f"({it['price']})" if it['price'] else "") for it in items[:12])
        prompt = (
            f"너는 소상공인 인쇄물(전단/POP) 카피라이터다. 아래로 '{label}'용 짧은 카피를 만들어라.\n"
            f"[가게] {name} (업종: {industry})\n[항목] {item_txt}\n[메모] {note}\n\n"
            f"{seo.FACTS_RULE}\n"
            "규칙: 헤드라인(12자 내외, 시선 끌기)과 태그라인(20자 내외, 한 줄) '만' 만들어라. "
            "항목·가격은 절대 지어내거나 바꾸지 마라(위 입력만이 사실). 없는 혜택·수치 금지.\n"
            "출력 형식(딱 2줄):\n헤드라인: ...\n태그라인: ..."
        )
        raw = llm.call(prompt, max_tokens=200) or ""
        for line in raw.splitlines():
            if line.startswith("헤드라인:"):
                headline = line.split(":", 1)[1].strip() or headline
            elif line.startswith("태그라인:"):
                tagline = line.split(":", 1)[1].strip()
        if headline.startswith("[") or "샘플" in headline:   # 무키 더미 방어 → 안전 폴백
            headline, tagline = (name or label), ""
    except Exception:
        pass
    return {"type": ptype, "label": label, "name": name, "industry": industry,
            "headline": headline, "tagline": tagline, "items": items}


def _esc(s: str) -> str:
    import html
    return html.escape(str(s or ""))


def _qr_data_uri(tenant, ptype: str) -> str:
    """매장 QR(추적 P4) — 스캔하면 /r/{code}?src=qr&content=print_{ptype} 경유로 이동.
    오프라인 유입이 리포트에 '매장 QR'로 구분 집계된다. 실패/목적지 없음 → ''(억지 삽입 금지)."""
    try:
        import base64
        import io

        import qrcode

        from app.services import tracklinks
        link = tracklinks.tenant_link(tenant)
        if not link:
            return ""
        url = f"{tracklinks._base()}/r/{link['code']}?src=qr&content=print_{ptype}"
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def build_html(data: dict, photo_uri: str = "", qr_uri: str = "") -> str:
    """타입별 HTML/CSS 템플릿 렌더. Jinja2 있으면 사용, 없으면 순수 문자열 폴백."""
    ptype = data.get("type", "menu")
    head = _esc(data.get("headline") or data.get("name") or "")
    tag = _esc(data.get("tagline") or "")
    name = _esc(data.get("name") or "")
    items = data.get("items") or []
    photo = (f"<div class='photo' style=\"background-image:url('{photo_uri}')\"></div>" if photo_uri else "")
    rows = "".join(
        f"<div class='row'><span class='iname'>{_esc(it['name'])}</span>"
        f"<span class='iprice'>{_esc(it['price'])}</span></div>"
        for it in items) or "<div class='row muted'>항목을 입력하면 여기에 표시돼요</div>"

    base_css = """
    *{margin:0;padding:0;box-sizing:border-box;font-family:'Nanum Gothic','Apple SD Gothic Neo','Malgun Gothic',sans-serif}
    body{width:100%;height:100vh;background:#fff;color:#1e293b}
    .wrap{width:100%;height:100%;padding:48px 44px;display:flex;flex-direction:column}
    .brand{font-size:15px;font-weight:800;color:#6366f1;letter-spacing:.5px}
    .headline{font-size:44px;font-weight:900;line-height:1.15;margin-top:8px}
    .tag{font-size:19px;color:#64748b;margin-top:10px}
    .photo{width:100%;height:280px;border-radius:20px;background-size:cover;background-position:center;margin:22px 0}
    .list{margin-top:18px;flex:1}
    .row{display:flex;justify-content:space-between;align-items:baseline;padding:12px 0;border-bottom:1px dashed #e2e8f0}
    .iname{font-size:22px;font-weight:700}
    .iprice{font-size:22px;font-weight:900;color:#4f46e5}
    .muted{color:#94a3b8;border:none;justify-content:center}
    .foot{margin-top:auto;font-size:15px;color:#94a3b8;text-align:center;padding-top:16px}
    .qrfoot{display:flex;align-items:center;justify-content:space-between;text-align:left;gap:14px}
    .qrfoot img{width:92px;height:92px;border-radius:8px}
    .qrcap{font-size:12px;color:#64748b;font-weight:700;text-align:center;margin-top:2px}
    .center{text-align:center;align-items:center;justify-content:center}
    .pop .headline{font-size:60px}.pop .tag{font-size:24px}
    .banner .wrap{flex-direction:row;align-items:center;gap:30px}
    .grad{background:linear-gradient(135deg,#6366f1,#ec4899)}
    """
    # 매장 QR(추적 P4) — 스캔 유입이 '매장 QR(오프라인)'로 집계. 있으면 foot이 좌우 배치로 전환
    qr = (f"<div><img src='{qr_uri}' alt='QR'><div class='qrcap'>QR 찍고 바로 보기</div></div>" if qr_uri else "")

    def _foot(text):
        if not qr:
            return f"<div class='foot'>{text}</div>"
        return f"<div class='foot qrfoot'><div style='flex:1'>{text}</div>{qr}</div>"
    # 타입별 본문
    if ptype == "pop":
        inner = (f"<div class='wrap center pop'><div class='brand'>{name}</div>"
                 f"<div class='headline grad' style='-webkit-background-clip:text;background-clip:text;color:transparent'>{head}</div>"
                 f"<div class='tag'>{tag}</div>{photo}"
                 f"<div class='list' style='width:100%'>{rows}</div>"
                 + (_foot(f"{name}") if qr else "") + "</div>")
    elif ptype == "banner":
        inner = (f"<div class='wrap banner'>{photo or ''}<div style='flex:1'>"
                 f"<div class='brand'>{name}</div><div class='headline'>{head}</div>"
                 f"<div class='tag'>{tag}</div></div>{qr}</div>")
    elif ptype == "store":
        inner = (f"<div class='wrap'><div class='brand'>{name}</div><div class='headline'>{head}</div>"
                 f"<div class='tag'>{tag}</div>{photo}<div class='list'>{rows}</div>"
                 + _foot(f"{name} · 올린다로 제작") + "</div>")
    else:  # menu / price / event
        inner = (f"<div class='wrap'><div class='brand'>{name}</div><div class='headline'>{head}</div>"
                 f"<div class='tag'>{tag}</div>{photo}<div class='list'>{rows}</div>"
                 + _foot("가격·구성은 매장 사정에 따라 변경될 수 있어요") + "</div>")
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{base_css}</style></head><body>{inner}</body></html>"


def generate(ptype: str, tenant, items: list, note: str = "", photo_path: str = "",
             fmt: str = "png", with_qr: bool = True) -> dict:
    """타입·데이터 → 문구 생성 → 템플릿 바인딩 → 렌더 → 저장. 반환 render()와 동일 + copy.
    with_qr: 매장 추적 QR 삽입(추적 P4) — 스캔 유입이 리포트에 '매장 QR'로 집계."""
    industry = getattr(tenant, "industry", "") or ""
    name = getattr(tenant, "name", "") or ""
    data = generate_copy(ptype, industry, name, items, note)
    photo_uri = _photo_data_uri(photo_path) if photo_path else ""
    qr_uri = _qr_data_uri(tenant, ptype) if with_qr else ""
    html = build_html(data, photo_uri, qr_uri)
    preset_name = PRINT_TYPES.get(ptype, {}).get("preset", DEFAULT_PRESET)
    res = render(html, preset_name, getattr(tenant, "id", "print"), fmt=fmt)
    res["copy"] = {"headline": data["headline"], "tagline": data["tagline"], "type": ptype, "label": data["label"]}
    return res


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
