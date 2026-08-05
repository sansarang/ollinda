"""🔬 역설계 1단계 골든 — 정답지가 가짜가 되지 않게.

무는 것은 기능이 아니라 규율이다: 공개 열람만, 사람 속도, 지면 식별 정합,
파서 단일 소스, 상관≠인과, 라벨 분리, 크레딧 보호, 원본 보존.
"""
import inspect

from app.services.reverse import collector as C
from app.services.reverse import contrast as CT
from app.services.reverse import features as F
from app.services.reverse import pipeline as P
from app.services.reverse import surfaces as S
from app.services.scout import session as SS


def test_R1_공개_결과만_읽는다():
    """로그인·세션 주입·트래픽 유발은 이 작업에 없다. 계정 리스크 0이 방법론의 전제다."""
    for mod in (SS, C, P, S, F):
        src = inspect.getsource(mod)
        for banned in ("login", "signin", "set_cookie", "add_cookies", "storage_state",
                       "NID_AUT", "NID_SES", "publish", "post(", "click("):
            assert banned not in src, f"{mod.__name__}에 금지 행위: {banned}"


def test_R2_사람_속도이고_차단되면_멈춘다():
    """재시도가 차단을 확정시킨다 — 감지하면 즉시 중단하고 사유를 올린다."""
    assert SS.GAP_MIN >= 2.0, "간격이 너무 짧다"
    src = inspect.getsource(SS.load_query)
    assert "Blocked" in src and "BLOCK_SIGNS" in src, "차단 감지가 없다"
    assert "random" in inspect.getsource(SS.gap), "기계적 주기로 돈다"
    csrc = inspect.getsource(C.collect)
    assert "break" in csrc and "재시도" in csrc, "차단 후에도 계속 돈다"
    assert "retry" not in csrc.lower(), "자동 재시도가 있다"


def test_R3_지면_식별이_선행이다():
    """UI 껍데기를 검색 블록으로 오인한 사고(2026-08-05)가 정답지에 번지면 전체가 오염된다."""
    # UI 껍데기는 template-id가 없거나 분류표 밖 → 지면으로 안 잡힌다
    items = [{"tpl": "layout", "text": "추천 검색어"}, {"tpl": "header", "text": "최근 검색어"},
             {"tpl": "ugcItem", "text": "글", "blog": "b", "post": "1"}]
    by = S.classify(items)
    assert set(by) == {"ugc"}, f"UI 껍데기가 지면으로 잡힌다: {by}"
    # ★ 표지어 존재 ≠ 지면 존재(실측 반증) — 판정은 지면 식별로만 한다
    v = S.verify(items, True)
    assert v["ok"] is True, "표지어만으로 수집을 막는다"
    assert v["brief_surface"] is False and v["marker_vs_surface"], "표지어/지면 차이를 안 남긴다"
    # 모르는 template이 있으면 통과시키지 않는다 — 구조가 바뀐 것이다
    assert S.verify(items + [{"tpl": "brandNew", "text": "x"}], False)["ok"] is False
    # 수집이 실제로 이 검증을 탄다(존재가 아니라 사용)
    src = inspect.getsource(C.collect)
    assert "_sf.verify" in src and "지면 식별 실패" in src, "검증 없이 정답지를 쌓는다"


def test_R3_모르는_template은_짐작하지_않고_드러낸다():
    """네이버가 구조를 바꾸면 조용히 틀리는 대신 미분류로 드러나야 한다."""
    items = [{"tpl": "newThing", "text": "x"}, {"tpl": "ugcItem", "text": "y"}]
    assert S.classify(items) == {"ugc": [items[1]]}, "모르는 template을 지면으로 넣는다"
    assert S.unknown_templates(items) == [("newThing", 1)]


def test_R4_파서와_브라우저는_단일_소스():
    """정찰과 역설계가 각자 브라우저를 열면 규칙이 갈라지고 한쪽만 고치게 된다."""
    from app.services.scout import blocks as B
    assert "session" in inspect.getsource(B.scan), "정찰이 공통 세션을 안 쓴다"
    for mod in (C, P):
        src = inspect.getsource(mod)
        assert "chromium.launch" not in src, f"{mod.__name__}이 브라우저를 따로 연다"
    # 파싱 규칙(JS)은 surfaces 하나에만 산다
    assert "querySelectorAll" not in inspect.getsource(P), "pipeline이 파싱을 복제한다"


