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
