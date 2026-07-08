"""
사진 자동 보정 — 폰으로 대충 찍은 사진을 '전문가 톤'으로.
PIL 기반이라 키·결제 없이 항상 작동(즉시). 업종별 톤(음식=먹음직, 상품=선명).
과하지 않게(은은하게) — 원본 왜곡 없이 '잘 찍은 사진' 느낌만.
"""
from __future__ import annotations

import os

# 음식/먹거리 업종 키워드 — 채도·따뜻함 강조(먹음직)
_FOOD = ("음식", "식당", "맛집", "카페", "베이커리", "빵", "고기", "정육", "분식", "한식",
         "일식", "중식", "양식", "치킨", "피자", "디저트", "떡", "반찬", "포차", "술집", "횟집", "국밥")


def _is_food(industry: str) -> bool:
    ind = industry or ""
    return any(w in ind for w in _FOOD)


def auto_enhance(src: str, out: str | None = None, industry: str = "") -> str:
    """폰 사진 → 전문가 톤 보정(밝기·대비·자동레벨·채도·선명도). 실패 시 원본 경로 반환."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except Exception:
        return src
    try:
        im = Image.open(src)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        elif im.mode == "L":
            im = im.convert("RGB")
        # 1) 자동 레벨(칙칙한 폰 사진의 톤 복원) — 극단값 1% 컷
        im = ImageOps.autocontrast(im, cutoff=1)
        food = _is_food(industry)
        # 2) 밝기·대비(은은하게)
        im = ImageEnhance.Brightness(im).enhance(1.06)
        im = ImageEnhance.Contrast(im).enhance(1.08)
        # 3) 채도 — 음식은 먹음직(강), 상품·매장은 자연스럽게(약)
        im = ImageEnhance.Color(im).enhance(1.22 if food else 1.12)
        # 4) 선명도(디테일 살리기)
        im = ImageEnhance.Sharpness(im).enhance(1.25)
        out = out or src
        im.save(out, "JPEG", quality=90)
        return out
    except Exception:
        return src


def enhance_all(paths: list[str], industry: str = "") -> int:
    """여러 장 일괄 보정(제자리). 보정 성공 개수 반환."""
    n = 0
    for p in paths:
        if p and os.path.exists(p) and auto_enhance(p, p, industry) == p:
            n += 1
    return n
