"""
영상 자막 규칙 박제(2026-08-01 수정분 부채 청산).

발행 산출물에 직결되는 계열이라 최우선으로 못 박는다. 각 테스트는 '수정 전 상태로
되돌리면 실패한다'를 기준으로 만들었다.

박제 대상(커밋 33c5c2d, 43c1016, 4368dcb, cb1b98e, 03cfdbe):
  A. 겁주기 목록이 생성·검사 단일 소스인가
  B. 훅 게이트 위반이 '영상 전체 중단'이 아니라 '훅만 교체'로 강등되는가
  C. 미완결·자기참조 자막이 걸러지는가
  D. 화면-자막 일치: 사진마다 그 사진에 대한 말이 붙는가
  E. 자막 조각이 완결되는가(라벨·짝없는 인용·조사 종결 없음)
"""
from __future__ import annotations

import re

from app.generators.video import (FEAR_PATTERNS, SceneScript, _hook_gate,
                                  _lines_for_photos, _SELFREF, _subtitle_gate)

SRC = ("2022년식 투싼 하이브리드 N라인, 실주행 57,216km, 무사고, 가격 2990만원. "
       "성능점검기록부 확인. 엔진룸 직접 검수. 부산 기장 주안모터스.")


def test_fear_phrases_blocked_in_subtitles():
    """A. 겁주기 표현은 자막 게이트가 잡는다.
    실측: '호구 될까 불안하다면'이 검사 정규식('호구 잡'만 봄)을 빠져나가 영상에 구워졌다."""
    for line in ("호구 될까 불안하다면, 여기부터 보세요", "호구 안 잡힙니다",
                 "사기 당할까 봐 걱정되셨죠?", "모르면 손해"):
        bad = _subtitle_gate(SceneScript(hook=line, sentences=["오시면 보여드립니다"], outro="",
                                         source="x", evidence=SRC), SRC, "주안모터스")
        assert bad, f"겁주기 표현이 통과함: {line}"


def test_normal_subtitles_pass():
    """A-역: 사실 서술은 통과해야 한다(과잉 차단 방지)."""
    for line in ("성능점검기록부부터 보여드릴게요", "2022년식 무사고, 2990만원입니다",
                 "엔진룸까지 직접 검수했습니다"):
        bad = _subtitle_gate(SceneScript(hook=line, sentences=["오시면 보여드립니다"], outro="",
                                         source="x", evidence=SRC), SRC, "주안모터스")
        assert not bad, f"정상 문장이 차단됨: {line} ({bad})"


def test_hook_violation_is_soft_not_fatal():
    """B. 훅 게이트 위반은 '훅만 교체'로 강등돼야 한다.
    실측 사고: 사유 문구에 '훅'이 없어 하드 위반으로 분류돼 영상 전체 생성이 중단됐다.
    호출부는 사유 문구 부분일치로 소프트/하드를 가른다 — 그래서 문구에 '훅'이 있어야 한다."""
    bad = _hook_gate("부산 기장 중고차 모르면 손해", "부산 기장 중고차", "local", "부산 기장")
    assert bad, "훅 게이트가 키워드 원형 삽입을 못 잡음"
    soft_keys = ("중복", "미이행", "과장", "서식", "인용", "훅")
    assert any(k in bad for k in soft_keys), f"강등 경로로 못 들어감(영상 전체 중단): {bad}"


def test_selfref_subtitles_rejected():
    """C. 영상에서 '글'을 가리키는 자막은 쓰지 않는다(영상만 보는 사람에겐 앞뒤가 끊긴 말)."""
    assert _SELFREF.search("부산 중고차, 고민 끝. 이 글이면 충분")
    assert _SELFREF.search("서류까지 본문에서 확인하세요")
    assert not _SELFREF.search("성능점검기록부 2페이지, 직인과 실차 사진까지")


