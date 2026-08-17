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
