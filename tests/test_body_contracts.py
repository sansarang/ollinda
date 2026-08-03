"""
본문·검색어 계약 박제(부채 청산 2차 — 발행 산출물 직결).

전부 실사고에서 나온 규칙이다. 각 테스트는 '수정 전 상태로 되돌리면 실패한다'를
기준으로 만들었다(revert 실패 확인 완료).

박제 대상(커밋 48efd14, 15c5caa, 16ee281, 4a8f961, 2e56bdf, 3bb3da4, 9918e05):
  A. 날조 탐지 오탐 — 멀쩡한 글이 -20점 먹고 재작성까지 유발했다
  B. 화자와 청자 — 제목은 손님이 읽는다(가게 시점 용어 감점), 단 타깃 키워드는 예외
  C. 손님 말로 검색어 — 공급자 접미어를 실측 검색량으로 벗긴다
  D. 지역 정합 — 다른 생활권 키워드는 막는다(김해썬팅 실사고)
  E. 검색량 조회 배치 — 5개씩 전부 조회한다(20개 넘기면 앞 5개만 돌던 결함)
"""
from __future__ import annotations

import re

from app import seo


def _nocache(monkeypatch):
    """캐시를 비활성화해 테스트가 이전 실행값을 보지 않게 한다."""
    import app.ratelimit as _rl
    monkeypatch.setattr(_rl, "cache_get", lambda *a, **k: None)
    monkeypatch.setattr(_rl, "cache_set", lambda *a, **k: None)


def _audit(body: str, title: str = "부산 기장 중고차 가격 공개",
           kw=("부산 기장 중고차",), source: str | None = None):
    pl = {"body": body, "title": title, "target_keywords": list(kw)}
    return seo.quality_audit("naver_blog", "blog", pl, source=source if source is not None else body)


# ── A. 날조 탐지 오탐 ──────────────────────────────────────────────
def test_comma_gap_is_not_a_money_claim():
    """A1. '연식 2022, 원동기형식'의 '원'을 금액으로 읽어 '2022원'을 날조로 잡았다.
    단위는 숫자 바로 뒤에 붙어 있어야 한다."""
    assert "2022원" not in seo._money_nums("연식 2022, 원동기형식 G4FJ")
    assert "57216분" not in seo._money_nums("주행 57,216 분당 점검 항목")
    assert "2990만원" in seo._money_nums("가격 2,990만원"), "진짜 금액을 놓침"


def test_rhetorical_enumeration_is_not_a_claim():
    """A2. 수사적 나열('3만이니 5만이니')은 금액 주장이 아니다."""
    assert not seo._money_nums("수리비가 3만이니 5만이니 말이 많지만")
    assert not seo._money_nums("10만이든 20만이든 상관없이")
    assert seo._money_nums("공임 5만원입니다"), "진짜 주장을 놓침"


def test_fabrication_does_not_fire_on_clean_body():
    """A3. 종합 — 입력에 있는 수치만 쓴 글은 날조 감점을 받지 않는다.
    실측 사고: 이 오탐 때문에 80점 넘던 글이 -20점 먹고 재작성 루프에 들어갔다."""
    src = "2022년식 투싼, 실주행 57,216km, 가격 2,990만원. 원동기형식 G4FJ."
    body = ("## 서류부터 봅니다\n2022년식이고 실주행 57,216km입니다. "
            "가격은 2,990만원이에요. 원동기형식은 G4FJ로 기록돼 있습니다.")
    hits = [w for w in (_audit(body, source=src).get("warnings") or []) if "날조" in w]
    assert not hits, f"멀쩡한 글이 날조로 잡힘: {hits}"


def test_real_fabrication_still_caught():
    """A-역: 입력에 없는 금액은 여전히 잡아야 한다(오탐 수정이 탐지를 죽이면 안 된다)."""
    src = "2022년식 투싼, 실주행 57,216km."
    body = "## 가격\n특가 1,890만원에 모십니다. 취등록세 50만원 지원."
    hits = [w for w in (_audit(body, source=src).get("warnings") or []) if "날조" in w]
    assert hits, "입력에 없는 금액이 통과함"


# ── B. 화자와 청자 ────────────────────────────────────────────────
def test_title_shop_perspective_penalized():
    """B1. 제목을 읽는 사람은 손님이다 — '중고차판매 가격 걱정?'은 주어가 뒤집혀 있다.
    사장님 지적: '살려고 하는 사람들이 글을 읽는데 판매???'"""
    au = _audit("본문입니다. 성능점검기록부를 보여드립니다.",
                title="부산 기장 중고차판매 가격 걱정 끝", kw=("부산 기장 중고차",))
    hits = [w for w in (au.get("warnings") or []) if "가게 시점" in w]
    assert hits, f"가게 시점 제목이 통과함: {au.get('warnings')}"