def test_photo_first_alignment():
    """D. 화면-자막 일치 — 사진을 먼저 놓고 그 사진에 대한 말을 고른다.
    실측 사고: 지시어 없는 자막에 '남은 사진 아무거나'가 배정돼 차 후면 사진에
    '고민 끝. 이 글이면 충분'이 붙었다."""
    imgs = [f"p{i}" for i in range(4)]
    src = ("[사진1] 흰색 차량의 전면 외관, 라디에이터 그릴\n"
           "[사진2] 성능점검기록부 서류, 사고 이력 없음 표기\n"
           "[사진3] 보닛을 연 엔진룸 내부, 고전압 배선\n"
           "[사진4] 디지털 계기판 화면, 주행거리 57,216km\n")
    cands = ["부산 중고차, 고민 끝. 이 글이면 충분",            # 자기참조 → 탈락해야
             "성능점검기록부에 사고 이력 없음으로 표기돼 있습니다",
             "계기판 주행거리 57,216km 그대로입니다"]

    def gate(ln):
        return "자기참조" if _SELFREF.search(ln) else ""
    gi, gl = _lines_for_photos(imgs, src, cands, gate=gate)
    assert len(gl) >= 3, f"자막이 너무 적게 생성됨: {gl}"
    assert not any("이 글" in x for x in gl), f"자기참조 자막이 사진에 붙음: {gl}"
    # 서류 사진에는 서류 이야기가 붙어야 한다(사진↔자막 일치)
    pair = dict(zip(gi, gl))
    assert "기록부" in pair.get("p1", ""), f"서류 사진에 다른 말이 붙음: {pair.get('p1')}"


def test_subtitle_fragments_are_complete():
    """E. 자막 조각은 완결돼야 한다 — 라벨·짝없는 인용·조사 종결 금지.
    실측: '* 피사체/문자: 공식', '…기록부 서식 문서와', "MICHELIN' 브랜드명과 '235/55 R"."""
    imgs = [f"p{i}" for i in range(3)]
    src = ("[사진1] * 피사체/문자: 공식 '자동차성능·상태점검기록부' (1페이지)\n"
           "[사진2] 'MICHELIN' 브랜드명과 '235/55 R 19' 규격 문자가 각인된 타이어\n"
           "[사진3] [오버레이]\n")
    gi, gl = _lines_for_photos(imgs, src, [], gate=lambda _l: "")
    for ln in gl:
        assert "피사체" not in ln, f"내부 라벨 노출: {ln}"
        assert "[오버레이]" not in ln, f"내부 표기 노출: {ln}"
        assert ln.count("'") % 2 == 0, f"짝 없는 인용으로 끝남: {ln}"
        assert not re.search(r"(와|과|의|에|으로|로|및)$", ln), f"조사로 끝남: {ln}"
        assert len(ln) >= 8, f"의미 없는 조각: {ln}"


def test_scene_expansion_adopts_its_photos_too():
    """F. 화면-자막 일치는 사장님이 '절대 불변'이라 하신 원칙이다.
    실측 결함(2026-08-02): 30초 하한 확장이 성공하면 자막만 sent2로 바꾸고 사진 목록(vid_imgs)은
    옛것을 그대로 뒀다. 그러면 뒤따르는 화질 재빌드가 '새 자막 + 옛 사진'으로 다시 굽는다 —
    어긋난 영상이 그대로 발행된다. 확장이 이겼으면 그 확장이 쓰던 사진도 함께 채택해야 한다."""
    import inspect
    from app.generators import video as _v
    src = inspect.getsource(_v.ShortVideoGenerator._naver_video)
    i = src.find("opening2, sent2")
    assert i > 0, "씬 확장 채택부를 못 찾음"
    seg = src[i:i + 400]
    assert "vid_imgs = _vi2" in seg, "자막만 바꾸고 사진은 옛것을 유지함(화면-자막 어긋남)"
    # 재빌드가 참조하는 변수와 같은 이름이어야 의미가 있다
    assert "vid_imgs, SceneScript(hook=opening, sentences=sent" in src, \
        "재빌드 경로가 바뀌었다 — 이 계약을 다시 확인하라"


