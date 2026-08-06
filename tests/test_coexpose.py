"""🏪 동시 노출 역설계 골든 — 프레임이 오염되지 않게.

★ 핵심 규율: 발행 이력은 **구조 인자가 아니라 반증 축**이다.
  인자에 넣는 순간 "꾸준해서 떴다"가 답이 되어 프레임이 무너진다.
"""
import inspect

from app.services.coexpose import collector as C
from app.services.reverse import surfaces as S


def test_R3_플레이스와_글이_갈린다():
    """플레이스 업체에는 data-template-id가 없다 — ID URL로 식별한다.
    UI 링크(launchApp·place/my)는 ID 패턴이 아니라 자동으로 빠진다."""
    src = S.PLACE_JS
    assert "place" in src and "d{6,}" in src, "업체 ID 패턴이 없다"
    v = S.coexpose_verify({"places": [{"id": "123456", "name": "가게"}],
                           "posts": [{"kind": "blog", "blog": "b", "post": "999999"}]})
    assert v["coexposed"] is True and v["ok"] is True
    assert v["evidence"]["place_ids"] and v["evidence"]["posts"], "근거를 안 남긴다"


def test_R6_한쪽만_떴으면_동시노출이_아니다():
    """추측으로 라벨을 붙이지 않는다 — 같은 화면에 둘 다 떠야 동시노출이다."""
    assert S.coexpose_verify({"places": [{"id": "1"}], "posts": []})["coexposed"] is False
    assert S.coexpose_verify({"places": [], "posts": [{"blog": "b", "post": "1"}]})["coexposed"] is False
    assert S.coexpose_verify({"places": [], "posts": []})["ok"] is False


def test_발행이력은_반증축이지_구조인자가_아니다():
    """★ 프레임 보호 — 발행 빈도를 구조 인자에 넣으면 '꾸준해서 떴다'가 답이 된다."""
    src = inspect.getsource(C.crank_check)
    assert "반증 축" in src and "프레임 오염 금지" in src, "축 구분이 문서화되지 않았다"
    # 대조군 없이 인과를 말하지 않는다
    assert "대조군 없이 말할 수 없다" in src, "구조가 원인이라고 단정한다"
    r = C.crank_check([])
    assert r["verdict"] == "측정 실패 — 판정 불가", "빈 표본에 판정을 낸다"


def test_반증_사례가_없으면_없다고_말한다():
    """프레임을 데이터가 지지 안 하면 그렇게 말한다(R5 정직)."""
    src = inspect.getsource(C.crank_check)
    assert "이 표본에선 C-RANK 반증 사례 없음" in src, "반증 없을 때 표기가 없다"
    assert "measured" in src and "failed" in src, "측정 실패를 숨긴다"


def test_R1_R2_공개_피드만_사람_속도로():
    """RSS는 공개 피드다. 로그인·조작 없음, 채널 사이 간격 유지."""
    src = inspect.getsource(C.rss_history)
    assert "rss.blog.naver.com" in src, "공개 피드를 안 쓴다"
    for banned in ("login", "cookie", "NID_AUT", "session"):
        assert banned not in src.lower(), f"금지 행위: {banned}"
    assert "sleep" in inspect.getsource(C.crank_check), "연속 호출에 간격이 없다"
    # 수집기도 차단 시 즉시 중단·재시도 금지
    csrc = inspect.getsource(C.collect)
    assert "Blocked" in csrc and "break" in csrc and "재시도 금지" in csrc


def test_R4_파서를_복제하지_않는다():
    """파싱은 reverse.surfaces, 브라우저는 scout.session 하나씩만 쓴다."""
    src = inspect.getsource(C)
    assert "chromium.launch" not in src, "브라우저를 따로 연다"
    assert "_sf.PLACE_JS" in src, "공통 파서를 안 쓴다"
    # 검색 화면 파싱은 surfaces가 전담한다 — collect 안에 자체 셀렉터가 있으면 복제다
    assert "querySelectorAll" not in inspect.getsource(C.collect), "수집이 파싱을 복제한다"


