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
