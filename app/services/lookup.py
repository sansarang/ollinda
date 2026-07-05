"""
상품/스토어 URL → 상품명·대표이미지·설명 자동 추출 (OG 메타태그).
셀러가 링크만 붙여넣으면 자동 인식 (쿠팡/스마트스토어/11번가 등). 실패 시 {} 폴백.
"""
from __future__ import annotations

import re

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _og(html: str, prop: str) -> str:
    for pat in (rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
                rf'<meta[^>]+name=["\']{prop}["\'][^>]+content=["\']([^"\']+)'):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1).strip()
    return ""


def parse_url(url: str) -> dict:
    """URL → {name, image, description}. 실패 시 {}."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {}
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": _UA})
        if r.status_code != 200:
            return {}
        html = r.text
        name = _og(html, "title")
        if not name:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
            name = m.group(1).strip() if m else ""
        return {
            "name": name,
            "image": _og(html, "image"),
            "description": _og(html, "description"),
        }
    except Exception:
        return {}
