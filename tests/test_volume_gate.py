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

_last_cands: list = []


def _spy_cands(monkeypatch):
    """_volume_first가 실제로 무엇을 후보로 받았는지 본다(거둔 후보가 들어왔나)."""
    orig = seo._volume_first

    def _wrap(cands, *a, **k):
        _last_cands.clear()
        _last_cands.extend(cands)
        return orig(cands, *a, **k)
    monkeypatch.setattr(seo, "_volume_first", _wrap)


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


# ── 지역 형식 (2026-08-19 다섯 번째 실측 — 배포 직후 잡은 회귀) ──────────
#   "매장은 지역이 붙은 후보 안에서만 고른다"는 가드를 넣고 배포했는데,
#   그 가드가 `_region_wide()`에 의존했다. 그 함수는 '광역시/특별시/도' 접미사가
#   있어야 값을 준다 — 실계정 주안모터스의 region은 **'부산 기장'**이고,
#   시연 tenant들도 '수원 영통'·'가평 청평'이다. 전부 빈 값이라 **가드가 통째로 통과**됐고,
#   헬스장(수원 영통) 실측에서 대표 키워드가 '헬스장 추천'(전국)으로 나왔다.
#
#   ★ 가드는 '조건이 맞을 때 막는 것'이 아니라 '조건을 못 읽으면 막는 것'이어야 한다.

def test_행정접미사가_없는_지역도_광역_자리를_만든다(monkeypatch):
    """실계정 주안모터스가 이 형식이다('부산 기장') — 광역 후보를 못 만들면 고를 자리가 없다.

    ★ 지역을 **강제하지는 않는다**(2026-08-19 사장님 지적). 여기서 '부산 중고차'가
      뽑히는 이유는 지역이라서가 아니라 상위글이 10년 낡아 세 축 점수가 높기 때문이다.
    """
    _with_sa(monkeypatch, {"중고차 추천": 5000, "부산 중고차": 800})
    _spy_cands(monkeypatch)
    got = seo.select_target_keyword(["중고차 추천"], biz_type="local",
                                    region="부산 기장", industry="중고차", verify_volume=True)
    assert got in ("부산 중고차", "중고차 추천"), got
    assert "부산 중고차" in _last_cands, "광역 자리를 후보에 못 넣었다"


def test_시군_이름만_있는_지역도_광역_자리를_만든다(monkeypatch):
    """후보에 **들어가는지**만 본다 — 이기는지는 세 축이 정한다(지역이라서 이기지 않는다).

    ★ 전에는 여기서 '수원 헬스장'이 뽑히는 것을 단언했다. 그건 지역 강제를 전제한 기대였다.
      강제를 걷어낸 지금, 상위글 나이·문서수가 같다면 수요 큰 쪽이 이기는 게 맞다.
    """
    _with_sa(monkeypatch, {"헬스장 추천": 5000, "수원 헬스장": 500, "수원 영통 헬스장": 30})
    _spy_cands(monkeypatch)
    seo.select_target_keyword(["수원 영통 헬스장", "헬스장 추천"], biz_type="local",
                              region="수원 영통", industry="헬스장", verify_volume=True)
    assert "수원 헬스장" in _last_cands, f"광역 자리를 후보에 못 넣는다: {_last_cands}"



def test_기초지역_후보도_후보로_남는다(monkeypatch):
    """'수원 영통 헬스장'도 이 가게의 자리다 — 광역만 남기고 지우지 않는다."""
    _with_sa(monkeypatch, {"수원 영통 헬스장": 300, "헬스장 추천": 9000})
    got = seo.select_target_keyword(["수원 영통 헬스장", "헬스장 추천"], biz_type="local",
                                    region="수원 영통", industry="헬스장", verify_volume=True)
    assert "수원" in got, f"전국으로 샜다: {got}"


def test_지역을_몰라도_글은_나간다(monkeypatch):
    """지역이 없는 tenant의 글을 막아버리면 안 된다."""
    _with_sa(monkeypatch, {"헬스장 추천": 5000})
    got = seo.select_target_keyword(["헬스장 추천"], biz_type="local",
                                    region="", industry="헬스장", verify_volume=True)
    assert got == "헬스장 추천", "지역 없는 tenant의 글을 막아버렸다"


