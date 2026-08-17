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
