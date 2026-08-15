"""질의별 독립 답변 문단 골든 (2026-08-16).

실측 근거: 남의 상위글 339개(3업종 12검색어)에서 같은 글이 검색어에 따라 다른 대목을
요약문으로 받았다 — 썬팅 83% · 중고차 100% · 이어폰 83%.
네이버는 글이 아니라 **문단**을 뽑아 노출한다. 노리는 질의마다 답하는 덩어리가
한 곳에 모여 있어야 하고, 흩어지면 뽑아갈 단위가 없다.

★ 규율 4(계측기부터 극단값으로 검증한다)를 여기서 실제로 지켰다:
  첫 구현은 소제목이 붙은 문단을 통째로 버려서 '가격을 한 문단에 모은 글'이
  0문단으로 잡혔고, 세 극단값이 전부 같은 판정을 냈다. 그 상태로 게이트에 걸었다면
  모든 글이 같은 사유로 걸렸을 것이다.
"""
from app.services import answerblock as ab

GATHERED = """## 가격
신차 썬팅 가격은 필름 등급으로 갈립니다. 중저가 필름은 30만원대, 중고가 필름은 50만원대,
프리미엄 필름은 80만원대에 시공하실 수 있습니다.

## 후기
정말 만족스러웠습니다."""

SCATTERED = """## 이야기
중저가는 30만원대입니다.

다른 문단입니다.

중고가는 50만원대예요.

또 다른 문단.

프리미엄은 80만원대고요."""

ABSENT = """## 소개
저희는 정성껏 시공합니다. 오래 하셨어요."""

PROCESS = """## 과정
먼저 차량을 검수합니다. 그다음 필름을 재단하고, 마지막으로 열성형을 합니다."""


# ── 계측기 눈금이 살아 있는가 (규율 4) ────────────────────────────────────

def test_meter_separates_four_extremes():
    """네 극단값이 네 판정으로 갈려야 한다. 하나로 뭉치면 자가 죽은 것."""
    kw = ["부산 썬팅 가격"]
    verdicts = {
        "모아둠": ab.detail(GATHERED, kw, "썬팅 가격"),
        "흩어짐": ab.detail(SCATTERED, kw, "썬팅 가격"),
        "없음": ab.detail(ABSENT, kw, "썬팅 가격"),
    }
    assert verdicts["모아둠"] == "", f"모은 글이 걸렸다: {verdicts['모아둠']}"
    assert "흩어짐" in verdicts["흩어짐"], verdicts["흩어짐"]
    assert "없음" in verdicts["없음"], verdicts["없음"]
    assert len(set(verdicts.values())) == 3, f"판정이 뭉쳤다: {verdicts}"


def test_heading_does_not_swallow_its_paragraph():
    """소제목 바로 아래 붙은 본문이 사라지면 안 된다 — 첫 구현의 실제 결함."""
    assert ab.paragraphs(GATHERED), "소제목이 붙은 문단이 통째로 버려졌다"
    assert any("30만원대" in p for p in ab.paragraphs(GATHERED))


def test_table_rows_are_not_counted_as_prose():
    """표는 그 자체로 덩어리다 — 산문 문단으로 세면 안 된다."""
    body = "## 가격\n| 항목 | 내용 |\n| 중저가 | 30만원 |\n| 프리미엄 | 80만원 |"
    assert not any("|" in p for p in ab.paragraphs(body))


# ── 의도별 판정 ──────────────────────────────────────────────────────────

def test_gathered_price_paragraph_passes():
    assert ab.ok(GATHERED, ["부산 썬팅 가격"], "썬팅 가격")


def test_scattered_price_is_caught():
    """재료는 충분한데 흩어진 경우 — 모으면 뽑힌다. '없음'과 다른 진단이어야 한다."""
    r = ab.audit(SCATTERED, ["부산 썬팅 가격"], "썬팅 가격")
    assert "가격" in r["scattered"] and "가격" not in r["missing"]


def test_process_intent_works_too():
    """가격만 되는 검사기가 아니다 — 과정·시간·비교도 같은 규칙."""
    assert ab.ok(PROCESS, ["썬팅 과정"], "썬팅 과정")


def test_unwanted_intent_is_not_demanded():
    """노리지 않는 의도까지 요구하면 모든 글이 걸린다."""
    r = ab.audit(ABSENT, ["부산 썬팅"], "부산 썬팅")
    assert r["intents"]["가격"]["wanted"] is False
    assert not r["missing"] and not r["scattered"]


# ── 업종 중립 (헌법) ─────────────────────────────────────────────────────

def test_industry_neutral_no_hardcoded_terms():
    """업종명·지명·상품명이 코드에 박히면 안 된다 — 언어 규칙만 쓴다."""
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app", "services", "answerblock.py"), encoding="utf-8").read()
    body = src.split('"""', 2)[-1]          # 독스트링(실측 사례 인용)은 제외
    for term in ("썬팅", "중고차", "네일", "이어폰", "부산", "기장", "빵집"):
        assert term not in body, f"업종·지명이 코드에 박혔다: {term}"


def test_works_across_industries():
    """서로 다른 업종·서로 다른 속성 축으로 통과해야 한다(규율 6: 같은 구조 2개는 1개와 같다)."""
    nail = ("## 시술 시간\n원컬러는 40분, 프렌치는 70분, 아트가 들어가면 120분 정도 걸립니다.")
    assert ab.ok(nail, ["네일 시술 시간"], "시술 시간"), ab.detail(nail, ["네일 시술 시간"], "시술 시간")
    earphone = ("## 비교\nA모델은 노이즈캔슬링이 강한 반면 배터리가 짧습니다. "
                "B모델은 그 대신 착용감이 좋고, 둘 다 통화 품질은 비슷합니다.")
    assert ab.ok(earphone, ["이어폰 비교"], "이어폰 비교"), ab.detail(earphone, ["이어폰 비교"], "이어폰 비교")


# ── 게이트·프롬프트 배선 ─────────────────────────────────────────────────

def test_gate_is_wired_into_self_check():
    """게이트 없는 표면 신설은 커밋 불가(헌법). 프롬프트 지시만으로는 보장이 아니다."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "services", "qualitycheck.py")
    src = open(p, encoding="utf-8").read()
    assert "answerblock" in src, "자체검사에 답변 문단 게이트가 없다"
    assert "질의별 답변 문단" in src, "검사 항목 이름이 없다"


def test_prompt_rule_is_wired_into_generator():
    """지시는 확률, 게이트는 보장 — 둘 다 있어야 한다(제목·FAQ와 같은 패턴)."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "generators", "text_claude.py")
    src = open(p, encoding="utf-8").read()
    assert "answerblock" in src and "_ab_rule" in src, "생성 프롬프트에 규칙이 안 걸렸다"


def test_prompt_rule_forbids_fabrication():
    """없는 값을 지어내 문단을 채우면 정직 게이트 위반이 된다."""
    rule = ab.prompt_rule(["부산 썬팅 가격"])
    assert "지어내" in rule and "빈칸" in rule
    assert "한 덩어리" in rule or "한 문단" in rule
