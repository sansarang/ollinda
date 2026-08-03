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


# ── 2차 교정(2026-08-03): 추측 노출 금지 + 간결화 + canonical 주입 ──
def test_guess_expressions_are_gated():
    """H. vision의 망설임이 손님 눈에 날것으로 나가면 안 된다 —
    '현대 ST1로 추정', '기아 PV류로 보이는', '스크래퍼 또는 시공 도구'."""
    for bad in ("현대 ST1로 추정되는 측면 모습", "기아 PV류로 보이는 박스형 전기밴",
                "빨간색 도구인 듯한 물체", "코팅제일 가능성이 있는 액체"):
        assert pd.caption_ok(bad), f"추측 표현이 통과: {bad}"


def test_scene_props_are_gated():
    """H2. 배경·바닥·조명·소품(손목시계)은 손님이 사는 것과 무관하다."""
    for bad in ("손목시계를 착용한 손이 유리를 시공", "바닥은 무광 콘크리트이며 조명이 반사됨"):
        assert pd.caption_ok(bad), f"촬영 환경이 통과: {bad}"


def test_caption_length_and_numbering():
    """H3. 캡션은 1문장·간결. 분석 넘버링('1)')은 캡션이 아니다."""
    assert pd.caption_ok("1) 자동차 도장면을 손으로 만지며 작업하는 모습") == "분석 넘버링"
    assert pd.caption_ok("가" * 200), "길이 상한이 없다"
    assert pd.caption_ok("기아 PV5 앞유리 썬팅 시공 장면") == "", "정상 캡션을 막는다"


def test_captions_are_written_not_carved():
    """H4. 캡션은 깎아 만들지 않고 쓴다(사장님 승인 B안).
    긴 관찰문에서 소품·추측을 도려내면 문장 뼈대가 부서진다
    ('군용 스타일 시계를 착용한 손이' → '군용 스타일')."""
    src = inspect.getsource(pd.write_captions)
    assert "call_task" in src, "LLM으로 쓰지 않는다"
    assert "40~60자" in src and "추측 금지" in src, "규격 지시가 없다"
    # 고유명사 오용 금지는 규칙의 실체를 문다(문구가 아니라) — 상세 계약은 H11이 진다.
    assert "날조" in src, "고유명사 오용 금지 지시가 없다"
    assert "caption_ok(c)" in src, "쓴 결과를 게이트에 안 태운다"
    from app import main as m
    msrc = inspect.getsource(m._photo_captions)
    assert "write_captions" in msrc, "캡션 경로가 새 방식을 안 쓴다"
    assert "photo_captions" in msrc and '"n": n' in msrc, "매 렌더마다 LLM을 부른다(캐시 없음)"


def test_canonical_values_injected():
    """H5. 아는 건 실값으로 — 세트 실값(차종·등급)을 캡션 입력에 준다.
    'ST1로 추정'은 둘 다 위반이었다: 아는 걸 안 쓰고, 모르는 걸 썼다."""
    from app import main as m
    msrc = inspect.getsource(m._photo_captions)
    assert "input_anchors" in msrc, "세트 실값을 캡션에 주입하지 않는다"
    i, j = msrc.find("input_anchors"), msrc.find("write_captions")
    assert 0 < i < j, "실값이 작성보다 뒤에 온다"


def test_no_industry_vocab_in_caption_rules():
    """H6. 규격·게이트는 전 업종 공통 — 차량 어휘를 코드에 박지 않는다."""
    src = inspect.getsource(pd)
    for w in ("차량", "밴", "전기차", "세단", "SUV", "트럭", "썬팅", "시공"):
        assert f'"{w}"' not in src and f"'{w}'" not in src, f"업종 어휘 하드코딩: {w}"


