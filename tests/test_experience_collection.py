"""
경험 수집 체계 박제(2026-08-03 사장님 지시 — 전면 교정).

바뀐 계약 셋:
  A. 질문은 '글 만들 때 그 주제로' 한 개. 상시 상자는 보조이고 재촉하지 않는다.
  B. 묻기 전에 자사 기록에서 먼저 캔다 — 쌓일수록 질문이 줄어든다.
  C. 남의 경험(웹)을 이 가게 것으로 쓰지 않는다. 손님 발화는 출처를 붙인다.
"""
from __future__ import annotations

import inspect
import uuid

from app import db
from app.services import gapscout as gs
from app.services import harvest as hv


# ── A. 인라인 질문 ───────────────────────────────────────────────
def test_inline_question_is_topic_scoped():
    """A1. 질문은 지금 만드는 그 세트의 주제에만 붙는다 — 맥락 없는 질문은 답이 안 나온다."""
    q = gs.inline_question("T_NONE_" + uuid.uuid4().hex[:6], "팰리세이드 썬팅")
    assert q["ask"] is True and q["topic"] == "팰리세이드 썬팅"
    assert gs.inline_question("T", "")["ask"] is False, "주제 없이 묻는다"


def test_question_has_no_kitchen_terms():
    """A2. 사장님 화면에 주방(SEO) 용어가 나가면 안 된다 — 사장님은 결과만 보신다.
    특정 업종 말투('작업')도 쓰지 않는다 — 빵집·카페에도 그대로 통해야 한다."""
    for tmpl in gs._Q_BY_ANGLE.values():
        for banned in ("검색", "키워드", "자리", "상위", "노출", "{kw}", "작업"):
            assert banned not in tmpl, f"{banned} in {tmpl}"


def test_standing_box_does_not_nag():
    """A3. 상시 상자는 보조다 — 뱃지·미답변 카운트·검색량 표기로 재촉하지 않는다."""
    from app import main as _m
    # ★ 주석은 사고 계보다 — 검사는 '화면에 나가는 문구'만 본다(주석의 낱말로 실패하면 안 된다).
    src = "\n".join(ln.split("#", 1)[0] for ln in inspect.getsource(_m.my_experience).splitlines())
    for banned in ("자리가 비어", "상위 글", "빈자리", "미답변", "검색량"):
        assert banned not in src, f"상시 상자가 주방을 노출하거나 재촉한다: {banned}"
    assert "limit=2" in src, "상시 상자가 질문을 몰아서 보여준다(재촉)"


# ── B. 수확 — 묻기 전에 안다 ─────────────────────────────────────
def test_harvest_reads_only_own_records():
    """B1. 수확처는 자사 실데이터뿐이다 — 외부(웹) 소스가 섞이면 남의 경험이 들어온다."""
    src = inspect.getsource(hv)
    for real in ("list_blog_publishes", "get_set_pieces", "list_owner_experience"):
        assert real in src, f"자사 수확처 누락: {real}"
    # 외부 호출 자체가 없어야 한다(정규식 .search()는 무관 — 웹을 부르는 코드를 본다)
    for ext in ("requests", "urllib", "http://", "https://", "webfetch", "blogrank", "searchad"):
        assert ext not in src, f"외부 소스에서 경험을 캔다: {ext}"


def test_harvest_skips_generic_sentences():
    """B2. '문의 주세요' 같은 안내문은 경험이 아니다 — 누구나 쓸 수 있는 문장은 거른다."""
    assert hv._EXP_SIGN.search("제가 직접 확인했습니다")
    assert hv._GENERIC.search("문의 주시면 상담해 드립니다")
    got = hv._sentences("## 소제목\n제가 직접 엔진룸까지 확인했습니다. 짧다.\n[사진1]\n")
    assert any("직접" in s for s in got), got
    assert all("[사진1]" not in s for s in got), "마커가 경험 문장으로 들어감"


def test_covered_topics_are_not_asked_again():
    """B3. 쌓일수록 질문이 줄어드는 구조 — 이미 답한 주제는 다시 묻지 않는다."""
    tid = "T_HV_" + uuid.uuid4().hex[:8]
    try:
        db.save_owner_experience(
            tid, "이번에 하고 나서 손님이 뭐라고 하셨어요?",
            "팰리세이드 신차 썬팅하고 나서 열이 확 줄었다고 하셨습니다. 제가 직접 확인했습니다." * 2)
        q = gs.inline_question(tid, "팰리세이드 썬팅")
        assert q["ask"] is False, f"이미 답한 주제를 또 묻는다: {q}"
        assert "이미" in (q.get("why_skip") or ""), q
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM owner_experience WHERE tenant_id=?", (tid,))


def test_harvest_wired_into_generation():
    """B4. 수확이 실제 생성에 들어가야 의미가 있다 — 만들어만 두면 아무 일도 안 일어난다."""
    from app.services import generate as _g
    src = inspect.getsource(_g.generate_for)
    assert "harvest" in src and "as_note_block" in src, "수확이 생성 관문에 없다"


# ── C. 출처·위조 금지 ────────────────────────────────────────────
def test_customer_words_are_labeled_not_forged():
    """C1. 손님 발화를 사장 경험으로 위조하지 않는다 — 출처를 달아 구분한다."""
    src = inspect.getsource(hv.as_note_block)
    assert "손님 후기" in src and "사장님 경험으로 쓰지 말고" in src, "출처 구분 지시가 없다"
    rsrc = inspect.getsource(hv.from_reviews)
    assert '"review"' in rsrc, "손님 발화가 사장 경험과 같은 종류로 저장된다"


def test_constitution_bans_borrowed_experience():
    """C2. 금지선이 헌법에 박혀 있어야 한다 — 코드에만 있으면 다음 세션이 모른다."""
    import pathlib
    txt = (pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text()
    assert "남의 경험을 이 가게 경험으로 쓰지 않는다" in txt
    assert "서치는 수요 정찰용이지 사장 대변용이 아니다" in txt
    assert '"실물" = 프로덕션' in txt, "실물 검증 표준이 없다"
