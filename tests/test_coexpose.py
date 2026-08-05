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
