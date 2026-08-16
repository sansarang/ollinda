"""질의별 독립 답변 문단 골든 (2026-08-16).

실측 근거: 남의 상위글 339개(3업종 12검색어)에서 같은 글이 검색어에 따라 다른 대목을
요약문으로 받았다 — 썬팅 83% · 중고차 100% · 이어폰 83%.
네이버는 글이 아니라 **문단**을 뽑아 노출한다. 노리는 질의마다 답하는 덩어리가
한 곳에 모여 있어야 하고, 흩어지면 뽑아갈 단위가 없다.

★ 규율 4(계측기부터 극단값으로 검증한다)를 여기서 실제로 지켰다:
  첫 구현은 소제목이 붙은 문단을 통째로 버려서 '수치를 한 문단에 모은 글'이
  0문단으로 잡혔고, 세 극단값이 전부 같은 판정을 냈다. 그 상태로 게이트에 걸었다면
  모든 글이 같은 사유로 걸렸을 것이다.
"""
from app.services import answerblock as ab


def axis_detail(body, kws, title):
    """축(흩어짐·약속미이행)만 본다 — 두께는 별도 검사에서 다룬다."""
    return " · ".join(x for x in ab.detail(body, kws, title).split(" · ")
                      if x and not x.startswith("문단얇음")).strip()

GATHERED = """## 소요 시간
전면만 하면 40분, 측후면까지 더하면 90분, 유리막코팅까지 같이 하면 3시간 정도 걸립니다.

## 후기
정말 만족스러웠습니다."""

SCATTERED = """## 이야기
전면만 하면 40분입니다.

다른 문단입니다.

측후면까지는 90분이에요.

또 다른 문단.

코팅까지면 3시간 걸립니다."""

ABSENT = """## 소개
저희는 정성껏 시공합니다. 오래 하셨어요."""

PROCESS = """## 과정
먼저 차량을 검수합니다. 그다음 필름을 재단하고, 마지막으로 열성형을 합니다."""


# ── 계측기 눈금이 살아 있는가 (규율 4) ────────────────────────────────────

def test_meter_separates_three_states():
    """모아둠 / 흩어짐 / 재료없음이 서로 다른 판정이어야 한다."""
    kw = ["썬팅 소요 시간"]
    assert axis_detail(GATHERED, kw, "썬팅 시간") == "", "모은 글이 걸렸다"
    assert "흩어짐" in axis_detail(SCATTERED, kw, "썬팅 시간")
    # 재료가 아예 없는 경우는 '실패'가 아니라 '관찰'이다(아래 정직 게이트 테스트 참조)
    assert axis_detail(ABSENT, kw, "썬팅 시간") == ""
    assert "재료없음" in ab.note(ABSENT, kw, "썬팅 시간")


def test_gate_never_forces_fabrication():
    """★ seo.target_keywords는 모든 가게에 '{업종} 가격'을 항상 붙인다.
    노린 축을 그대로 요구하면 가격을 공개 안 하는 가게는 **가격을 지어내야** 통과한다.
    게이트가 날조를 강요하면 안 된다(정직 게이트)."""
    kw = ["부산 썬팅", "썬팅 추천", "썬팅 소요 시간"]   # 실제 target_keywords가 만드는 모양
    assert ab.ok(ABSENT, kw, "부산 썬팅"), \
        f"재료 없는 축 때문에 글이 막혔다 — 날조 압력: {ab.detail(ABSENT, kw, '부산 썬팅')}"
    r = ab.audit(ABSENT, kw, "부산 썬팅")
    assert "시간" in r["unfilled"] and "시간" not in r["missing"]


def test_promise_in_heading_must_be_kept():
    """소제목으로 어떤 축을 약속했으면 덩어리가 있어야 한다 — 약속 위반은 실패."""
    body = "## 소요 시간 안내\n저희는 정성껏 시공합니다. 문의 주세요."
    r = ab.audit(body, ["부산 썬팅"], "부산 썬팅")
    assert "시간" in r["missing"], r
    assert "약속미이행" in ab.detail(body, ["부산 썬팅"], "부산 썬팅")


def test_heading_does_not_swallow_its_paragraph():
    """소제목 바로 아래 붙은 본문이 사라지면 안 된다 — 첫 구현의 실제 결함."""
    assert ab.paragraphs(GATHERED), "소제목이 붙은 문단이 통째로 버려졌다"
    assert any("40분" in p for p in ab.paragraphs(GATHERED))


def test_table_rows_are_not_counted_as_prose():
    """표는 그 자체로 덩어리다 — 산문 문단으로 세면 안 된다."""
    body = "## 가격\n| 항목 | 내용 |\n| 중저가 | 30만원 |\n| 프리미엄 | 80만원 |"
    assert not any("|" in p for p in ab.paragraphs(body))


# ── 의도별 판정 ──────────────────────────────────────────────────────────

def test_gathered_paragraph_passes():
    assert ab.ok(GATHERED, ["썬팅 소요 시간"], "썬팅 시간")


