"""문단 단위 채점 골든 (2026-08-16 사장님 지적).

사장님: "점수 로직도 문단으로 채점해야 하는 거 아니야?"

맞았다. 기존 점수는 전부 **글 전체 세기**라 흩어져 있어도 개수만 채우면 통과했다.
  실물 ①: 투싼 글은 '2,990만원'이 다섯 문단에 하나씩 흩어져 '구체 수치 충분'으로 통과했지만
          한 문단에 모인 게 없어 네이버가 뽑아갈 단위는 0이었다.
  실물 ②: 90점 글이 두꺼운 문단 0개(최장 111자), 66점 글이 3개(최장 308자)로 **뒤집혔다.**

★ 발행 게이트와 분리한다.
  실측: 새 점수로 기존 12건을 다시 채점하니 80점 이상이 **0건**이었다
        (분포 5·29·37·37·38·42·49·49·49·58·72·74, 중간 46).
  기준선을 실측으로 다시 잡기 전에 봉인하면 모든 글이 막힌다 — 표시만 한다.
"""
from app import seo

_THICK = "가" * 200


def _audit(body, plan=None):
    pl = {"body": body, "title": "제목", "image_paths": ["a.jpg"] * 5}
    if plan:
        pl["query_plan"] = plan
    return seo.quality_audit("naver", "blog", pl)


def test_paragraph_score_is_separate_from_the_publish_gate():
    """게이트(score)는 그대로 두고 문단 점수(score_para)만 따로 낸다."""
    a = _audit("## 소개\n짧다.\n\n또 짧다.")
    assert "score" in a and "score_para" in a and "para_penalty" in a
    assert a["score_para"] <= a["score"], "문단 감점이 반영되지 않았다"
    assert a["para_penalty"] > 0, "얇은 글인데 문단 감점이 0이다"


def test_gate_score_unchanged_by_paragraph_penalty():
    """봉인은 기존 점수만 본다 — 안 그러면 기존 12건이 전부 막힌다(실측)."""
    thin = _audit("## 소개\n짧다.\n\n또 짧다.")
    assert thin["score"] > thin["score_para"], "두 점수가 같으면 분리가 안 된 것"


def test_scattered_numbers_are_now_caught():
    """흩어진 수치는 '충분'이 아니다 — 한 문단에 모여야 뽑아갈 단위가 된다."""
    scattered = "## 안내\n30만원입니다.\n\n다른 말.\n\n50만원이에요.\n\n또 다른 말.\n\n80만원이고요."
    gathered = f"## 안내\n중저가 30만원대, 중고가 50만원대, 프리미엄 80만원대입니다. {_THICK}"
    ws = " ".join(_audit(scattered)["warnings"])
    assert "수치가 한 문단에 모인 곳이 없다" in ws, "흩어진 수치를 못 잡는다"
    assert "수치가 한 문단에 모인 곳이 없다" not in " ".join(_audit(gathered)["warnings"])


def test_thin_paragraphs_are_caught():
    ws = " ".join(_audit("## 소개\n한 줄.\n\n또 한 줄.")["warnings"])
    assert "문단이 얇다" in ws


def test_uncovered_target_query_is_penalised():
    """노린 질의에 답 문단이 없으면 감점 — 커버 0인데 통과하던 실물이 있었다."""
    plan = {"core": "부산 동구 썬팅업체", "attrs": []}
    a = _audit(f"## 안내\n전혀 다른 이야기입니다. {_THICK}", plan)
    assert any("노린 질의에 답 문단 없음" in w for w in a["warnings"])
    b = _audit(f"## 안내\n부산 동구 썬팅업체 고르실 때 보실 것을 정리했습니다. {_THICK}", plan)
    assert not any("노린 질의에 답 문단 없음" in w for w in b["warnings"])


def test_scoring_failure_never_blocks_generation():
    """채점기가 죽어도 글 생성은 계속돼야 한다."""
    a = seo.quality_audit("naver", "blog", {"body": "", "title": ""})
    assert isinstance(a.get("score"), int)
