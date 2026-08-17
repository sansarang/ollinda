"""웹 취재 골든 (2026-08-17 사장님 지적: "검색 기능을 넣어도 다 나온다").

무엇을 하나: 판의 언어 중 재료에 없는 것을 **공신력 있는 출처에서만** 가져온다.
헌법이 허용하는 범위다 — "검색은 취재다. 금지는 인칭 위조뿐이다."

여기서 막는 재발:
  ① 경쟁 업체 블로그가 재료로 들어오는 것 — 실제 조회에서 이런 게 같이 나왔다:
     "gv80 썬팅 재시공 이유? 레인보우 VS200으로! … 비교견적 받아보세요"
     이걸 재료로 쓰면 남의 글을 베끼는 것이고 금지선(내용 복제 변주)에 걸린다.
  ② 취재한 사실이 1인칭 경험으로 둔갑하는 것 — 그 순간 정직 게이트가 무너진다.
  ③ 같은 출처가 여러 주제어에 중복으로 실려 재료가 한 말로 채워지는 것.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import research as rs


def test_경쟁업체_블로그는_차단한다():
    """★ 실제 조회에 섞여 나온 것들 — 재료로 들어오면 안 된다."""
    for link, txt in (("https://blog.naver.com/shop/123", "gv80 썬팅 재시공 레인보우 VS200"),
                      ("https://cafe.naver.com/x", "시공 후기입니다"),
                      ("https://xxx.tistory.com/1", "썬팅 맛집 추천"),
                      ("https://brunch.co.kr/@a/1", "내 경험담")):
        assert not rs.trusted(link, txt), f"경쟁 블로그가 통과했다: {link}"


def test_광고문구는_도메인이_깨끗해도_차단한다():
    assert not rs.trusted("https://example.go.kr/x", "시공 사례 소개. 비교견적 받아보세요")
    assert not rs.trusted("https://example.or.kr/x", "지금 상담 신청하면 할인 이벤트")


def test_법령과_백과는_통과한다():
    assert rs.trusted("https://law.go.kr/lsInfo", "제28조 자동차 창유리 가시광선 투과율의 기준")
    assert rs.trusted("https://terms.naver.com/entry", "틴팅 — 유리에 필름을 붙이는 시공을 가리킨다")
    assert rs.trusted("https://ko.wikipedia.org/wiki/x", "자외선은 파장이 짧은 전자기파다")


def test_모르는_출처는_기본이_차단이다():
    """의심스러우면 버린다 — 막는 쪽이 기본값이어야 한다."""
    assert not rs.trusted("https://some-random-site.com/a", "썬팅 필름 설명입니다")
    assert not rs.trusted("", "출처 없는 사실")


def test_재료에_이미_있으면_취재하지_않는다(monkeypatch):
    called = []
    monkeypatch.setattr(rs, "facts_for", lambda t, c="", l=2: called.append(t) or [])
    r = rs.gather(["투과율", "재시공"], material="이번 시공 필름 투과율은 35%입니다")
    assert "투과율" in r["already_had"], "이미 있는 것을 굳이 밖에서 찾았다"
    assert "투과율" not in called and "재시공" in called


def test_같은_출처는_한_번만_실린다(monkeypatch):
    """실측: '투과율'·'재시공'·'자외선'이 전부 같은 백과 항목을 물어왔다."""
    same = [{"term": "x", "title": "t", "text": "본문" * 30,
             "source": "https://terms.naver.com/entry?docId=1", "kind": "encyc"}]
    monkeypatch.setattr(rs, "facts_for", lambda t, c="", l=2: list(same))
    r = rs.gather(["투과율", "재시공", "자외선"], material="")
    assert len(r["facts"]) == 1, f"같은 출처가 {len(r['facts'])}번 실렸다"


def test_재료블록이_인칭위조를_같은_자리에서_막는다():
    """지시는 재료 옆에 있어야 지켜진다 — 멀리 두면 모델이 잊는다."""
    res = {"facts": [{"term": "투과율", "title": "t", "text": "법으로 정해져 있다",
                      "source": "https://law.go.kr/x", "kind": "webkr"}]}
    block = rs.as_material(res)
    assert "3인칭" in block
    assert "저희가 해보니" in block, "1인칭 위조 금지가 재료 블록에 없다"
    assert "law.go.kr" in block, "출처가 재료에서 빠졌다(출처 없는 사실은 못 쓴다)"


def test_취재_결과가_없으면_빈칸이다():
    """없는 정보는 빈칸으로 둔다 — 지어내서 채우지 않는다."""
    assert rs.as_material({"facts": []}) == ""
    assert rs.as_material({}) == ""


def test_생성기가_취재를_실제로_쓴다():
    """★ 오늘 이미 한 번 당했다 — env를 넣고도 라우팅을 안 타서 Solar가 안 불렸다.
    '만들었다'와 '그 경로로 간다'는 다르다(헌법 2번: 사용 기준 대조)."""
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert "research" in src and "gather" in src, "취재가 생성 경로에 안 물렸다"
    assert "_fact_block" in src, "취재 재료가 프롬프트에 안 들어간다"
    # 프롬프트 조립부에 실제로 붙었는지(변수만 만들고 안 쓰는 경우 방지)
    body = src[src.find("prompt = ("):]
    assert "_fact_block" in body, "재료를 만들어놓고 프롬프트에 안 붙였다"
    assert "term_coverage" in src, "커버율이 payload에 안 남는다(전후 비교 불가)"


def test_지역명은_주제어가_아니다():
    """★ 2026-08-17 실측 결함 — '부산광역시'가 판의 언어로 뽑혔고, 그걸로 취재했더니
    '부산광역시 자동차매매사업조합'(중고차 조합)이 썬팅 글 재료로 들어왔다.
    업종 중립이라 지명을 코드에 박지 않고 행정구역 '형태'로 판정한다."""
    from app.services import marketterms as mt
    for bad in ("부산광역시", "서울특별시", "동구", "기장군", "초량동", "제주특별자치도"):
        assert not mt._usable(bad), f"지역명이 주제어로 통과했다: {bad}"
    for good in ("투과율", "열차단", "재시공", "시인성", "가시광선"):
        assert mt._usable(good), f"주제어가 막혔다: {good}"


def test_그_가게의_지역토큰도_뺀다():
    from app.services import marketterms as mt
    assert not mt._usable("부산썬팅", region="부산광역시 동구")
    assert mt._usable("부산썬팅", region="")      # 지역을 모르면 막지 않는다


def test_생성기가_지역을_넘긴다():
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert "region=_rg" in src, "지역을 안 넘겨 지역명이 주제어로 샌다"


def test_교차출현_규칙은_폐기됐다():
    """★ 실측이 반증했다 — 레이노 4 · 솔라가드 4(브랜드) vs 투과율 1(주제어).
    유명 브랜드일수록 여러 판에 나온다. 이 신호로 거르면 브랜드는 통과하고
    진짜 주제어가 죽는다. 되살리면 안 된다."""
    from app.services import marketterms as mt
    assert not hasattr(mt, "MIN_CROSS"), "반증된 교차출현 규칙이 되살아났다"


def test_재료에_없는_브랜드_언급을_잡는다():
    """코드가 '무엇이 브랜드인가'를 판별하지 않는다(불가능함이 실측으로 드러남).
    '우리 재료에 있느냐'로 가른다 — 업종을 몰라도 성립한다."""
    from app.services import marketterms as mt
    terms = ["열차단", "레이노", "솔라가드"]
    material = "글로벌리맥 XS 프리미엄 썬팅, 열차단 시공"
    body = "열차단 성능을 봤습니다. 레이노 같은 제품도 있지만 저희는 다릅니다."
    out = mt.outsider_mentions(body, terms, material)
    assert "레이노" in out, "재료에 없는 브랜드 언급을 놓쳤다"
    assert "열차단" not in out, "재료에 있는 말을 외부 브랜드로 오인했다"
    assert "솔라가드" not in out, "글에 없는 말까지 잡았다"


def test_지시문이_브랜드_언급을_금지한다(monkeypatch):
    from app.services import marketterms as mt
    monkeypatch.setattr(mt, "topic_terms", lambda *a, **k: ["열차단", "레이노"])
    d = mt.directive("썬팅업체")
    assert "브랜드는 언급 자체를 하지 마라" in d or "언급 자체를 하지 마라" in d


def test_생성기가_브랜드_게이트를_쓴다():
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert "outsider_mentions" in src, "브랜드 언급 게이트가 생성 경로에 없다"
    assert "directive" in src, "판의 언어 지시문이 프롬프트에 안 들어간다"


def test_도로명주소도_주제어가_아니다():
    """★ 2026-08-17 실물 — '중앙대로274번길'이 판의 언어로 잡혀 outsider_terms에 떴다.
    주소는 재료(고정정보 블록)에 있으니 주제어로 다룰 이유가 없다.
    업종 중립이라 특정 지명을 박지 않고 도로명 '형태'로 판정한다."""
    from app.services import marketterms as mt
    for bad in ("중앙대로274번길", "반룡산단3로", "테헤란로", "종로3가", "강남대로"):
        assert not mt._usable(bad), f"주소가 주제어로 통과했다: {bad}"
    # 과하게 막으면 진짜 주제어가 죽는다
    for good in ("열차단", "투과율", "시인성", "재시공", "유리막코팅", "차단율"):
        assert mt._usable(good), f"주제어가 막혔다: {good}"
