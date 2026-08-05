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


def test_스케줄러에_야간_스캔이_등록돼_있다():
    """수동 호출만 되면 '사장님보다 먼저 발견'이 성립하지 않는다.
    등록이 빠지면 면역계는 있으나 마나다 — 그래서 회귀로 문다."""
    import inspect
    from app import scheduler as S
    src = inspect.getsource(S)
    assert 'id="immune_nightscan"' in src, "야간 스캔이 스케줄러에 없다"
    assert "_immune_nightscan" in src and "cron" in src, "주기 실행이 아니다"
    job = inspect.getsource(S._immune_nightscan)
    # ★ R7 — 크레딧을 먼저 보고, 없으면 수선하지 않는다
    assert "credit_out()" in job, "크레딧 잔량을 안 본다"
    assert "allow_fix=ok" in job, "크레딧이 없어도 수선을 시도한다"
    # ★ 폐루프는 배포 시점에 닫힌다 — 서버엔 git이 없어 여기서 원장을 만들 수 없다(실측: 0행)
    assert "_led.write" not in job, "서버가 원장을 덮어쓴다(0행으로 지워진다)"
    # 야간 작업이 낮 생성과 겹치지 않는다
    assert "hour=3" in src.split('id="immune_nightscan"')[0][-300:], "생성 시간대와 겹친다"


def test_R2_백업이_배포를_못_넘기면_고치지_않는다():
    """실측(2026-08-05): 백업·진단서를 상대경로 data/ 에 두었다. 컨테이너 파일시스템이라
    배포 한 번에 사라진다 — '원본 보존'이라 해놓고 지워지면 침묵 수정과 같다.
    설정 실수로도 그 일이 안 생기게 규율이 아니라 구조로 막는다."""
    import inspect
    import os
    from app.services import immune as I
    # 경로는 DB가 사는 곳(영속 볼륨)을 따른다
    old = os.environ.get("SHOPCAST_DB")
    try:
        os.environ["SHOPCAST_DB"] = "/data/shopcast.sqlite"
        assert I.data_root() == "/data", I.data_root()
        assert I.path("immune_backup") == "/data/immune_backup"
        os.environ["SHOPCAST_DB"] = ""
        assert I.data_root() == "data", "볼륨 설정이 없을 때 폴백이 없다"
        assert I.is_persistent() is False, "상대경로를 영속이라고 한다"
    finally:
        if old is None:
            os.environ.pop("SHOPCAST_DB", None)
        else:
            os.environ["SHOPCAST_DB"] = old
    # 영속이 아니면 수선하지 않는다
    src = inspect.getsource(N.run)
    assert "_persistent()" in src and "allow_fix = False" in src, \
        "백업이 안 남는 경로인데도 고친다"
    assert "탐지만 한다" in src


def test_경로_규칙은_면역계_안에서도_한_곳이다():
    """면역계가 감시하는 '경로 이원화'를 스스로 어기면 그 항체는 가짜다."""
    import inspect
    from app.services.immune import nightscan as N2, rules as RU2
    for mod in (RU2, N2):                       # 런타임 산출물 — 볼륨 단일 함수를 쓴다
        src = inspect.getsource(mod)
        assert '"data/' not in src, f"{mod.__name__}이 경로를 직접 박았다"
        assert "_ipath(" in src, f"{mod.__name__}이 단일 경로 함수를 안 쓴다"
    # 원장만은 코드 트리다(성격이 다르다) — 그 이유가 코드에 적혀 있어야 한다
    lsrc = inspect.getsource(L)
    assert "볼륨이 아니라 **코드 트리**" in lsrc, "원장이 왜 볼륨 밖인지 근거가 없다"


def test_원장은_코드트리에_살고_런타임_산출물은_볼륨에_산다():
    """실측(2026-08-05): 원장까지 볼륨으로 옮겼더니 프로덕션에서 0행이 됐다.
    원장은 git 이력에서 파생되는데 배포 이미지엔 .git이 없다(.dockerignore).
    성격이 다른 둘을 같은 곳에 두면 한쪽이 죽는다."""
    import inspect
    import os
    assert not os.path.isabs(L.LEDGER_PATH), "원장이 볼륨에 있다(프로덕션에서 못 읽는다)"
    assert "data/incidents.jsonl" in L.LEDGER_PATH
    # 런타임 산출물은 볼륨이 맞다
    src = inspect.getsource(N)
    assert "_ipath(" in src, "백업·진단서가 볼륨을 안 쓴다"
    # 배포에 실리는가 — .gitignore 예외가 없으면 이미지에 안 들어간다
    with open(".gitignore", encoding="utf-8") as f:
        gi = f.read()
    assert "!data/incidents.jsonl" in gi, "원장이 git에서 제외돼 배포에 안 실린다"
    # ★ 디렉터리를 통째로 빼면 하위 파일 예외가 안 먹는다(git 동작, 2026-08-05 실측)
    assert not any(ln.strip() == "data/" for ln in gi.split("\n")), \
        "data/ 통째 제외가 남아 있어 예외가 안 먹는다"
    import subprocess
    r = subprocess.run(["git", "check-ignore", "data/incidents.jsonl"],
                       capture_output=True, text=True)
    assert r.returncode != 0, "원장이 여전히 무시된다(배포에 안 실린다)"
    r2 = subprocess.run(["git", "ls-files", "data/incidents.jsonl"],
                        capture_output=True, text=True)
    assert r2.stdout.strip(), "원장이 git에 없다"
    # ★ git에 있어도 이미지에 안 실리면 서버는 못 읽는다(실측: Dockerfile이 app·assets만 복사했다)
    with open("Dockerfile", encoding="utf-8") as f:
        assert "COPY data" in f.read(), "원장이 배포 이미지에 안 실린다"


def test_빈_원장으로_덮어쓰지_않는다():
    """git 없는 환경에서 build()는 0행을 낸다. 그걸 쓰면 기억을 통째로 잃는다."""
    import json
    import os
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "led.jsonl")
    L.write({"rows": [{"id": "x", "confirmed": True, "cause_types": []}]}, p)
    assert len(L.read(p)) == 1
    try:
        L.write({"rows": []}, p)
        raise AssertionError("빈 원장으로 덮어썼다")
    except RuntimeError as e:
        assert "덮어쓰기 거부" in str(e)
    assert len(L.read(p)) == 1, "원장이 지워졌다"


def test_야간스캔은_원장을_갱신하지_않는다():
    """서버엔 git이 없다 — 거기서 build()를 부르면 0행을 쓴다. 갱신은 배포 시점의 일이다."""
    import inspect
    from app import scheduler as S
    job = inspect.getsource(S._immune_nightscan)
    # 규칙의 실체를 문다 — 주석에 'build()'가 적혀 있는 것은 위반이 아니다(문구가 아니라 호출)
    body = "\n".join(ln for ln in job.split("\n")
                     if ln.strip() and not ln.strip().startswith("#") and '"""' not in ln)
    assert "_led.write" not in body and "_led.build" not in body, "서버가 원장을 덮어쓴다"
    with open("scripts/safe-push.sh", encoding="utf-8") as f:
        sp = f.read()
    assert "원장 갱신" in sp and "L.build()" in sp and "data/incidents.jsonl" in sp, \
        "배포 시점에 원장을 안 싣는다"
