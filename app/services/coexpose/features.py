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
    return {
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