# ── 후보 수집 (2026-08-19 사장님 지적) ────────────────────────────────
#   "중고차면 중고차지 왜 하필 지역특화로 한 거냐."
#   후보 생성기가 [업종어+지역+접미사] 조합만 만들어서 풀 안에 쓸 자리가 없었다.
#   검색광고 API는 힌트 하나에 연관검색어 ~40개를 검색량과 함께 준다 — 받아놓고 버리고 있었다.

class _FakeRelated:
    """연관검색어까지 돌려주는 검색광고 대역(실제 API 동작 — 공백 없는 형태로 온다)."""
    RELATED = [{"keyword": "자동차썬팅", "total": 5660},
               {"keyword": "썬팅가격", "total": 4280},
               {"keyword": "썬팅재시공", "total": 620},
               {"keyword": "버텍스1100", "total": 3040},      # 업종어 없음 → 버려야 한다
               {"keyword": "신차패키지", "total": 1300},      # 업종어 없음
               {"keyword": "윈도틴팅", "total": 20}]          # 수요 미달

    def configured(self):
        return True

    def keyword_volumes(self, kws, limit=80):
        base = [{"keyword": k, "total": {"부산 동구 썬팅": 30}.get(k)} for k in kws]
        return base + self.RELATED


def _with_related_sa(monkeypatch):
    import app.services.searchad as sa
    f = _FakeRelated()
    monkeypatch.setattr(sa, "configured", f.configured)
    monkeypatch.setattr(sa, "keyword_volumes", f.keyword_volumes)


def test_시장이_쓰는_말을_후보로_거둔다(monkeypatch):
    """★ 이 판정의 존재 이유. 우리가 만들 수 없는 말이 여기서 들어온다."""
    _with_related_sa(monkeypatch)
    _spy_cands(monkeypatch)
    seo.select_target_keyword(["부산 동구 썬팅"], biz_type="local",
                              region="부산광역시 동구", industry="썬팅", verify_volume=True)
    flat = {c.replace(" ", "") for c in _last_cands}
    assert "자동차썬팅" in flat and "썬팅가격" in flat, f"연관검색어를 안 거뒀다: {_last_cands}"
    assert "썬팅재시공" in flat, "롱테일도 거둬야 한다"


def test_업종어가_없는_말은_거두지_않는다(monkeypatch):
    """'버텍스1100'·'신차패키지'는 우리 재료가 뒷받침한다는 보장이 없다 — 날조 후보가 된다."""
    _with_related_sa(monkeypatch)
    _spy_cands(monkeypatch)
    seo.select_target_keyword(["부산 동구 썬팅"], biz_type="local",
                              region="부산광역시 동구", industry="썬팅", verify_volume=True)
    flat = {c.replace(" ", "") for c in _last_cands}
    assert "버텍스1100" not in flat and "신차패키지" not in flat, f"근거 없는 후보를 넣었다: {flat}"


def test_수요_미달은_거두지_않는다(monkeypatch):
    _with_related_sa(monkeypatch)
    _spy_cands(monkeypatch)
    seo.select_target_keyword(["부산 동구 썬팅"], biz_type="local",
                              region="부산광역시 동구", industry="썬팅", verify_volume=True)
    assert "윈도틴팅" not in {c.replace(" ", "") for c in _last_cands}


def test_거둔_검색량을_다시_조회하지_않는다(monkeypatch):
    """★ 거두고도 못 쓰는 결함 방지. 조회 창(앞 8개) 밖으로 밀리면 '무측정'이 되어 버려진다."""
    _with_related_sa(monkeypatch)
    calls = []
    import app.services.searchad as sa
    orig = sa.keyword_volumes
    monkeypatch.setattr(sa, "keyword_volumes",
                        lambda kws, limit=80: (calls.append(list(kws)), orig(kws, limit))[1])
    seo._volume_first(["가 나"], industry="썬팅", known={"가나": 900}, deep=False)
    assert not calls, "이미 잰 값을 두고 다시 조회했다"


