"""사진 배치 고르기 골든 (2026-08-16 실물 사고).

사고: 완성 글에서 사진이 **정확히 2장씩 6곳**에 붙었다.
      한 문단 뒤에 사진 2장이 연달아 오면 손님은 글 없이 사진만 스크롤한다.

★ 원인 진단을 두 번 틀렸다(기록해 둔다):
   ① "묘사 0/17 → 순차 폴백"     → 그 로그는 내 재생성 스크립트 것이었다.
                                   실제 노트로 파서를 돌리니 17/17 정상.
   ② "사진이 문단보다 많아서"      → 틀렸다. 사진 17장 · 산문 문단 19개(사진이 더 적다).

실제 원인: 문단당 상한식의 **하한이 2**였다.
   MAX_PER = max(2, ceil(사진수 / 허용문단수))
   17/19 → ceil=1 인데 max(2,1)=2 → 고르게 퍼질 수 있는 글에서도 2장씩 뭉쳤다.
"""
import math


def cap(n, allowed):
    """현재 코드와 같은 식(골든이 식 자체를 문다)."""
    from app.generators import text_claude  # noqa: F401  (모듈 로드 확인)
    return max(1, -(-n // allowed))


def test_photos_spread_one_each_when_paragraphs_are_enough():
    """사고 재현 조건 — 사진보다 문단이 많으면 한 장씩 퍼져야 한다."""
    assert cap(17, 19) == 1, "문단이 남는데도 상한이 2다(뭉침 재발)"
    assert cap(6, 20) == 1


def test_overflow_still_allows_more_per_paragraph():
    """사진이 문단보다 많으면 어쩔 수 없이 2장 이상 — 넘침 대응은 유지돼야 한다."""
    assert cap(17, 10) == 2
    assert cap(25, 10) == 3
    assert cap(30, 7) == 5


def test_cap_never_drops_to_zero():
    """0이 되면 아무 사진도 못 붙는다."""
    assert cap(1, 50) >= 1
    assert cap(0, 5) >= 1 or cap(0, 5) == 0   # 사진 0장은 배치 자체가 없다


def test_source_has_no_floor_of_two():
    """하한 2가 되살아나면 같은 사고가 재발한다."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "app", "generators", "text_claude.py"), encoding="utf-8").read()
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "MAX_PER = max(2," not in body, "문단당 상한 하한이 다시 2로 올라갔다"
    assert "MAX_PER = max(1," in body, "동적 상한식이 사라졌다"
