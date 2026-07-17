"""
근거 카드(trust) — 자동 글감 큐/배치가 이 글을 왜 이렇게 썼는지 사장님 언어 3줄로 렌더.

원칙(작업 R2~R4):
- 읽기 전용: 버튼·링크·액션 없음(판단을 유저에게 되돌리지 않는다).
- 내부 용어(P1~P4·큐·승률·앵글·배치 등)와 검색량·%·점수는 화면에 절대 노출하지 않는다.
- 수치는 실측(순위·일수)만 삽입. 값이 없으면 그 문장을 빼고 렌더(빈 괄호·null 금지).
- reason(내부 로그 JSON) 파싱 실패 시 기본(P4) 템플릿 폴백 — 카드가 글 표시를 막지 않는다.
"""
from __future__ import annotations

import json
import logging

_log = logging.getLogger("shopcast.trustcard")

# 앵글 → 사장님 말 (내부 용어 노출 금지)
_ANGLE_DESC = {
    "review": "실제 작업 과정을 그대로 보여주는 후기",
    "howto": "방법을 차근차근 알려주는 안내",
    "price": "비용이 왜 다른지 풀어주는 설명",
}
_DEFAULT_ANGLE = "손님이 찾는 답을 주는"

TITLE = "이 글, 왜 이렇게 썼냐면"
FOOTER = "순위는 예상이 아니라 발행 후 실제로 확인해서 알려드려요. 첫 페이지까지 보통 2~4주 걸려요."
PUBLISHED_LINE = "발행 완료 — 이제 순위를 매일 자동으로 확인해서 알려드릴게요."


def _meta(reason: str) -> dict:
    """reason 필드 파싱 — JSON이면 dict, 아니면(구형 로그) 빈 dict."""
    try:
        m = json.loads(reason or "")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def render_trust_card(item: dict | None) -> dict | None:
    """queue_item({source_type,target_keyword,angle,reason}) → {title, lines[≤3], footer}.
    target_keyword 없으면 None(카드 생략 — '근거 없음' 류 문구 금지)."""
    if not item:
        return None
    kw = " ".join((item.get("target_keyword") or "").split())
    if not kw:
        return None
    st = (item.get("source_type") or "P4").strip()
    ang = _ANGLE_DESC.get((item.get("angle") or "").strip(), _DEFAULT_ANGLE)
    meta = _meta(item.get("reason") or "")

    lines: list[str] = []
    if st == "P1" and meta.get("lowctr"):
        # 저CTR 재도전(CTR 4-3): 순위는 있는데 클릭이 없는 글 — 제목을 바꿔 재도전
        lines = [f"'{kw}' 검색 첫 페이지에는 있는데 클릭이 적어서,",
                 "제목을 바꿔 다시 도전하는 글이에요."]
        return {"title": TITLE, "lines": lines, "footer": FOOTER}
    if st == "P1":
        rank, days = meta.get("last"), meta.get("days")
        l1 = f"'{kw}' 검색에서 내 글이 "
        l1 += (f"{rank}위에서 " if isinstance(rank, int) and rank >= 1 else "")
        l1 += (f"{days}일째 멈춰 있어요." if isinstance(days, int) and days >= 1 else "한동안 멈춰 있어요.")
        lines = [l1,
                 "같은 검색어라도 글의 각도가 다르면 결과가 달라지는 경우가 많아서,",
                 f"이번엔 {ang} 글로 다시 도전해요."]
    elif st == "P2":
        lines = [f"'{kw}'로 검색하면 아직 사장님 가게가 안 나와요.",
                 "이 검색어를 잡으려고 새로 쓴 글이에요.",
                 f"{ang} 글로 풀었어요."]
    elif st == "P3":
        lines = [f"'{kw}'에서 지금 잘 나가고 있어요.",
                 "한 편 더 올려서 자리를 굳히는 글이에요."]
        if meta.get("gap"):
            lines.append("지금 한 곳만 넘으면 순위가 오르는 상황이라, 그 차이를 메우는 내용을 넣었어요.")
    else:
        if st not in ("P4",):
            _log.info("[trustcard] 알 수 없는 source_type=%r → 기본 템플릿 폴백", st)
        lines = [f"'{kw}'를 검색하는 손님을 노렸어요.",
                 "이 검색어는 찾는 사람은 있는데 제대로 된 글이 적어서, 지금 쓰면 첫 페이지에 갈 가능성이 높은 편이에요.",
                 f"그래서 {ang} 글로 썼어요."]
    return {"title": TITLE, "lines": lines, "footer": FOOTER}