def test_다른_지역_키워드는_고르지_않는다(monkeypatch):
    """★ 2026-08-01 '김해썬팅' 실사고 · 2026-08-19 재발.

    지역 강제를 걷어내자 연관검색어에서 '대구중고차사이트'가 **부산 가게**의 대표
    키워드로 뽑혔다. 전국 키워드는 정당하지만 남의 동네는 미끼 글이다 — 헌법 금지.
    """
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"대구 중고차": ["대구 중고차 후기", "대구 중고차 매매"],
                         "부산 중고차": ["부산 중고차 후기", "부산 중고차 시세"]},
                   {"대구 중고차": 9000, "부산 중고차": 800})
        _mp.setattr(seo, "region_conflict",
                    lambda kw, reg: "대구" in kw and "대구" not in reg)
        got = seo._volume_first(["대구 중고차", "부산 중고차"],
                                industry="중고차", region="부산 기장")
        assert got == "부산 중고차", f"남의 동네 키워드를 골랐다: {got}"
    finally:
        _mp.undo()


def test_전국_키워드는_막지_않는다(monkeypatch):
    """지역이 없는 말('중고차 시세')은 남의 동네가 아니다 — 이건 정당한 자리다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"중고차 시세": ["중고차 시세 조회", "중고차 시세표"]},
                   {"중고차 시세": 50000})
        _mp.setattr(seo, "region_conflict", lambda kw, reg: False)
        got = seo._volume_first(["중고차 시세"], industry="중고차", region="부산 기장")
        assert got == "중고차 시세", f"전국 자리를 막았다: {got}"
    finally:
        _mp.undo()


def test_지역_판정이_실패해도_글은_나간다(monkeypatch):
    """LLM 판정이 죽었다고 생성을 멈추면 안 된다(무키·실패는 통과)."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"부산 중고차": ["부산 중고차 후기", "부산 중고차 시세"]},
                   {"부산 중고차": 800})

        def _boom(*a, **k):
            raise RuntimeError("판정 죽음")
        _mp.setattr(seo, "region_conflict", _boom)
        assert seo._volume_first(["부산 중고차"], industry="중고차",
                                 region="부산 기장") == "부산 중고차"
    finally:
        _mp.undo()


# ── 세트 앵커 (2026-08-19 사장님 질문) ────────────────────────────────
#   "테슬라 중고차 판매를 목적으로 글을 쓴다고 가정하자. 키워드 선정은 어떻게 되는데?"
#   실측 답: '부산 중고차' — **그 차가 키워드에 한 글자도 없었다.**
#   앵커를 업종 스키마의 미리 만든 토큰 목록에서만 찾았는데, 그 목록은 국산차뿐이라
#   테슬라·BMW·벤츠·볼보가 통째로 안 보였다. 목록은 끝이 없다 — 재료가 답을 갖고 있다.

def test_목록에_없는_대상도_재료에서_뽑는다(monkeypatch):
    """★ 이 판정의 존재 이유. 수입차 전체가 안 보이던 구멍."""
    from app import llm
    monkeypatch.setattr(llm, "call_task", lambda *a, **k: "테슬라 모델3")
    note = "[사진1] 매장 전시장의 흰색 테슬라 모델3, 무사고.\n[사진2] 주행거리 3만8천km."
    assert seo.set_anchor(note, "중고차판매") == "테슬라 모델3"


def test_재료에_없는_말은_앵커로_쓰지_않는다(monkeypatch):
    """★ 프롬프트가 날조를 시키는 통로가 되면 안 된다 — LLM이 헛말을 해도 코드가 막는다."""
    from app import llm
    monkeypatch.setattr(llm, "call_task", lambda *a, **k: "그랜저 하이브리드")
    note = "[사진1] 흰색 테슬라 모델3 외관."
    assert seo.set_anchor(note, "중고차판매") == "", "재료에 없는 말을 앵커로 썼다"


