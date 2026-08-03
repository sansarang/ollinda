"""
사진 묘사 파싱·캡션 게이트 박제(2026-08-03 — 10회 재발 계열 종결).

사고: 발행 키트 캡션 20개 중 8개가 사진 묘사가 아니라 vision 응답의 제목 줄이었다
("사진 분석 (썬팅 업종 관점)", "썬팅 업종 마케팅 분석").
원인 ① gen_source에 같은 [사진N]이 최대 10번 나오는데 캡션이 '첫 매치'를 썼다
      ② 그 첫 줄이 헤더·라벨·마크다운 잔해인 번호가 있었다
      ③ 결함 캡션끼리 같아지자 중복 구분 로직이 '— 썬팅 가격'을 덧붙여 증폭했다
      ④ 캡션 품질 게이트가 없었다
영상 자막에는 ①②의 방어가 이미 있었다 — 한쪽만 고쳐서 반복됐다.
"""
from __future__ import annotations

import inspect

from app.services import photodesc as pd

# 실측 gen_source 축약(2026-08-03, 세트 89a221b5) — 같은 번호가 여러 번, 첫 줄이 헤더
REAL = (
    "[사진 분석 — AI 추측(사장님 미확인)]\n"
    "[사진1] **\n"
    "[사진1] 손에 붉은색 스퀴지와 흰색 천을 들고 차량 하단부 표면을 닦는 모습\n"
    "[사진2] **\n"
    "[사진2] 피사체/제품\n"
    "[사진2] 사진 분석 (썬팅 업종 관점)\n"
    "[사진2] 회색 밴 차량의 측면 및 전면부, 창문 틴팅 작업 중인 모습\n"
    "[사진8] 사진 분석 (썬팅 업종 관점)\n"
    "[사진9] 썬팅 업종 마케팅 분석\n"
)


def test_meta_headers_never_become_captions():
    """A. vision이 뱉은 제목 줄은 묘사가 아니다 — 캡션이 될 수 없다."""
    for bad in ("사진 분석 (썬팅 업종 관점)", "썬팅 업종 마케팅 분석", "썬팅 업체 마케팅 관점 사진 분석",
                "피사체/제품", "**", "* 피사체:", "---"):
        assert not pd.is_description(bad), f"메타/라벨을 묘사로 인정: {bad}"
    for ok in ("손에 붉은색 스퀴지와 흰색 천을 들고 차량 하단부를 닦는 모습",
               "회색 밴 차량의 측면, 창문 틴팅 작업 중"):
        assert pd.is_description(ok), f"정상 묘사를 버림: {ok}"


def test_best_line_not_first_match():
    """B. 첫 매치를 쓰면 안 된다 — 배치가 이어붙어 첫 줄이 헤더인 번호가 실제로 있었다."""
    assert "스퀴지" in pd.best_line(REAL, 1), "1번이 '**'를 캡션으로 씀"
    assert "틴팅" in pd.best_line(REAL, 2), "2번이 헤더/라벨을 캡션으로 씀"


def test_no_description_means_blank_not_template():
    """C. 침묵 폴백 금지 — 쓸 만한 묘사가 없으면 빈칸이다.
    업종명·키워드·템플릿으로 채우면 사장님이 결함을 못 본다."""
    assert pd.best_line(REAL, 8) == "", "헤더뿐인 번호를 채웠다"
    assert pd.best_line(REAL, 9) == "", "헤더뿐인 번호를 채웠다"
    assert pd.best_line(REAL, 99) == "", "없는 번호를 채웠다"
    src = inspect.getsource(pd)
    for banned in ("업종", "키워드", "기본 캡션"):
        assert f'return f"{banned}' not in src, "템플릿으로 채운다"


def test_caption_path_uses_the_single_parser():
    """D. 캡션이 단일 파서를 쓰는가 — 자체 정규식으로 첫 매치를 뽑으면 같은 사고가 난다."""
    from app import main as m
    src = inspect.getsource(m._photo_captions)
    assert "photodesc" in src and "best_line" in src, "캡션이 단일 파서를 안 쓴다"
    assert 'rf"\\[사진{i}\\]' not in src, "캡션이 자체 정규식으로 첫 매치를 뽑는다"


def test_video_uses_the_same_parser():
    """E. 같은 재료를 읽는 소비자가 둘이면 파서는 하나여야 한다(조항).
    영상만 고치고 캡션을 안 고쳐서 10회 재발했다."""
    from app.generators import video as v
    src = inspect.getsource(v._lines_for_photos)
    assert "photodesc" in src, "영상이 다른 파서를 쓴다"
    assert "_META = _r.compile" not in src, "영상에 중복 파서가 남아 있다"


def test_duplicate_captions_resolved_by_real_alternates():
    """F. 중복 구분을 키워드로 하지 않는다 — 그 사진의 '다른 실제 묘사'로 가른다(조항)."""
    from app import main as m
    src = inspect.getsource(m._photo_captions)
    assert "alternates(" in src, "중복을 실제 묘사로 가르지 않는다"
    i = src.find("_key in _seen")
    seg = src[i:i + 700]
    assert "— {kw}" not in seg and "f\"{_c.rstrip('. ')} — {kw}\"" not in seg, \
        "중복을 키워드로 채운다(침묵 폴백)"


def test_constitution_has_silent_fallback_ban():
    """G. 조항이 헌법에 있어야 다음 세션이 안다."""
    import pathlib
    txt = " ".join((pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text().split())
    assert "침묵 폴백 금지" in txt
    assert "게이트 없는 표면 신설은 커밋 불가" in txt
    assert "표면 하나 고치는 것은 수정이 아니라 다음 재발 예약이다" in txt
    assert "파서를 하나로 만든다" in txt
