"""규율 강제 골든 (2026-08-14).

왜 테스트로 만드나:
  규율을 문서로만 적으면 바쁠 때 안 읽는다. 실제로 2026-08-14 하루에 같은 실수를
  4회 냈다 — 이동하면서 원위치를 안 비웠다(상호칸 2개 · 글없는가게 제목만 고침 ·
  AI제목 죽은 링크 · 가입버튼 7개). 매번 다짐했고 매번 어겼다.

세 겹 중 셋째 층이다:
  ① docs/DISCIPLINE.md(방향) → ② CLAUDE.md 최상단 트리거(읽게 강제)
  → ③ 이 파일(안 읽어도 강제)

여기서 무는 것:
  · 문서가 사라지거나 링크가 끊기면 실패한다(1층·2층이 조용히 죽는 것을 막는다)
  · 규율 본문이 두 곳에 복사되면 실패한다(단일 소스 — 갈라지면 한쪽만 고친다)
  · 각 규율에 실패 이력이 붙어 있는지 본다(이유 없는 규율은 안 지켜진다)
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "DISCIPLINE.md")
CONST = os.path.join(ROOT, "CLAUDE.md")


def _doc():
    with open(DOC, encoding="utf-8") as f:
        return f.read()


def _const():
    with open(CONST, encoding="utf-8") as f:
        return f.read()


def test_discipline_doc_exists():
    """규율 문서가 사라지면 1층이 통째로 죽는다."""
    assert os.path.isfile(DOC), "docs/DISCIPLINE.md 가 없다"
    assert len(_doc()) > 1500, "규율 문서가 비었거나 너무 짧다"


def test_constitution_triggers_the_doc_at_the_top():
    """맨 위에 있어야 읽는다. 아래로 밀리면 안 읽는다."""
    c = _const()
    head = c[:900]
    assert "DISCIPLINE.md" in head, "헌법 최상단에 규율 문서 트리거가 없다"
    assert "이동 = 원위치 비우기" in head, "가장 많이 어긴 규율이 트리거에 없다"
    assert "전체 훑기" in head, "완료 전 전체 훑기가 트리거에 없다"


def test_constitution_links_but_does_not_copy_rules():
    """규율 본문은 DISCIPLINE.md 한 곳에만 산다.
    두 곳에 살면 한쪽만 고쳐지고, 그게 오늘 하루 종일 낸 사고의 모양이다."""
    c, d = _const(), _doc()
    # 문서의 '실패 이력' 표·체크리스트 같은 본문이 헌법에 복사돼 있으면 안 된다
    for frag in ("### 실패 이력", "죽은 자리 — 눌릴 것처럼 생겼는데",
                 "이게 무엇을 대체하나? 대체 대상은 지웠나?"):
        assert frag in d, f"규율 문서에 있어야 할 내용이 없다: {frag}"
        assert frag not in c, f"규율 본문이 헌법에 복사됐다(단일 소스 위반): {frag}"


def test_every_rule_carries_its_failure_history():
    """이유를 모르면 어긴다. 규율마다 '왜 생겼는지'가 붙어야 한다."""
    d = _doc()
    rules = re.findall(r"^## \d+\. .+$", d, re.M)
    assert len(rules) >= 9, f"규율이 {len(rules)}개뿐이다(9개 이상이어야 한다)"
    # 각 규율 블록에 실패 이력이 있는지
    blocks = re.split(r"^## ", d, flags=re.M)[1:]
    missing = [b.splitlines()[0].strip() for b in blocks
               if re.match(r"^\d+\.", b.strip()) and "실패 이력" not in b]
    assert not missing, f"실패 이력이 없는 규율: {missing}"


def test_the_most_broken_rule_is_documented_with_all_four_cases():
    """하루 4회 어긴 규율은 사례를 다 적어둔다 — 하나만 적으면 우연처럼 보인다."""
    d = _doc()
    i = d.find("## 1. 이동 = 원위치 비우기")
    assert i > 0, "가장 많이 어긴 규율이 1번이 아니다"
    seg = d[i:i + 1800]
    for case in ("상호 입력칸", "글 없는 가게", "AI 제목", "가입 버튼"):
        assert case in seg, f"실패 사례 누락: {case}"


def test_count_caps_are_actually_enforced_by_goldens():
    """규율 2(개수 상한)는 문서가 아니라 골든이 강제해야 한다.
    문서에만 있으면 다섯 번째 실패가 온다."""
    lc = os.path.join(ROOT, "tests", "test_landing_contracts.py")
    src = open(lc, encoding="utf-8").read()
    assert "가입 진입로가" in src, "가입 진입로 총량 상한 골든이 없다"
    assert "주 CTA" in src and "출구" in src, "결과 화면 구성(주CTA 1+출구 1) 골든이 없다"


def test_discipline_doc_is_referenced_in_docs_list():
    """참조 문서 목록에도 있어야 이어받는 사람이 찾는다."""
    c = _const()
    assert re.search(r"작업 규율.*DISCIPLINE\.md", c), "참조 문서 목록에 규율 문서가 없다"