def test_재료가_없으면_앵커도_없다(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "call_task", lambda *a, **k: "아무거나")
    assert seo.set_anchor("", "중고차판매") == ""


def test_LLM이_죽어도_글은_나간다(monkeypatch):
    from app import llm

    def _boom(*a, **k):
        raise RuntimeError("죽음")
    monkeypatch.setattr(llm, "call_task", _boom)
    assert seo.set_anchor("[사진1] 흰색 테슬라 모델3", "중고차판매") == ""


def test_앵커가_후보와_씨앗에_들어간다(monkeypatch):
    """★ '뽑았다'와 '그 경로로 간다'는 다르다 — 앵커가 후보에 안 들어가면 장식이다."""
    seeds = []
    import app.services.searchad as sa
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes",
                        lambda kws, limit=80: (seeds.append(list(kws)),
                                               [{"keyword": k, "total": 500} for k in kws])[1])
    _spy_cands(monkeypatch)
    # industry는 이미 손님말로 바뀐 값이 온다(resolve_target_keyword가 searcher_term 통과) —
    # '중고차판매'가 아니라 '중고차'. 실호출과 같은 값을 준다.
    seo.select_target_keyword(["부산 중고차"], biz_type="local", region="부산 기장",
                              industry="중고차", primary_model="테슬라 모델3",
                              verify_volume=True)
    flat = {c.replace(" ", "") for c in _last_cands}
    assert "테슬라모델3중고차" in flat, f"앵커 자리가 후보에 없다: {_last_cands}"
    assert any("테슬라" in " ".join(s) for s in seeds), f"앵커를 씨앗으로 안 썼다: {seeds}"


def test_앵커가_든_후보를_먼저_측정한다(monkeypatch):
    """★ 2026-08-19 실측 — 검색량 순으로 줄 세웠더니 거대 일반어가 앞을 다 차지했다.

      중고차(290,700)·중고차매매사이트(99,800)·중고차사이트(45,900)·현대중고차사이트(12,550)
      → 이 세트의 '테슬라중고차'(4,930회·문서 10만·점수 19.40)가 7번째로 밀려
        **측정도 안 된 채** 탈락하고, 점수 9.98짜리가 뽑혔다.
      검색량 순 ≠ 좋은 자리 순이다.
    """
    import app.services.searchad as sa
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes", lambda kws, limit=80: [
        {"keyword": "중고차", "total": 290700},
        {"keyword": "중고차매매사이트", "total": 99800},
        {"keyword": "중고차사이트", "total": 45900},
        {"keyword": "현대중고차사이트", "total": 12550},
        {"keyword": "포르쉐인증중고차", "total": 6840},
        {"keyword": "테슬라중고차", "total": 4930}])
    got, _known = seo._with_related(["부산 중고차"], "중고차", "부산", anchor="테슬라 모델3")
    idx = [i for i, c in enumerate(got) if "테슬라" in c]
    assert idx and idx[0] <= 2, f"앵커 자리가 뒤로 밀렸다: {got}"


def test_측정_창이_좁아지지_않는다():
    """자르는 지점이 곧 판정의 상한이다 — 값을 리터럴로 박는다(상수 참조 금지)."""
    assert seo.DEEP_MEASURE >= 10, f"측정 후보가 {seo.DEEP_MEASURE}개로 줄었다"


# ── 확장 목록·폴백 (2026-08-19 실측) ──────────────────────────────────
#   대표 키워드만 고치고 확장 목록을 그대로 뒀더니 두 곳에서 죽은 자리가 되살아났다:
#     ⑴ 의도 게이트가 대표를 기각하자 되돌아갈 곳이 구·군 조합뿐 → '부산 기장 중고차'(70회)
#     ⑵ 그 말들이 그대로 **태그로 발행**됐다(루마썬팅 글 태그에 '부산 동구 썬팅업체' 계열 5개)

