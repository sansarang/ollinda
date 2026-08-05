"""🚧 정찰 수집 게이트 — 실패를 데이터로 둔갑시키지 않는다.

★ 2026-08-05 실측 사고: 네이버가 자동화 브라우저에 검색 결과를 안 줬다(본문 7,102자).
  그 빈 껍데기에서 파서는 h2/h3를 긁어 '추천 검색어·플레이스 MY·숏텐츠 NOW'를
  지면 블록으로 보고했다. 수집 실패가 지면 지도가 된 것이다.
  맥북·서버 양쪽에서 같았으므로 그동안 쌓인 데이터도 같은 방식으로 오염됐을 수 있다.

원칙(침묵 폴백 금지의 정찰판): 결과 지면이 0이면 **빈칸 + 사유**다. 추측으로 채우지 않는다.
"""
from __future__ import annotations

# 네이버 UI 껍데기 — 검색 '결과'가 아니라 화면 장치다(플랫폼 어휘라 업종 무관).
UI_CHROME = ("최근 검색어", "추천 검색어", "이 정보가 표시된 이유", "숏텐츠", "플레이스 MY",
             "네이버 클립", "네이버 가격비교", "네이버플러스", "쇼핑 광고", "관련 검색어",
             "도움말", "검색 옵션", "자주 찾는", "인기 주제")
MIN_TEXT_LEN = 20000          # 실제 통합검색 결과 페이지의 본문 길이(실측: 껍데기는 7,102자)


def is_chrome(title: str) -> bool:
    t = (title or "").strip()
    return any(u in t for u in UI_CHROME)


def real_blocks(blocks: list) -> list:
    """UI 껍데기를 걷어낸 '진짜 결과 블록'."""
    return [b for b in (blocks or []) if b and not is_chrome(b)]


def verdict(blocks: list, blogs_seen: list = None, text_len: int = 0) -> dict:
    """이 수집을 지면 지도에 써도 되는가.

    쓸 수 있는 조건: 진짜 결과 블록이 하나라도 있을 것.
    본문이 너무 짧으면 렌더가 안 끝났거나 껍데기를 받은 것이다 — 그것도 실패다.
    """
    real = real_blocks(blocks)
    reasons = []
    if not real:
        reasons.append("결과 지면 0 — UI 껍데기만 잡혔다")
    if text_len and text_len < MIN_TEXT_LEN:
        reasons.append(f"본문 {text_len}자 < {MIN_TEXT_LEN} — 검색 결과가 안 그려졌다")
    ok = not reasons
    return {"ok": ok, "real_blocks": real, "chrome_dropped": len(blocks or []) - len(real),
            "status": "수집" if ok else "수집 실패", "reasons": reasons}


def suspect_row(blocks_json: str) -> bool:
    """이미 쌓인 행이 오염 의심인가 — 진짜 결과 블록이 하나도 없으면 의심이다.

    ★ 의심 행을 지우지 않는다. 지우면 오염 규모의 증거가 사라지고,
      어느 판정이 오염 위에서 내려졌는지 역추적할 수 없다(사장님 지시).
    """
    import json
    try:
        b = json.loads(blocks_json or "[]")
    except Exception:
        return False
    if not isinstance(b, list) or not b:
        return False
    return not real_blocks([x if isinstance(x, str) else (x or {}).get("title", "") for x in b])