def test_R5_상관을_인과로_말하지_않는다():
    """표본이 적으면 유의여도 미확정이다 — 확신 없는 것을 확정처럼 쓰는 게 유일한 실패다."""
    hi = [{"tables": 3}, {"tables": 4}, {"tables": 3}]
    lo = [{"tables": 0}, {"tables": 0}, {"tables": 1}]
    assert CT.compare(hi, lo)[0]["verdict"] == "표본 부족(미확정)", "적은 표본을 인자로 채택한다"
    hi6 = hi * 2
    lo6 = lo * 2
    r = CT.compare(hi6, lo6)[0]
    assert r["verdict"] == "인자 후보" and r["p"] < CT.ALPHA
    same = CT.compare([{"x": 1}] * 6, [{"x": 1.02}] * 6)
    assert same[0]["verdict"] == "유의차 없음", "차이 없는 것을 인자라 한다"
    assert "인과" in inspect.getsource(CT)[:600], "상관/인과 구분이 문서화되지 않았다"


def test_R6_인용_라벨과_순위_라벨을_섞지_않는다():
    """'채널이 159만 번 인용됨'과 '이 글이 인용됨'은 다른 주장이다."""
    src = inspect.getsource(S)
    assert "CITED_SURFACES" in src and "aipickItem" in src
    assert S.CITED_SURFACES == ("ai_brief_channel",), "브리핑 인용 근거가 넓다"
    # articleSource는 브리핑 없는 질의에도 나온다 — 인용 근거가 아니다(실측 반증)
    assert S.SURFACE_BY_TPL["articleSource"] == "article_source", "일반 출처를 인용으로 라벨한다"
    lsrc = inspect.getsource(C.labeled_posts)
    assert "channel_cited" in lsrc, "채널 단위임을 이름으로 구분하지 않는다"
    assert "글 단위 인용이 아니다" in lsrc, "라벨 의미가 문서화되지 않았다"


def test_R7_크레딧이_없으면_LLM을_안_부른다():
    """기계 계측이 1급이고 LLM은 보조다 — 크레딧이 말라도 분해가 멈추면 안 된다."""
    src = inspect.getsource(F.enrich_with_llm)
    assert "credit_out()" in src, "크레딧을 안 본다"
    i_credit = src.index("credit_out()")
    i_call = src.index("_llm.call")
    assert i_credit < i_call, "크레딧 확인 전에 LLM을 부른다"
    # 기계 계측에는 LLM 호출이 없다 — 문구가 아니라 호출을 문다(docstring의 'LLM 0콜'은 위반이 아니다)
    body = "\n".join(ln for ln in inspect.getsource(F.measure).split("\n")
                     if ln.strip() and not ln.strip().startswith("#") and '"""' not in ln)
    for banned in ("llm.call", "_llm.", "call_task", "messages.create"):
        assert banned not in body, f"기계 계측이 LLM을 부른다: {banned}"
    m = F.measure({"title": "썬팅 가격 얼마나 할까요?", "text": "직접 해보니 30만원이었다. " * 20,
                   "h2": 3, "tables": 1, "images": 8})
    assert m["title_is_question"] and m["money"] >= 1 and m["exp_hits"] >= 1


def test_R8_원본을_보존한다():
    """분해 결과와 원문을 분리 저장 — '이 인자가 어느 글에서 나왔나'를 되짚을 수 있어야 한다."""
    assert C.RAW_PATH != P.OUT_PATH, "원문과 분해 결과가 같은 파일이다"
    assert "a" in inspect.signature(open).parameters or True
    src = inspect.getsource(C._save_raw)
    assert '"a"' in src, "원문을 덮어쓴다"
    psrc = inspect.getsource(P._save)
    assert '"a"' in psrc, "분해 결과를 덮어쓴다"
    # 역추적 근거 — 어느 글에서 나온 인자인가
    rsrc = inspect.getsource(P.run)
    for k in ("url", "blog", "post", "keyword"):
        assert f'"{k}"' in rsrc, f"역추적 키 누락: {k}"


def test_R9_생성_발행_경로를_건드리지_않는다():
    """역설계는 관측·분석 레이어다."""
    for mod in (C, P, F, CT, S):
        src = inspect.getsource(mod)
        for banned in ("save_piece", "update_piece_payload", "generate_for", "score_gate",
                       "publish"):
            assert banned not in src, f"{mod.__name__}이 본체를 건드린다: {banned}"