def test_수요_미달로_버린_말을_확장에_남기지_않는다(monkeypatch):
    """★ 태그·소제목이 이 목록에서 나온다 — 죽은 말이 표면에 실리면 키워드 스터핑이다."""
    _with_sa(monkeypatch, {"부산 썬팅": 670, "부산 동구 썬팅업체": 20,
                           "부산 동구 썬팅업체 추천": 20})
    _stub_resolve(monkeypatch, ["부산 동구 썬팅업체", "부산 동구 썬팅업체 추천"],
                  "부산 동구 썬팅업체")
    kw0, kws = seo.resolve_target_keyword("썬팅", "부산광역시 동구", "", biz="local")
    dead = [k for k in kws if "썬팅업체" in k]
    assert not dead, f"수요 미달로 버린 말이 확장에 남았다: {dead}"


def test_판정_보고서가_살아남은_자리를_준다():
    """의도 게이트가 되돌아갈 곳이 있어야 한다 — 없으면 죽은 자리로 떨어진다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"부산 썬팅": ["부산 썬팅 후기", "부산 썬팅 가격"],
                         "썬팅 가격": ["썬팅 가격 비교", "썬팅 가격표"]},
                   {"부산 썬팅": 670, "썬팅 가격": 4280})
        _mp.setattr(seo, "region_conflict", lambda kw, reg: False)
        rep: dict = {}
        got = seo._volume_first(["부산 썬팅", "썬팅 가격"], industry="썬팅",
                                region="부산광역시 동구", report=rep)
        assert got in rep["ranked"], "고른 자리가 보고서에 없다"
        assert len(rep["ranked"]) >= 2, f"차순위가 없다: {rep}"
    finally:
        _mp.undo()


def test_남의_동네가_확장에도_남지_않는다():
    """★ 2026-08-19 실측 — 대표에서 걸러낸 '대구중고차사이트'가 확장에 그대로 실렸다.
    확장은 태그·소제목이 되어 **발행된다**. 대표만 막는 것은 절반만 막는 것이다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"대구 중고차": ["대구 중고차 후기", "대구 중고차 매매"],
                         "부산 중고차": ["부산 중고차 후기", "부산 중고차 시세"]},
                   {"대구 중고차": 9000, "부산 중고차": 800})
        _mp.setattr(seo, "region_conflict", lambda kw, reg: "대구" in kw and "대구" not in reg)
        rep: dict = {}
        seo._volume_first(["대구 중고차", "부산 중고차"], industry="중고차",
                          region="부산 기장", report=rep)
        assert "대구 중고차" in rep["dropped"], f"버린 이유를 안 남겼다: {rep}"
        assert "대구 중고차" not in rep["ranked"], "남의 동네가 확장 후보로 남았다"
    finally:
        _mp.undo()


# ── 업종 구분자 (2026-08-19 8업종 실측) ───────────────────────────────
#   자동차 업종에서는 하루 종일 안 보이던 결함. 실계정 둘이 '썬팅,광택'·'중고차판매'라
#   쉼표뿐이었고, 코드가 `,`와 `/`만 잘랐다. 가운뎃점이 든 업종은 통째로 깨졌다:
#     동물병원 → '병원·의원' → 키워드 '강아지 병원·의원'·'인천 청라 병원·의원 잘하는곳'
#     인테리어 → '인테리어·리모델링' · 펜션 → '펜션·숙박'
#   ★ 같은 규칙이 네 곳에 복사돼 있었다(헌법: 파서를 하나로).

def test_가운뎃점_업종이_키워드에_그대로_실리지_않는다():
    """★ 이 골든의 존재 이유. '병원·의원'은 아무도 검색하지 않는다."""
    assert seo.industry_first("병원·의원") == "병원"
    assert seo.industry_first("인테리어·리모델링") == "인테리어"
    assert seo.industry_first("펜션·숙박") == "펜션"
    assert seo.industry_first("썬팅,광택") == "썬팅"
    assert seo.industry_first("중고차/매매") == "중고차"
    # 구분자가 없으면 그대로 — 과하게 자르면 엉뚱한 업종이 된다
    assert seo.industry_first("중고차판매") == "중고차판매"
    assert seo.industry_first("동물병원") == "동물병원"


