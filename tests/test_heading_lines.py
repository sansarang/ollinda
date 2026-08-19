"""소제목 줄 보정 골든 — 소제목이 문단 끝에 붙으면 '소제목 없는 글'이 된다.

2026-08-19 실측(루마썬팅 테스트 생성 1편):
    …이런 정밀함이 쌓여서 기포 없는 마감으로 이어집니다. ## 가장자리 맞춤이 결과 차이를 만듭니다
  소제목 4개 중 **3개**가 이 꼴이었다. 결과:
    · 소제목 집계 1개(사실은 4개) · FAQ 미검출 · GEO 50점
    · 발행하면 네이버에도 본문 한 덩이로 올라간다 — 문단 노출 자체가 불가능해진다

★ 프롬프트로 '소제목은 줄을 바꿔라'라고 지시하는 것은 확률이다. 기계 보정이 보장이다
  (제목 3안·FAQ 보강·체류 장치와 같은 패턴).
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.generators.text_claude import _own_line_headings as fix  # noqa: E402


def test_문단_끝에_붙은_소제목을_제_줄로_내린다():
    """★ 이 파일의 존재 이유."""
    got = fix("정밀함이 쌓입니다. ## 가장자리 맞춤이 결과를 만듭니다\n\n다음 문단.")
    assert "\n## 가장자리 맞춤이 결과를 만듭니다" in got
    import re
    assert len(re.findall(r"^#{2,4} ", got, re.M)) == 1


def test_원래_제_줄인_소제목은_건드리지_않는다():
    src = "## 소제목\n내용입니다.\n\n## 다음 소제목\n내용."
    assert fix(src) == src


def test_해시태그를_소제목으로_만들지_않는다():
    """본문 끝 태그 줄('#부산썬팅 #루마썬팅')이 소제목으로 바뀌면 글이 망가진다."""
    src = "본문입니다.\n\n#부산썬팅 #부산썬팅업체 #루마썬팅"
    assert fix(src) == src


def test_내용은_바뀌지_않는다():
    """보정은 줄바꿈만 넣는다 — 글자를 지우거나 더하면 그게 더 큰 사고다."""
    src = "앞 문장. ## 소제목 하나\n\n뒤 문장. ### 소제목 둘"
    assert fix(src).replace("\n", " ").split() == src.replace("\n", " ").split()


def test_생성기가_본문을_받자마자_보정한다():
    """뒤에서 고치면 그 사이 단계(체류 장치·사진 배치·게이트)가 소제목을 못 본다."""
    import inspect
    from app.generators import text_claude as tc
    src = inspect.getsource(tc)
    i = src.find("_ensure_dwell_devices(")
    j = src.find("_ensure_dwell_devices(", i + 10)     # 정의가 아니라 호출부
    seg = src[j - 200:j + 200] if j > 0 else src[i - 200:i + 200]
    assert "_own_line_headings" in seg, "본문 진입점에서 보정하지 않는다"