def test_scattered_material_is_caught():
    """재료는 충분한데 흩어진 경우 — 모으면 뽑힌다. '없음'과 다른 진단이어야 한다."""
    r = ab.audit(SCATTERED, ["썬팅 소요 시간"], "썬팅 시간")
    assert "시간" in r["scattered"] and "시간" not in r["missing"]


def test_process_intent_works_too():
    """가격만 되는 검사기가 아니다 — 과정·시간·비교도 같은 규칙."""
    assert ab.ok(PROCESS, ["썬팅 과정"], "썬팅 과정")


def test_unwanted_intent_is_not_demanded():
    """노리지 않는 의도까지 요구하면 모든 글이 걸린다."""
    r = ab.audit(ABSENT, ["부산 썬팅"], "부산 썬팅")
    assert r["intents"]["가격"]["aimed"] is False
    assert not r["missing"] and not r["scattered"]


# ── 핵심 1개 + 속성 2~3개 ────────────────────────────────────────────────

def test_plan_splits_core_and_attributes():
    """실측: 검색어마다 판이 분리(겹침 0.8~4.2%) → 핵심은 하나, 속성만 함께 딴다."""
    kws = ["부산 동구 썬팅", "썬팅 소요 시간", "썬팅 시공 과정", "썬팅 추천"]
    p = ab.plan("부산 동구 썬팅", kws)
    assert p["core"] == "부산 동구 썬팅"
    got = {a["intent"] for a in p["attrs"]}
    assert "시간" in got and "과정" in got
    assert all(a["query"] != p["core"] for a in p["attrs"]), "핵심이 속성으로도 잡혔다"


def test_plan_caps_attribute_count():
    """다중 타깃은 환상 — 상한이 없으면 문단이 얕아져 전부 안 뽑힌다."""
    kws = ["핵심", "가격 얼마", "시간 얼마나 걸려", "과정 방법", "비교 차이"]
    assert len(ab.plan("핵심", kws)["attrs"]) <= ab.MAX_ATTRS <= 3


def test_plan_does_not_invent_axes():
    """후보에 없는 축을 지어내면 안 된다 — 없는 질의를 노리게 된다."""
    p = ab.plan("부산 썬팅", ["부산 썬팅"])
    assert p["attrs"] == []


def test_prompt_states_core_and_attribute_roles():
    """평평한 목록만 주면 모델이 무엇을 글 전체로 답할지 모른다."""
    rule = ab.prompt_rule(["부산 동구 썬팅", "썬팅 소요 시간"], core="부산 동구 썬팅")
    assert "핵심 질의" in rule and "속성 질의" in rule
    assert "전용 문단" in rule


def test_generator_records_query_plan():
    """발행 후 '노린 질의 vs 실제로 잡힌 질의'를 대조하려면 계획이 남아야 한다."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "generators", "text_claude.py")
    src = open(p, encoding="utf-8").read()
    assert "query_plan" in src and "_ab_plan" in src


# ── 업종 중립 (헌법) ─────────────────────────────────────────────────────

def test_industry_neutral_no_hardcoded_terms():
    """업종명·지명·상품명이 **판정 로직**에 박히면 안 된다 — 언어 규칙만 쓴다.

    독스트링의 실측 사례 인용('썬팅 83%')은 근거 기록이라 허용한다.
    검사 대상은 실제로 실행되는 코드다 → AST로 독스트링만 걷어내고 본다.
    """
    import ast
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "services", "answerblock.py")
    src = open(p, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):            # 독스트링 노드를 빈 문자열로 치환
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = ""
    code = ast.unparse(tree)
    for term in ("썬팅", "중고차", "네일", "이어폰", "부산", "기장", "빵집"):
        assert term not in code, f"업종·지명이 판정 로직에 박혔다: {term}"


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
    """없는 값을 지어내 문단을 채우면 정직 게이트 위반이 된다.

    ★ 뜻으로 검사한다 — 문구를 다듬을 때마다 깨지는 골든은 사람을 문구에 묶는다
      (2026-08-14에 약속 골든 6개가 그렇게 깨졌다).
    """
    rule = ab.prompt_rule(["부산 동구 썬팅", "썬팅 소요 시간"], core="부산 동구 썬팅")
    assert "지어내" in rule and "빈칸" in rule, "날조 금지 문장이 없다"
    assert "문단" in rule, "문단 단위 원칙이 안 들어갔다"


# ── 가격 축 제외 (2026-08-16 사장님 지시) ────────────────────────────────

def test_price_axis_is_excluded_everywhere():
    """가격은 노리지도 요구하지도 않는다.
    실제 단가를 받은 적이 없어 쓸 수 없고, 소제목만 걸고 금액을 못 써서 2회 연속 걸렸다.
    답할 수 없는 축을 노리면 게이트가 날조를 압박하게 된다."""
    assert "가격" in ab.EXCLUDED_INTENTS
    kws = ["부산 동구 썬팅", "부산 동구 썬팅 가격"]
    assert all(a["intent"] != "가격" for a in ab.plan("부산 동구 썬팅", kws)["attrs"])
    body = "## 가격 안내\n금액은 실물 보고 안내드려요."
    r = ab.audit(body, kws, "부산 동구 썬팅 가격")
    assert "가격" not in r["missing"] and "가격" not in r["scattered"]
    assert axis_detail(body, kws, "부산 동구 썬팅 가격") == ""


def test_generator_forbids_writing_prices():
    """본문·표·소제목·FAQ 어디에도 금액을 쓰지 말라는 지시가 있어야 한다."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "generators", "text_claude.py")
    src = open(p, encoding="utf-8").read()
    assert "[가격 — 쓰지 마라]" in src


