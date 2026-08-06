"""🎯 빈 질문 선점 골든 — 빈자리이기만 하면 소용없다."""
import inspect

from app.services.vacantq import finder as F
from app.services.vacantq import scan as S


def test_판정은_실제_검색_화면_근거다():
    """추측하지 않는다 — 상위 글 제목에 질문 토큰이 대부분 있으면 답한 것으로 본다."""
    q = "EV6 전면 썬팅 얼마나 걸리나요"
    assert F.is_answered(q, [{"title": "EV6 전면 썬팅 얼마나 걸리나요 시공 시간", "blog": "a", "post": "1"}])["answered"]
    assert not F.is_answered(q, [{"title": "부산 맛집 추천", "blog": "b", "post": "2"}])["answered"]
    # 일부만 겹치는 것은 답이 아니다 — 그냥 같은 업종 글이다
    r = F.is_answered(q, [{"title": "EV6 썬팅 후기", "blog": "c", "post": "3"}])
    assert not r["answered"] and 0 < r["best"] < 0.7
    # 근거를 남긴다
    src = inspect.getsource(S.scan)
    assert "top_titles" in src, "왜 비었다고 봤는지 근거가 없다"


def test_하는_일에서_지역어_형식어를_뺀다():
    """빈도만 세면 '동구·부산·후기'가 하는 일로 잡힌다."""
    mats = {"anchors": ["EV6"],
            "titles": ["부산 동구 썬팅업체 EV6 신차썬팅·유리막코팅 전과정 후기",
                       "부산광역시 동구 썬팅 추천, 유리막코팅 시공 후기",
                       "부산 동구 썬팅업체 후기, 유리막코팅 시공"]}
    w = F.work_terms(mats, "부산 동구")
    for bad in ("동구", "부산", "후기", "추천", "EV6"):
        assert bad not in w, f"형식어·지역어·실값이 하는 일로 잡혔다: {bad}"
    assert "유리막코팅" in w
    # 기존 목록을 재사용한다(사본 금지)
    assert "INTENT_WORDS" in inspect.getsource(F._noise_words)


def test_하는_일을_모르면_질문을_만들지_않는다():
    """날조 금지 — 재료가 없으면 없는 대로 둔다."""
    assert F.candidates({"anchors": ["EV6"], "titles": []}) == []


def test_빈자리여도_수요가_없으면_소용없다():
    """'EV6 기아 얼마나 걸리나요'는 비어 있는 게 당연하고 써도 아무도 안 온다."""
    src = inspect.getsource(S.with_demand)
    assert "keyword_volumes" in src, "검색량을 안 본다"
    assert "min_volume" in src and "수요 부족" in src
    # ★ 조회 실패를 0으로 단정하지 않는다(정직 게이트)
    assert "수요 미확인" in src and "버리지 않는다" in src


def test_R4_파서를_복제하지_않는다():
    src = inspect.getsource(S)
    assert "chromium.launch" not in src, "브라우저를 따로 연다"
    assert "_sf.PLACE_JS" in src, "공통 파서를 안 쓴다"
    assert "querySelectorAll" not in inspect.getsource(S.scan)


def test_차단되면_멈춘다():
    src = inspect.getsource(S.scan)
    assert "Blocked" in src and "break" in src
    assert "retry" not in src.lower()
