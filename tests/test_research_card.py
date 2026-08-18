"""발행글 조사 카드 골든 (2026-08-18 사장님 지시로 신설).

  "가게를 누르고 발행글을 눌렀을때 조사한 항목들이 그래프로 나와야 한다."

이 카드는 **실측만** 싣는다. payload에는 스스로 '추정'이라 적어둔 값(`reach`)이
같이 들어 있는데, 그걸 실측 그래프 옆에 나란히 놓으면 전체가 추정이 된다.
정직 게이트는 산출물만이 아니라 측정에도 적용된다 — 그래서 골든으로 막는다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import main


class _P:
    """실제 프로덕션 payload에서 뽑은 모양(루마썬팅 8/17 글)."""
    id = "piece-test"
    tenant_id = "t-test"
    payload = {
        "target_kw": "부산 동구 썬팅업체",
        "ranking_audit": {"score": 82, "grade": "양호"},
        "geo_audit": {"score": 75},
        "term_coverage": {"n": 10, "hit": 5, "pct": 50},
        "photo_placement": {"rows": [{"n": 1, "ok": True}, {"n": 2, "ok": True},
                                     {"n": 3, "ok": False}, {"n": 4, "ok": True}]},
        "battle_plan": {"kw": "부산 동구 썬팅업체", "docs": 1057, "age_median": 2304},
        "win_score": {"score": 75},
        "photo_capped": {"uploaded": 25, "in_body": 17},
        "body_route": {"provider": "upstage", "model": "solar-pro4"},
        # ↓ 이건 추정치다. 카드에 실려서는 안 된다.
        "reach": {"low": 81, "high": 340, "label": "81~340", "note": "추정"},
    }


class _T:
    id = "t-test"
    name = "테스트 가게"


def test_추정치는_카드에_실리지_않는다():
    """★ payload의 reach는 스스로 '추정'이라 적어둔 값이다.
    실측 그래프 옆에 놓이면 보는 사람은 둘 다 실측으로 읽는다."""
    html = main._research_card(_T(), _P())
    for banned in ("81~340", "월 검색유입", "추정"):
        assert banned not in html, f"추정치가 카드에 실렸다: {banned}"


def test_조사한_실측값들이_보인다():
    html = main._research_card(_T(), _P())
    assert "82" in html, "상위노출 점검 점수가 없다"
    assert "1,057" in html, "경쟁 글 수가 없다"
    assert "25장 중 17장" in html, "사진 사용 수가 없다"
    assert "solar-pro4" in html, "어느 모델이 썼는지가 없다"


def test_순위가_없으면_사유를_적는다():
    """빈칸을 그냥 두면 '아직 안 됐다'인지 '고장'인지 모른다(침묵 폴백 금지)."""
    html = main._research_card(_T(), _P())
    assert ("아직" in html or "위" in html), "순위 자리에 아무 말도 없다"


def test_노리는_검색어가_없으면_그렇게_말한다():
    p = _P()
    p.payload = dict(p.payload)
    p.payload.pop("target_kw")
    html = main._research_card(_T(), p)
    assert "검색어가 지정" in html


def test_조사_기록이_전혀_없으면_카드를_그리지_않는다():
    """빈 카드를 띄우는 것은 '조사했는데 결과가 없다'로 읽힌다."""
    class _Empty:
        id = "x"
        tenant_id = "t-test"
        payload = {}
    assert main._research_card(_T(), _Empty()) == ""
