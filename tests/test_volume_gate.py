"""검색량 관문 골든 — 아무도 안 찾는 키워드로 글을 쓰지 않는다.

2026-08-19 실사고:
  루마썬팅(biz_type=local)의 주력 키워드가 '부산 동구 썬팅업체'였고 **월 20회**였다.
  12편을 그 키워드로 썼다. 1위를 해도 하루 0.7명이라 손님이 오지 않는다.

  그 검색 결과 상위 8개에는 썬팅 글이 하나도 없었다 —
  '평택시 지역화폐', '국민내일배움카드', '2008년 자동차 홈페이지 모음', 스팸 글.
  네이버가 그 쿼리에 블로그를 제대로 안 뿌린다는 뜻이고,
  우리가 1위였던 것은 잘해서가 아니라 **아무도 없어서**였다.

원인은 한 줄이었다:
    if biz not in ("seller", "hybrid"):
        return cands[0]          # ← 매장은 검색량 검증 없이 첫 후보 그대로
  함수 설명에는 '③ 검색량 검증(월 100회+)'이 있었는데
  그 코드가 **셀러 분기 안에만** 있었다. 실계정 둘 다 local이라 한 번도 안 거쳤다.

헌법 금지선: '검색량 없는 키워드 욱여넣기'.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import seo  # noqa: E402


class _FakeSA:
    """검색량 API 대역 — 호출 없이 판정만 검증한다."""
    def __init__(self, table):
        self.table = table

    def configured(self):
        return True

    def keyword_volumes(self, kws, limit=80):
        return [{"keyword": k, "total": self.table.get(k)} for k in kws
                if k in self.table]


def _with_sa(monkeypatch, table):
    import app.services.searchad as sa
    fake = _FakeSA(table)
    monkeypatch.setattr(sa, "configured", fake.configured)
    monkeypatch.setattr(sa, "keyword_volumes", fake.keyword_volumes)


def test_월_100회_미만은_고르지_않는다(monkeypatch):
    """★ 이 파일의 존재 이유. 월 20회짜리가 12편의 주력 키워드였다."""
    _with_sa(monkeypatch, {"부산 동구 썬팅업체": 20, "부산 썬팅": 670})
    got = seo._volume_first(["부산 동구 썬팅업체", "부산 썬팅"])
    assert got == "부산 썬팅", f"미달 키워드를 골랐다: {got}"


def test_매장도_같은_관문을_거친다(monkeypatch):
    """★ 실사고의 직접 원인 — 셀러만 검증하고 매장은 그냥 통과시켰다."""
    _with_sa(monkeypatch, {"부산 동구 썬팅업체": 20, "부산 썬팅": 670})
    got = seo.select_target_keyword(
        ["부산 동구 썬팅업체", "부산 썬팅"], biz_type="local",
        region="부산광역시 동구", industry="썬팅")
    assert got != "부산 동구 썬팅업체", "매장이 검색량 관문을 건너뛴다(사고 재발)"


def test_셀러도_같은_함수를_쓴다():
    """같은 판정이 두 곳에 살면 한쪽만 고쳐진다(헌법: 파서를 하나로)."""
    import inspect
    src = inspect.getsource(seo.select_target_keyword)
    assert src.count("_volume_first") >= 2, "매장·셀러가 같은 관문을 안 쓴다"
    assert "v >= 100" not in src, "옛 검증 코드가 남아 두 벌이 됐다"


def test_측정_안_된_말은_통과시킨다(monkeypatch):
    """검색광고 API가 못 재는 말도 있다. 임의 숫자로 채우면 그게 날조다(정직 게이트)."""
    _with_sa(monkeypatch, {})           # 아무것도 못 잼
    assert seo._volume_first(["새로운 말"]) == "새로운 말"


def test_0회는_버린다(monkeypatch):
    """측정됐는데 0이면 통과시키면 안 된다 — '무측정 통과'와 구분해야 한다."""
    _with_sa(monkeypatch, {"없는말": 0, "있는말": 500})
    assert seo._volume_first(["없는말", "있는말"]) == "있는말"


def test_전부_미달이면_빈값을_준다(monkeypatch):
    """부르는 쪽이 자기 폴백을 쓰게 한다 — 미달 키워드를 억지로 고르지 않는다."""
    _with_sa(monkeypatch, {"a": 10, "b": 20})
    assert seo._volume_first(["a", "b"]) == ""


def test_조회_실패가_생성을_막지_않는다(monkeypatch):
    """API가 죽어도 글은 나가야 한다 — 다만 첫 후보로 진행한다."""
    import app.services.searchad as sa

    def boom(*a, **k):
        raise RuntimeError("API 죽음")
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes", boom)
    assert seo._volume_first(["아무거나"]) == "아무거나"


def test_기준값이_임의로_낮아지지_않는다():
    """값을 리터럴로 박는다 — 상수를 참조하면 상수가 바뀔 때 테스트가 따라가 아무것도 못 잡는다."""
    assert seo.MIN_MONTHLY_VOLUME == 100, "월 100회 = 하루 3~4명. 이 밑은 1위여도 의미가 없다"


# ── 이길 수 있는 자리 판정 (2026-08-19 사장님 지시) ─────────────────────
#   "이길 수 있는 자리에 글을 써야 1위를 뛰어넘고 1위를 지속할 수 있다"
#
#   실측(루마썬팅):
#     부산 썬팅      670회 · 26만건  · 494일  → 수요 있고 상위글 낡음 = 최선
#     썬팅 가격    4,280회 · 86만건  ·  37일  → 수요 크지만 새 글이 계속 들어옴
#     차량 썬팅    1,770회 · 226만건 ·  51일  → 레드오션
#     부산 동구 썬팅   30회 · 3,489건 · 2076일 → 기회지수 1위인데 수요가 없다

def test_수요_없는_자리는_기회지수가_1등이어도_버린다():
    """★ 지금까지의 함정. 문서 수만 보면 아무도 안 찾는 말이 최고 점수를 받는다.
    '부산 동구 썬팅'은 기회지수 0.0086로 전 후보 중 1등인데 월 30회다."""
    s = seo.slot_score("부산 동구 썬팅", 30, 3489, 2076)
    assert s["ok"] is False, "수요 없는 자리를 통과시켰다"
    assert s["opp"] > 0.008, "이 키워드의 기회지수는 실제로 1등이다(그래서 위험하다)"


def test_지킬_수_있는_자리를_더_높게_본다():
    """★ 이 판정의 핵심. 올라가는 것보다 **지키는 것**이 어렵다.

    처음엔 낡은 자리에 ×1.6만 줬더니 '썬팅 가격'(수요 4,280회)이 1등으로 뽑혔다.
    상위글이 37일이면 새 글이 계속 밀고 들어와 1위를 해도 지키지 못한다.
    """
    stale = seo.slot_score("부산 썬팅", 670, 264310, 494)      # 상위글 낡음
    fresh = seo.slot_score("썬팅 가격", 4280, 857585, 37)       # 상위글 최근
    assert stale["rank"] > fresh["rank"], (
        f"수요가 6배 큰 자리를 골랐다 — 지키지 못한다 "
        f"({stale['rank']:.2f} vs {fresh['rank']:.2f})")
    assert stale["stale"] is True and fresh.get("stale") is False


def test_거대_키워드가_독식하지_않는다():
    """문서 226만 건짜리는 신생 블로그가 못 뚫는다. 수요만 크다고 뽑히면 안 된다."""
    huge = seo.slot_score("차량 썬팅", 1770, 2263184, 51)
    good = seo.slot_score("부산 썬팅", 670, 264310, 494)
    assert good["rank"] > huge["rank"], "레드오션을 골랐다"


def test_지속성_기준이_임의로_바뀌지_않는다():
    """값을 리터럴로 박는다 — 상수 참조면 상수가 바뀔 때 테스트가 따라간다."""
    assert seo.STALE_TOP_DAYS == 180
    assert seo.slot_score("x", 500, 1000, 179).get("stale") is False
    assert seo.slot_score("x", 500, 1000, 180).get("stale") is True


def test_세_축을_전부_본다():
    """수요·경쟁·지속 중 하나라도 빠지면 지금까지의 실수가 반복된다."""
    s = seo.slot_score("테스트", 500, 100000, 400)
    for k in ("vol", "docs", "opp", "age", "stale", "rank", "why"):
        assert k in s, f"판정 근거 {k}가 없다 — 왜 골랐는지 설명할 수 없다"


# ── 진짜 경로 (2026-08-19 두 번째 실측) ────────────────────────────────
#   위 테스트들을 통과시키고도 루마썬팅 글은 여전히 '부산 동구 썬팅업체'로 나왔다.
#   생성기는 select_target_keyword를 직접 부르지 않는다 — resolve_target_keyword가
#   단일 관문이고, 그 안에서 select_target_keyword 호출이
#       if content_type != "info" and _biz in ("seller","hybrid"):
#   블록 **안**에 있었다. 실계정 둘 다 local이라 검색량 관문까지 오지도 못했다.
#
#   ★ 교훈: 관문 안쪽만 고치면 관문 앞의 분기가 그대로 통과시킨다.
#     '고쳤다'의 증거는 함수 단위 테스트가 아니라 **부르는 쪽 경로**여야 한다.

def _stub_resolve(monkeypatch, cands, headline):
    monkeypatch.setattr(seo, "target_keywords", lambda *a, **k: list(cands))
    monkeypatch.setattr(seo, "keyword_plan", lambda *a, **k: {"headline": headline})
    monkeypatch.setattr(seo, "region_conflict", lambda *a, **k: False)
    monkeypatch.setattr(seo, "keyword_intent_ok", lambda *a, **k: True)
    monkeypatch.setattr(seo, "searcher_term", lambda s: s)


def test_매장이_실제_경로에서도_검색량_관문을_거친다(monkeypatch):
    """★ 이 파일에서 가장 중요한 테스트. 함수는 고쳤는데 경로가 안 지나갔다."""
    _with_sa(monkeypatch, {"부산 동구 썬팅업체": 20, "부산 썬팅": 670})
    _stub_resolve(monkeypatch, ["부산 동구 썬팅업체", "부산 썬팅"], "부산 동구 썬팅업체")
    kw0, _ = seo.resolve_target_keyword("썬팅", "부산광역시 동구", "", biz="local")
    assert kw0 != "부산 동구 썬팅업체", "매장이 관문을 건너뛴다(실사고 재발)"


def test_매장도_검색량을_실제로_조회한다(monkeypatch):
    """조회 자체가 안 일어나면 판정이 없는 것이다 — 결과값만 보면 우연히 맞을 수 있다."""
    seen = []
    import app.services.searchad as sa
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes",
                        lambda kws, limit=80: (seen.extend(kws),
                                               [{"keyword": k, "total": 500} for k in kws])[1])
    _stub_resolve(monkeypatch, ["가 나", "다 라"], "가 나")
    seo.resolve_target_keyword("업종", "지역", "", biz="local")
    assert seen, "매장 경로에서 검색량 API를 한 번도 안 부른다"


def test_정보성_글은_그대로_둔다(monkeypatch):
    """content_type='info'(트랙B)는 원래 이 관문을 안 쓴다 — 범위를 넓히지 않는다."""
    _with_sa(monkeypatch, {"가 나": 10})
    _stub_resolve(monkeypatch, ["가 나"], "가 나")
    kw0, _ = seo.resolve_target_keyword("업종", "지역", "", biz="local", content_type="info")
    assert kw0 == "가 나"


def test_매장은_전국_키워드로_새지_않는다(monkeypatch):
    """★ 관문을 열자마자 나온 부작용(2026-08-19).

    '썬팅 추천'은 전국 1,020회에 상위글이 낡아 점수가 가장 높았다. 그런데 부산 동구
    가게가 전국 키워드 1위를 지킬 수 없고, 되더라도 검색자가 전국이라 가게에 안 온다.
    헌법 1항(각 업체가 노출되는 것)의 주어는 **그 가게**다.
    """
    _with_sa(monkeypatch, {"썬팅 추천": 1020, "부산 썬팅": 670, "부산 동구 썬팅업체": 20})
    got = seo.select_target_keyword(["썬팅 추천", "부산 썬팅", "부산 동구 썬팅업체"],
                                    biz_type="local", region="부산광역시 동구", industry="썬팅",
                                    verify_volume=True)
    assert "부산" in got, f"지역 없는 전국 키워드를 골랐다: {got}"


def test_지역_후보가_전부_미달이면_제네릭으로_간다(monkeypatch):
    """수요 없는 지역이라면 전국 키워드로 도망가지 않는다 — 지역+업종 제네릭으로 둔다."""
    _with_sa(monkeypatch, {"썬팅 추천": 5000, "부산 썬팅": 20})
    got = seo.select_target_keyword(["부산 썬팅", "썬팅 추천"], biz_type="local",
                                    region="부산광역시 동구", industry="썬팅", verify_volume=True)
    assert got != "썬팅 추천", "지역 후보 미달 시 전국으로 샜다"
    assert "썬팅" in got


def test_광역_자리가_후보에_들어간다(monkeypatch):
    """★ 관문만 열고 끝냈다면 아무것도 안 바뀌었다(2026-08-19 실측).

    후보 생성기는 구·군 조합만 만든다 — '부산 동구 썬팅'(30회)·'부산 동구 썬팅 추천'(20회)…
    전부 미달이라 관문을 통과시켜도 고를 자리가 없었다. 같은 판의 광역 '부산 썬팅'은 670회다.
    """
    _with_sa(monkeypatch, {"부산 동구 썬팅": 30, "부산 썬팅": 670})
    got = seo.select_target_keyword(["부산 동구 썬팅"], biz_type="local",
                                    region="부산광역시 동구", industry="썬팅", verify_volume=True)
    assert got == "부산 썬팅", f"광역 자리를 후보에 못 넣는다: {got}"


# ── 지면 생존 (2026-08-19 세 번째 실측) ───────────────────────────────
#   관문을 고치고 광역 자리를 넣었더니 '부산 썬팅업체'(월 100회+)가 뽑혔다.
#   그 검색 결과 상위 10개는 '평택시 지역화폐', '화성시 지역화폐',
#   '국민내일배움카드'였다 — 죽은 '부산 동구 썬팅업체'와 **같은 모양**이다.
#   검색량이 있어도 네이버가 그 쿼리에 블로그를 안 뿌리면 판이 없다.
#   여기서 1위를 하는 것은 잘해서가 아니라 아무도 없어서다.

def _with_serp(monkeypatch, table, vols):
    """검색결과 대역 — 키워드별 상위 10개 제목."""
    _with_sa(monkeypatch, vols)
    import app.services.blogrank as br
    monkeypatch.setattr(br, "configured", lambda: True)
    monkeypatch.setattr(br, "doc_count", lambda k: 100000)
    monkeypatch.setattr(br, "_search_blog",
                        lambda k, n=10: [{"title": t, "postdate": "20240101"}
                                         for t in table.get(k, [])])


def test_상위글이_그_업종이_아니면_그_자리를_버린다():
    """★ 이 판정의 존재 이유. 검색량만 보면 스팸 판을 고른다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {
            "부산 썬팅업체": ["평택시 지역화폐 가맹점", "화성시 지역화폐 가맹점",
                          "국민내일배움카드", "2008년 자동차 홈페이지", "파주시 지역화폐"],
            "부산 썬팅": ["부산 썬팅 후기", "부산 북구 썬팅 추천", "썬팅 필름 비교",
                       "부산 썬팅 가격", "차량 썬팅 실측"],
        }, {"부산 썬팅업체": 1000, "부산 썬팅": 670})
        got = seo._volume_first(["부산 썬팅업체", "부산 썬팅"], industry="썬팅")
        assert got == "부산 썬팅", f"지면이 죽은 자리를 골랐다: {got}"
    finally:
        _mp.undo()