def test_지면_생존_판정도_같은_관문을_쓴다():
    """구분자가 남으면 상위 글 제목과 절대 안 맞아 **살아 있는 판도 죽은 것으로** 나온다."""
    assert seo.industry_core("병원·의원") == "병원"
    assert seo.industry_core("인테리어·리모델링") == "인테리어"


def test_업종_구분자_규칙이_한_곳에만_산다():
    """네 곳에 복사돼 있던 것이 이 결함의 원인이다 — 다시 흩어지면 한쪽만 고쳐진다."""
    import inspect
    src = inspect.getsource(seo)
    assert 'replace("/", ",").split(",")[0]' not in src, "옛 규칙 사본이 남아 있다"


def test_넘어온_업종명도_관문을_거친다(monkeypatch):
    """★ 2026-08-19 8업종 재실측 — 어간만 고치고 후보 생성기는 원본을 받고 있었다.

    `prof_name or industry_first(...)`는 **부르는 쪽이 값을 넘기면 무효**다.
    생성기가 prof.name('병원·의원')을 넘기므로 or 뒤가 영영 실행되지 않았고,
    후보가 '인천 청라 병원·의원 후기'로 만들어졌다.
    """
    seen = {}

    def _tk(prof, region, note, axis="local", brand=""):
        seen["prof"] = prof
        return [f"{region} {prof}"]
    monkeypatch.setattr(seo, "target_keywords", _tk)
    monkeypatch.setattr(seo, "keyword_plan", lambda *a, **k: {"headline": ""})
    monkeypatch.setattr(seo, "region_conflict", lambda *a, **k: False)
    monkeypatch.setattr(seo, "keyword_intent_ok", lambda *a, **k: True)
    monkeypatch.setattr(seo, "searcher_term", lambda s: s)
    _with_sa(monkeypatch, {})
    seo.resolve_target_keyword("병원·의원", "인천 청라", "", biz="local",
                               prof_name="병원·의원")
    assert "·" not in seen["prof"], f"후보 생성기가 구분자를 그대로 받았다: {seen['prof']}"


# ── 상권 반경 · 업종 우선순위 · 채널어 (2026-08-19 8업종 실측) ──────────

