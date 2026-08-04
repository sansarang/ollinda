"""순위 분석의 정직성 — 2026-08-05 실물 사고 3건 박제.

사장님 화면에 나간 분석문:
  "사장님 글은 품질점수 88점에 2941자로 … 사진도 4장 들어가 있어서"   ← 실제 20장
  "사실 상위 글들과 비교해도 구조상 뚜렷하게 부족한 점은 안 보입니다"  ← 비교 데이터 없음
  상단 '1일차' / 추적 카드 '0일차'                                    ← 같은 화면 두 값
"""
import inspect

from app import seo
from app.services import analyst as an
from app.services import race, whynot


def test_A1_사진_수는_마커가_아니라_실물이다():
    """본문 '[사진N]' 마커는 '본문에 몇 장 걸었나'이지 사진 수가 아니다.
    생성은 상위 선별분만 마커로 넣어서, 20장짜리 글이 4장으로 세어졌다.
    되돌리면(count("[사진")) 이 테스트가 실패한다."""
    pl = {"image_paths": ["p%d.jpg" % i for i in range(20)],
          "body": "본문 [사진1] 어쩌고 [사진2] 저쩌고 [사진3] 또 [사진4] 끝"}
    assert seo.photo_count(pl) == 20, "실제 사진 수를 안 센다"
    assert seo.photo_count({}) == 0
    # 세는 곳은 하나뿐이다 — 두 곳이 각자 세어서 이 사고가 났다
    for mod in (seo, an):
        src = inspect.getsource(mod)
        assert 'count("[사진")' not in src, f"{mod.__name__}이 마커를 사진 수로 센다"
    assert "photo_count(payload)" in inspect.getsource(seo.quality_audit), \
        "품질 채점이 실물 사진 수를 안 쓴다"
    assert "seo.photo_count(pl)" in inspect.getsource(an), "순위 분석이 실물 사진 수를 안 쓴다"


def test_A2_모르는_것으로_비교하지_않는다():
    """상위 글 데이터에는 사진·글자수·품질점수가 없다. 그런데 '비교해보니 부족하지 않다'고 썼다.
    비교할 데이터가 없는데 비교 결론을 낸 것 — 허위 양성보다 미표시가 낫다."""
    my = {"rank": None, "days": 1, "title": "t", "audit": 88, "chars": 2941,
          "photos": 20, "power": {"posts_4w": 3}}
    top = [{"rank": 1, "title": "a", "desc": "d", "blogger": "b", "blog_id": "x",
            "postdate": "20260427", "power": {"posts_4w": 9}}]
    known, blind = an.comparable_axes(my, top)
    assert "photos" in blind and "audit" in blind and "chars" in blind, \
        f"상위 글에 없는 축을 비교 가능으로 본다: {known}"
    # 같은 축을 다른 이름으로 담았다고 비교 불가로 몰면 정당한 비교까지 잘린다
    assert "age" in known, "발행 시점(days≡postdate)을 비교 불가로 본다"

    txt = ("사장님 글은 사진 20장이 들어가 기본기가 탄탄합니다. "
           "사실 상위 글들과 비교해도 구조상 뚜렷하게 부족한 점은 안 보입니다. "
           "상위 글은 평균 81일 전 발행이라 신선도에선 사장님이 우위입니다. "
           "상위 글보다 품질점수가 높아 유리합니다.")
    out, dropped = an.strip_blind_compare(txt, known)
    assert "구조상 뚜렷하게 부족한 점은 안 보입니다" not in out, "근거 없는 비교가 통과한다"
    assert "품질점수가 높아 유리" not in out, "상위 글 점수를 모르는데 우열을 말한다"
    assert "신선도에선 사장님이 우위" in out, "정당한 비교(발행 시점)까지 잘린다"
    assert "사진 20장이 들어가" in out, "비교가 아닌 내 글 소개까지 잘린다"
    assert len(dropped) == 2, dropped
    # 한국어 문장 분리 — '~보다 '의 '다'에서 자르면 문장이 부서진다
    assert "상위 글보다" not in out, "잘린 조각이 남는다"


def test_A3_일차는_한_곳에서만_센다():
    """같은 화면에 '1일차'와 '0일차'가 동시에 떴다.
    whynot은 KST 달력, race는 UTC 경과시간(.days)으로 각자 셌다.
    같은 것을 세는 코드가 두 곳에 살면 반드시 갈라진다."""
    for ts in ("2026-08-04T09:30:00", "2026-08-04T23:50:00", "2026-07-01T00:00:00", ""):
        assert race._days_since(ts) == whynot._days_since(ts), f"두 계산이 갈린다: {ts}"
    src = inspect.getsource(race._days_since)
    assert "utcnow() - datetime.fromisoformat" not in src, "race가 자체 계산을 들고 있다"
    assert "whynot" in src, "단일 계산을 안 쓴다"


def test_A4_버린_문장은_조용히_사라지지_않는다():
    """근거 없는 비교를 지웠으면 무엇을 왜 지웠는지 남는다 — 조용한 수정은 그 자체가 사고 유형이다."""
    src = inspect.getsource(an)
    assert "근거 없는 비교" in src and "_log.warning" in src, "제거 사유가 안 남는다"
    # 전부 걸러졌을 때 빈칸이 아니라 '아는 범위'를 밝힌다
    assert "우리가 아는 것은 제목·발행일·블로그 체급뿐" in src, "빈칸만 남기고 이유를 안 말한다"