def test_업종을_모르면_판정하지_않는다():
    """업종 인자가 없으면(옛 호출부) 예전대로 — 임의로 자리를 버리지 않는다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"가 나": ["전혀 다른 글", "또 다른 글"]}, {"가 나": 500})
        assert seo._volume_first(["가 나"]) == "가 나"
    finally:
        _mp.undo()


def test_검색이_안_되면_자리를_버리지_않는다():
    """못 잰 것과 죽은 것은 다르다 — 무측정으로 후보를 죽이면 글이 멈춘다(정직 게이트)."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {}, {"부산 썬팅": 670})       # 검색결과 0건 = 못 잼
        assert seo._volume_first(["부산 썬팅"], industry="썬팅") == "부산 썬팅"
    finally:
        _mp.undo()


# ── 업태어와 죽은 판 (2026-08-19 네 번째 실측) ────────────────────────
#   지면 생존 판정을 넣었는데도 '부산 썬팅업체'가 또 뽑혔다. 두 가지가 겹쳤다:
#     ① 이 가게의 프로필 업종명이 '썬팅업체'였다. 그래서 상위 글 제목에서 '썬팅업체'를
#        찾았는데, 실제 글은 '부산 썬팅 후기'처럼 쓴다 — **살아 있는 판까지 죽었다고** 판정.
#     ② 후보가 전부 탈락하자 코드가 **방금 죽었다고 판정한 첫 후보를 그대로 돌려줬다**.
#        헌법 '침묵 폴백 금지'를 판정 함수 자신이 어겼다.

