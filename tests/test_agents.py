"""에이전트 체제 골든 (2026-08-17 사장님 지시).

지시: "너가 계속 수정하지 말고 각각의 에이전트들한테 역할분담을 해라. 24시간 내내."
      "내가 발행하는 순간 데이터도 즉시 로직에 적용되어야 한다."
      "각각의 에이전트들이 어떻게 일을 하는지 로그로 보여줘."

무엇이 문제였나 — 이날 하루에 내가 손으로 박은 상수가 셋이고 셋 다 틀렸다:
  PER_PARA 0.7(뭉침 5곳) · CHARS_PER_PHOTO 200(문단 수 예측 실패) · MIN_PHOTOS 3(사진 17→3장)
그리고 기존 자율 시스템(lessons)은 교훈 24건이 전부 wins 0·fails 0 — 검증이 없었다.

여기서 막는 재발:
  ① 검증 없이 파라미터가 굳는 것 — 승격은 반드시 판정을 통과해야 한다
  ② 한 번에 여러 값을 바꿔 원인을 못 가리는 것
  ③ 자율 계층이 죽었을 때 생성이 멈추는 것 — 코드 기본값으로 계속 돌아야 한다
  ④ AI가 규칙(금지선)을 바꾸는 것 — L3은 영구 금지
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

import app.agents as ag
from app.agents import journal, params


def _clean(scope="test:t1"):
    from app import db
    with db._conn() as c:
        params._ensure(c)
        c.execute("DELETE FROM agent_params WHERE scope=?", (scope,))
        c.execute("DELETE FROM agent_trials WHERE scope=?", (scope,))


def test_저장소가_비어도_코드기본값으로_돈다():
    """★ 자율 계층이 죽었다고 생성이 멈추면 그게 더 큰 사고다."""
    _clean()
    assert params.get("test:없는스코프", "없는값", 0.7) == 0.7


def test_실험값이_기본값보다_우선한다():
    _clean()
    assert params.propose("test:t1", "per_para", 0.6, ag.LEARNER, "시험", 0.7)
    assert params.get("test:t1", "per_para", 0.7) == 0.6
    _clean()


def test_한번에_하나만_실험한다():
    """두 값을 동시에 바꾸면 어느 것이 효과인지 못 가른다."""
    _clean()
    assert params.propose("test:t1", "a", 1, ag.LEARNER, "첫째", 2)
    assert not params.propose("test:t1", "b", 3, ag.LEARNER, "둘째", 4), "동시 실험이 허용됐다"
    _clean()


def test_변경폭이_제한된다():
    """급격한 변경은 원인 추적을 불가능하게 한다."""
    _clean()
    params.propose("test:t1", "per_para", 99.0, ag.LEARNER, "폭주", 0.7)
    v = params.get("test:t1", "per_para", 0.7)
    assert v <= 0.7 * (1 + params.MAX_STEP) + 0.001, f"변경폭 제한이 안 걸렸다: {v}"
    _clean()


def test_검증을_통과해야_승격된다():
    """★ 핵심 — tenant_lessons 24건이 wins 0으로 쌓이던 상태의 재발 방지."""
    _clean()
    params.propose("test:t1", "per_para", 0.6, ag.LEARNER, "시험", 0.7)
    for i in range(params.PROMOTE_WINS - 1):
        assert params.judge("test:t1", f"p{i}", True) == "", "임계 전에 승격됐다"
    assert params.judge("test:t1", "plast", True) == "promoted"
    assert params.get("test:t1", "per_para", 0.7) == 0.6, "승격 후 값이 안 남았다"
    _clean()


def test_지면_실패하면_이전값으로_복귀한다():
    _clean()
    params.propose("test:t1", "per_para", 0.6, ag.LEARNER, "시험", 0.7)
    for i in range(params.RETIRE_FAILS - 1):
        params.judge("test:t1", f"f{i}", False)
    assert params.judge("test:t1", "flast", False) == "retired"
    assert params.get("test:t1", "per_para", 0.7) == 0.7, "폐기 후 이전 값으로 안 돌아갔다"
    _clean()


def test_자율등급이_코드로_강제된다():
    """문서가 아니라 계약이어야 한다. L3(코드 수정)은 어떤 에이전트에도 없다."""
    assert ag.can_tune(ag.LEARNER) and ag.can_tune(ag.EDITOR)
    assert not ag.can_tune(ag.OBSERVER), "관측이 파라미터를 바꿀 수 있다"
    assert not ag.can_tune(ag.WRITER)
    assert max(ag.LEVEL.values()) <= 2, "L3(코드 수정) 권한이 생겼다 — 금지선까지 바꿀 수 있다"


def test_일지가_남고_사람이_읽을_수_있다():
    journal.write(ag.LEARNER, "실험 제안 — per_para 0.7→0.6", why="뭉침 5곳", kind="act",
                  tenant_id="tt", piece_id="pp")
    rows = journal.recent(limit=10, tenant_id="tt")
    assert rows and rows[0]["what"].startswith("실험 제안")
    txt = journal.as_text(rows)
    assert "▶" in txt and "per_para" in txt, "로그 파일 형식이 사람이 읽을 수 없다"


def test_발행훅이_실제로_연결돼_있다():
    """★ 오늘 이미 당했다 — env를 넣고도 라우팅을 안 타서 Solar가 안 불렸다.
    '만들었다'와 '그 경로로 간다'는 다르다."""
    import inspect

    from app.services import pipesync
    src = inspect.getsource(pipesync.confirm_publish)
    assert "learner" in src and "on_publish" in src, "발행 즉시 학습이 안 걸렸다"


def test_사진상한이_학습값을_읽는다():
    import inspect

    from app.services import photocap as pc
    src = inspect.getsource(pc.cap_for)
    assert "params" in src and "per_para" in src, "사진 상한이 여전히 하드코딩만 쓴다"


def test_생성기가_tenant를_넘긴다():
    """가게마다 글 길이·리듬이 달라 하나의 상수로는 맞을 수 없다."""
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert "tenant_id=tenant.id" in src, "학습값이 가게별로 갈리지 않는다"


# ── 코드 수정 사령관 ──────────────────────────────────────
def test_사령관은_헌법과_게이트를_못_건드린다():
    """★ 파라미터는 틀려도 1건을 잃지만, 코드는 틀리면 금지선이 열린다.
    게이트를 끄는 한 줄이면 충분하고, 테스트도 같이 고치면 안 보인다."""
    from app.agents import commander as cm
    for p in ("CLAUDE.md", "docs/DISCIPLINE.md", "app/services/qualitycheck.py",
              "app/seo.py", "scripts/safe-push.sh"):
        assert cm.forbidden_hit([p]), f"금지 구역이 뚫렸다: {p}"


def test_사령관은_테스트를_못_고친다():
    """자기 검증을 무력화할 수 있으면 이 구조 전체가 무의미해진다."""
    from app.agents import commander as cm
    assert cm.forbidden_hit(["tests/test_agents.py"])
    assert cm.forbidden_hit(["tests/"])


def test_사령관은_자기_안전장치를_못_고친다():
    from app.agents import commander as cm
    assert cm.forbidden_hit(["app/agents/commander.py"])
    assert cm.forbidden_hit(["app/agents/params.py"])


def test_경로를_모르면_막는다():
    """막는 쪽이 기본값이어야 한다."""
    from app.agents import commander as cm
    assert cm.forbidden_hit([""]), "빈 경로가 통과했다"


def test_일반코드는_제안_가능하다():
    """과하게 막으면 사령관이 아무것도 못 한다."""
    from app.agents import commander as cm
    assert not cm.forbidden_hit(["app/services/photocap.py"])
    assert not cm.forbidden_hit(["app/generators/video.py"])


def test_자동적용은_기본_꺼짐():
    from app.agents import commander as cm
    import os
    assert cm.AUTO_APPLY == (os.environ.get("COMMANDER_AUTO") == "1")
    assert not cm.AUTO_APPLY, "자동 코드 수정이 기본으로 켜져 있다"


def test_금지구역_수정안은_등록조차_안된다():
    from app.agents import commander as cm
    r = cm.order("게이트 완화", "빠르게 통과시키자", ["app/services/qualitycheck.py"], "patch")
    assert not r["ok"] and r["error"] == "금지 구역"


def test_너무_넓은_수정안은_거부된다():
    """넓으면 원인 추적이 죽는다."""
    from app.agents import commander as cm
    many = [f"app/services/x{i}.py" for i in range(cm.MAX_FILES + 2)]
    r = cm.order("대공사", "리팩토링", many, "patch")
    assert not r["ok"]


def test_발행확인까지_경로가_끊기지_않는다():
    """★ 발행 확인이 학습 에이전트의 출발 신호다(pipesync.confirm_publish → learner.on_publish).
    누르는 자리가 어디든 상관없지만, **거기까지 가는 길이 끊기면** 에이전트가 영영 안 깨어난다.

    ★ 2026-08-18 — 전에는 대시보드 카드에 발행 줄(_publish_row·pubDone)이 있었다.
      사장님 지시로 목록이 '날짜 + 제목'이 되면서 그 카드가 사라졌다.
      지우기 전에 확인했다 — 발행 확인 UI는 /kit/{asset}/naver 에 온전히 있다.
      그래서 검사를 '대시보드에 버튼이 있는가'에서 **'경로가 이어지는가'**로 바꾼다.

        홈 목록(제목) → /me?view={asset} 미리보기 → /kit/{asset}/naver 발행 확인
    """
    import inspect

    from app import main
    src = inspect.getsource(main)
    # ① 목록의 제목이 미리보기로 간다
    dash = inspect.getsource(main.my_dashboard)
    assert "/me?view=" in dash, "목록에서 미리보기로 가는 길이 없다"
    # ② 미리보기에 발행 화면으로 가는 버튼이 있다
    res = inspect.getsource(main._result_html)
    assert "/kit/" in res and "naver" in res, "미리보기에서 발행 화면으로 가는 길이 없다"
    # ③ 발행 화면에 확인 수단이 있다(자동 감지 + 주소 붙여넣기 폴백)
    assert "/me/blog/published" in src and "check-published" in src, \
        "발행 확인이 실제 엔드포인트로 안 간다"


def test_발행_버튼이_화면에_실제로_붙는다():
    """만들어놓고 안 붙이면 화면에 안 나온다 — 그 실수를 실제로 했다.

    ★ 2026-08-18 — 발행 버튼이 대시보드 카드에 있었는데 목록이 '날짜+제목'으로
      정리되면서 카드가 사라졌다. 지우기 전에 확인했다: 발행 버튼은 미리보기에
      이미 있었다(naver_btn). 그래서 카드 쪽만 걷어냈고, 검사는 미리보기에서 한다.
      **발행 확인이 학습 에이전트의 출발 신호**라 이 버튼이 묻히면 에이전트가 안 깨어난다.
    """
    import inspect

    from app import main
    src = inspect.getsource(main._result_html)
    assert "naver_btn" in src, "발행 버튼을 만들지 않았다"
    i = src.find("naver_btn = ")
    assert i > 0
    seg = src[i:i + 2000]
    # ★ 2026-08-18 — 발행이 두 갈래가 됐다.
    #   ① 자동: 확인만 하면 로컬 에이전트가 가게 계정으로 올린다(주 경로)
    #   ② 손으로: 자동이 막히거나 직접 손보고 싶을 때(보조). 자동은 실패할 수 있으니 없애지 않는다.
    assert "/me/publish/" in seg, "자동 발행 경로가 없다"
    assert "/kit/" in seg, "손으로 올리는 길이 사라졌다 — 자동이 막히면 발행 자체가 막힌다"
    # 만들기만 하고 안 붙이는 것이 바로 그 실수다 — 실제로 카드에 조립되는지 본다
    used = [ln for ln in src.splitlines()
            if "naver_btn" in ln and "naver_btn = " not in ln.strip()]
    assert used, "발행 버튼을 만들어놓고 화면에 안 붙였다"


def test_생성_과정도_일지에_남는다():
    """★ 사장님 지시: "각각의 에이전트들이 어떻게 일을 하는지 로그로 보여줘."
    발행해야만 기록이 생기면 생성 과정이 통째로 깜깜하다 —
    사장님이 아침에 열었을 때 볼 것이 있어야 한다."""
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert "journal" in src, "생성 경로에서 일지를 안 쓴다"
    assert "SCOUT" in src and "RESEARCH" in src and "EDITOR" in src, \
        "정찰·취재·편집 중 일지를 안 남기는 에이전트가 있다"


def test_일지_실패가_생성을_막지_않는다():
    """기록이 본체를 죽이면 그게 더 큰 사고다."""
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    i = src.find("from app.agents import RESEARCH")
    assert i > 0
    seg = src[i:i + 1400]
    assert "except Exception" in seg, "일지 기록에 예외 보호가 없다"


def test_완료했는데_산출물이_없으면_잡는다():
    """★ 2026-08-17 — 진행률 done 1.0인데 글 0건인 일이 세 번 있었다.
    원인은 ① 크레딧 전면차단 ② 배포가 진행 중 스레드를 죽인 것.
    완료라고 말하면서 아무것도 안 만든 것을 그대로 두면 다음 사람이 또 속는다."""
    import inspect

    from app import main
    src = inspect.getsource(main.admin_gen_progress)
    assert "ghost" in src, "유령 완료(done인데 산출물 0)를 감지하지 않는다"
    assert "스레드 사망" in src or "글이 없다" in src


def test_골든을_못돌리면_이상없음이라고_하지_않는다(monkeypatch):
    """★ 2026-08-17 실측 결함 — 골든 실행이 600초 타임아웃으로 죽었는데
    signals=0을 반환했고, 일지에 "이상 없음 — 골든 전체 통과"가 찍혔다.
    **거짓 안심**이다. 못 돌린 것과 통과한 것은 다르다(침묵 폴백 금지)."""
    import subprocess

    from app.agents import commander as cm

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0] if a else "pytest", 60)

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(cm.journal, "recent", lambda **k: [])
    sigs = cm.scan()
    kinds = {s["kind"] for s in sigs}
    assert "golden_unknown" in kinds, "골든을 못 돌렸는데 신호가 없다(이상 없음으로 둔갑)"
    why = next(s["why"] for s in sigs if s["kind"] == "golden_unknown")
    assert "모른다" in why or "확인 못" in why


def test_비정상종료도_신호가_된다(monkeypatch):
    import subprocess

    from app.agents import commander as cm

    class _R:
        returncode = 2
        stdout = "collection error"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    monkeypatch.setattr(cm.journal, "recent", lambda **k: [])
    assert any(s["kind"] == "golden_unknown" for s in cm.scan()), \
        "골든이 비정상 종료했는데 통과로 봤다"