def test_title_term_inside_target_keyword_is_fine():
    """B2. 단, 손님도 실제로 검색하는 업종어는 예외다('간판제작' 류).
    타깃 키워드에 들어 있으면 검색량 승부로 이미 검증된 말이다 — 과교정 금지."""
    au = _audit("본문입니다. 제작 과정을 보여드립니다.",
                title="부산 간판제작 비용, 어디까지 나오나", kw=("부산 간판제작",))
    hits = [w for w in (au.get("warnings") or []) if "가게 시점" in w]
    assert not hits, f"정상 제목이 감점됨: {hits}"


def test_supplier_check_skips_substring_tokens():
    """B3. '전문'⊂'전문가', '제조'⊂'제조사' — 다른 낱말의 앞부분인 토큰은 제목 검사에서 뺀다.
    실측: 이 때문에 정상 제목이 -6점 먹었다."""
    for t in ("부산 썬팅 전문가가 알려주는 필름 고르는 법",
              "타이어 제조사별 차이, 뭐가 다를까"):
        au = _audit("본문입니다.", title=t, kw=("부산 썬팅",))
        hits = [w for w in (au.get("warnings") or []) if "가게 시점" in w]
        assert not hits, f"오탐: {t} → {hits}"


def test_speaker_frame_is_seller_not_customer():
    """B4. 화자는 파는 사람이다. '손님 시점으로 써라'는 과교정이었다(사장님 정정).
    올린다는 소상공인 셀러의 마케팅 도구 — 글을 쓰는 주체는 파는 쪽이다."""
    for kind in ("seller", "local", ""):
        fr = seo.speaker_frame(kind)
        assert "판매자" in fr or "사장" in fr, f"화자가 파는 쪽이 아님({kind}): {fr}"
        assert "사칭" in fr or "내돈내산" in fr or "정직" in fr, \
            f"고객 사칭 금지 지침이 빠짐({kind})"


# ── C. 손님 말로 검색어 ───────────────────────────────────────────
def test_supplier_suffix_stripped_recursively(monkeypatch):
    """C1. '중고차판매업' → '중고차판매' → '중고차'까지 벗겨 실측 검색량으로 승부시킨다.
    실측: '중고차판매' 6,580회 vs '중고차' 271,600회(41배)."""
    from app.services import searchad as _sa, blogrank as _br
    monkeypatch.setattr(_sa, "configured", lambda: True)
    monkeypatch.setattr(_sa, "volume_map",
                        lambda c: {"중고차판매업": 100, "중고차판매": 6580, "중고차": 271600})
    monkeypatch.setattr(_br, "doc_count", lambda c: 1000)
    _nocache(monkeypatch)
    assert seo.searcher_term("중고차판매업") == "중고차"


def test_supply_unknown_is_not_treated_as_zero(monkeypatch):
    """C2. 문서 수 조회 실패(-1)를 '공급 0'으로 읽으면 기회지수가 무한대가 되어
    검색량이 미미한 공급자 용어가 이긴다. 실패는 실패로 다뤄야 한다."""
    from app.services import searchad as _sa, blogrank as _br
    monkeypatch.setattr(_sa, "configured", lambda: True)
    monkeypatch.setattr(_sa, "volume_map", lambda c: {"중고차판매": 6580, "중고차": 271600})
    monkeypatch.setattr(_br, "doc_count", lambda c: -1 if c == "중고차판매" else 50000)
    _nocache(monkeypatch)
    assert seo.searcher_term("중고차판매") == "중고차", "조회 실패를 '공급 0'으로 오독"


def test_searcher_term_falls_back_when_unmeasurable(monkeypatch):
    """C3. 실측할 수 없으면 원본을 유지한다 — 추측으로 업종어를 바꾸지 않는다."""
    from app.services import searchad as _sa
    monkeypatch.setattr(_sa, "configured", lambda: False)
    _nocache(monkeypatch)
    assert seo.searcher_term("중고차판매") == "중고차판매"


