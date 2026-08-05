"""🛡 면역계 골든 — 항체가 가짜가 되지 않게 지키는 규칙들.

면역계 자체가 사고를 만들 수 있다. 이 테스트가 무는 것은 기능이 아니라 **규율**이다.
"""
import inspect
import time

from app.services.immune import ledger as L
from app.services.immune import nightscan as N
from app.services.immune import prediag as P
from app.services.immune import report as R
from app.services.immune import rules as RU


def test_R1_원장은_짐작을_확정으로_적지_않는다():
    """원인 유형은 커밋 본문이 스스로 말한 문구를 근거로 인용할 때만 확정한다.
    근거 없이 유형을 붙이면 그게 날조된 항체다."""
    types, ev = L.classify("같은 규칙이 두 곳에 살아 한쪽만 고쳤다")
    assert "경로 이원화" in types and ev and ev[0]["signs"], "근거 문구를 안 남긴다"
    # 근거가 없으면 분류하지 않는다 — '모른다'가 정직한 상태다
    assert L.classify("사진 순서를 바꿨다")[0] == [], "근거 없이 유형을 붙인다"
    src = inspect.getsource(L.extract_from_git)
    assert "근거 없는 것은 짐작하지 않는다" in src, "짐작 배제 규율이 없다"


def test_R1_해시_없는_사고는_구전이다():
    """커밋으로 확인 안 되는 사고는 confirmed=False이고, 재발 집계에서 빠진다."""
    rows = [{"confirmed": True, "cause_types": ["침묵 폴백"]},
            {"confirmed": False, "cause_types": ["침묵 폴백"]}]
    assert L.recurrence(rows) == {"침묵 폴백": 1}, "구전을 재발로 센다"
    assert L.commit_exists("") is False
    assert L.commit_exists("0" * 40) is False, "없는 해시를 있다고 한다"


def test_R3_기본은_경고이고_차단은_재발_2회_이상만():
    """오탐 축적으로 검진을 끄게 만드는 것이 최악의 결말이다."""
    assert P.BLOCK_MIN_RECURRENCE == 2
    rows = [{"id": "a1", "confirmed": True, "cause_types": ["게이트 사각"]}]
    diff = "+@app.get('/x')\n+def x():\n+    return 1\n"
    res = P.inspect(diff, rows)
    got = [f for f in res["findings"] if f["cause"] == "게이트 사각"]
    assert got and got[0]["severity"] == "경고", "1회 유형인데 차단한다"
    assert not res["blocked"]
    rows2 = rows + [{"id": "a2", "confirmed": True, "cause_types": ["게이트 사각"]}]
    assert P.inspect(diff, rows2)["blocked"], "2회 재발인데 차단하지 않는다"


def test_R3_오탐을_부르는_규칙은_좁게_문다():
    """소급 검진 실측(2026-08-05): 긴 문자열을 다 보다가 코드 조각(' 기준')을 잡았다.
    경로 이원화의 실제 모양은 규칙이 정규식으로 복제되는 것이다."""
    src = inspect.getsource(RU)
    assert "_RE_META" in src, "정규식다움 판정이 없다"
    assert "줄을 가로지르지 않는다" in src, "diff 줄을 가로질러 매칭한다"
    calls = []
    ctx = {"diff": '+    txt = "이 문장은 그냥 산문입니다 정말로요"\n', "grep": lambda s: calls.append(s) or []}
    assert RU._r_path_dup(ctx) == [], "산문을 규칙 복제로 잡는다"
    assert not calls, "산문까지 grep한다(비용·오탐)"


def test_R4_무탐지_규칙은_강등된다():
    """규칙의 단조 증가로 스캔이 비대해지는 것을 막는다."""
    now = time.time()
    old = now - (RU.RETIRE_DAYS + 1) * 86400
    assert RU.frequency("x", {"x": {"last_hit": old}}, now) == "weekly"
    assert RU.frequency("x", {"x": {"last_hit": now - 3600}}, now) == "daily"