def test_동네_반경이면_같은_시_다른_동네도_막는다():
    """★ 사장님 결정 ⓒ — 가게마다 정한다. 헬스장·미용실은 매주 와야 해서 동네가 중요하다.
    실측: 수원 영통 헬스장에 '인계동헬스장', 대전 둔산 미용실에 '관저동미용실'."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"인계동헬스장": ["인계동 헬스장 후기", "인계동 헬스장 가격"],
                         "수원 헬스장": ["수원 헬스장 추천", "수원 헬스장 후기"]},
                   {"인계동헬스장": 900, "수원 헬스장": 500})
        _mp.setattr(seo, "region_conflict", lambda kw, reg: False)   # 같은 시라 게이트는 통과
        got = seo._volume_first(["인계동헬스장", "수원 헬스장"], industry="헬스장",
                                region="수원 영통", radius="동네")
        assert got == "수원 헬스장", f"동네 반경인데 다른 동네를 골랐다: {got}"
    finally:
        _mp.undo()


def test_광역_반경이면_같은_시_안은_허용한다():
    """기본값은 광역 — 같은 시 안이면 동네가 달라도 손님이 올 수 있다."""
    import pytest
    _mp = pytest.MonkeyPatch()
    try:
        _with_serp(_mp, {"인계동헬스장": ["인계동 헬스장 후기", "인계동 헬스장 가격"]},
                   {"인계동헬스장": 900})
        _mp.setattr(seo, "region_conflict", lambda kw, reg: False)
        got = seo._volume_first(["인계동헬스장"], industry="헬스장",
                                region="수원 영통", radius="광역")
        assert got == "인계동헬스장", "광역 반경인데 같은 시를 막았다"
    finally:
        _mp.undo()


def test_전국_일반어는_동네_반경에서도_안_막힌다():
    """'헬스장 추천'은 동네 이름이 아니다 — 지명 사전 없이 구분해야 한다."""
    assert seo._looks_local_kw("헬스장 추천", "헬스장") is False
    assert seo._looks_local_kw("인계동헬스장", "헬스장") is True
    assert seo._looks_local_kw("관저동미용실", "미용실") is True


def test_가게가_적은_업종을_프로필명보다_우선한다(monkeypatch):
    """★ 실측 — 동물병원 가게의 프로필명이 '병원·의원'이라 '인천 병원'이 뽑혔다.
    사람 병원 검색자를 노리게 된다. 1위를 해도 오는 손님이 다르다."""
    seen = {}
    monkeypatch.setattr(seo, "target_keywords",
                        lambda prof, region, note, axis="local", brand="": (
                            seen.setdefault("prof", prof), [f"{region} {prof}"])[1])
    monkeypatch.setattr(seo, "keyword_plan", lambda *a, **k: {"headline": ""})
    monkeypatch.setattr(seo, "region_conflict", lambda *a, **k: False)
    monkeypatch.setattr(seo, "keyword_intent_ok", lambda *a, **k: True)
    monkeypatch.setattr(seo, "searcher_term", lambda s: s)
    _with_sa(monkeypatch, {})
    seo.resolve_target_keyword("동물병원", "인천 청라", "", biz="local", prof_name="병원·의원")
    assert seen["prof"] == "동물병원", f"프로필명이 가게 업종을 덮었다: {seen['prof']}"


def test_매장에_온라인_채널어를_거두지_않는다(monkeypatch):
    """'중고차매매사이트'(99,800회)·'인테리어사이트'가 대표로 뽑혔다 — 사이트 찾는 사람은
    우리 손님이 아니다. 의도 게이트(LLM)가 한 번은 잡고 한 번은 놓쳤다."""
    import app.services.searchad as sa
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes", lambda kws, limit=80: [
        {"keyword": "중고차매매사이트", "total": 99800},
        {"keyword": "중고차사이트", "total": 45900},
        {"keyword": "부산중고차", "total": 9250}])
    got, _ = seo._with_related(["부산 중고차"], "중고차", "부산")
    flat = {c.replace(" ", "") for c in got}
    assert "중고차매매사이트" not in flat and "중고차사이트" not in flat, f"채널어를 거뒀다: {flat}"


def test_카페_가게는_카페가_업종어다(monkeypatch):
    """★ 채널어 목록을 만들자마자 낸 결함 — '카페'를 넣었더니 카페 가게는 후보가 전멸한다."""
    import app.services.searchad as sa
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes", lambda kws, limit=80: [
        {"keyword": "성수동카페", "total": 5000}, {"keyword": "카페추천", "total": 3000}])
    got, _ = seo._with_related(["성남 카페"], "카페", "성남")
    assert len(got) > 1, f"카페 가게의 후보가 전멸했다: {got}"


# ── 업종 기본 반경 (2026-08-19 사장님 지시: "모든 업종에 적용해라") ────────

def test_업종_목록을_코드에_갖지_않는다():
    """헌법 업종 중립 — 판정은 LLM+캐시로 한다(region_conflict와 같은 패턴)."""
    import inspect
    src = inspect.getsource(seo.industry_radius)
    body = src.split('"""')[-1]
    for w in ("중고차", "헬스", "미용", "맛집", "펜션", "썬팅", "인테리어"):
        assert w not in body, f"업종명이 코드에 박혔다: {w}"


def test_판정이_실패해도_글은_나간다(monkeypatch):
    from app import llm

    def _boom(*a, **k):
        raise RuntimeError("죽음")
    monkeypatch.setattr(llm, "call_task", _boom)
    assert seo.industry_radius("중고차판매") == "광역"


def test_이상한_답은_광역으로_떨어진다(monkeypatch):
    """LLM이 '반경 넓음' 같은 말을 해도 아는 값만 받는다."""
    from app import llm
    monkeypatch.setattr(llm, "call_task", lambda *a, **k: "반경 넓음")
    monkeypatch.setattr(seo, "industry_first", lambda x: "이상업종")
    assert seo.industry_radius("이상업종") == "광역"