def test_scene_pairs_are_recorded_for_verification():
    """F2. 불변 원칙이면 검증 가능해야 한다. 자막만 남기면 '일치했는가'를 영상을 눈으로
    봐야만 알 수 있다 — 사진 basename과 자막의 짝을 기록으로 남긴다."""
    import inspect
    from app.generators import video as _v
    src = inspect.getsource(_v.ShortVideoGenerator._naver_video)
    assert '"scene_pairs"' in src, "화면-자막 짝을 기록하지 않음"
    i = src.find('"scene_pairs"')
    seg = src[i:i + 300]
    assert "vid_imgs" in seg and "enumerate(sent)" in seg, "짝이 실제 렌더 입력에서 나오지 않음"
    from app import main as _m
    assert "naver_pairs" in inspect.getsource(_m), "진단에서 짝을 읽을 수 없음"


def test_split_lines_keep_their_photo():
    """G. 분할이 짝을 깨면 안 된다(2026-08-02 실측: 사진 9장인데 자막 12줄 → 뒤 3씬 사진 없음).
    _cap_lines 주석은 '분할 조각은 같은 사진을 쓴다'고 적혀 있었지만, 사진을 늘려주는 코드가
    어디에도 없었다. 주석이 약속하고 코드가 안 지킨 계약."""
    from app.generators.video import _cap_lines
    long_ = ("아주 긴 문장인데 쉼표로 나뉘고, 또 이어지며 계속되고, "
             "더 길어져서 세 줄을 훌쩍 넘기게 만드는 문장입니다")
    lines, imgs = _cap_lines(["짧은 줄", long_], imgs=["A.jpg", "B.jpg"])
    assert len(lines) == len(imgs), f"자막 {len(lines)}줄 vs 사진 {len(imgs)}장"
    assert all(i for i in imgs), f"사진 없는 씬이 있음: {imgs}"
    assert imgs[0] == "A.jpg"
    assert set(imgs[1:]) == {"B.jpg"}, f"분할 조각이 원본 사진을 안 물음: {imgs}"


def test_naver_path_keeps_pairing_through_recap():
    """G2. 네이버 경로는 캡을 두 번 탄다 — 두 번 다 짝을 유지해야 한다."""
    import inspect
    from app.generators import video as _v
    src = inspect.getsource(_v.ShortVideoGenerator._naver_video)
    assert "_cap_lines(_pairs_l[:9], imgs=_pairs_i[:9])" in src, "1차 캡에서 짝이 끊김"
    assert "_cap_lines([_strip_labels(s) for s in sent], imgs=vid_imgs)" in src, "2차 캡에서 짝이 끊김"


def test_camera_meta_is_not_a_subtitle():
    """H. 자막은 사진을 설명하는 말이 아니라 파는 말이어야 한다(2026-08-02 실측).
    실제로 구워진 자막: '흰색 투싼 전면 45도 앵글, 스튜디오 배경' — 손님은 각도·배경을 사지 않는다.
    화자는 가게(파는 쪽)여야 한다는 원칙에도 어긋난다."""
    from app.generators.video import _lines_for_photos
    src = ("[사진1] 흰색 투싼 전면 45도 앵글, 스튜디오 배경\n"
           "[사진2] 디지털 계기판 클러스터, 주행거리 57,216km 표시\n"
           "[사진3] 후드 오픈 상태의 엔진룸, 클로즈업 샷\n")
    gi, gl = _lines_for_photos(["p0", "p1", "p2"], src, [], gate=lambda _l: "")
    for ln in gl:
        for bad in ("앵글", "배경", "구도", "샷", "클로즈업", "조명", "화각"):
            assert bad not in ln, f"촬영 메타가 자막에 남음: {ln}"
    # 실제 정보(주행거리)는 살아야 한다 — 메타 제거가 사실까지 지우면 안 된다
    assert any("57,216km" in ln for ln in gl), f"천 단위 쉼표가 깨졌거나 사실이 사라짐: {gl}"


