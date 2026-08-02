"""
빈자리 선점 엔진 1단계 계약 박제(2026-08-02 사장님 승인 — 판정만, 읽기 전용).

핵심 원칙 두 개를 못 박는다:
  A. 제안 자격 = 빈자리(검색 실측) ∩ 사장님 영역(실데이터).
     실사진·실경험 없는 주제를 시키는 것은 날조 유도다 — 근거 0이면 '미지'이고 제안하지 않는다.
  B. 낡은 지도로 '빈자리'를 주장하지 않는다 — 허위 양성보다 미표시가 낫다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app import db
from app.services import gapscout as gs


# ── A. 영역 판정 — 근거 없는 것은 제안하지 않는다 ────────────────
DOM = {"tokens": {"팰리세이드", "썬팅", "신차패키지"},
       "sources": {"팰리세이드": "과거 세트", "썬팅": "발행 이력", "신차패키지": "실경험 Q&A"}}
AXES = [{"axis": "차종", "tokens": ["팰리세이드", "GV80", "쏘렌토", "모닝"]},
        {"axis": "시공", "tokens": ["신차패키지", "유리막코팅", "PPF"]}]


def test_unknown_domain_is_never_proposed():
    """A1. 근거가 0이면 '미지'다. 여기서 새 글을 시키면 사장님이 겪지도 않은 일을 쓰게 된다."""
    for kw in ("제주 감귤 배송", "강남 필라테스 등록"):
        d, why = gs.classify(kw, DOM, AXES, set())
        assert d == "미지", f"{kw} → {d}({why})"
        assert "근거" in why


def test_certain_domain_cites_its_source():
    """A2. '확실'은 어느 실데이터에서 왔는지 밝힌다 — 근거 없는 확신은 추측이다."""
    d, why = gs.classify("부산 팰리세이드 썬팅", DOM, AXES, set())
    assert d == "확실"
    assert "팰리세이드" in why and "과거 세트" in why, why


def test_adjacent_requires_same_axis_and_own_value():
    """A3. '인접'은 같은 축에 사장님의 실제 값이 있을 때만이다.
    축만 겹치고 사장님 값이 하나도 없으면 인접이 아니라 미지다(근거 없는 확장 금지)."""
    d, why = gs.classify("GV80 신차패키지", DOM, AXES, set())
    assert d == "인접" and "같은 축" in why, (d, why)

    empty = {"tokens": set(), "sources": {}}
    d2, _ = gs.classify("GV80 신차패키지", empty, AXES, set())
    assert d2 == "미지", "사장님 값이 없는 축을 인접으로 인정함"


def test_unproven_axis_value_blocks_certain():
    """A3-2. 박제 중 발견한 실제 위험 — 'GV80 신차패키지'가 '확실'로 분류됐다.
    '신차패키지'가 실데이터에 있다는 이유였는데, 정작 GV80은 근거가 0이다.
    그대로 두면 사장님이 다루지도 않는 차종으로 새 글을 시킨다(날조 유도).
    축에 속한 값이 하나라도 미검증이면 '확실'이 될 수 없다."""
    d, why = gs.classify("GV80 신차패키지", DOM, AXES, set())
    assert d != "확실", f"미검증 축 값이 확실로 통과: {why}"
    assert "GV80" in why and "미검증" in why, why
    # 반대로 축 값이 전부 검증된 것은 확실이어야 한다(과잉 차단 방지)
    d2, why2 = gs.classify("부산 팰리세이드 신차패키지", DOM, AXES, set())
    assert d2 == "확실", (d2, why2)


def test_declined_tokens_never_return():
    """A4. '안 해요'는 영구 제외다 — 같은 걸 또 물으면 잔소리이자 신뢰 손실이다."""
    d, why = gs.classify("GV80 썬팅", DOM, AXES, {"GV80"})
    assert d == "제외" and "안 하신다" in why, (d, why)


def test_owner_domain_reads_only_real_data():
    """A5. 영역 재료는 실데이터에서만 온다 — 업종명·추측에서 만들지 않는다."""
    import inspect
    src = inspect.getsource(gs.owner_domain)
    for real in ("list_blog_publishes", "list_sets", "list_owner_experience"):
        assert real in src, f"실데이터 출처 누락: {real}"
    assert "industry" not in src, "업종명에서 영역을 추측함"


# ── B. 빈자리 판정 — 자리 없으면 0점, 낡은 지도는 안 쓴다 ────────
def test_no_surface_scores_zero():
    """B1. 지면이 없는 판에서는 아무리 좋은 글도 노출로 이어지지 않는다(실측).
    수요가 아무리 커도 0점이어야 우선순위에 끼지 못한다."""
    assert gs._score(50000, False, 100, 400) == 0.0


def test_volume_gate_enforced():
    """B2. 사람이 안 치는 검색어는 빈자리가 아니다 — 기존 정찰과 같은 잣대(100)."""
    assert gs._score(gs.MIN_VOLUME - 1, True, 100, 400) == 0.0
    assert gs._score(gs.MIN_VOLUME, True, 100, 400) > 0


def test_unknown_doc_count_gets_no_bonus():
    """B3. 문서 수 조회 실패(-1)를 '경쟁 없음'으로 읽으면 안 된다 —
    같은 오독이 검색어 정찰에서 실사고를 냈다(공급 0 오독)."""
    unknown = gs._score(5000, True, -1, -1)
    weak = gs._score(5000, True, 50, 400)
    assert unknown < weak, "모르는 것을 '경쟁 약함'으로 읽음"


def test_weak_competition_scores_higher():
    """B4. 경쟁 약도(문서 적음·상위 글 낡음)가 점수에 실제로 반영된다."""
    assert gs._score(5000, True, 50, 400) > gs._score(5000, True, 900000, 10)


def test_weights_are_data_not_logic():
    """B5. 가중치는 데이터 필드다 — 계산식 안에 숫자를 박지 않는다(조정 가능해야 한다)."""
    assert set(gs.WEIGHTS) == {"volume_log", "surface", "weak_comp"}
    base = gs._score(5000, True, 50, 400)
    gs.WEIGHTS["surface"] += 10
    try:
        assert gs._score(5000, True, 50, 400) > base, "가중치를 바꿔도 점수가 그대로"
    finally:
        gs.WEIGHTS["surface"] -= 10


def test_stale_map_is_not_claimed_as_gap():
    """B6. 지면 지도는 로컬 스캐너가 채운다 — 노트북이 꺼지면 낡는다.
    낡은 지도로 '빈자리'라고 말하면 허위 양성이다(측정 원칙)."""
    # ★ 상수를 그대로 쓰면 상수를 늘려도 테스트가 따라가 통과한다(자기충족).
    #   유효기간에 상한을 두고, 고정된 옛 날짜로 배제를 확인한다.
    assert gs.MAP_TTL_DAYS <= 30, f"지도 유효기간이 너무 길다({gs.MAP_TTL_DAYS}일) — 낡은 지도로 판정한다"
    tid = "T_GAP_" + uuid.uuid4().hex[:8]
    old = (datetime.utcnow() - timedelta(days=60)).isoformat()
    fresh = datetime.utcnow().isoformat()
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            c.execute("INSERT OR REPLACE INTO kw_blocks VALUES(?,?,?,?,?,?)",
                      (tid, "낡은키워드", "인기글", "인기글", 0, old))
        assert gs._surface_rows(tid) == [], "유효기간 지난 지도를 판정에 씀"
        with db._conn() as c:
            c.execute("INSERT OR REPLACE INTO kw_blocks VALUES(?,?,?,?,?,?)",
                      (tid, "최근키워드", "인기글", "인기글", 0, fresh))
        assert len(gs._surface_rows(tid)) == 1, "유효한 지도를 못 읽음"
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM kw_blocks WHERE tenant_id=?", (tid,))


def test_scan_says_why_when_it_cannot_judge():
    """B7. 판정 못 하면 빈 목록을 조용히 주지 않고 사유를 남긴다(조용한 실패 금지)."""
    tid = "T_GAP_" + uuid.uuid4().hex[:8]
    try:
        with db._conn() as c:
            c.execute("INSERT INTO tenants(id, name) VALUES(?,?)", (tid, "테스트가게"))
    except Exception:
        pass
    try:
        r = gs.scan(tid)
        assert r.get("ok") is True
        assert r.get("gaps") == []
        assert "지면 지도" in (r.get("note") or ""), r
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM tenants WHERE id=?", (tid,))


def test_block_names_are_not_used_as_evidence():
    """B8. 블록 이름은 근거로 쓰지 않는다(2026-08-02 오전 실사고).
    '숏텐츠·클립에 노출 중'이라고 표시했는데 실제로는 타사만 있었다 — 귀속이 미검증이다.
    실측 데이터에서도 blog_blocks에 '플레이스 MY'·'네이버 클립'이 들어 있다.
    우리가 쓰는 신호는 이름이 아니라 '비어 있지 않다' 하나뿐이다."""
    import inspect
    src = inspect.getsource(gs.scan)
    assert '"blocks"' not in src, "블록 이름을 결과에 실어 보낸다"
    assert "surface_note" in src, "지면 신호를 사람 말로 설명하지 않는다"
    # 주석·독스트링에 사건을 적는 것은 좋다(사고 계보). 금지는 '판정에 쓰는 것'이다.
    code = "\n".join(ln.split("#", 1)[0] for ln in inspect.getsource(gs).splitlines()
                     if not ln.strip().startswith("#"))
    import re as _re
    code = _re.sub(r'"""[\s\S]*?"""', "", code)          # 독스트링 제거
    for name in ("숏텐츠", "네이버 클립", "플레이스 MY", "인기글"):
        assert name not in code, f"블록 이름을 판정에 씀: {name}"


# ── D. 확인 응답(영역 프로필) ────────────────────────────────────
def test_yes_promotes_but_does_not_authorize_writing():
    """D1. '해요'는 분류만 올린다. 답변만으로 글을 쓰면 그게 날조다 —
    실사진·실경험이 있어야 글이 나간다(앵커·경험 게이트는 그대로)."""
    tid = "T_DOM_" + uuid.uuid4().hex[:8]
    try:
        assert gs.classify("부산 유리막코팅", {"tokens": set(), "sources": {}},
                           [{"axis": "시공", "tokens": ["유리막코팅"]}], set())[0] == "미지"
        assert gs.answer(tid, "유리막코팅", "yes", axis="시공")["ok"] is True
        dom = gs.owner_domain(tid)
        assert "유리막코팅" in dom["tokens"], "확인 응답이 영역에 합류하지 않음"
        assert dom["sources"]["유리막코팅"] == "사장님 확인", dom["sources"]
        # 승격은 분류까지다 — 글감·생성 경로를 건드리지 않는다
        import inspect
        assert "writing_queue" not in inspect.getsource(gs.answer)
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM tenant_domain WHERE tenant_id=?", (tid,))


def test_no_is_permanent_but_reversible():
    """D2. '안 해요'는 영구 제외다 — 같은 걸 또 물으면 잔소리다.
    단 되돌릴 수 있어야 한다: 나중에 그 일을 시작하실 수 있다."""
    tid = "T_DOM_" + uuid.uuid4().hex[:8]
    try:
        gs.answer(tid, "유리막코팅", "no")
        assert "유리막코팅" in gs.excluded_tokens(tid)
        assert gs.classify("부산 유리막코팅", {"tokens": set(), "sources": {}}, [],
                           gs.excluded_tokens(tid))[0] == "제외"
        gs.answer(tid, "유리막코팅", "yes")                 # 나중에 시작하셨다
        assert "유리막코팅" not in gs.excluded_tokens(tid), "되돌릴 수 없다"
        assert "유리막코팅" in gs.owner_domain(tid)["tokens"]
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM tenant_domain WHERE tenant_id=?", (tid,))


def test_answer_rejects_garbage():
    """D3. 판정에 쓰이는 기록이다 — 빈 값·모르는 판정을 조용히 삼키지 않는다."""
    tid = "T_DOM_" + uuid.uuid4().hex[:8]
    assert gs.answer(tid, "", "yes")["ok"] is False
    assert gs.answer(tid, "유리막코팅", "maybe")["ok"] is False


# ── C. 판정과 적재는 분리돼 있다 ─────────────────────────────────
def test_judging_never_writes():
    """C1. 판정(scan/classify)은 절대 글감을 만들지 않는다. 적재는 feed() 하나뿐이다 —
    판정이 곧 적재면 틀린 분류가 그대로 큐에 쌓인다."""
    import inspect
    for fn in (gs.scan, gs.classify, gs.owner_domain, gs._score):
        src = inspect.getsource(fn)
        for forbidden in ("enqueue", "writing_queue", "save_piece", "generate"):
            assert forbidden not in src, f"{fn.__name__}이 글감/생성을 건드린다: {forbidden}"


def test_feed_is_dry_by_default():
    """C2. 기본은 미리보기다. 무엇이 들어갈지 먼저 보여주고 승인된 실행만 실제로 넣는다."""
    import inspect
    sig = inspect.signature(gs.feed)
    assert sig.parameters["dry"].default is True, "기본이 실행이면 사고가 조용히 난다"


def test_never_generates_by_itself():
    """C3. 자동 발행 금지 — 큐에 넣는 것까지다. 생성·발행은 사장님이 누른다."""
    import inspect
    src = inspect.getsource(gs)
    # 발행 '조회'(list_blog_publishes)는 판정 재료다 — 금지는 발행 '실행'이다
    for forbidden in ("generate_for", "consume(", "services import publish", "publish.publish"):
        assert forbidden not in src, f"스스로 생성·발행한다: {forbidden}"


# ── E. 적재 계약 ─────────────────────────────────────────────────
def test_queue_source_sorts_after_all_existing():
    """E1. 트랙 A 매물 글 우선순위 불변 — 큐는 source_type 알파벳순으로 소비된다.
    빈자리는 '추정'이고 실유입·경쟁격차는 '실측'이다. 실측이 먼저 쓰여야 한다."""
    existing = ["P1", "P2", "P3", "P4", "R1", "inflow"]
    assert sorted(existing + [gs.QUEUE_SOURCE])[-1] == gs.QUEUE_SOURCE, \
        f"빈자리 글감({gs.QUEUE_SOURCE})이 기존 소재보다 먼저 소비된다"


def test_only_certain_domain_is_fed():
    """E2. '확실'만 큐에 넣는다 — 인접은 편승(3단계), 미지는 확인 질문이다.
    근거 없는 주제를 큐에 넣으면 사장님이 겪지도 않은 일을 쓰게 된다."""
    import inspect
    src = inspect.getsource(gs.feed)
    assert 'domain="확실"' in src, "확실 외 분류가 큐에 들어갈 수 있다"
    assert "score" in src and "> 0" in src, "0점(관문 미달) 키워드가 큐에 들어간다"


def test_weekly_cap_protects_owner_material():
    """E3. 주 N건 상한 — 빈자리 글감이 큐를 삼키면 사장님의 실제 소재가 밀린다."""
    assert 1 <= gs.WEEKLY_CAP <= 3, f"상한이 비현실적이다({gs.WEEKLY_CAP})"
    import inspect
    src = inspect.getsource(gs.feed)
    assert "WEEKLY_CAP" in src and "days=7" in src, "주간 상한이 실제로 적용되지 않는다"


def test_feed_blocked_without_materials():
    """E5. 재료(경험 한 줄)가 없으면 큐에 넣지 않는다(2026-08-02 사장님 결정).
    넣어두면 사장님이 사진 올리실 때 자동으로 그 글감이 쓰이는데, 재료가 부족한 채로 쓰이면
    사진 설명만 있는 낮은 점수 글이 나간다 — 자리를 한 번 잘못 먹는 것보다 늦게 제대로 먹는 게 낫다."""
    import inspect
    src = inspect.getsource(gs.feed)
    i = src.find('mats["ready"]')
    assert i > 0, "재료 게이트가 없다"
    seg = src[i:i + 500]
    assert "not dry" in src[max(0, i - 60):i + 60], "미리보기까지 막으면 무엇이 필요한지 못 본다"
    assert "questions" in seg, "막기만 하고 무엇을 답해야 하는지 안 알려준다"
    assert "blocked_by" in seg, "왜 막혔는지 기계가 읽을 수 없다"


def test_questions_only_for_certain_domain():
    """E6. 경험 질문은 '확실' 영역에만 만든다 — 미지 영역을 묻는 것은 성격이 다른
    확인 질문(3단계)이다. 여기서는 '하시는 일'의 속을 여쭙는다."""
    import inspect
    src = inspect.getsource(gs.questions)
    assert 'domain="확실"' in src, "미지 영역까지 경험을 묻는다"
    assert "score" in src, "관문 미달 키워드까지 묻는다"
    # 각도별로 질문이 갈린다(가격 의도에 후기 질문을 하면 답이 안 나온다)
    assert set(gs._Q_BY_ANGLE) == {"price", "howto", "review"}
    for tmpl in gs._Q_BY_ANGLE.values():
        assert "{kw}" in tmpl and "한 줄" in tmpl or "하나만" in tmpl, tmpl


def test_experience_page_shows_gap_questions():
    """E7. 질문은 사장님이 답할 수 있는 곳에 떠야 한다 — 만들어만 두면 아무도 못 본다."""
    import inspect
    from app import main as _m
    src = inspect.getsource(_m.my_experience)
    assert "gapscout" in src and "questions" in src, "빈자리 질문이 화면에 없다"
    assert "gapbox" in src, "질문 상자가 조립되지 않는다"


def test_targeted_claim_does_not_change_normal_order():
    """E8. 특정 글감 지목(only_id)은 진단 경로 전용이다.
    평소 소비는 순서를 따라야 한다 — 지목이 기본이 되면 우선순위 규칙이 무의미해진다."""
    import inspect
    src = inspect.getsource(db.claim_writing)
    assert "only_id: int = 0" in src, "지목이 기본값이면 순서가 무너진다"
    assert "ORDER BY source_type ASC, created_at ASC" in src, "순서 규칙이 사라짐"
    from app.services import autoqueue as _aq
    assert inspect.signature(_aq.consume).parameters["only_id"].default == 0


def test_feed_reports_what_is_missing():
    """E4. 재료가 없으면 글이 안 된다 — 무엇이 더 필요한지 함께 돌려준다(조용한 실패 금지)."""
    import inspect
    src = inspect.getsource(gs.feed)
    assert "materials" in src and "need" in src, "필요 재료를 알려주지 않는다"
