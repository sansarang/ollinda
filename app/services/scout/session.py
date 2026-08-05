"""🌐 네이버 검색 페이지 세션 — 브라우저를 여는 코드는 여기 하나뿐이다.

★ R4(사본 금지): 정찰(blocks.scan)과 역설계(reverse.collector)가 같은 페이지를 연다.
  각자 브라우저를 열면 대기 시간·UA·간격 규칙이 갈라지고, 한쪽만 고치게 된다 —
  2026-08-05에 파서를 두 곳에 둬서 겪은 그 사고다.

★ R1(공개 결과만): 로그인·쿠키·세션 주입을 하지 않는다. 일반 사용자가 보는 화면만 읽는다.
★ R2(사람 수준): 요청 간 간격을 두고 무작위 지연을 섞는다. 폭주하지 않는다.
  캡차·403·비정상 응답이 보이면 그 수집을 즉시 멈추고 사유를 올린다 — 자동 재시도는 하지 않는다
  (재시도가 차단을 확정시킨다).
"""
from __future__ import annotations

import logging
import random
import time

_log = logging.getLogger("shopcast.scout.session")

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
             "Mobile/15E148 Safari/604.1")
SEARCH_URL = "https://m.search.naver.com/search.naver?query="
GAP_MIN, GAP_MAX = 2.5, 5.0          # 키워드 사이 간격(초) — 사람 속도
SCROLL_ROUNDS = 7                    # 지연 로딩 유도

# 비정상 응답 신호 — 하나라도 보이면 그 수집을 멈춘다(R2)
BLOCK_SIGNS = ("자동입력 방지", "비정상적인 검색", "일시적으로 제한", "captcha", "CAPTCHA")


class Blocked(Exception):
    """차단·캡차 감지 — 재시도하지 않고 즉시 올린다."""


def open_page(p, show: bool = False):
    b = p.chromium.launch(headless=not show)
    pg = b.new_page(viewport={"width": 420, "height": 900}, user_agent=UA_MOBILE)
    return b, pg


def load_query(pg, kw: str, rounds: int = SCROLL_ROUNDS) -> None:
    """검색 한 건을 사람 속도로 연다. 차단 신호가 보이면 Blocked를 올린다."""
    r = pg.goto(SEARCH_URL + kw.replace(" ", "+"), wait_until="networkidle", timeout=45000)
    if r is not None and r.status in (403, 429):
        raise Blocked(f"HTTP {r.status}")
    for _ in range(rounds):
        pg.mouse.wheel(0, 2000 + random.randint(0, 400))
        pg.wait_for_timeout(900 + random.randint(0, 400))
    txt = pg.evaluate("() => document.body.innerText || ''")
    hit = [s for s in BLOCK_SIGNS if s in txt]
    if hit:
        raise Blocked("차단 신호: " + ", ".join(hit))


def gap() -> None:
    """다음 키워드까지 쉰다 — 무작위 지연으로 기계적 주기를 피한다."""
    time.sleep(random.uniform(GAP_MIN, GAP_MAX))
