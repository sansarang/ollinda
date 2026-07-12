"""휴먼터치(A블록) 전후 비교 — 결정적 검증(LLM 0콜).

'전' = AI 클리셰 문체(개선 전 전형 출력), '후' = 휴먼터치 규칙 반영 문체.
실생성 전후 비교는 2026-07-12 프로덕션 3종(빵집·썬팅·셀러 폴딩박스)으로 실측:
  클리셰 0/1/0건 · 경험담 3종 모두 도입부(상위 20%) 배치 · 문단 변동계수 0.32/0.29/0.18 · audit 81/76/91점.
여기서는 그 회귀 조건(채점기 감점·프롬프트 주입·배치 지시)을 고정한다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")
os.environ.setdefault("SHOPCAST_DISABLE_SCHEDULER", "1")

from app import seo  # noqa: E402

_BEFORE = (  # 개선 전 전형(클리셰 도입·마무리 + 균일 문단)
    "안녕하세요~ 오늘은 부산 빵집을 알아보겠습니다.\n\n"
    "저희 빵집은 정말 맛있는 빵을 만들고 있습니다 방문해 주세요 감사합니다 하나.\n\n"
    "저희 빵집은 정말 맛있는 빵을 만들고 있습니다 방문해 주세요 감사합니다 둘요.\n\n"
    "저희 빵집은 정말 맛있는 빵을 만들고 있습니다 방문해 주세요 감사합니다 셋째.\n\n"
    "저희 빵집은 정말 맛있는 빵을 만들고 있습니다 방문해 주세요 감사합니다 넷째.\n\n"
    "강력 추천드립니다. 지금까지 부산 빵집이었습니다. 도움이 되셨길 바랍니다. 어떠셨나요? 😊✨🔥"
)
_AFTER = (  # 개선 후 문체(경험 도입·길이 변주·클리셰 없음)
    "반죽을 저온에서 17시간 숙성해요. 그래서 아침이 늦습니다.\n\n"
    "처음엔 저도 반신반의했어요. 근데 저온 숙성을 시작하고 나서 크러스트 결이 눈에 띄게 달라졌고, "
    "단골손님들이 먼저 알아봐 주시더라고요. 버터는 프랑스산만 씁니다.\n\n"
    "짧게 정리하면 이래요.\n\n"
    "## 자주 묻는 질문\nQ. 몇 시에 빵이 나오나요?\nA. 오전 11시쯤 첫 판이 나와요."
)


def test_audit_penalizes_cliche_style():
    b = seo.quality_audit("naver_blog", "blog", {"body": _BEFORE, "target_keywords": []})
    a = seo.quality_audit("naver_blog", "blog", {"body": _AFTER, "target_keywords": []})
    assert any("AI 클리셰" in w for w in b["warnings"])
    assert any("균일" in w for w in b["warnings"])
    assert any("이모지" in w for w in b["warnings"])
    assert not any(("AI 클리셰" in w or "균일" in w or "이모지" in w) for w in a["warnings"])


def test_human_touch_injected_into_prompts():
    import inspect
    from app.generators import text_claude, x_text
    assert "HUMAN_TOUCH" in inspect.getsource(text_claude.BlogDraftGenerator.generate)
    assert "HUMAN_TOUCH" in inspect.getsource(text_claude.CaptionGenerator._prompt)
    assert "HUMAN_TOUCH" in inspect.getsource(x_text)
    for tok in ("알아보겠습니다", "추천드립니다", "문장 길이", "1인칭"):
        assert tok in seo.HUMAN_TOUCH


def test_experience_placement_directive_only_when_present():
    from app.services import smart_intake
    with_exp = smart_intake.build_intake_note("빵집", "", {}, "반죽을 저온에서 17시간 숙성해요")
    without = smart_intake.build_intake_note("빵집", "확인", {"a": "b"}, "")
    assert "[경험 중심 배치]" in with_exp
    assert "[경험 중심 배치]" not in without   # 경험 없으면 억지 배치 지시 금지(정직)
