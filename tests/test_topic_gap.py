"""내용 단위 빈자리 골든 — 상위 10개가 안 다룬 항목으로 치고 들어간다.

2026-08-19 사장님: "이길자리는 1위부터 10위까지 글을 크롤링해서 정보성을 차별화 두는거는 어때?"

왜 필요했나:
  battle_plan은 상위글을 **4각도**(후기·가격·방법·추천)로만 갈랐다. 그래서 작전 지시가
  "가격형 각도로 진입하라"까지밖에 못 갔고, 같은 각도 안에서 또 비슷한 글이 나왔다.
  상위 글들이 '가격의 무엇'을 다뤘는지는 한 번도 안 봤다.

★ 이 판정의 위험은 **날조**다. 우리 재료에 없는 항목을 '빈자리'라고 지시하면
  모델이 그것을 지어내서 쓴다 — 헌법 정직 게이트 위반이 프롬프트에서 시작된다.
  그래서 재료가 없으면 빈자리를 제안하지 않는다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import bloganatomy as ba  # noqa: E402

_AN = {"common_phrases": [{"p": "가격 비교", "n": 5}, {"p": "시공 기간", "n": 3},
                          {"p": "관리 방법", "n": 2}]}


def _no_llm(monkeypatch, ret=""):
    from app import llm
    monkeypatch.setattr(llm, "call_task", lambda *a, **k: ret)


def test_재료가_없으면_빈자리를_지어내지_않는다(monkeypatch):
    """★ 이 파일의 존재 이유. 근거 없는 빈자리 지시 = 프롬프트가 날조를 시킨다."""
    _no_llm(monkeypatch, "무상 점검, 야간 예약")      # LLM이 답해도 부르지 않아야 한다
    r = ba.topic_gap("부산 썬팅", _AN, materials="")
    assert r["gaps"] == [], "재료 없이 빈자리를 제안했다"
    assert r["covered"], "덮인 항목은 그대로 보고해야 한다"
    assert r.get("why"), "왜 비었는지 사유가 없다(침묵 폴백)"


def test_해부_데이터가_없으면_사유를_남긴다(monkeypatch):
    _no_llm(monkeypatch, "아무거나")
    r = ba.topic_gap("측정안된말", {}, materials="[사진1] 뭔가")
    assert r["gaps"] == [] and r["covered"] == []
    assert r.get("why"), "빈 반환에 사유가 없다"


def test_이미_덮인_항목은_빈자리로_주지_않는다(monkeypatch):
    """★ LLM이 규칙을 어겨도 코드가 막아야 한다 — 덮인 것을 다시 시키면 유사문서다."""
    _no_llm(monkeypatch, "가격 비교, 시공 기간, 셀프 측정")
    r = ba.topic_gap("부산 썬팅", _AN, materials="[사진1] 측정기로 가시광선 투과율을 재는 장면")
    assert "가격 비교" not in r["gaps"] and "시공 기간" not in r["gaps"]
    assert "셀프 측정" in r["gaps"], f"진짜 빈자리를 버렸다: {r}"


def test_최대_3개까지만_준다(monkeypatch):
    """지시가 길면 글이 나열식이 된다. 소제목 하나에 들어갈 만큼만."""
    _no_llm(monkeypatch, "가,나,다,라,마,바")
    r = ba.topic_gap("부산 썬팅", _AN, materials="재료 있음")
    assert len(r["gaps"]) <= 3, f"{len(r['gaps'])}개를 지시한다"


def test_LLM이_죽어도_글쓰기를_막지_않는다(monkeypatch):
    from app import llm

    def boom(*a, **k):
        raise RuntimeError("LLM 죽음")
    monkeypatch.setattr(llm, "call_task", boom)
    r = ba.topic_gap("부산 썬팅", _AN, materials="재료 있음")
    assert r["gaps"] == [] and r.get("why"), "실패 사유 없이 조용히 비었다"


def test_작전_지시서가_실제로_이_판정을_쓴다(monkeypatch):
    """★ '만들었다'와 '그 경로로 간다'는 다르다 — 하루에 4번 같은 실수를 했다.
    battle_plan이 topic_gap을 부르지 않으면 이 파일 전체가 장식이다."""
    import inspect
    src = inspect.getsource(ba.battle_plan)
    assert "topic_gap(" in src, "작전 지시서가 내용 빈자리를 안 쓴다"
    assert "materials" in inspect.signature(ba.battle_plan).parameters, \
        "재료를 못 받으면 빈자리 판정이 항상 빈다"


def test_생성기가_재료를_넘긴다():
    """호출부가 materials를 안 주면 위 판정은 영원히 '재료 없음'으로 산다."""
    import inspect
    from app.generators import text_claude as tc
    src = inspect.getsource(tc)
    i = src.find("battle_plan(")
    assert i > 0, "생성기가 battle_plan을 안 부른다"
    assert "materials" in src[i:i + 260], "생성기가 재료를 안 넘긴다 — 빈자리 판정이 죽는다"


def test_업종어를_프롬프트에_박지_않는다():
    """헌법 업종 중립 — 항목 목록을 코드가 갖지 않는다(재료에서만 나온다)."""
    import inspect
    src = inspect.getsource(ba.topic_gap)
    for w in ("썬팅", "중고차", "매물", "차량", "필름", "시공"):
        assert w not in src.split('"""')[-1], f"판정 코드에 업종어가 박혔다: {w}"


