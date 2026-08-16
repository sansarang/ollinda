"""사람이 쓴 글처럼 — 말 걸기·뼈대 다양화·사장님 말투 골든 (2026-08-16).

근거:
  · 해외 연구 — 사람 글은 burstiness(문장 길이·리듬의 들쭉날쭉함)가 크고,
    engagement marker(질문·개인적 곁말)가 많다. AI 글은 문서 내부 분산이 낮다.
  · 우리 글 실측 — 쉼표 33%(AI 61%/사람 26%), 접속어 0, 지시관형사 0 ← 이미 양호
    그러나 **물음표 0개**, 그리고 '한눈 요약'·'자주 묻는 질문'이 6/6 동일 문구.
  → AI 티의 실제 원인은 어휘가 아니라 ①말 걸기 부재 ②같은 뼈대 반복이었다.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


# ── ① 말 걸기 ───────────────────────────────────────────────────────────

def test_prompt_asks_for_engagement_without_fixing_a_count():
    """개수를 못 박으면 그 자체가 새로운 기계 패턴이 된다."""
    src = _src("app/generators/text_claude.py")
    assert "독자에게 말을 걸어라" in src
    assert "몇 개를 넣으라는 규칙은 없다" in src, "개수를 고정하면 새 AI 패턴이 된다"
    assert "들쭉날쭉" in src, "문장 리듬(burstiness) 지시가 없다"


# ── ② 뼈대 다양화 ───────────────────────────────────────────────────────

def test_section_names_vary_but_are_deterministic():
    """같은 글은 항상 같은 이름이어야 재생성·검증이 가능하다."""
    from app.services import sections as sec
    a1 = sec.prompt_names("asset-A")
    a2 = sec.prompt_names("asset-A")
    b = sec.prompt_names("asset-B")
    assert a1 == a2, "같은 글인데 이름이 달라진다"
    names = {sec.summary_head(f"a{i}") for i in range(30)}
    assert len(names) >= 2, f"요약 섹션 이름이 하나로 고정됐다: {names}"


def test_checkers_accept_every_variant():
    """이름만 바꾸고 검사기를 안 고치면 모든 글이 'FAQ 없음'으로 걸린다."""
    from app.services import sections as sec
    for v in sec.SUMMARY:
        assert sec.has_summary(f"## {v}\n내용")
    for v in sec.FAQ:
        assert sec.has_faq(f"## {v}\nQ. 무엇\nA. 답")
    assert sec.has_summary("## 한 눈 요약\n내용"), "기존 표기 하위호환이 깨졌다"
    assert sec.has_faq("Q&A 목록"), "기존 판정어 하위호환이 깨졌다"
    assert not sec.has_summary("## 시공 과정\n내용")


def test_no_consumer_hardcodes_the_section_words():
    """이 문구를 읽는 코드가 여러 곳이다 — 목록이 복사되면 한쪽만 고쳐진다."""
    for rel in ("app/services/qualitycheck.py", "app/generators/video.py",
                "app/generators/text_claude.py"):
        src = _src(rel)
        body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        assert not re.search(r'\("한눈 요약",\s*"자주', body), f"섹션 목록이 복사됐다: {rel}"


def test_admin_head_detection_covers_variants():
    from app.services import sections as sec
    assert sec.is_admin_head("## 요약하면")
    assert sec.is_admin_head("많이들 물어보시는 것")
    assert not sec.is_admin_head("전창 썬팅 이렇게 잡았습니다")


# ── ③ 사장님 말투(A안) ──────────────────────────────────────────────────

def test_owner_voice_uses_only_owner_written_text():
    """과거 발행 글은 우리가 AI로 쓴 것이다. 그걸 학습하면 우리 AI 목소리를 다시 배우는 순환이다."""
    src = _src("app/services/ownervoice.py")
    assert "owner_experience" in src
    assert "content_pieces" not in src and "list_sets" not in src, \
        "AI가 쓴 과거 글을 말투 재료로 쓰고 있다"


def test_owner_voice_stays_silent_when_samples_are_thin(monkeypatch):
    """빈약한 페르소나는 없는 것보다 나쁘다 — 근거 없는 말투는 날조다."""
    from app.services import ownervoice as ov
    monkeypatch.setattr(ov, "samples", lambda tid, limit=40: ["짧아요."])
    assert ov.directive("t") == ""
    monkeypatch.setattr(ov, "samples", lambda tid, limit=40: [])
    assert ov.directive("t") == ""


def test_owner_voice_extracts_endings_and_habits(monkeypatch):
    """실제 사장님 답변으로 눈금 확인(규율 4)."""
    from app.services import ownervoice as ov
    real = ["출고 당일 바로 하고 가시는 분들이 좋다는 말씀을 제일 많이 하십니다. "
            "신차는 유리가 깨끗해서 필름 밀착이 제일 잘 나오는 시기입니다.",
            "여름에 에어컨 트는 시간이 줄었다는 말씀을 제일 많이 하십니다. "
            "앞유리까지 하신 분들은 팔이 따갑던 게 없어졌다고 하십니다.",
            "필름 등급이 절반이고 시공 실력이 절반입니다. 저희는 정품만 쓰는데 같은 제품이라도 "
            "등급에 따라 값이 몇 배 차이 납니다. 열차단 성능과 보증 기간이 등급마다 다릅니다."]
    monkeypatch.setattr(ov, "samples", lambda tid, limit=40: real)
    p = ov.profile("t")
    assert p["enough"] and p["endings"], p
    assert "하십니다" in p["endings"][0] or any("하십니다" in e for e in p["endings"])
    d = ov.directive("t")
    assert "사장님 말투" in d and "지어내지 말고" in d


def test_owner_voice_habits_are_not_overlapping_fragments(monkeypatch):
    """n-gram은 한 말버릇을 여러 토막으로 낸다 — 겹치면 하나로."""
    from app.services import ownervoice as ov
    real = ["말씀을 제일 많이 하십니다 그리고 또 말씀을 제일 많이 하십니다 정말로."] * 2
    monkeypatch.setattr(ov, "samples", lambda tid, limit=40: real)
    hs = ov.profile("t")["habits"]
    for i, a in enumerate(hs):
        for b in hs[i + 1:]:
            assert not (set(a.split()) & set(b.split())), f"겹치는 습관어: {a} / {b}"


def test_owner_voice_is_wired_into_generator():
    src = _src("app/generators/text_claude.py")
    assert "ownervoice" in src and "_voice_rule" in src