def test_cleanup_order_and_modifier_split():
    """I. 실측 2건(2026-08-02, 재생성 영상에서 확인).
    ① 쉼표 정리가 괄호 제거보다 먼저 돌아 '다이얼(P/R/N/D 버튼), 듀얼'이
       '다이얼 , 듀얼'로 남았다 — 정리는 다 걷어낸 뒤에 해야 한다.
    ② 하드 분할이 수식어에서 끊어 '…오렌지색 고전압'처럼 무엇이 고전압인지 없는 조각이
       자막으로 구워졌다 — 색·형·식·용·급 뒤에는 이름이 와야 말이 된다."""
    import re as _re
    from app.generators.video import _cap_lines, _lines_for_photos

    _, gl = _lines_for_photos(
        ["p0"], "[사진1] 센터콘솔부, 전자식 기어 다이얼(P/R/N/D 버튼), 듀얼 컵홀더, 주행 57,216km\n",
        [], gate=lambda _l: "")
    assert gl, "자막이 안 나옴"
    assert " ," not in gl[0], f"쉼표 앞 공백이 남음: {gl[0]!r}"
    assert "57,216km" in gl[0], f"천 단위 쉼표가 깨짐: {gl[0]!r}"

    # ② 이름을 기다리는 말에서 끊지 않는다 — 실측 자막 '…오렌지색 고전압'
    _, gl2 = _lines_for_photos(
        ["p0"], "[사진1] 후드 오픈 상태의 엔진룸, 하이브리드 시스템 관련 오렌지색 고전압 케이블과 각종 부품.\n",
        [], gate=lambda _l: "")
    assert gl2, "자막이 안 나옴"
    for ln in gl2 + _cap_lines(gl2):
        assert not _re.search(r"([가-힣]{2,}(색|형|식|용|급|압)|관련|포함|기반|전용)$", ln), \
            f"이름을 기다리는 말에서 끊김: {ln!r}"
    # 정상 문장은 그대로 살아야 한다(과잉 절단 방지)
    for ok_src, want in (("[사진1] 디지털 계기판 클러스터, 주행거리 57,216km 표시\n", "57,216km"),
                         ("[사진1] 성능점검기록부 1페이지, 사고 이력 없음 표기\n", "사고 이력 없음")):
        _, g = _lines_for_photos(["p0"], ok_src, [], gate=lambda _l: "")
        assert g and want in g[0], f"정상 묘사가 잘림: {g}"


def test_selling_lines_replace_descriptions(monkeypatch):
    """J. 자막은 '사진 설명'이 아니라 '파는 말'이어야 한다(2026-08-02 사장님 승인).
    실측 자막: '파노라마 선루프', '블랙 그릴과 라디에이터 그릴 하이라이트, 스포크형' —
    정확하지만 손님을 사게 만드는 말이 아니다. 사진 순서는 이미 고정돼 있으니
    화면-자막 일치는 그대로 두고 말만 바꾼다."""
    from app.generators import video as _v
    from app import llm as _llm

    drafts = ["파노라마 선루프", "후드 오픈 상태의 엔진룸", "실내 프론트 시트"]
    descs = ["파노라마 선루프, 블랙", "후드 오픈 엔진룸", "실내 프론트 시트"]
    monkeypatch.setattr(_llm, "call_task", lambda *a, **k: (
        "1. 선루프까지 열어서 보여드립니다\n"
        "2. 보닛 열어 엔진룸부터 확인하세요\n"
        "3. 시트 상태는 앉아보시면 압니다\n"))
    out = _v._selling_lines(descs, drafts, "무사고, 57,216km", "주안모터스", "부산 기장 중고차",
                            gate=lambda _l: "")
    assert out == ["선루프까지 열어서 보여드립니다", "보닛 열어 엔진룸부터 확인하세요",
                   "시트 상태는 앉아보시면 압니다"], out


def test_selling_lines_keep_draft_when_gate_fails(monkeypatch):
    """J2. 걸린 줄만 되돌린다 — 한 줄 때문에 전체를 버리면 영상이 통째로 나빠진다."""
    from app.generators import video as _v
    from app import llm as _llm
    drafts = ["파노라마 선루프", "엔진룸"]
    monkeypatch.setattr(_llm, "call_task", lambda *a, **k: (
        "1. 호구 잡히기 전에 보세요\n2. 보닛 열어 직접 확인하세요\n"))
    out = _v._selling_lines(["a", "b"], drafts, "사실", "가게", "키워드",
                            gate=lambda l: "겁주기" if "호구" in l else "")
    assert out[0] == "파노라마 선루프", "게이트에 걸린 줄이 그대로 나감"
    assert out[1] == "보닛 열어 직접 확인하세요", "멀쩡한 줄까지 버림"


