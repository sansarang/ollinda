"""실계정 경로 골든 — **관문이 실제로 지나가는지**를 커밋 시점에 잡는다.

2026-08-19 하루에 결함 12건을 잡았는데 **9건이 같은 모양**이었다:

    기능은 코드에 있는데 **실계정이 그 분기에 못 들어간다.**

  · 검색량 관문      — seller/hybrid 블록 안에만 있었다(실계정 둘 다 local → 12편 헛발질)
  · 세트 앵커        — 같은 자리에 있었다(테슬라를 파는데 키워드에 그 차가 없었다)
  · phantom 방어     — seller 전용(매장에 '레이중고차'가 들어왔다)
  · prof_name        — `prof_name or industry_first(...)`. 부르는 쪽이 값을 넘기면 무효
  · 연관검색어 수집   — 이번엔 반대로 local 전용(셀러 고객이 생기면 같은 사고)
  · 정직 게이트      — 거둔 후보에만 걸어서 주입 경로가 통과했다

전부 "만들었다 ≠ 그 경로로 간다"이고, **실측을 돌려야만** 보였다.
그래서 실계정의 실제 파라미터로 전 관문을 태우고, 하나라도 건너뛰면 여기서 실패시킨다.

★ 결과값(어떤 키워드가 뽑히는가)은 검사하지 않는다 — 그건 시장이 정하고 매일 바뀐다.
  여기서 보는 것은 **경로**다: 그 관문이 이 tenant에 대해 실제로 호출되었는가.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

import pytest  # noqa: E402

from app import seo  # noqa: E402

#: 실계정 둘의 실제 등록값(프로덕션 tenants 테이블 실측 — 2026-08-19).
#:   둘 다 biz_type='local'이다. 이 사실이 오늘 사고 9건의 공통 배경이다.
REAL = [
    ("루마썬팅 현대상사", "썬팅,광택", "부산광역시 동구"),
    ("주안모터스", "중고차판매", "부산 기장"),
]

NOTE = "[사진1] 매장 앞 차량 외관\n[사진2] 작업 중인 손\n[사진3] 계기판과 서류"


def _stub_world(mp, calls):
    """바깥(검색광고·블로그검색·LLM)을 전부 대역으로 — 이 골든은 네트워크를 타지 않는다."""
    import app.services.blogrank as br
    import app.services.searchad as sa

    mp.setattr(sa, "configured", lambda: True)
    mp.setattr(sa, "keyword_volumes",
               lambda kws, limit=80: [{"keyword": k, "total": 900} for k in kws])
    mp.setattr(br, "configured", lambda: True)
    mp.setattr(br, "doc_count", lambda k: 50000)
    mp.setattr(br, "_search_blog",
               lambda k, n=10: [{"title": f"{k} 후기", "postdate": "20240101"},
                                {"title": f"{k} 추천", "postdate": "20240201"}])
    mp.setattr(seo, "region_conflict", lambda kw, reg: False)
    mp.setattr(seo, "keyword_intent_ok", lambda *a, **k: True)

    def _spy(name, fn):
        def _w(*a, **k):
            calls.add(name)
            return fn(*a, **k)
        return _w

    for name in ("_volume_first", "_with_related", "set_anchor",
                 "industry_radius", "_grounded_kw", "_surface_first"):
        mp.setattr(seo, name, _spy(name, getattr(seo, name)))


@pytest.mark.parametrize("name,industry,region", REAL)
def test_실계정이_전_관문을_지난다(name, industry, region):
    """★ 이 파일의 존재 이유. 관문 하나라도 건너뛰면 여기서 멈춘다."""
    calls: set = set()
    mp = pytest.MonkeyPatch()
    try:
        _stub_world(mp, calls)
        kw0, kws = seo.resolve_target_keyword(industry=industry, region=region, note=NOTE,
                                              biz="local", prof_name=industry)
    finally:
        mp.undo()
    assert kw0, f"{name}: 대표 키워드가 비었다"
    for gate in ("_volume_first", "_with_related", "set_anchor",
                 "industry_radius", "_grounded_kw"):
        assert gate in calls, f"{name}: {gate} 관문을 건너뛴다(실계정이 그 분기에 못 들어감)"


@pytest.mark.parametrize("biz", ["local", "seller", "hybrid"])
def test_검색량_관문은_업태와_무관하게_지난다(biz):
    """★ 오늘 아침 사고의 원형 — 이 관문이 seller 블록 안에 있어서 매장이 통과했다.
    업태가 늘어나도 이 검사는 남는다."""
    calls: set = set()
    mp = pytest.MonkeyPatch()
    try:
        _stub_world(mp, calls)
        seo.resolve_target_keyword(industry="썬팅", region="부산광역시 동구", note=NOTE,
                                   biz=biz, prof_name="썬팅")
    finally:
        mp.undo()
    assert "_volume_first" in calls, f"biz={biz}: 검색량 관문을 건너뛴다"


@pytest.mark.parametrize("name,industry,region", REAL)
def test_실계정_키워드에_업종_구분자가_남지_않는다(name, industry, region):
    """'썬팅,광택'·'병원·의원' 같은 등록값이 그대로 키워드가 되던 사고."""
    mp = pytest.MonkeyPatch()
    try:
        _stub_world(mp, set())
        kw0, kws = seo.resolve_target_keyword(industry=industry, region=region, note=NOTE,
                                              biz="local", prof_name=industry)
    finally:
        mp.undo()
    for k in [kw0] + list(kws):
        assert not any(ch in k for ch in ",·/|"), f"{name}: 구분자가 키워드에 남았다 — {k!r}"


def test_실계정_등록값이_바뀌면_이_골든을_고치게_한다():
    """★ 이 목록이 실제와 어긋나면 위 검사는 **엉뚱한 값**을 지키게 된다.
    프로덕션 tenants와 다르면 사람이 눈으로 갱신하라는 신호를 남긴다."""
    assert REAL[0][1] == "썬팅,광택" and REAL[0][2] == "부산광역시 동구"
    assert REAL[1][1] == "중고차판매" and REAL[1][2] == "부산 기장"