# ── 시장 공통 용어 추출 (내용 빈자리의 원재료) ──────────────────────────
#   2026-08-19 실측 '부산 썬팅': 뽑힌 25개 중 15개가 형용사·부사였다 —
#   합리적인·쾌적한·필요한·고급스러운·만족스러운·깔끔하게·꼼꼼하게·실제로·그대로·효과적…
#   이걸 '시장이 이미 덮은 항목'으로 주면 무엇이 덮였는지 알 수 없고,
#   빈자리 판정이 노이즈 위에서 돈다. 소비자가 셋(topic_gap·queryscout·marketterms)이라
#   추출부 한 곳에서 거른다(헌법: 파서를 하나로).

def test_형용사_부사가_시장_용어로_잡히지_않는다():
    """★ 실측 그대로. 이 15개가 다시 통과하면 빈자리 판정이 도로 무의미해진다."""
    from app.services import bloganatomy as _ba
    html = ("<p>합리적인 쾌적한 필요한 고급스러운 만족스러운</p>"
            "<p>깔끔하게 꼼꼼하게 세련된 선명한 뜨거운</p>"
            "<p>실제로 그대로 효과적 아니라</p>"
            # 어간 조각 — 정규화가 어미를 떼면서 '차단하다→차단하'로 남는다(실측)
            "<p>차단하 유지하</p>")
    got = [g for g in _ba._query_phrases(html, "") if " " not in g]
    assert got == [], f"활용형이 시장 용어로 잡힌다: {got}"
    # '최상위'는 명사라 남는다 — 언어 규칙만으로 못 거른다(형태소 분석기 없음).
    # 남겨두는 쪽을 택했다: 명사를 잃는 손실이 노이즈 하나보다 크다.
    assert "최상위" in _ba._query_phrases("<p>최상위 노출</p>", "")


def test_도메인_명사는_그대로_살아남는다():
    """거르다가 진짜 용어까지 죽이면 판정 재료가 사라진다."""
    from app.services import bloganatomy as _ba
    html = ("<p>시인성 프라이버시 유리막코팅 세라믹 차단율</p>"
            "<p>적외선 자외선 장거리 주행거리 성능점검기록부</p>")
    got = [g for g in _ba._query_phrases(html, "") if " " not in g]
    for w in ("시인성", "프라이버시", "유리막코팅", "차단율", "적외선", "성능점검기록부"):
        assert w in got, f"도메인 용어를 잃었다: {w} — {got}"


# ── 부분 해부 (2026-08-19 실측) ───────────────────────────────────────
#   '부산 중고차'·'부산 썬팅업체'에서 anatomize가 6초 만에 None을 냈다.
#   상위 결과가 티스토리 등 네이버 밖 글이라 본문 파싱이 전부 실패한 것이다.
#   None이면 작전 지시서와 내용 빈자리가 **통째로 죽는다**
#   (그 글들이 "빈자리 없음 — 해부 데이터 없음"으로 나온 이유).
#   ★ 본문을 못 읽어도 검색 API가 준 것(제목 각도·발행일·계정 활력)은 안다.

def test_본문을_못_읽어도_아는_것은_남긴다(monkeypatch):
    """★ 이 골든의 존재 이유. 전부 아니면 무(無)로 두면 그 판 정보가 사라진다."""
    from app.services import bloganatomy as _ba
    from app.services import blogrank as _br
    monkeypatch.setattr(_br, "_search_blog", lambda kw, n=10: [
        {"title": "부산 중고차 후기", "link": "https://kt99.tistory.com/1", "postdate": "20240101"},
        {"title": "부산 중고차 가격", "link": "https://kt99.tistory.com/2", "postdate": "20240201"}])
    monkeypatch.setattr(_ba, "_fetch_post_html", lambda url: "")     # 본문 파싱 전부 실패
    monkeypatch.setattr(_ba, "_blog_vitals", lambda bid: None)
    got = _ba.anatomize("부산 중고차")
    assert got is not None, "아는 것이 있는데 None을 돌려줬다"
    assert got.get("partial") is True, "부분 결과임을 표시하지 않았다"
    assert got.get("age_days_median") is not None, "발행일은 본문 없이도 안다"
    assert got.get("angles"), "제목 각도는 본문 없이도 안다"
    assert got.get("common_phrases") == [], "본문을 못 읽었는데 구절이 있다(날조)"


def test_아무것도_못_얻으면_None이다(monkeypatch):
    """검색 결과 자체가 없으면 남길 것이 없다 — 빈 껍데기를 캐시하면 그게 거짓말이다."""
    from app.services import bloganatomy as _ba
    from app.services import blogrank as _br
    monkeypatch.setattr(_br, "_search_blog", lambda kw, n=10: [])
    assert _ba.anatomize("없는말") is None
