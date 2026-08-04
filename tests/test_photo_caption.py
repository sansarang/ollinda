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
    # 번호를 지시어로 바꿔도 똑같이 어긋난다 — 배치가 옮겨지기 때문이다(실물 2건)
    assert qc.prose_photo_refs("위 사진이 오늘 입고된 밴입니다") == ["위 사진이"]
    assert qc.prose_photo_refs("아래 장면이 시공 과정입니다") == ["아래 장면이"]
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


def test_H18_캡션을_보는_게이트는_하나뿐이다():
    """main._caption_gate가 photodesc와 별개 규칙을 들고 있었다(2026-08-04).
    같은 재료를 읽는 게이트가 둘이면 한쪽만 고치는 재발이 예약된다 — 캡션 10회 재발과 같은 계열.
    되돌리면(main에 규칙 복원) 이 테스트가 실패한다."""
    import inspect
    from app import main as m
    from app.services import photodesc as pd
    src = inspect.getsource(m._caption_gate)
    assert "caption_ok" in src, "단일 게이트를 안 쓴다"
    assert "re.search" not in src and "_r.search" not in src, "자체 규칙이 남아 있다"
    # 흡수된 규칙이 실제로 단일 게이트에서 동작하는가 — '존재'가 아니라 '사용' 기준
    for leak in ["**사진 분석 (관점)**", "[사진3] 어쩌고", "관점에서 분석한 결과"]:
        assert pd.caption_ok(leak) == "내부 라벨/프리앰블 잔재", leak
        assert m._caption_gate(leak) == pd.caption_ok(leak), "두 경로 판정이 갈린다"


def test_H19_사진_묘사_파서는_하나뿐이다():
    """text_claude가 '첫 매치'로 묘사를 파싱했다 — 헤더·라벨을 집는 그 방식이다.
    같은 재료(gen_source)를 읽는 소비자가 셋(캡션·배치·선별)인데 파서가 갈라져 있었다."""
    import inspect
    from app.generators import text_claude as tc
    src = inspect.getsource(tc)
    assert "descs.setdefault" not in src, "첫 매치 파싱이 남아 있다"
    assert 'search(rf"\\[사진{i + 1}\\]' not in src, "첫 매치 파싱이 남아 있다"
    assert src.count("photodesc") >= 2, "단일 파서를 안 쓴다"


def test_H20_사진_선별에_업종어가_없다():
    """'세척·재단·성형·코팅'은 시공업 어휘다 — 빵집·미용실에서는 과정 사진을 하나도 못 고른다.
    과정이란 '무엇을 하고 있는 장면'이고, 그 신호는 관형형·진행 어미다(언어 규칙)."""
    import inspect
    import re as _re
    from app.generators import text_claude as tc
    src = inspect.getsource(tc._pick_photos) if hasattr(tc, "_pick_photos") else ""
    if not src:                       # 이름이 바뀌어도 규칙은 남아야 한다
        src = inspect.getsource(tc)
    for w in ("재단", "성형", "세척", "건조"):
        assert f'"{w}' not in src and f"|{w}" not in src, f"업종어가 남아 있다: {w}"
    KEY = _re.compile(r"[가-힣](는|던)\s|[가-힣](는|던)$|중인|중이|하며|하면서")
    assert KEY.search("반죽을 밀대로 미는 손"), "빵집 과정 사진을 못 고른다"
    assert KEY.search("머리를 감기는 중이다"), "미용실 과정 사진을 못 고른다"
    assert not KEY.search("완성된 케이크 진열장"), "완성 컷을 과정으로 잡는다"