def test_가게_설정이_업종_기본값을_이긴다(monkeypatch):
    """★ 가게마다 정한다는 결정(ⓒ)이 업종 일괄값에 덮이면 안 된다."""
    seen = {}
    monkeypatch.setattr(seo, "industry_radius",
                        lambda ind: (seen.setdefault("called", True), "전국")[1])
    import app.db as _db
    monkeypatch.setattr(_db, "market_radius", lambda tid: "동네")
    orig = seo._volume_first
    monkeypatch.setattr(seo, "_volume_first",
                        lambda *a, **k: (seen.setdefault("radius", k.get("radius")), "x")[1])
    _with_sa(monkeypatch, {"가 나": 500})
    seo.select_target_keyword(["가 나"], biz_type="local", region="수원 영통",
                              industry="헬스장", tenant_id="t1", verify_volume=True)
    assert seen.get("radius") == "동네", f"가게 설정이 무시됐다: {seen}"
    assert "called" not in seen, "가게가 정했는데 업종 판정을 또 불렀다(불필요한 LLM 콜)"


# ── 거둔 키워드 정직 게이트 (2026-08-19 실측 회귀) ─────────────────────
#   연관검색어 수집을 켜자 실계정 둘이 이렇게 나왔다:
#     루마썬팅  → '자동차썬팅지'  (필름 자재 — 시공 손님이 아니다)
#     주안모터스 → '레이중고차'    (기아 레이. 우리 매물이 아니다)
#   방어(drop_phantom_attr_kws)는 있었지만 셀러 전용이었고, 매장은 과거 오탐 사고
#   (썬팅지 오제거) 때문에 의도적으로 빠져 있었다 → 거둔 키워드에만 게이트를 건다.

def test_우리_매물이_아닌_차종은_거두지_않는다():
    """★ 이 게이트의 존재 이유. 쏘나타를 파는 글에 '레이중고차'가 대표가 됐다.
    '레이'는 업종 스키마가 차종으로 아는 말인데 이 세트 재료에 없다."""
    ctx = "부산중고차쏘나타디엣지성능점검기록부주행거리"
    assert seo._grounded_kw("레이중고차", "중고차판매", ctx) is False
    assert seo._grounded_kw("쏘나타중고차", "중고차판매", ctx) is True


def test_그_가게가_하는_일은_막지_않는다():
    """★ 처음엔 '재료에 없는 말은 전부 버린다'로 짰다가 골든이 잡았다 —
    '자동차썬팅'·'썬팅재시공'까지 버렸다. 그 둘은 썬팅 가게가 실제로 하는 일이다.
    모르는 말을 버리는 게 아니라, **아는 말이 어긋날 때** 버린다."""
    ctx = "부산썬팅앞유리필름시공"
    for kw in ("자동차썬팅", "썬팅재시공", "앞유리 썬팅", "썬팅 가격", "중고차 추천"):
        assert seo._grounded_kw(kw, "썬팅", ctx) is True, kw


def test_스키마를_못_읽으면_판정하지_않는다(monkeypatch):
    """못 재는 것과 어긋난 것은 다르다 — 모르면 막지 않는다(정직 게이트)."""
    from app.services import indschema
    monkeypatch.setattr(indschema, "get_schema", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert seo._grounded_kw("아무말", "중고차판매", "") is True


def test_수집이_실제로_이_게이트를_쓴다(monkeypatch):
    """'만들었다'와 '그 경로로 간다'는 다르다 — 오늘 네 번 당했다."""
    import app.services.searchad as sa
    monkeypatch.setattr(sa, "configured", lambda: True)
    monkeypatch.setattr(sa, "keyword_volumes", lambda kws, limit=80: [
        {"keyword": "레이중고차", "total": 5000},
        {"keyword": "부산중고차", "total": 9250}])
    got, _ = seo._with_related(["부산 중고차"], "중고차판매", "부산",
                               materials="[사진1] 쏘나타 디 엣지 외관")
    assert "레이중고차" not in {c.replace(" ", "") for c in got}, f"게이트를 안 쓴다: {got}"