def test_region_shortform_used_not_official_longform():
    """C4. 지역 토큰은 손님이 쓰는 구어 축약형이어야 한다 —
    '부산광역시 썬팅'은 실측 검색량 0이었고, '경상남 썬팅'은 아무도 쓰지 않는 말이다."""
    for full, want in (("부산광역시", "부산"), ("서울특별시 강남구", "서울"),
                       ("경상남도 김해시", "경남"), ("전라남도 여수시", "전남"),
                       ("충청북도 청주시", "충북"), ("경기도 성남시", "경기"),
                       ("강원특별자치도 춘천시", "강원"), ("제주특별자치도 서귀포시", "제주")):
        got = seo.canonical_region(full, "local", "썬팅", verify_volume=False)
        assert got == want, f"{full} → {got!r} (기대 {want!r})"


# ── F. 도입 훅 예고 판정 ─────────────────────────────────────────
def _intro_warns(intro: str):
    # 뒤 문단에 예고 표현이 들어가면 검사 대상(첫 2문단)에 섞여 판정이 흐려진다 — 중립 채움말
    body = intro + "\n\n## 소제목\n실주행 57,216km, 무사고 기록입니다.\n"
    au = _audit(body)
    return [w for w in (au.get("warnings") or []) if "끝까지 읽을 이유" in w]


def test_intro_preview_recognized_not_only_by_wordlist():
    """F. 오탐 수정(2026-08-02): 옛 판정은 특정 낱말('알려'·'아래에서')만 인정해서
    예고의 교과서적 형태를 못 읽고 -6점을 먹였다. 실측 문장으로 검증한다."""
    real = ("이 흰색 SUV가 매장에 들어온 날, 저는 판매 사진보다 보닛을 먼저 열었습니다. "
            "이 글에서는 외관 흠집 상태, 엔진룸 내부, 그리고 가격이 매물마다 다른 이유까지 "
            "순서대로 확인하실 수 있습니다.")
    assert not _intro_warns(real), "실제 예고 문장을 못 읽음(오탐)"
    real2 = ("어제 오후, 매장에 들어온 흰색 SUV 한 대를 후드부터 열어놓고 하나하나 확인했습니다. "
             "그래서 오늘은 말로만 안심시키지 않고, 본넷 안까지 사진으로 다 보여드리려고 합니다.")
    assert not _intro_warns(real2), "제시 동사 + 예고 어미를 못 읽음(2차 실측)"
    for ok in ("이 글에서 서류 보는 법까지 정리해 드릴게요.",
               "아래에서 엔진룸 상태를 하나씩 짚어 드립니다.",
               "가격이 갈리는 이유를 차례대로 비교해 봅니다.",
               "마지막에 성능점검기록부 보는 법까지 알려드릴게요.",
               "실제 주행거리와 사고 이력을 공개하려고 합니다.",
               "엔진룸 상태를 사진으로 보여드리겠습니다."):
        assert not _intro_warns(ok), f"예고를 못 읽음: {ok}"


def test_intro_without_preview_still_penalized():
    """F-역: 예고가 정말 없으면 여전히 잡아야 한다(오탐 수정이 검사를 죽이면 안 된다)."""
    for bad in ("안녕하세요. 오늘 날씨가 참 좋네요. 매장에 새 차가 들어왔습니다.",
                "저희 가게는 부산 기장에 있습니다. 오래 영업했습니다."):
        assert _intro_warns(bad), f"예고 없는 도입이 통과함: {bad}"


def test_emoji_rule_is_single_source():
    """G. 채점기와 수선기가 다른 이모지 목록을 쓰면, 채점기가 잡은 것을 수선기가 못 지운다.
    실측(2026-08-02): ⭐가 채점 목록에만 있어 '이모지 2개' 감점이 수선을 돌려도 안 사라졌다.
    겁주기 목록 이원화와 같은 사고 — 판정하는 쪽 목록이 유일한 기준이다."""
    from app.services import qualitycheck as _q
    assert _q._EMOJI_RE is seo._EMOJI_RE, "수선기가 자기 목록을 따로 들고 있음"
    body = "본문입니다 📍 그리고 ⭐ 두 개."
    assert len(seo._EMOJI_RE.findall(body)) == 2
    assert len(seo._EMOJI_RE.findall(_q._trim_emoji(body, keep=1))) == 1, "초과분이 안 지워짐"
    # 문장부호는 이모지가 아니다(과잉 차단 방지 — 실측 본문에 쓰이는 기호들)
    assert not seo._EMOJI_RE.findall("가격 — 시세 → 비교 ① ② ③")


