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


def test_빈자리는_글감_큐로_이어진다():
    """목록만 나오면 사장님이 제목을 옮겨 적어야 한다 — 그럼 노동이 는다."""
    import inspect
    from app.services.vacantq import feed as FD
    src = inspect.getsource(FD.feed)
    assert "enqueue_writing" in src, "큐에 안 넣는다"
    assert "reason" in src and "top_titles" in src, "왜 빈자리인지 근거를 안 남긴다"
    assert "cap" in src, "주당 상한이 없다(큐가 넘치면 안 본다)"


def test_같은_질문을_두_번_쓰지_않는다():
    """우리 글끼리 부딪힌다."""
    import inspect
    from app.services.vacantq import feed as FD
    assert "already_ours" in inspect.getsource(FD.feed)
    src = inspect.getsource(FD.already_ours)
    assert "blog_id" in src and "writing_queue_rows" in src


def test_선점_검증이_가설을_자동으로_확인한다():
    """'빈 자리에 쓰면 뜬다'가 맞는지 따로 실험할 필요가 없어야 한다."""
    import inspect
    from app.services.vacantq import feed as FD
    src = inspect.getsource(FD.verify_claims)
    assert "our_rank" in src and "선점 성공" in src
    assert "days" in src, "며칠 지났는지를 안 본다"
    assert "PLACE_JS" in src, "공통 파서를 안 쓴다"
    # 시도 기록이 남아야 나중에 대조할 수 있다
    assert "was_vacant" in inspect.getsource(FD._claim)


def test_야간에_다시_훑는다():
    """빈자리는 시간이 지나면 남이 채운다."""
    import inspect
    from app import scheduler as S
    src = inspect.getsource(S)
    assert 'id="vacantq_nightly"' in src, "야간 실행이 등록되지 않았다"
    job = inspect.getsource(S._vacantq_nightly)
    assert "feed" in job and "verify_claims" in job, "훑기만 하고 큐·검증을 안 한다"
    assert "PRODUCTION_TENANTS" in job, "실계정 대상이 아니다"


def test_하는_일과_무관한_질문은_글감이_아니다():
    """실물 사고(2026-08-06): '오늘 부산 날씨'가 썬팅집 글감 큐에 들어갔다.
    region이 '부산광역시 동구'라 '부산'이 안 걸러졌고, 그게 '하는 일'로 잡혔다."""
    from app.services.vacantq import suggest as SG
    rows = [{"q": "오늘 부산 날씨"}, {"q": "부산 썬팅 가격"}, {"q": "썬팅 농도 법"}]
    out = SG.relevant(rows, ["썬팅", "유리막코팅"])
    assert [r["q"] for r in out] == ["부산 썬팅 가격", "썬팅 농도 법"], out
    assert SG.relevant(rows, []) == [], "하는 일을 모르면 전부 통과시킨다"
    # 지역은 부분 문자열까지 막는다
    mats = {"anchors": [], "titles": ["부산 동구 썬팅 후기", "부산광역시 동구 썬팅 시공"]}
    assert "부산" not in F.work_terms(mats, "부산광역시 동구")
    # 두 경로가 모두 이 게이트를 탄다(존재가 아니라 사용)
    import inspect
    from app import main as m, scheduler as s
    assert "_sg.relevant(" in inspect.getsource(m.admin_vacantq_feed)
    assert "_sg.relevant(" in inspect.getsource(s._vacantq_nightly)


def test_시뮬레이션은_진짜_그_함수를_부른다():
    """흉내는 진짜가 아니다 — 별도 코드로 흉내 내면 스케줄러 경로 문제를 못 잡는다."""
    import inspect
    from app import main as m
    src = inspect.getsource(m.admin_vacantq_simulate)
    assert "from app.scheduler import _vacantq_nightly" in src, "스케줄러 함수를 안 쓴다"
    assert "_vacantq_nightly()" in src
    # 실행 기록이 남아야 아침에 확인된다
    from app import scheduler as s
    assert "_vacantq_run_log" in inspect.getsource(s._vacantq_nightly), "결과를 안 남긴다"
    for k in ("n_vacant", "n_queued", "claims_won"):
        assert k in inspect.getsource(s._vacantq_nightly), f"기록 항목 누락: {k}"


def test_플랫폼을_찾는_질문은_우리_글감이_아니다():
    """실물 사고(2026-08-06): 주안모터스 글감에 '중고차사이트추천'이 들어갔다.
    이건 엔카·KB차차차를 찾는 사람이지 기장에서 차를 사려는 손님이 아니다 —
    지역 업체가 그 글을 써도 답이 될 수 없다."""
    from app.services.vacantq import suggest as SG
    rows = [{"q": "중고차사이트추천"}, {"q": "믿을만한중고차사이트"}, {"q": "중고차 순위"},
            {"q": "중고차 앱 추천"}, {"q": "기장 중고차 시세"}, {"q": "중고차 실매물 확인"}]
    got = [r["q"] for r in SG.relevant(rows, ["중고차"])]
    assert got == ["기장 중고차 시세", "중고차 실매물 확인"], got
    assert SG.is_platform_seek("중고차 비교사이트") and not SG.is_platform_seek("기장 중고차")


def test_수치는_질문_씨앗이_아니다():
    """'216km 중고차 얼마나 걸리나요'는 헛질문이다.
    본문에서는 살아야 할 정보지만(주행거리) 검색어의 축은 아니다."""
    from app.services.vacantq import suggest as SG
    seeds = SG.seeds_for(["중고차"], "부산 기장", ["216km", "토레스", "30만원", "7km"])
    assert not [s for s in seeds if "km" in s or "만원" in s], seeds
    assert "토레스 중고차" in seeds
