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


# ── ①-보강: 지시문끼리 부딪히지 않는가 (2026-08-16 실패 원인) ────────────

def test_question_instruction_does_not_contradict_the_ban():
    """실패 원인: 말 걸기 예시가 '이거 궁금하셨죠?'였는데,
    seo.HOOK_STYLES가 "수사 의문 '~하셨죠?' 말고 진짜 질문문"으로 그걸 금지하고 있었다.
    모델은 충돌하면 금지 쪽을 따른다(어기면 감점) → 물음표 0개가 됐다."""
    src = _src("app/generators/text_claude.py")
    i = src.find("[독자에게 말을 걸어라]")
    assert i > 0
    seg = src[i:i + 600]
    # 예시 부분(넣어라 ~ 상투 질문 사이)에 금지 패턴이 있으면 안 된다
    ex = seg.split("넣어라", 1)[1].split("상투 질문", 1)[0]
    assert "하셨죠" not in ex, f"말 걸기 예시가 금지 대상(수사 의문)을 쓰고 있다: {ex[:120]}"
    assert "진짜 묻는 질문은 본문 어디서든" in seg, "허용 범위를 밝히지 않았다"


def test_rhetorical_ban_is_scoped_to_the_opening():
    """금지가 본문 전체로 읽히면 모델이 질문 자체를 회피한다."""
    from app import seo
    q = dict(seo.HOOK_STYLES)["질문형"]
    assert "첫 문장" in q, "수사 의문 금지가 도입부 한정임을 밝히지 않았다"


def test_cliche_ban_does_not_suppress_all_questions():
    from app import seo
    assert "진짜 궁금증을 묻는 질문 문장은 권장" in seo.HUMAN_TOUCH


# ── ②: 두께 규칙이 리듬을 죽이지 않는가 ─────────────────────────────────

def test_thickness_rule_keeps_rhythm():
    """실측: 두께만 요구했더니 모든 문단이 길어져 길이편차 54→45, 쉼표 47%→58%로
    오히려 AI 쪽으로 갔다. 사람 글의 표식은 두께가 아니라 들쭉날쭉함이다."""
    from app.services import answerblock as ab
    rule = ab.prompt_rule(["부산 동구 썬팅", "썬팅 과정"], core="부산 동구 썬팅")
    assert "모든 문단이 길 필요는 없다" in rule
    assert "다 비슷하면" in rule, "길이 균일이 기계 표식이라는 경고가 없다"


# ── 3-1: 섹션 이름을 박아둔 자리가 남아 있지 않은가 (2026-08-16) ─────────

def test_no_prompt_hardcodes_a_section_name():
    """섹션 이름을 글마다 변형하게 만들면서, 이름을 박아둔 자리를 다 훑지 않았다.
    '## 한눈 요약 줄 안에 키워드를 넣어라'고 시켜놓고 실제 섹션은 '짧게 정리'가 되면
    모델이 갈린다 — 내가 만든 모순이다(규율 9: 완료 전 전체 훑기)."""
    for rel in ("app/generators/text_claude.py", "app/seo.py"):
        src = _src(rel)
        for i, line in enumerate(src.splitlines(), 1):
            l = line.lstrip()
            if l.startswith("#") or l.startswith('"""'):
                continue
            if "'## 한눈 요약'" in line or '"## 한눈 요약"' in line:
                assert "_sm" in line or "_sec" in line, \
                    f"섹션 이름이 박혀 있다: {rel}:{i}"


def test_faq_fallback_uses_this_articles_name():
    """FAQ 누락 폴백이 기준형을 박으면, 변형 이름을 쓴 글에 다른 이름 섹션이 하나 더 붙는다."""
    src = _src("app/generators/text_claude.py")
    i = src.find("FAQ 섹션 누락 대비")
    assert i > 0
    seg = src[i:i + 500]
    assert "_sec_names['faq']" in seg, "폴백이 이번 글의 섹션 이름을 안 쓴다"
    assert "has_faq" in seg, "존재 판정이 관문을 안 거친다"


# ── 3-2: 화자 선언 중복 제거 (2026-08-16) ────────────────────────────────

def test_speaker_declaration_is_not_repeated():
    """seo.speaker_frame이 이미 "너는 사장 본인이다"를 선언한다.
    블로그 프롬프트가 같은 말을 다시 하면 지시만 늘고 새 지시가 묻힌다
    (실물 프롬프트 12,019자·54블록·금지표현 96회)."""
    src = _src("app/generators/text_claude.py")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "이 글을 쓰는 사람은 '가게 사장'이다" not in body, "화자 선언이 중복으로 남아 있다"
    assert "[청자 — 손님 말로 불러라]" in body, "청자 규칙(블로그 전용)이 사라졌다"


def test_blog_only_rules_survive_the_merge():
    """통폐합은 '중복 제거'지 '기능 삭제'가 아니다 — 고유 규칙은 남아야 한다."""
    src = _src("app/generators/text_claude.py")
    for rule in ("손님이 쓰는 말로 불러라", "'후기'는 손님이 쓴 경험담", "사전에 없는 조어"):
        assert rule in src, f"통폐합으로 고유 규칙이 사라졌다: {rule}"


def test_shared_frame_stays_generic():
    """speaker_frame은 캡션·영상도 쓴다 — 블로그 전용 규칙이 새면 다른 산출물이 오염된다."""
    from app import seo
    f = seo.speaker_frame("local")
    for blog_only in ("소제목", "[사진", "한눈 요약", "FAQ"):
        assert blog_only not in f, f"공유 프레임에 블로그 전용 규칙이 샜다: {blog_only}"