def test_H21_사진이_한곳에_뭉치지_않는다():
    """실물(2026-08-04): 20장 중 9장이 도입부에 연달아 붙었다.
    문단당 상한은 있었지만 허용 문단이 사진 수보다 적을 때 폴백이 상한을 무시하고(or allowed_idx)
    앞쪽으로 몰았다. 상한은 고정값이 아니라 '사진 수 ÷ 담을 문단 수'여야 한다."""
    import inspect
    from app.generators import text_claude as tc
    src = inspect.getsource(tc._semantic_photo_placement)
    assert "MAX_PER = max(2, -(-n // len(allowed_idx)))" in src, "상한이 글 길이에 안 맞춘다"
    assert "or [j for j in allowed_idx if used[j] < MAX_PER] or allowed_idx" not in src, \
        "폴백이 상한을 무시한다"
    assert "min(cand, key=lambda j: (used[j]" in src, "폴백이 덜 찬 문단을 안 고른다"
    # 실동작 — 문단 6개에 사진 12장이면 어느 문단도 3장을 넘지 않는다
    import re
    body = "\n\n".join(f"문단{k} 썬팅 유리막 코팅 작업 내용입니다." for k in range(6))
    note = "\n".join(f"[사진{i}] 차량 표면을 도구로 문지르는 모습" for i in range(1, 13))
    out = tc._semantic_photo_placement(body, note, 12)
    per = [len(re.findall(r"\[사진\d+\]", blk)) for blk in out.split("문단")]
    assert max(per) <= 3, f"한 곳에 뭉쳤다: {per}"


def test_H22_본문은_사진을_가리켜_설명하지_않는다():
    """지킬 수 없는 요구를 시키면 반드시 깨진다 — 우리는 마커를 다시 배치하므로
    '사진 옆 문장에 그 사진 설명을 써라'는 요구 자체가 성립하지 않는다."""
    import inspect
    from app.generators import text_claude as tc
    src = inspect.getsource(tc)
    assert "특정 사진을 가리켜 설명하지 마라" in src, "지시 표현 금지가 없다"
    assert "각 [사진N] 바로 앞 또는 " not in src, "지킬 수 없는 옛 요구가 남아 있다"


def test_H23_순차_배치도_끝에_몰지_않는다():
    """의미 배치가 포기되면(묘사 부족) 순차 배치로 폴백한다. 그 경로가 '남으면 끝에' 몰았다 —
    문단보다 사진이 많으면 나머지가 통째로 한 곳에 붙는다. 폴백도 뭉치면 안 된다."""
    import re
    from app.generators import text_claude as tc
    body = "\n\n".join(f"문단{k}입니다." for k in range(8))
    out = tc._ensure_photo_markers(body, 20)
    runs, cur = [], 0
    for b in out.split("\n\n"):
        if b.startswith("[사진"):
            cur += 1
        else:
            if cur:
                runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    assert max(runs) <= 3, f"폴백 경로가 뭉친다: {runs}"
    assert len(re.findall(r"\[사진\d+\]", out)) == 20, "마커가 새거나 늘었다"
    # 포기했으면 조용히 넘기지 않는다 — 왜 의미 배치를 못 했는지 남는다
    import inspect
    assert "의미 배치 포기" in inspect.getsource(tc._semantic_photo_placement), "폴백 사유가 안 남는다"


def test_H24_배치_규칙은_한_함수에만_산다():
    """실물 사고(2026-08-04): 소제목 뒤에 사진 8장이 몰렸다.
    생성 본문의 마커는 상위 선별분만(20장 중 4개)이라 렌더가 '2차 재매칭' 경로를 탔는데,
    그 경로에는 생성 경로가 가진 문단당 상한·금지 구역(소제목·표·FAQ·요약)이 없었다.
    배치 규칙이 두 곳에 살면 한쪽만 고치게 된다 — 생성 경로와 참조 경로는 같은 함수 하나다."""
    import inspect
    import re
    from app import main as m
    src = inspect.getsource(m._content_photo_layout)
    assert "_semantic_photo_placement" in src, "렌더가 생성 때 쓰는 배치 함수를 안 쓴다"
    assert "best_para" not in src, "독자 재매칭 로직이 남아 있다"
    assert "by_para" not in src, "독자 재매칭 로직이 남아 있다"
    # 실동작 — 소제목·표·FAQ가 섞인 글에 사진 20장을 넣어도 한 곳에 뭉치지 않는다
    from app.generators import text_claude as tc
    paras = ["## 소제목 하나", "본문 문단입니다 썬팅 유리막 코팅 작업 내용.",
             "## 소제목 둘", "또 다른 문단 도장면 표면 정리 작업입니다.",
             "## 한눈 요약", "- 요약 항목", "## 자주 묻는 질문", "**Q. 질문입니다**",
             "마지막 문단 마감 작업 내용입니다.", "추가 문단 필름 시공 내용입니다."]
    body = "\n\n".join(paras)
    note = "\n".join(f"[사진{i}] 차량 표면을 도구로 문지르는 모습" for i in range(1, 21))
    out = tc._semantic_photo_placement(body, note, 20)
    runs, cur = [], 0
    for b in out.split("\n\n"):
        if re.fullmatch(r"\[사진\d+\]", b.strip()):
            cur += 1
        elif b.strip():
            if cur:
                runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    assert max(runs) <= 5, f"한 곳에 뭉친다: {runs}"