def test_R7_자동수정은_무비용_기계수선만():
    """야간 전량 재생성이 크레딧을 말려 아침 생성을 죽이는 것이 면역계가 만드는 새 사고다."""
    src = inspect.getsource(N)
    assert "credit_out()" in src, "크레딧 잔량을 안 본다"
    fsrc = inspect.getsource(N._fix_free)
    for banned in ("write_captions", "score_gate", "generate", "llm.call", "_call_llm"):
        assert banned not in fsrc, f"자동 수선이 비싼 경로를 부른다: {banned}"
    assert "fix_orphan_parens" in fsrc, "무비용 수선이 없다"
    # 재생성·코드 수정은 진단서로 대기 — 자동 실행 금지
    assert "사람 승인 필요" in inspect.getsource(N._diagnose)


def test_R2_수정은_보존과_diff가_전제다():
    """diff 없는 침묵 수정은 그 자체가 사고 유형이다."""
    src = inspect.getsource(N._fix_free)
    assert "_backup(" in src and "보존이 먼저다" in src, "원본 보존 없이 고친다"
    assert "unified_diff" in src, "전후 diff를 안 남긴다"
    assert src.index("_backup(") < src.index("update_piece_payload"), "저장이 보존보다 먼저다"


def test_R5_지표에는_분모가_있다():
    """절대 건수는 아무 뜻이 없다 — 변경 커밋 100건당으로 정규화해야 비교가 성립한다."""
    rows = [{"at": int(time.time()), "found_by": "사용자"}]
    m = R.monthly(rows, months=1)[0]
    assert "commits" in m and "per100" in m, "분모가 없다"
    assert m["per100"] is None or isinstance(m["per100"], float)
    # 기준선은 추측 위에 서지 않는다
    assert R.BASELINE_NOTE["confidence"] == "추정(미확정)", "기준선을 확정으로 적었다"


def test_R8_검진은_LLM에게_판정을_묻지_않는다():
    """LLM에 '이게 경로 이원화냐'고 물으면 오탐이 검진을 끄게 만든다."""
    for mod in (RU, P):
        src = inspect.getsource(mod)
        for banned in ("llm.call", "_call_llm", "messages.create", "anthropic"):
            assert banned not in src, f"{mod.__name__}이 판정을 LLM에 맡긴다: {banned}"


def test_R6_본체_경로를_고치지_않는다():
    """면역계는 관측·검진 레이어다. 생성·발행 경로를 건드리면 그 자체가 사고다."""
    for mod in (L, RU, P, R):
        src = inspect.getsource(mod)
        for banned in ("save_piece", "update_piece_payload", "publish", "generate_for"):
            assert banned not in src, f"{mod.__name__}이 본체를 쓴다: {banned}"
    # 야간 스캔만 예외적으로 payload를 고치되, 무비용 수선 + 보존 절차 안에서만
    assert "update_piece_payload" in inspect.getsource(N._fix_free)


def test_기계로_못_잡는_것은_못_잡는다고_적는다():
    """의미 판정·런타임 경합은 정적 검진 밖이다. 숨기면 '다 잡는다'는 착각을 준다."""
    assert "식별자 혼동" in RU.UNDETECTABLE and "세션 간 덮어쓰기" in RU.UNDETECTABLE
    d = RU.derive_for("식별자 혼동", {})
    assert "기계검출불가" in d["status"], d
    assert "야간 스캔" in d["status"]


def test_사고_1회는_항체_1개다():
    """신규 사고 유형이 들어오면 검진 항목이 파생된다(없으면 대기로라도 남는다)."""
    st = {}
    assert RU.derive_for("경로 이원화", st)["status"] == "이미 있음"
    r = RU.derive_for("새로운유형", st)
    assert "대기" in r["status"] and "_pending" in st, "새 유형이 흔적 없이 사라진다"