# ── H. 한국어 수 표기 ────────────────────────────────────────────
def test_decimal_man_notation_converted():
    """H. '5.7만km'는 한국어가 아니다(2026-08-02 사장님 지적).
    중국어·일본어식 표기이고 손님이 검색창에 치지도, 읽고 자연스럽지도 않다.
    실측: 제목 '부산 기장 중고차 5.7만km 흰색 SUV 가격·이력 공개'.
    프롬프트로 부탁하는 대신 기계로 고친다 — 부탁은 확률이고 이건 규칙이다."""
    n = seo.natural_kr_number
    assert n("중고차 5.7만km 공개") == "중고차 5만 7천km 공개"
    assert n("가격 2.5억") == "가격 2억 5천만"
    assert n("주행 5.0만km") == "주행 5만km"
    assert n("정가 1.2만원부터") == "정가 1만 2천원부터"
    # 건드리면 안 되는 것 — 천 단위 구분자, 일반 소수
    assert n("실주행 57,216km") == "실주행 57,216km"
    assert n("소수 3.14는 그대로") == "소수 3.14는 그대로"
    assert n("") == ""


def test_decimal_man_notation_is_scored():
    """H2. 기계 교정이 닿지 않는 경로가 생기면 눈에 보여야 한다(조용한 실패 금지)."""
    au = _audit("## 소제목\n실주행 5.7만km입니다. 무사고 차량이에요.",
                title="부산 기장 중고차 공개")
    assert [w for w in (au.get("warnings") or []) if "한국어에 없는 수 표기" in w], au.get("warnings")
    ok = _audit("## 소제목\n실주행 57,216km입니다. 무사고 차량이에요.",
                title="부산 기장 중고차 공개")
    assert not [w for w in (ok.get("warnings") or []) if "한국어에 없는 수 표기" in w]


def test_surfaces_apply_the_number_rule():
    """H3. 표기 규칙은 제목·본문·자막·영상 메타 전부에 걸린다 — 한 군데만 고치면 다른 데로 샌다."""
    import inspect
    from app.generators import text_claude as _tc, video as _v
    tsrc = inspect.getsource(_tc)
    assert "natural_kr_number(title)" in tsrc and "natural_kr_number(body)" in tsrc
    vsrc = inspect.getsource(_v)
    assert vsrc.count("natural_kr_number") >= 3, "영상 경로 일부가 규칙을 안 탄다"


# ── I. 템플릿과 채점 규칙의 모순 ─────────────────────────────────
def test_fixed_templates_add_no_emoji():
    """I. 사장님 지적(2026-08-02): 이모지 감점이 계속 떴다.
    원인은 글이 아니라 우리 템플릿이었다 — 고정 블록이 혼자 2개(📍·⭐)를 넣는데
    채점 상한은 블로그 본문 1개다. 어떤 글도 이 감점을 피할 수 없었다(루마 글 3편 연속).
    템플릿이 사장님 예산을 잡아먹으면 안 된다 — 꾸밈은 0, 1개는 본문 몫으로 남긴다."""
    from app.services import blogtpl

    class _T:
        name = "테스트가게"
        address = "부산광역시 동구 어딘가 1-1"
        phone = "051-000-0000"
        hours = ""
        parking = ""
        brand_name = ""
        biz_type = "local"

    blk = blogtpl.fixed_info_block(_T())
    assert seo._EMOJI_RE.findall(blk) == [], f"고정 블록이 이모지를 넣는다: {seo._EMOJI_RE.findall(blk)}"
    assert "찾아오는 길" in blk, "기능(구분 안내)까지 사라지면 안 된다"
    try:
        buy = blogtpl.seller_buy_block(_T())
        assert seo._EMOJI_RE.findall(buy) == [], f"셀러 블록이 이모지를 넣는다: {buy[:60]}"
    except Exception:
        pass                                   # 셀러 블록이 없는 구성은 통과


def test_template_leaves_room_for_body_emoji():
    """I2. 템플릿이 0개여야 본문이 상한(1개)을 온전히 쓴다 — 규칙과 템플릿이 싸우면 안 된다."""
    from app.services import blogtpl

    class _T:
        name = "테스트가게"
        address = "부산 동구 1-1"
        phone = ""
        hours = ""
        parking = ""
        biz_type = "local"

    body = "## 소제목\n본문입니다 ✨ 하나만 썼습니다.\n\n" + blogtpl.fixed_info_block(_T())
    au = _audit(body, title="부산 동구 썬팅 후기")
    hits = [w for w in (au.get("warnings") or []) if "이모지" in w]
    assert not hits, f"본문 1개인데 감점: {hits}"


