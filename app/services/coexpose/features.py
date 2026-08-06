"""📐 장소 신호 계측 — 글이 '실제 장소'를 얼마나 가리키는가. LLM 0콜(R7).

★ 업종 중립: 업종어를 목록으로 갖지 않는다. 재료는 질의에서 온 지역·업종 토큰과
  글 안의 장소 신호(주소·전화·영업시간·예약·지도)뿐이다.
★ 한 업종에만 나오는 신호는 인자가 아니라 잡음이다 — 채택은 contrast가 교차 검증 후에.
"""
from __future__ import annotations

import re

_PHONE = re.compile(r"0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}")
_ADDR = re.compile(r"(시|도)\s?[가-힣]+(구|군|시)\s?[가-힣0-9]+(동|읍|면|로|길)")
_HOURS = re.compile(r"(영업\s?시간|오픈|open|평일|주말|휴무|연중무휴|\d{1,2}\s?시\s?~\s?\d{1,2}\s?시)")
_BOOK = re.compile(r"(예약|상담\s?문의|카카오톡|톡톡|네이버\s?예약|전화\s?주세요|DM)")
_MAP = re.compile(r"(map\.naver\.com|place\.naver\.com|지도\s?보기|찾아오는\s?길|오시는\s?길)")
_TOK = re.compile(r"[가-힣A-Za-z0-9]+")

# ★ 2026-08-06 정독에서 눈으로 잡은 축 — 장소 신호가 아니라 **문서 구조**였다.
#   같은 채널·같은 주제·같은 길이(2741 vs 2792자)인데 하나만 떴고, 차이는 이것들이었다:
#     표 2 vs 0 · 목록 6 vs 1 · 영상 2 vs 0 · FAQ 있음/없음 · 내부링크 있음/없음
#     평균 줄 길이 37 vs 22자(안 뽑힌 글은 빈 줄을 많이 넣어 문단이 잘게 부서짐)
#     제목: '기아 EV6'(고유명사) vs '추천·원스톱'(일반 홍보어)
_FAQ = re.compile(r"(^|\n)\s*Q\s*[.:]|자주\s?묻는|Q&A")
_BLANK = re.compile(r"^[\s\u200b\u200c\ufeff·ㆍ]*$")
# 일반 홍보어 — 어느 업종이나 쓰는 말(업종어가 아니라 마케팅 어휘라 중립)
_PROMO = re.compile(r"(추천|최고|전문|원스톱|믿을\s?수|후회\s?없|만족도|1위|저렴|합리적|친절)")
# 고유 식별자 — 영문+숫자 결합(EV6·PV5), 브랜드형 표기. seo.input_anchors와 같은 계열.
_PROPER = re.compile(r"\b([A-Za-z]{1,6}\d{1,4}[A-Za-z]?|\d{1,4}[A-Za-z]{1,6})\b")


def _toks(s):
    return {t for t in _TOK.findall(s or "") if len(t) >= 2}


def measure(post: dict, query: str = "", shop_hint: str = "") -> dict:
    """글 하나의 장소 신호. 뽑힘/안뽑힘 라벨은 여기서 안 본다(자기 자신 설명 금지)."""
    title = (post.get("title") or "")
    text = (post.get("text") or "")
    n = max(1, len(text))
    qt = _toks(query)
    tt, bt = _toks(title), _toks(text)
    # 질의 엔티티가 제목·본문에 얼마나 살아 있는가(지역+업종 매칭)
    lines = (text or "").split("\n")
    blanks = sum(1 for x in lines if _BLANK.match(x))
    real = [x for x in lines if not _BLANK.match(x) and x.strip()]
    internal = len(re.findall(r"blog\.naver\.com/" + re.escape(post.get("blog") or "zzz"), text)) \
        if post.get("blog") else 0
    return {
        # ── 문서 구조(정독에서 나온 축) ──
        "faq": len(_FAQ.findall(text)),
        "has_faq": int(bool(_FAQ.search(text))),
        "videos": int(post.get("videos") or 0),
        "internal_links": internal,
        "blank_ratio": round(blanks / max(1, len(lines)), 2),
        "line_avg_len": round(sum(len(x) for x in real) / max(1, len(real)), 1),
        "structure_score": (int(post.get("tables") or 0) * 2 + int(post.get("lists") or 0)
                            + int(bool(_FAQ.search(text))) * 2 + int(post.get("videos") or 0)),
        # ── 제목 미세 구조 ──
        "title_proper": len(_PROPER.findall(title)),
        "title_promo": len(_PROMO.findall(title)),
        "title_promo_ratio": round(len(_PROMO.findall(title)) / max(1, len(_toks(title))), 2),
        "promo_density": round(len(_PROMO.findall(text)) * 1000.0 / n, 2),
        # ── 장소 신호(소거된 축 — 기록은 남긴다) ──
        "phone": len(_PHONE.findall(text)),
        "addr": len(_ADDR.findall(text)),
        "hours": len(_HOURS.findall(text)),
        "booking": len(_BOOK.findall(text)),
        "map_signal": len(_MAP.findall(text)),
        "has_place_link": int("place.naver.com" in text or "map.naver.com" in text),
        "shop_mentions": (text.count(shop_hint) if shop_hint else 0),
        "q_in_title": len(qt & tt),
        "q_in_title_ratio": round(len(qt & tt) / max(1, len(qt)), 2),
        "q_in_text": len(qt & bt),
        "q_density": round(sum(text.count(t) for t in qt) * 1000.0 / n, 2),
        "title_len": len(title),
        "text_len": len(text),
        "images": int(post.get("images") or 0),
        "tables": int(post.get("tables") or 0),
        "lists": int(post.get("lists") or 0),
        "h2": int(post.get("h2") or 0),
        "h3": int(post.get("h3") or 0),
    }