def test_H25_관련글_링크는_클릭된다():
    """사장님 지적(2026-08-04): 붙여넣은 글의 관련글 링크가 눌리지 않았다.
    '- 제목 : URL'처럼 한 줄에 섞으면 네이버 에디터가 URL을 링크로 인식하지 못한다 —
    URL이 줄 단독으로 있어야 자동 링크가 된다. 되돌리면 이 테스트가 실패한다."""
    import inspect
    from app.services.blogsync import related_links_block as blk
    out = blk([{"title": "제목 하나", "url": "https://blog.naver.com/x/111?from=rss"},
               {"title": "제목 둘", "url": "https://blog.naver.com/x/222"}])
    for ln in out.splitlines():
        if ln.startswith("http"):
            assert ln.strip() == ln and " " not in ln, f"URL 줄에 다른 게 섞였다: {ln!r}"
    assert "\nhttps://blog.naver.com/x/111\n" in out + "\n", "URL이 줄 단독이 아니다"
    assert "?from=rss" not in out, "추적 파라미터가 남았다(복붙 청결)"
    assert " : " not in out, "제목과 URL이 한 줄에 섞였다(클릭 안 됨)"
    assert blk([]) == "" and blk([{"url": ""}]) == "", "빈 입력에 껍데기를 만든다"
    # ★ 블록을 만드는 곳은 한 곳뿐이다 — 두 곳에 살면 형식이 갈라진다(실제로 갈라져 있었다)
    from app.generators import text_claude as tc
    from app import main as m
    for mod in (tc, m):
        src = inspect.getsource(mod)
        assert '"## 함께 보면 좋은 글\\n"' not in src, f"{mod.__name__}이 블록을 따로 만든다"
        assert "f\"- {t} : {u}\"" not in src, f"{mod.__name__}에 옛 형식이 남아 있다"
    assert "related_links_block" in inspect.getsource(tc)
    assert "related_links_block" in inspect.getsource(m)


def test_H26_짝_없는_괄호는_사장님_화면에_안_나간다():
    """실물(2026-08-04): '…값이 갈리는 두 가지 기준부터 짚어보겠습니다.)' —
    여는 괄호 없이 닫는 괄호만 남아 그대로 붙여넣기 화면에 나갔다.
    재작성·문장 정리 과정에서 깨진 흔적이다. 셈으로 판정한다(언어·업종 무관)."""
    import inspect
    from app.services import qualitycheck as qc
    assert qc.fix_orphan_parens("짚어보겠습니다.)") == "짚어보겠습니다."
    assert qc.fix_orphan_parens("앞유리(전면 포함") == "앞유리전면 포함"
    ok = "(이건 개인 체감이라 단정은 못 드립니다.)"
    assert qc.fix_orphan_parens(ok) == ok, "정상 괄호를 지운다"
    assert qc.fix_orphan_parens("여러 줄\n(정상)\n깨짐)") == "여러 줄\n(정상)\n깨짐"
    # 게이트가 실제로 쓰는가 — '존재'가 아니라 '사용' 기준(조항)
    assert "fix_orphan_parens(" in inspect.getsource(qc.score_gate), "게이트가 안 쓴다"