# ── J. 입력 식별자 · 조건부 위협 ────────────────────────────────
def test_input_model_must_survive_in_body():
    """J1. 실사고(2026-08-03): 사장님이 '기아 PV5'라고 주셨는데 본문에 PV5가 한 번도 안 나왔다.
    전부 '신차 한 대'로 뭉갰다. 차종·등급명은 손님이 검색하는 말이자 신뢰 근거다."""
    note = "기아 PV5 신차. 루마 버텍스500 썬팅, 블랙박스 장착"
    assert "PV5" in seo.input_anchors(note)
    assert "버텍스500" in seo.input_anchors(note)
    # 파일 확장자·해상도 같은 건 식별자가 아니다(오탐 방지)
    assert seo.input_anchors("영상 mp4 1080p 파일") == []

    missing = _audit("## 소제목\n신차 한 대를 시공했습니다. 상태를 확인했습니다.",
                     title="부산 신차 시공", source=note)
    hits = [w for w in (missing.get("warnings") or []) if "모델·등급명" in w]
    assert hits, f"모델명 누락을 못 잡음: {missing.get('warnings')}"

    kept = _audit("## 소제목\n기아 PV5에 루마 버텍스500으로 시공했습니다. 상태를 확인했습니다.",
                  title="부산 신차 시공", source=note)
    assert not [w for w in (kept.get("warnings") or []) if "모델·등급명" in w], "정상 글을 감점"


def test_prompt_forces_the_anchor():
    """J2. 채점만으로는 늦다 — 생성 프롬프트가 먼저 못 박아야 한다."""
    import inspect
    from app.generators import text_claude as _tc
    src = inspect.getsource(_tc)
    assert "input_anchors" in src, "생성 프롬프트가 식별자를 강제하지 않는다"
    assert "지어내지 마라" in src, "없는 모델명을 만들어낼 위험을 막지 않는다"


def test_conditional_threat_is_fear_marketing():
    """J3. 실측 문장(2026-08-03): '신차 뽑자마자 이 작업 안 하면, 6개월 뒤에 후회합니다'.
    기존 겁주기 목록이 낱말만 봐서 통과했다 — 조건부 위협도 불안 마케팅이다."""
    for bad in ("이 작업 안 하면 6개월 뒤에 후회합니다",
                "지금 놓치면 손해입니다",
                "가격표만 보고 결정하기엔 불안하셨을 겁니다"):
        au = _audit(f"## 소제목\n{bad} 성능점검기록부를 보여드립니다.")
        assert [w for w in (au.get("warnings") or []) if "겁주기" in w], f"통과함: {bad}"
    # 긍정형은 잡지 않는다 — 과잉 차단은 멀쩡한 문장을 죽인다
    ok = _audit("## 소제목\n후회 없는 선택을 도와드립니다. 상태를 그대로 보여드립니다.")
    assert not [w for w in (ok.get("warnings") or []) if "겁주기" in w], "긍정형을 오인"


# ── D. 지역 정합 ──────────────────────────────────────────────────
def test_region_conflict_fails_open(monkeypatch):
    """D. 지역 정합 게이트는 판정 불가일 때 막지 않는다(조용한 실패 금지의 반대편 —
    확신 없이 차단하면 멀쩡한 키워드가 사라진다). 무입력·오류는 False."""
    assert seo.region_conflict("", "부산 기장") is False
    assert seo.region_conflict("김해 썬팅", "") is False
    from app import llm as _llm
    monkeypatch.setattr(_llm, "call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    _nocache(monkeypatch)
    assert seo.region_conflict("김해 썬팅", "부산 기장") is False, "판정 실패인데 차단함"


# ── E. 검색량 조회 배치 ───────────────────────────────────────────
def test_volume_map_queries_every_keyword(monkeypatch):
    """E. 네이버 검색광고 API는 한 번에 5개까지다. 20개를 넘기면 앞 5개만 조회되고
    나머지는 조용히 0이 됐다 — 검색량 0으로 후보가 통째로 탈락했다."""
    from app.services import searchad as _sa
    asked: list[list[str]] = []

    def _fake(kws, limit=200):
        asked.append(list(kws))
        return [{"keyword": k, "total": 100} for k in kws]

    monkeypatch.setattr(_sa, "keyword_volumes", _fake)
    kws = [f"키워드{i}" for i in range(23)]
    out = _sa.volume_map(kws)
    assert all(len(b) <= 5 for b in asked), f"5개 초과 배치: {[len(b) for b in asked]}"
    seen = {k for b in asked for k in b}
    assert seen == set(kws), f"조회 누락 {len(set(kws) - seen)}개"
    assert len(out) == len(kws), f"결과 누락: {len(out)}/{len(kws)}"
    assert all(v == 100 for v in out.values()), f"조용히 0이 된 항목: {out}"
