"""
인스타 캐러셀(정보 슬라이드) 생성 — 사진 1장 → 커버+포인트+CTA 카드 묶음 (#2).
캐러셀은 저장·도달이 높아 셀러/소상공인 모두 유효. 브랜드 테마(사업형태)로 일관 디자인.
규격: 1080x1350(4:5, 인스타 피드 최적).
"""
from __future__ import annotations

import os
import uuid

from app.generators.video import _pil_font, _theme_rgb

CW, CH = 1080, 1350


def _wrap(d, text, font, maxw):
    out, cur = [], ""
    for ch in (text or ""):
        if ch == "\n":
            out.append(cur); cur = ""; continue
        if d.textlength(cur + ch, font=font) <= maxw:
            cur += ch
        else:
            out.append(cur); cur = ch
    if cur:
        out.append(cur)
    return out


def _bg(theme_key, idx):
    from PIL import Image, ImageDraw
    rgb = _theme_rgb(theme_key)
    dark = (14, 16, 24)
    c2 = tuple(int(rgb[k] * 0.4 + dark[k] * 0.6) for k in range(3))
    a, b = ((26, 22, 42), c2) if idx % 2 == 0 else (c2, (16, 16, 26))
    img = Image.new("RGB", (CW, CH), a)
    ov = Image.new("RGB", (CW, CH), b)
    m = Image.new("L", (CW, CH))
    md = ImageDraw.Draw(m)
    for y in range(CH):
        md.line([(0, y), (CW, y)], fill=int(255 * y / CH))
    img.paste(ov, (0, 0), m)
    return img


def _logo(d, theme_key):
    rgb = _theme_rgb(theme_key)
    d.rounded_rectangle([64, 60, 132, 128], 16, fill=rgb)
    d.line([80, 114, 94, 92, 106, 104, 124, 76], fill="white", width=7, joint="curve")
    d.ellipse([116, 72, 130, 86], fill="white")
    d.text((150, 72), "올린다", font=_pil_font(46, "ExtraBold"), fill=(255, 255, 255))


def build_carousel(tenant, title, points, theme_key, out_dir) -> list[str]:
    """커버 → 포인트(최대5) → CTA 슬라이드 PNG 경로 리스트."""
    from PIL import ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    rgb = _theme_rgb(theme_key)
    pts = [p for p in (points or []) if p and p.strip()][:5]
    paths: list[str] = []

    # 1) 커버
    img = _bg(theme_key, 0); d = ImageDraw.Draw(img); _logo(d, theme_key)
    ft = _pil_font(84, "ExtraBold")
    lines = _wrap(d, title, ft, CW - 160)[:4]
    y = CH // 2 - len(lines) * 56 - 90
    for ln in lines:
        d.text((80, y), ln, font=ft, fill="white"); y += 104
    d.text((80, y + 24), "👉 넘겨보기", font=_pil_font(46, "SemiBold"), fill=rgb)
    p = os.path.join(out_dir, f"car0_{uuid.uuid4().hex}.png"); img.save(p); paths.append(p)

    # 2) 포인트 슬라이드
    for i, pt in enumerate(pts):
        img = _bg(theme_key, i + 1); d = ImageDraw.Draw(img); _logo(d, theme_key)
        d.ellipse([80, 210, 196, 326], fill=rgb)
        num = str(i + 1)
        fn = _pil_font(72, "ExtraBold")
        d.text((138 - d.textlength(num, font=fn) / 2, 232), num, font=fn, fill="white")
        fb = _pil_font(62, "Bold")
        lines = _wrap(d, pt, fb, CW - 160)[:6]
        y = 392
        for ln in lines:
            d.text((80, y), ln, font=fb, fill=(245, 245, 250)); y += 86
        d.text((80, CH - 92), f"{i + 1} / {len(pts)}", font=_pil_font(36), fill=(150, 155, 180))
        p = os.path.join(out_dir, f"car{i + 1}_{uuid.uuid4().hex}.png"); img.save(p); paths.append(p)

    # 3) CTA 마지막
    img = _bg(theme_key, 9); d = ImageDraw.Draw(img); _logo(d, theme_key)
    d.text((80, CH // 2 - 150), "지금 바로", font=_pil_font(56, "SemiBold"), fill=(200, 205, 235))
    fc = _pil_font(72, "ExtraBold")
    lines = _wrap(d, (getattr(tenant, "name", "") or "여기") + " 둘러보기", fc, CW - 160)[:3]
    y = CH // 2 - 50
    for ln in lines:
        d.text((80, y), ln, font=fc, fill="white"); y += 92
    d.text((80, y + 28), "저장 ⭐  공유 📤  팔로우 ➕", font=_pil_font(40, "SemiBold"), fill=rgb)
    p = os.path.join(out_dir, f"carZ_{uuid.uuid4().hex}.png"); img.save(p); paths.append(p)
    return paths