def test_업태어를_떼고_업종을_본다():
    """'업체·전문점·매장'은 업종이 아니라 업태다. 어느 업종에나 똑같이 붙는다(업종 중립)."""
    assert seo.industry_core("썬팅업체") == "썬팅"
    assert seo.industry_core("인테리어 전문점") == "인테리어"
    assert seo.industry_core("자동차매장") == "자동차"
    # 업태어가 아닌 말은 건드리지 않는다 — 과하게 자르면 엉뚱한 업종이 된다
    assert seo.industry_core("헬스장") == "헬스장"
    assert seo.industry_core("맛집") == "맛집"
    assert seo.industry_core("동물병원") == "동물병원"


def test_업태어가_붙어도_살아있는_판을_알아본다():
    """★ ①의 재발 방지. 제목은 '썬팅'이라고 쓰지 '썬팅업체'라고 쓰지 않는다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"부산 썬팅": ["부산 썬팅 후기", "부산 북구 썬팅 추천",
                                    "썬팅 필름 비교", "부산 썬팅 가격"]},
                   {"부산 썬팅": 670})
        got = seo._volume_first(["부산 썬팅"], industry="썬팅업체")
        assert got == "부산 썬팅", "업태어 때문에 살아 있는 판을 버렸다"
    finally:
        _mp.undo()


def test_전부_죽은_판이면_그중_하나를_고르지_않는다():
    """★ ②의 재발 방지. 죽었다고 판정한 자리를 그대로 쓰면 판정이 없는 것과 같다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"부산 썬팅업체": ["평택시 지역화폐", "국민내일배움카드", "화성시 지역화폐"]},
                   {"부산 썬팅업체": 1000})
        assert seo._volume_first(["부산 썬팅업체"], industry="썬팅") == "", \
            "죽은 판을 그대로 돌려줬다(침묵 폴백)"
    finally:
        _mp.undo()
