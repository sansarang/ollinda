"""🤖 에이전트 체제 — 사진을 올리면 나머지는 에이전트들이 알아서 한다.

2026-08-17 사장님 지시:
  "너가 계속 수정하지 말고 각각의 에이전트들한테 역할분담을 해라. 24시간 내내."
  "내가 발행하는 순간 데이터도 즉시 로직에 적용되어야 해."
  "난 사진과 글을 올리면 에이전트들이 알아서 했으면 좋겠어. 그게 인공지능이다."

무엇이 문제였나 — 이날 하루에 내가 손으로 박은 상수가 셋이고 셋 다 틀렸다.
  PER_PARA 0.7 · CHARS_PER_PHOTO 200 · MIN_PHOTOS 3
그리고 이미 만들어둔 자율 시스템(lessons·adapt_consume·immune)은 돌고는 있었지만
교훈 24건이 전부 wins 0·fails 0 — **효과를 모른 채 쌓이기만** 했다.

자율 등급(이 패키지가 강제한다):
  L0 관측만 · L1 제안→사람 승인 · L2 파라미터 자율 조정(검증 의무) · L3 코드 수정=금지

  ★ 파라미터는 자율, 규칙은 사람.
    "사진이 뭉치면 안 된다"는 규칙이라 사람이 정한다.
    "몇 장이 상한인가"는 숫자라 에이전트가 결과를 보고 정한다.
    규칙을 AI가 바꾸면 금지선도 바꾼다 — 그래서 L3은 영구 금지다.

에이전트 8종(전부 기존 부품의 재배치 — 새로 만든 것이 아니다):
  정찰 board·bloganatomy·blogreach        L2
  취재 research·harvest·marketterms       L2
  작가 text_claude(+Solar)                L1
  편집 qualitycheck·answerblock·photocap  L2
  관측 ranktrack·rivaltrack·botlog        L0
  학습 lessons·adapt_consume + params     L2  ← 파라미터를 고치는 유일한 주체
  면역 immune·watchtower                  L1
  관제 weekly_report·scheduler            L1
"""
from __future__ import annotations

from app.agents import journal, params  # noqa: F401  (재수출 — 호출부가 여기서 받는다)

#: 에이전트 이름 — 일지·파라미터 scope가 이 값을 쓴다(문자열이 흩어지면 집계가 깨진다)
SCOUT = "정찰"
RESEARCH = "취재"
WRITER = "작가"
EDITOR = "편집"
OBSERVER = "관측"
LEARNER = "학습"
IMMUNE = "면역"
OPS = "관제"

ALL = (SCOUT, RESEARCH, WRITER, EDITOR, OBSERVER, LEARNER, IMMUNE, OPS)

#: 자율 등급 — 코드가 이 표를 실제로 강제한다(문서가 아니라 계약)
LEVEL = {SCOUT: 2, RESEARCH: 2, WRITER: 1, EDITOR: 2,
         OBSERVER: 0, LEARNER: 2, IMMUNE: 1, OPS: 1}


def can_tune(agent: str) -> bool:
    """그 에이전트가 파라미터를 스스로 바꿔도 되는가(L2 이상)."""
    return LEVEL.get(agent, 0) >= 2