def test_R8_원본을_보존한다():
    src = inspect.getsource(C.collect)
    assert '"a"' in src, "수집 원본을 덮어쓴다"
    for k in ("industry", "region", "evidence"):
        assert f'"{k}"' in src, f"역추적 키 누락: {k}"


def test_브리핑_자산은_삭제되지_않았다():
    """봉인은 삭제가 아니다 — 정보성 콘텐츠용으로 재개 가능해야 한다(R9)."""
    from app.services.reverse import contrast, pipeline, surfaces
    assert hasattr(surfaces, "BRIEF_JS") and hasattr(pipeline, "run")
    assert hasattr(contrast, "compare")
    with open("docs/HANDOVER.md", encoding="utf-8") as f:
        doc = f.read()
    assert "브리핑 역설계 — 보류" in doc, "보류 상태가 문서에 없다"
    assert "재개 가능" in doc, "재개 가능 표기가 없다"


def test_대조군은_같은_채널_같은_주제다():
    """축을 두 번 바꿨다: ①동시노출 여부(대조군 없음) ②2페이지 밖(URL 페이징이 안 먹음)
    → ③같은 채널의 같은 주제 글. 채널 파워·발행 이력이 상수로 통제된다."""
    import inspect
    from app.services.coexpose import control as CT
    src = inspect.getsource(CT)
    assert "상수로 통제" in src, "왜 이 대조군인지 근거가 없다"
    assert "폐기" in src and "불가" in src, "버린 축의 이유가 기록되지 않았다"
    r = CT.build("부산 동구 썬팅업체",
                 [{"blog": "x", "post": "1", "title": "t"}])
    assert r["picked"] and "control" in r
    # 뽑힌 글이 대조군에 섞이면 안 된다
    keys = {(c["blog"], c["post"]) for c in r["control"]}
    assert ("x", "1") not in keys


def test_한_업종_신호는_인자가_아니다():
    """업종 교차 검증 — 한 업종에만 나오는 신호는 잡음이다(R5·업종 중립)."""
    import inspect
    from app.services.coexpose import pipeline as PP
    src = inspect.getsource(PP.analyze)
    assert "cross_industry" in src and "단일 업종 신호(잡음 의심)" in src, "교차 검증이 없다"
    assert "len(hits) >= 2" in src, "한 업종만으로 인자를 채택한다"
    # 발행 이력은 인자 목록에 없다(대조군이 이미 통제한다)
    fsrc = inspect.getsource(__import__("app.services.coexpose.features",
                                        fromlist=["x"]).measure)
    for banned in ("per_month", "history", "발행"):
        assert banned not in fsrc, f"발행 이력이 구조 인자에 섞였다: {banned}"


def test_라벨은_인자에서_제외된다():
    """picked를 인자에 넣으면 자기 자신을 설명한다."""
    import inspect
    from app.services.coexpose import pipeline as PP
    assert 'k != "picked"' in inspect.getsource(PP.analyze), "라벨이 인자에 들어간다"


def test_실운영_업종은_수집_자체가_막힌다():
    """썬팅·중고차는 사장님 실운영 업종이라 편향이 낀다 — 규율이 아니라 구조로 막는다."""
    import inspect
    from app.services.coexpose import scope as SC
    assert SC.is_excluded("부산 동구 썬팅업체")
    assert SC.is_excluded("", "자동차시공", "")
    assert SC.is_excluded("부산 기장 중고차 추천")
    assert not SC.is_excluded("강남 미용실 추천")
    assert not SC.is_excluded("수원 영통 치과 추천")
    ok, dropped = SC.filter_queries([{"q": "강남 미용실 추천", "industry": "미용"},
                                     {"q": "부산 동구 썬팅업체", "industry": "자동차시공"}])
    assert len(ok) == 1 and len(dropped) == 1
    # 수집기가 실제로 이 필터를 탄다(존재가 아니라 사용)
    src = inspect.getsource(C.collect)
    assert "_sc.filter_queries" in src, "수집이 제외 규칙을 안 탄다"
    assert "영구 제외" in src, "제외 사유를 안 남긴다"