def test_selling_lines_fall_back_on_llm_failure(monkeypatch):
    """J3. 호출이 실패하면 묘사 자막을 유지한다 — 영상이 사라지면 안 된다."""
    from app.generators import video as _v
    from app import llm as _llm
    monkeypatch.setattr(_llm, "call_task",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    drafts = ["파노라마 선루프", "엔진룸"]
    assert _v._selling_lines(["a", "b"], drafts, "사실", "가게", "키워드") == drafts


def test_selling_report_surfaces_why(monkeypatch):
    """J5. 왜 그 줄이 묘사로 남았는지는 로그에만 두면 화면에서 읽을 수 없다(조용한 실패 금지).
    실측: 7씬 중 2씬이 묘사로 남았는데 이유를 확인할 방법이 없었다."""
    import inspect
    from app.generators import video as _v
    from app import llm as _llm
    monkeypatch.setattr(_llm, "call_task", lambda *a, **k: "1. 호구 잡히기 전에 보세요\n")
    rep = {}
    _v._selling_lines(["a", "b"], ["묘사1", "묘사2"], "사실", "가게", "kw",
                      gate=lambda l: "겁주기" if "호구" in l else "", report=rep)
    assert rep.get("swapped") == 0 and rep.get("kept") == 2
    assert any("반려" in w for w in rep.get("why") or []), rep
    assert any("문장 없음" in w for w in rep.get("why") or []), rep
    # 진단으로 읽을 수 있어야 한다
    assert '"selling": _sell_rep' in inspect.getsource(_v.ShortVideoGenerator._naver_video)
    from app import main as _m
    assert "naver_selling" in inspect.getsource(_m)


def test_selling_lines_wired_into_naver_path():
    """J4. 실제 경로에 붙어 있는가 — 사진 순서를 고정한 '뒤'에 말만 바꾼다(일치 유지)."""
    import inspect
    from app.generators import video as _v
    src = inspect.getsource(_v.ShortVideoGenerator._naver_video)
    i, j = src.find("_lines_for_photos("), src.find("_selling_lines(")
    assert i > 0 and j > i, "일치 고정보다 먼저 말을 바꾸면 짝이 깨진다"
    k = src.find("_cap_lines(_pairs_l[:9]")
    assert k > j, "판매 문장 교체가 캡 뒤에 오면 길이 계약이 깨진다"
    assert "desc_map=_desc_of" in src, "원본 묘사를 재료로 넘기지 않음"


def test_outro_is_self_contained_and_industry_neutral():
    """K. 마무리 줄도 자기참조 금지다(2026-08-02 사장님 지적).
    '서류까지 본문에서 확인하세요'가 아웃트로에 박혀 있었고, 그 영상이 네이버 클립 지면으로도
    나간다 — 거기엔 '본문'이 없다. 게이트를 안 타는 경로였다.
    함께: '중고'가 업종 문자열로 박혀 있었다(업종 중립 위반)."""
    import inspect
    from app.generators import video as _v
    src = inspect.getsource(_v.ShortVideoGenerator._naver_video)
    i = src.find("_cta_line =")
    assert i > 0, "마무리 줄 생성부를 못 찾음"
    seg = src[i - 400:src.find("outro = f", i) + 200]
    for bad in ("본문에서 확인", "자세한 내용은 본문", "본문에"):
        assert bad not in seg, f"마무리가 글을 가리킨다: {bad}"
    assert "_SELFREF.search(_cta_line)" in seg, "규칙이 바뀌어도 막을 안전장치가 없음"
    # 업종 문자열이 판정에 쓰이면 안 된다 — 근거는 본문이 무엇을 다루는지에서 온다
    assert 'tenant.industry' not in seg, "업종명으로 분기함(업종 중립 위반)"
    for kw in ("중고", "썬팅", "카페", "미용"):
        assert f'"{kw}"' not in seg, f"업종어 하드코딩: {kw}"


def test_fear_list_shared_with_body_scoring():
    """A-구조: 겁주기 목록은 본문 채점기와 같은 뿌리여야 한다.
    두 곳에 따로 두면 어긋난다 — 영상에서 고친 뒤 본문에서 같은 사고가 재발했다."""
    from app import seo
    assert tuple(seo._fear_patterns()) == tuple(FEAR_PATTERNS)
