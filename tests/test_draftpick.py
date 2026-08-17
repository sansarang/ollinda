"""초안 선별 골든 (2026-08-17 — UFO ②).

무엇이 가능해졌나: 본문 원가가 $0.54 → $0.003이 되면서 **버리는 게 공짜**가 됐다.
대행사는 편당 3~5만원이라 3편 써서 2편 버리는 걸 못 한다. 우리는 12원이면 된다.

여기서 막는 재발:
  ① 커버리지 빈 글이 총점으로 이기는 것 — 네이버는 문단을 뽑아 노출한다.
     노린 질의에 답하는 문단이 없으면 총점이 높아도 그 검색어에서 안 뜬다.
  ② 3편을 다 발행하는 것 — 저품질 양산은 블로그를 죽인다(헌법 금지선). 골라서 1편만.
  ③ 판정을 LLM에게 맡기는 것 — 그럴듯한 이유를 지어낸다. 잴 수 있는 것만으로 판단한다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import answerblock as ab
from app.services import draftpick as dp

PLAN = ab.plan("썬팅업체", ["부산 동구 썬팅업체 추천"])


def _para(n):
    return "가" * n


def test_커버리지를_채운_초안이_총점보다_우선한다():
    """★ 핵심 — 총점이 높아도 노린 질의에 답이 없으면 그 검색어에서 안 뜬다."""
    covered = {"angle": "질의형",
               "body": ("썬팅업체를 고를 때 가장 헷갈리는 게 어느 창에 어떤 등급을 넣느냐입니다. "
                        + _para(200) + "\n\n"
                        "부산 동구 썬팅업체 추천을 찾으신다면 창별 등급을 먼저 물어보세요. "
                        + _para(200))}
    fat = {"angle": "두껍기만", "body": (_para(400) + "\n\n" + _para(400) + "\n\n" + _para(400))}
    res = dp.pick([fat, covered], PLAN)
    assert res["ok"]
    assert res["picked"]["angle"] == "질의형", "커버리지 빈 글이 총점으로 이겼다"
    assert res["picked"]["score"]["cover_ok"]


def test_커버를_아무도_못_채우면_총점으로_고르되_사유를_밝힌다():
    a = {"angle": "A", "body": _para(300)}
    b = {"angle": "B", "body": _para(100)}
    res = dp.pick([a, b], PLAN)
    assert res["ok"] and res["picked"]["angle"] == "A"
    assert "다 덮은 초안이 없어" in res["why"], "커버 미달을 숨겼다"


def test_사진이_뭉치면_점수가_깎인다():
    spread = {"angle": "분산", "body": f"{_para(120)}\n\n[사진1]\n\n{_para(120)}\n\n[사진2]"}
    bunch = {"angle": "뭉침", "body": f"{_para(120)}\n\n[사진1][사진2]\n\n{_para(120)}"}
    s1 = dp.score(spread["body"], PLAN)["parts"]["사진분산"]
    s2 = dp.score(bunch["body"], PLAN)["parts"]["사진분산"]
    assert s1 > s2, "사진 뭉침이 점수에 반영되지 않는다"


def test_리듬이_없으면_점수가_깎인다():
    """전부 같은 길이면 기계 티다(실측: 발행글 편차 106, 추론 끈 초안 55)."""
    flat = "\n\n".join([_para(150)] * 5)
    varied = "\n\n".join([_para(40), _para(300), _para(90), _para(250), _para(60)])
    assert dp.score(varied, PLAN)["parts"]["리듬"] > dp.score(flat, PLAN)["parts"]["리듬"]


def test_빈_초안은_채점에서_빠진다():
    res = dp.pick([{"angle": "빈", "body": "   "}, {"angle": "정상", "body": _para(200)}], PLAN)
    assert len(res["all"]) == 1 and res["picked"]["angle"] == "정상"


def test_초안이_없으면_실패를_명시한다():
    res = dp.pick([], PLAN)
    assert res["ok"] is False and res["picked"] is None, "빈 입력에 아무거나 골랐다"


def test_버린_초안도_기록에_남는다():
    """왜 그걸 골랐는지 나중에 대조할 수 있어야 한다."""
    res = dp.pick([{"angle": "A", "body": _para(300)}, {"angle": "B", "body": _para(80)}], PLAN)
    assert res["dropped"], "버린 초안 기록이 없다"
    assert "angle" in res["dropped"][0] and "score" in res["dropped"][0]


def test_요약문에_주방용어가_없다():
    res = dp.pick([{"angle": "A", "body": _para(300)}, {"angle": "B", "body": _para(200)}], PLAN)
    line = dp.summary_line(res)
    for w in ("커버", "점수", "총점", "질의", "키워드"):
        assert w not in line, f"주방 용어가 샜다: {w} / {line}"


def test_한_편뿐이면_고르는_말을_하지_않는다():
    """1편인데 '골랐다'고 하면 그게 과장이다."""
    res = dp.pick([{"angle": "A", "body": _para(300)}], PLAN)
    assert dp.summary_line(res) == ""
