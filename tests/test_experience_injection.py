"""
사장님 실경험이 실제로 글에 들어가는가 박제(2026-08-02 실사고).

사고: '썬팅 가격' 글을 뽑았는데 사장님이 주신 '루마 정품·버텍스700·열성형'이 하나도
안 들어갔다. owner_experience가 트랙 B에서만 쓰이고 트랙 A는 아예 안 읽었기 때문이다.
경험이 있어야 큐에 넣는다고 게이트를 걸어놓고 정작 글에는 안 쓴 셈이다 — 게이트가 형식만 남았다.
"""
from __future__ import annotations

import uuid

from app import db
from app.services import autoqueue as aq

EXPS = [
    ("'썬팅 가격' 찾는 손님이 많아요. 가격이 갈리는 이유가 뭔가요?",
     "필름 등급이 절반, 시공 실력이 절반입니다. 루마 정품만 쓰는데 버텍스700이냐 아래 등급이냐에 "
     "따라 필름값이 몇 배 차이 납니다. 열성형을 제대로 하느냐에 따라 몇 년 뒤 기포·들뜸이 갈립니다."),
    ("'신차 썬팅' 작업 후 손님이 만족하신 점은?",
     "출고 당일 바로 하고 가시는 분들이 새 차 그대로 시작해서 좋다고 하십니다. 신차는 유리가 "
     "깨끗해서 필름 밀착이 제일 잘 나오는 시기라 마감 상태에 만족하시는 경우가 많습니다."),
]


def _seed(tid):
    for q, a in EXPS:
        db.save_owner_experience(tid, q, a)


def test_relevant_experience_matches_keyword():
    """A. 키워드와 겹치는 답변만 고른다 — 엉뚱한 경험을 넣으면 글이 딴 데로 샌다."""
    tid = "T_EXP_" + uuid.uuid4().hex[:8]
    try:
        _seed(tid)
        got = aq.relevant_experience(tid, "썬팅 가격")
        assert got, "관련 답변을 못 찾음"
        assert "버텍스700" in got[0]["a"], f"가격 질문에 다른 답변이 1순위: {got[0]['q']}"
        none = aq.relevant_experience(tid, "제주 감귤 배송")
        assert none == [], f"무관한 키워드에 답변을 붙임: {none}"
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM owner_experience WHERE tenant_id=?", (tid,))


def test_experience_note_keeps_specifics():
    """B. 주입 블록은 '구체적인 것을 살리라'고 지시해야 한다.
    실측: 지시가 없으니 '정품 필름도 라인업이 나뉩니다' 같은 일반론으로 바뀌었다 —
    어느 가게나 쓸 수 있는 글은 98일 된 상위 글을 못 이긴다."""
    tid = "T_EXP_" + uuid.uuid4().hex[:8]
    try:
        _seed(tid)
        note = aq.experience_note(tid, "썬팅 가격")
        assert "사장님 실제 답변" in note
        assert "버텍스700" in note, "실제 답변 원문이 안 들어감"
        assert "일반론" in note, "일반론으로 바꾸지 말라는 지시가 없음"
        assert "지어내지" in note, "답변에 없는 것을 만들지 말라는 지시가 없음"
        assert aq.experience_note(tid, "제주 감귤 배송") == "", "무관한 키워드에도 주입"
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM owner_experience WHERE tenant_id=?", (tid,))


def test_track_a_injects_experience():
    """C. 트랙 A(매물·시공) 생성 경로가 실제로 주입한다 — 여기가 빠져서 사고가 났다."""
    import inspect
    src = inspect.getsource(aq.consume)
    assert "experience_note(t.id, kw)" in src, "트랙 A가 실경험을 안 읽는다"
    i = src.find("experience_note(t.id, kw)")
    j = src.find("generate_for(")
    assert 0 < i < j, "주입이 생성보다 뒤에 있으면 프롬프트에 안 들어간다"
    assert "note += _expn" in src, "주입 결과를 노트에 붙이지 않는다"


def test_missing_experience_is_logged_not_silent():
    """D. 관련 답변이 없으면 그 사실이 로그에 남아야 한다 — 왜 일반론 글이 나왔는지
    나중에 알 수 있어야 한다(조용한 실패 금지)."""
    import inspect
    src = inspect.getsource(aq.consume)
    assert "실경험 없음" in src, "관련 답변 0을 조용히 넘긴다"