def test_format_is_fewer_sections_thicker_paragraphs():
    """형식 개편(2026-08-16 ②): 섹션을 줄이고 문단을 두껍게.
    근거 — 상위글 소제목 중간값 2개(우리는 6~9개), 표 0%(우리는 필수)."""
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app", "generators", "text_claude.py")
    src = open(p, encoding="utf-8").read()
    assert "소제목 3~5개" not in src, "옛 형식(소제목 3~5개 강제)이 남아 있다"
    assert "소제목은 **2~3개만**" in src, "줄인 소제목 규칙이 없다"
    assert "두꺼운 답변 문단" in src, "문단을 두껍게 쓰라는 규칙이 없다"
    assert "표는 **필수가 아니다.**" in src, "표 필수 해제가 안 됐다"


# ── 문단 두께 (2026-08-16) ───────────────────────────────────────────────

def test_thin_paragraphs_are_caught():
    """실측: 우리 글 문단 262개 중 180자 넘는 것이 0개(중간값 70·최장 164)였다.
    축 판정만으로는 '그냥 짧아진 글'을 못 잡는다 — 두께는 따로 재야 한다."""
    thin = "## 소개\n짧은 문단.\n\n또 짧은 문단.\n\n계속 짧다."
    assert not ab.thickness(thin)["ok"]
    assert "문단얇음" in ab.detail(thin, ["부산 썬팅"], "부산 썬팅")


def test_thick_paragraphs_pass():
    para = "가" * ab.MIN_THICK_CHARS
    body = f"## 하나\n{para}\n\n## 둘\n{para}"
    t = ab.thickness(body)
    assert t["ok"] and t["n_thick"] >= ab.MIN_THICK_PARAS


def test_query_coverage_measures_the_real_thing():
    """★ 핵심 — 축 신호가 아니라 '노린 그 질의에 답하는 문단이 있는가'를 잰다."""
    plan = {"core": "부산 동구 썬팅업체", "attrs": [{"intent": "과정", "query": "썬팅 시공 과정"}]}
    filler = "구체적인 설명을 이어서 씁니다. " * 12          # 180자 넘김
    good = f"## 안내\n부산 동구 썬팅업체 고르실 때 보실 것을 정리했습니다. {filler}"
    cov = {c["query"]: c for c in ab.query_coverage(good, plan)}
    assert cov["부산 동구 썬팅업체"]["covered"], cov
    assert not cov["썬팅 시공 과정"]["covered"], "글에 없는 질의가 커버로 잡혔다"


def test_query_coverage_needs_thickness_not_just_mention():
    """질의어가 한 줄 스쳐 지나간 것은 '답'이 아니다."""
    plan = {"core": "부산 동구 썬팅업체", "attrs": []}
    body = "## 안내\n부산 동구 썬팅업체입니다."
    assert not ab.query_coverage(body, plan)[0]["covered"]


# ── 노릴 축 확보 (2026-08-16) ────────────────────────────────────────────

def test_candidate_keywords_supply_time_and_process_axes():
    """가격을 뺐더니 노릴 속성 축이 '비교' 하나로 줄었다(실측).
    _INTENTS[:4]만 지역 변형과 결합되는데 그 앞자리를 가격·비용이 차지하고 있었고,
    그 둘은 EXCLUDE_PRICE_KEYWORDS로 걸러져 실제로는 추천·후기만 남았다."""
    from app import seo
    kws = seo.target_keywords("썬팅", "부산 동구", "신차 시공", limit=12)
    joined = " ".join(kws)
    assert "과정" in joined, "과정 축 후보가 없다"
    assert "시간" in joined, "시간 축 후보가 없다"
    assert not [k for k in kws if any(x in k for x in ("가격", "비용", "견적", "시세"))], \
        "가격 의도 키워드가 후보에 남았다"


def test_plan_picks_up_the_new_axes():
    """후보만 늘고 계획에 안 잡히면 의미가 없다."""
    from app import seo
    kws = seo.target_keywords("썬팅", "부산 동구", "신차 시공", limit=12)
    got = {a["intent"] for a in ab.plan(kws[0], kws)["attrs"]}
    assert {"시간", "과정"} <= got, f"새 축이 계획에 안 잡혔다: {got}"


def test_price_axis_never_returns_via_candidates():
    """축 제외와 후보 배제가 따로 놀면 한쪽만 고쳐진다."""
    from app import seo
    kws = seo.target_keywords("네일", "서울 강남", "젤네일", limit=12)
    assert all(a["intent"] != "가격" for a in ab.plan(kws[0], kws)["attrs"])