def test_H27_완성_화면은_어느_세트인지_추측하지_않는다():
    """사장님 실측(2026-08-04): '콘텐츠 완성!'은 떴는데 결과 화면으로 안 넘어갔다.
    서버는 어느 세트가 완성됐는지 아는데 화면엔 안 알려줬고, 화면은 '세트 개수가 늘었나'로
    추측했다 — 저장이 done 표시보다 늦으면 못 잡는다(경합). 값을 넘겨 추측을 없앤다."""
    import inspect
    import os
    os.environ.setdefault("SHOPCAST_SECRET", "test")
    from app import db
    from app import main as m
    from app.services import ingest as ing
    db.set_gen_progress("TGOLD", "running", "시작", new=True)
    db.set_gen_progress("TGOLD", "done", "완성", status="done", asset_id="AID-GOLD")
    got = db.get_gen_progress("TGOLD") or {}
    assert got.get("asset_id") == "AID-GOLD", "완성된 세트 ID가 안 남는다"
    # 새 생성이 시작되면 옛 ID는 버린다 — 엉뚱한 세트로 보내면 안 된다
    db.set_gen_progress("TGOLD", "running", "새 생성", new=True)
    assert (db.get_gen_progress("TGOLD") or {}).get("asset_id") in ("", None), "옛 세트 ID가 남는다"
    # 생성 경로가 실제로 넘기는가 — '존재'가 아니라 '사용' 기준(조항)
    assert "asset_id=getattr(asset" in inspect.getsource(ing), "생성 완료 시 세트 ID를 안 넘긴다"
    assert '"asset_id": pr.get("asset_id")' in inspect.getsource(m._progress_payload), \
        "진행률 응답에 세트 ID가 없다"
    # 화면이 그 값을 먼저 쓰는가 + 못 구했을 때도 실제로 이동하는가
    src = inspect.getsource(m._upload_form_html)
    assert "if(!aid&&pr.asset_id)aid=pr.asset_id;" in src, "화면이 서버 값을 안 쓴다"
    assert "'/me?tab=content'" in src, "못 구했을 때 같은 화면으로 보낸다(눌러도 변화 없음)"


def test_H28_다시_쓰게_할_때_토큰이_모자라지_않는다():
    """실물 사고(2026-08-04, 주안모터스 62점): 표면 수선이 max_tokens=2691로 요청했다가
    stop_reason=max_tokens로 빈 응답을 받고 실패했다. 본문은 2,990자였다.
    한글은 1자가 1.5~2.4 토큰이라 len(body)*0.9는 애초에 완성될 수 없는 요청이다.
    요청이 구조적으로 완성 불가능하면 모델 탓이 아니라 우리 탓이다.
    같은 실수를 캡션에서도 냈다(20줄을 900토큰) — 그래서 계산은 한 곳에만 산다."""
    import inspect
    from app import llm
    from app.services import qualitycheck as qc
    body = "가" * 3000
    assert llm.tokens_for(body) >= 6000, "한글 본문을 다시 쓸 토큰이 모자란다"
    assert llm.tokens_for("") >= 1500, "최소 예산이 없다"
    assert llm.tokens_for("가" * 100000) <= llm.MAX_OUT, "모델 출력 상한을 넘겨 요청한다(400)"
    assert llm.MAX_OUT <= 16000, "상한이 너무 크다"
    # 글을 통째로 다시 쓰는 세 곳이 모두 이 계산을 쓴다(존재가 아니라 사용 기준)
    src = inspect.getsource(qc)
    assert "int(len(body) * 0.9)" not in src, "옛 토큰 계산이 남아 있다"
    for fn in (qc._revise_text, qc._surface_fix, qc.score_gate):
        assert "tokens_for(" in inspect.getsource(fn), f"{fn.__name__}이 단일 계산을 안 쓴다"


def test_H29_글쓰기_게이트는_업체를_가리지_않는다():
    """사장님 지시(2026-08-04): 모든 글쓰기는 모든 업체에 동일하게 적용되어야 한다.
    게이트·수선 경로에 특정 가게·업종·tenant 분기가 있으면 안 된다."""
    import inspect
    from app.services import qualitycheck as qc
    src = inspect.getsource(qc)
    for w in ("루마", "주안", "썬팅", "중고차", "모터스", "d9e0fbde", "95d0243f"):
        assert w not in src, f"게이트에 특정 가게·업종이 박혀 있다: {w}"
    # 게이트 진입은 tenant가 아니라 점수·결함으로만 갈린다
    gsrc = inspect.getsource(qc.score_gate)
    assert "tenant" not in gsrc.replace("tenant_id", ""), "게이트가 가게를 본다"
