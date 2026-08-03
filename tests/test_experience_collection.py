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


def test_constitution_scopes_the_ban_to_person_forgery():
    """C2. 금지 범위가 정확해야 한다(2026-08-03 사장님 정정).
    검색으로 확인한 사실을 3인칭으로 쓰는 것은 취재다 — 금지는 그것을 1인칭 경험으로
    바꾸는 인칭 위조뿐이다. 범위를 넓게 잡으면 쓸 수 있는 재료까지 막는다."""
    import pathlib
    txt = (pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text()
    assert "검색은 취재다" in txt, "검색 자체를 금지한 것처럼 읽힌다"
    assert "3인칭 사실 서술" in txt and "1인칭 경험" in txt, "허용/금지 경계가 없다"
    assert "경험이 없다고 글을 멈추지 않는다" in txt, "보류 원칙이 옛것 그대로다"
    assert '"실물" = 프로덕션' in txt, "실물 검증 표준이 없다"


def test_third_person_rule_injected_when_no_experience():
    """C3. 경험 기록이 없으면 3인칭 사실 서술로 쓰라고 지시한다 —
    지시가 없으면 모델이 없는 경험을 지어낸다(1인칭 위조)."""
    from app.services import generate as _g
    src = inspect.getsource(_g.generate_for)
    assert "인칭 규칙" in src, "인칭 규칙 주입이 없다"
    assert "1인칭 경험 주장 금지" in src and "3인칭 사실 서술" in src
    i, j = src.find("인칭 규칙"), src.find("_generate_sequential")
    assert 0 < i < j, "생성 실행보다 뒤에 있으면 프롬프트에 안 들어간다"


def test_hold_only_for_first_person_formats():
    """C4. 보류는 1인칭 서사가 형식상 필수인 글(후기)에만 — 가격·방법 글은 사실로 먼저 나간다."""
    src = inspect.getsource(gs.feed)
    assert "_needs_first_person" in src, "모든 글을 경험 없다고 막는다"
    assert '_angle(g["keyword"]) == "review"' in src, "후기형 판정이 없다"


def test_first_person_detector():
    """C5. 1인칭 위조를 실제로 잡아낼 수 있어야 검증이 된다."""
    assert hv.first_person_claims("저희가 직접 확인했습니다")
    assert hv.first_person_claims("우리 손님이 좋다고 하셨습니다")
    assert not hv.first_person_claims("필름 등급에 따라 열차단 성능이 달라집니다")
    assert not hv.first_person_claims("일반적으로 시공 후 하루는 창문을 내리지 않습니다")