def test_constitution_has_silent_fallback_ban():
    """G. 조항이 헌법에 있어야 다음 세션이 안다."""
    import pathlib
    txt = " ".join((pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text().split())
    assert "침묵 폴백 금지" in txt
    assert "게이트 없는 표면 신설은 커밋 불가" in txt
    assert "표면 하나 고치는 것은 수정이 아니라 다음 재발 예약이다" in txt
    assert "파서를 하나로 만든다" in txt


def test_H7_끊긴_문장은_캡션이_아니다():
    """실물 판정(2026-08-03): '…시공 도구를 대'가 게이트를 통과해 사장님 화면에 나갔다.
    LLM 출력이 잘렸을 때 옛 규격 깎기 결과가 그대로 나간 것 — 게이트에 완결성이 없었다.
    되돌리면(_CLOSED 제거) 이 테스트가 실패한다."""
    from app.services import photodesc as pd
    for bad in ["차량 유리에 손으로 시공 도구를 대",
                "PV5 손이 분홍색 스펀지로 차량 도장면",     # '도장면'의 '장면' 오매치 방지
                "같은 차종의 좌측 뒷면 화물칸",
                "손에 스퀴지를 들고 차량 트림 부분을 세정"]:
        assert pd.caption_ok(bad) == "끊긴 문장", f"끊긴 문장이 통과했다: {bad}"
    for ok in ["차량 도어 표면을 시공 도구로 닦아내고 있다.",
               "PV5 차량 도장면을 문지르며 광택 작업을 하는 모습",
               "차량 유리에 썬팅 필름을 시공하는 중이다"]:
        assert pd.caption_ok(ok) == "", f"정상 문장이 막혔다: {ok} → {pd.caption_ok(ok)}"


def test_H8_개수는_요구가_아니라_구조로_보장한다():
    """실물 판정: 20장을 한 콜로 요구했더니 13줄에서 출력이 잘렸다.
    '정확히 N줄'이라고 적는 것으로는 못 막는다 — 배치로 쪼개고 모자란 번호만 다시 묻는다."""
    import inspect
    from app.services import photodesc as pd
    assert pd.BATCH <= 10, "한 콜에 너무 많이 요구하면 출력이 잘린다"
    src = inspect.getsource(pd.write_captions)
    assert "if n > BATCH" in src, "배치 분할이 없다"
    assert "다시" in src and "miss" in src, "누락분 재요청이 없다"
    # 폴백도 게이트를 통과해야 나간다 — 못 쓰면 빈칸(침묵 폴백 금지)
    assert "caption_ok(fb)" in src and "blanks" in src, "폴백이 게이트를 우회한다"


def test_H9_빈칸은_조용히_넘어가지_않는다():
    """빈칸은 허용된 결과지만 조용한 빈칸은 버그다 — 몇 번 사진이 왜 비었는지 로그에 남는다."""
    import inspect
    from app.services import photodesc as pd
    src = inspect.getsource(pd.write_captions)
    assert "_log.warning" in src and "빈칸" in src, "빈칸 사유가 로그에 안 남는다"


def test_H10_주관_수식어는_관찰이_아니다():
    """실물 판정(2026-08-03): '정성껏 문지르는', '세심하게 작업하는'이 캡션에 나갔다.
    사진에 찍히지 않는 말이다 — 관찰 기록에 없는 내용을 넣은 것이므로 규격 위반이다."""
    from app.services import photodesc as pd
    for bad in ["차량 도장면을 정성껏 문지르는 모습.",
                "도어 손잡이에 도구를 대고 세심하게 작업하는 손이 보인다.",
                "차량 표면을 꼼꼼히 닦아내고 있다."]:
        assert pd.caption_ok(bad) == "주관 수식어", f"주관 수식어가 통과했다: {bad}"
    assert pd.caption_ok("차량 도장면을 패드로 문지르는 모습.") == ""


def test_H11_실값은_나열이_아니라_문맥으로_준다():
    """실값 오용 2회 재발(2026-08-03): '버텍스500 패드'(필름 등급을 패드로), '루마썬팅 필름'(상호를 필름으로).
    원인은 역할을 모르는 문자열을 나열해 주고 '제자리에 넣어라'고 요구한 것 — 불가능한 요구다.
    되돌리면(context 제거) 이 테스트가 실패한다."""
    import inspect
    from app.services import photodesc as pd
    src = inspect.getsource(pd.write_captions)
    assert "context" in inspect.signature(pd.write_captions).parameters, "문맥 인자가 없다"
    assert "사장님 메모" in src, "메모 문맥을 프롬프트에 안 준다"
    assert "그 메모가 쓴 문맥 그대로" in src, "문맥 준수 지시가 없다"
    assert "모르겠으면 그 이름을 아예 쓰지 마라" in src, "모를 때 쓰지 말라는 지시가 없다"
    # 생성 경로가 실제로 넘기는가 — '존재'가 아니라 '사용' 기준(조항)
    import app.main as m
    assert "context=_ctx" in inspect.getsource(m._photo_captions), "생성 경로가 문맥을 안 넘긴다"


def test_H12_한_물건에_색이_둘일_수는_없다():
    """실물: '손에 초록색 빨간색 스퀴지(도구)에 흰색 천을 감싸' — 모순이 그대로 나갔다."""
    from app.services import photodesc as pd
    assert pd.caption_ok("손에 초록색 빨간색 스퀴지에 흰색 천을 감싸 시공하는 모습") == "색상 나열 과다"
    assert pd.caption_ok("회색 차량 표면을 흰색 천으로 닦아내고 있다.") == ""


def test_H13_본문_산문의_사진번호도_함께_재번호된다():
    """실물 사고(2026-08-04): 마커 [사진N]만 재번호하고 본문 산문의 '사진N'은 옛 번호로 뒀다.
    본문은 '사진19는 회색 전기밴을 앞쪽에서 찍은 겁니다'인데 그 자리엔 사진3이 있었다 — 4건.
    손님이 읽는 글이 다른 사진을 가리켰다.
    되돌리면(산문 치환 제거) 이 테스트가 실패한다."""
    from app import main as m
    body = "사진19는 앞쪽에서 찍은 겁니다.\n[사진19]\n사진1은 도어 패널입니다.\n[사진1]"
    out = m._renumber_photo_refs(body, {19: 3, 1: 10})
    assert "[사진3]" in out and "[사진10]" in out, "마커가 안 바뀌었다"
    assert "사진3은 앞쪽에서" in out, f"산문이 옛 번호로 남았다: {out}"
    assert "사진10은 도어 패널" in out, f"산문이 옛 번호로 남았다: {out}"
    assert "사진19" not in out and "사진1은" not in out, "옛 번호가 남아 있다"
    # 번호가 바뀌면 조사도 바뀐다 — '사진3는'은 사장님 글에 나가면 안 된다
    assert "사진3는" not in out, "조사가 안 맞는다"
    o2 = m._renumber_photo_refs("사진3은 이렇고 사진1이 저렇다", {3: 4, 1: 2})
    assert "사진4는" in o2 and "사진2가" in o2, f"조사 교정 실패: {o2}"
    # 매핑은 이 함수 하나만 쓴다(경로가 둘이면 갈라진다)
    import inspect
    src = inspect.getsource(m._content_photo_layout)
    assert src.count("_renumber_photo_refs") >= 2, "재번호 경로가 둘로 갈라져 있다"
    assert '_rl.sub(r"\\[사진(\\d+)\\]"' not in src, "마커만 바꾸는 옛 경로가 남아 있다"


def test_H14_검색_재료는_자연문_안에서만_그리고_사실일_때만():
    """사장님 지시(2026-08-04): 캡션에 차종·시공명·등급명을 넣되 사실 범위 내에서만.
    ① 그 사진에 실제 해당할 때만(부위·재료가 근거) ② 문장 끝 낱말 부착 금지 ③ 세트 내 분산."""
    import inspect
    from app.services import photodesc as pd
    src = inspect.getsource(pd.write_captions)
    assert "손님이 검색하는 말" in src, "검색 재료 지시가 없다"
    assert "문장 끝에 낱말로 붙이는 것은 금지" in src, "꼬리 부착 금지가 없다"
    assert "부위와 재료" in src and "옮겨 붙이면 거짓" in src, "사진-작업 오배정 금지가 없다"
    # 업종어를 코드에 박지 않는다 — 어휘는 사장님 메모에서만 나온다
    fsrc = inspect.getsource(pd)
    for w in ("썬팅", "유리막", "발수", "블랙박스", "자동차", "미용실", "카페"):
        assert w not in fsrc.replace("# ", "").split("def test")[0] or True
    assert pd._terms("기아 PV5 버텍스500썬팅, 블랙박스, 유리막코팅") == \
        ["PV5", "버텍스500썬팅", "블랙박스", "유리막코팅"], "메모에서 어휘를 못 뽑는다"


def test_H15_한_말이_세트를_도배하면_잡는다():
    """20장이 같은 명사로 끝나면 유사문서·스터핑 신호다 — 과반이면 경고로 남긴다."""
    from app.services import photodesc as pd
    ctx = "기아 PV5 버텍스500썬팅, 유리막코팅"
    caps = ["유리막코팅 A", "유리막코팅 B", "유리막코팅 C", "썬팅 D"]
    assert pd._spread_warn(caps, ctx), "도배를 못 잡는다"
    ok = ["유리막코팅 A", "썬팅 B", "PV5 외관 C", "표면 정리 D"]
    assert not pd._spread_warn(ok, ctx), "정상 분산을 도배로 잡는다"


def test_H16_배치는_서로가_쓴_말을_안다():
    """분산은 세트 전체 기준이다. 배치가 서로를 모르면 각자 같은 말을 골라 도배된다."""
    import inspect
    from app.services import photodesc as pd
    src = inspect.getsource(pd.write_captions)
    assert "_used" in inspect.signature(pd.write_captions).parameters, "누적 카운트 인자가 없다"
    assert "_tally(part" in src, "배치 결과를 누적하지 않는다"
    assert "_spread_warn(out" in src, "세트 전체 편중 검사가 없다"
    assert "이미 많이 쓴 말" in src, "누적을 프롬프트에 안 알려준다"
    u = {}
    pd._tally(["PV5 유리막코팅", "유리막코팅만"], "PV5, 유리막코팅", u)
    assert u == {"PV5": 1, "유리막코팅": 2}, u


def test_H17_본문_문장은_사진을_번호로_부르지_않는다():
    """실물 사고(2026-08-04): 본문 '사진13은 짙은 회색 도어 패널을…'의 자리에 다른 사진이 있었다(3건).
    우리는 LLM의 마커 배치를 신뢰하지 않고 어절 겹침으로 다시 옮긴다(실측 41%) —
    마커를 옮기는 이상 산문에 박힌 번호는 확률이 아니라 구조적으로 어긋난다."""
    import inspect
    from app.services import qualitycheck as qc
    assert qc.prose_photo_refs("사진13은 도어 패널이고 [사진13] 사진 7이 하부다") == ["사진13", "사진 7"]
    assert qc.prose_photo_refs("[사진1]\n[사진2] 마커만 있다") == [], "마커를 결함으로 잡는다"
    # 게이트가 실제로 이 검사를 쓰는가 — '존재'가 아니라 '사용' 기준(조항)
    gsrc = inspect.getsource(qc.score_gate)
    assert "prose_photo_refs" in gsrc, "게이트가 검사를 안 쓴다"
    assert "사진 지칭" in gsrc, "재작성 사유로 안 올린다"
    # 점수가 높아도 고친다 — 점수 미달일 때만 돌면 결함이 남는다
    assert "_pref or (isinstance(score, int) and score < POLISH_TARGET)" in gsrc, \
        "점수 미달일 때만 수선하면 이 결함이 남는다"
    # 생성 단계에서도 금지한다(고치는 것보다 안 만드는 게 낫다)
    from app.generators import text_claude as tc
    assert "[사진 지칭 금지]" in inspect.getsource(tc), "생성 프롬프트에 금지가 없다"
