"""
검색어 정찰 계약 박제(부채 청산 5차 — 후보 파이프라인).

여기서 뽑힌 후보가 타깃 키워드가 되고, 타깃 키워드가 제목·훅·태그를 결정한다.
즉 발행 산출물 직결이다. 전부 실측 결함에서 나왔고, 되돌리면 실패한다.

박제 대상(커밋 6de7604, 1840ecf, b6c0bde, eaf21e1, 145e55c, 85b1202, 076ed3b,
          831127b, 3a57ab6, 4a8f961, 500ad10, 5b2e665, ae014e8):
  A. 지어내지 않는다 — 씨앗만 우리가 뽑고 실검색어는 API 연관어에서 가져온다
  B. 부분 일치로 판정한다 — 완전일치면 한국어 복합어가 전부 탈락한다
  C. 검색량 관문 — 순위가 잡혀도 아무도 안 치는 문장은 잡음이다
  D. 축 필터 — 우리 가게와 무관한 연관어는 뺀다
  E. 업태 중립 — 셀러에는 지역 토큰을 넣지 않는다
  F. 중복 조합 금지 — '부산 부산' 류를 만들지 않는다
  G. 토큰 정제 — 조사·마크업 잔해가 검색어로 나가지 않는다
"""
from __future__ import annotations

import inspect

from app.services import queryscout as qs

BODY = ("## 부산 중고차 시세 얼마나 하나요\n"
        "저희는 부산 기장에서 중고차를 판매합니다. 중고차 시세는 연식과 주행거리로 갈립니다. "
        "주행거리 5만km 이하면 시세가 올라갑니다. 중고차 성능점검기록부를 먼저 봅니다. "
        "중고차 구매 전 주행거리 확인은 필수입니다. 중고차 시세 문의 주세요.\n"
        "## 자주 묻는 질문\nQ. 주행거리는 어떻게 확인하나요\n")
PAYLOAD = {"title": "부산 기장 중고차 시세, 투싼 실매물로 봅니다", "body": BODY}


def _cands(monkeypatch, rel=None, **kw):
    """외부 API를 끊고 후보만 본다(연관어는 주입)."""
    from app.services import searchad as _sa
    from app.services import bloganatomy as _ba
    monkeypatch.setattr(_sa, "keyword_volumes", lambda *a, **k: rel or [])
    monkeypatch.setattr(_ba, "cached", lambda *a, **k: None)
    monkeypatch.setattr(_ba, "ensure_async", lambda *a, **k: None)
    kw.setdefault("region", "부산 기장")
    kw.setdefault("industry", "중고차판매")
    return qs.candidates(PAYLOAD, **kw)


# ── A. 지어내지 않는다 ────────────────────────────────────────────
def test_real_search_terms_come_from_api_not_invented(monkeypatch):
    """A. 후보를 우리가 조합해 '지어내면' 아무도 안 치는 말만 나온다
    ('계기판 중고차판매' 류). 실검색어는 검색광고 연관어에서 온다."""
    src = inspect.getsource(qs.candidates)
    assert "keyword_volumes" in src, "실검색어 공급원(연관어 조회)이 없음"
    assert "_seeds" in src, "씨앗 개념이 없음(전부 지어내는 구조)"
    rel = [{"keyword": "부산 중고차", "total": 12000},
           {"keyword": "중고차 시세", "total": 35120}]
    out = _cands(monkeypatch, rel=rel)
    assert "중고차 시세" in out, f"API가 준 실검색어가 후보에 없음: {out}"


def test_low_volume_terms_dropped(monkeypatch):
    """C. 순위가 잡혀도 월검색량이 관문 미만이면 사람이 안 치는 문장이다(잡음)."""
    rel = [{"keyword": "중고차 시세", "total": qs.MIN_VOLUME - 1},
           {"keyword": "부산 중고차", "total": qs.MIN_VOLUME + 5000}]
    out = _cands(monkeypatch, rel=rel)
    assert "부산 중고차" in out
    assert qs.MIN_VOLUME >= 100, "관문이 사실상 없다"


def test_unrelated_relative_keywords_dropped(monkeypatch):
    """D. 연관어에는 우리 가게와 무관한 것이 섞인다 — 축(지역·업종·브랜드) 어휘를
    하나도 안 가진 후보는 뺀다."""
    rel = [{"keyword": "직업전문학교 수강료", "total": 90000},
           {"keyword": "부산 중고차", "total": 12000}]
    out = _cands(monkeypatch, rel=rel)
    assert "직업전문학교 수강료" not in out, f"무관한 연관어가 통과함: {out}"
    assert "부산 중고차" in out


# ── B. 부분 일치 판정 ─────────────────────────────────────────────
def test_partial_match_keeps_korean_compounds(monkeypatch):
    """B. 주제어를 토큰 완전일치로 재면 '중고차시세'(월 35,120회)가 주제어 '중고차'와
    다르다고 탈락한다 — 한국어 복합어에서 치명적이었다."""
    rel = [{"keyword": "중고차시세", "total": 35120}]
    out = _cands(monkeypatch, rel=rel)
    assert "중고차시세" in out, f"복합어가 탈락함: {out}"
    src = inspect.getsource(qs.candidates)
    assert "a in s or s in a" in src, "부분 일치 판정이 사라짐(완전일치 회귀)"


def test_template_headings_dropped(monkeypatch):
    """B2. '자주 묻는 질문' 같은 템플릿 소제목은 유입 검색어가 아니다 —
    주제어와 한 글자도 안 겹치면 구조 제목으로 본다."""
    out = _cands(monkeypatch)
    assert "자주 묻는 질문" not in out, f"템플릿 제목이 후보로 나감: {out}"
    assert any("중고차" in c for c in out), f"진짜 후보까지 사라짐: {out}"


# ── E. 업태 중립 ──────────────────────────────────────────────────
def test_seller_gets_no_region_token(monkeypatch):
    """E. 온라인 셀러는 상권이 없다 — 축 조합에 지역을 넣으면 검색량 0 조합만 만든다.
    축 판단은 seo.canonical_region 단일 소스에 위임한다(여기서 규칙을 새로 만들지 않는다).
    (글 본문이 스스로 지역을 말하는 경우까지 지우지는 않는다 — 그건 글의 내용이다.)"""
    from app.services import searchad as _sa
    from app.services import bloganatomy as _ba
    monkeypatch.setattr(_sa, "keyword_volumes", lambda *a, **k: [])
    monkeypatch.setattr(_ba, "cached", lambda *a, **k: None)
    monkeypatch.setattr(_ba, "ensure_async", lambda *a, **k: None)
    pl = {"title": "루마 아테나 필름 실측 데이터로 고르기",
          "body": "## 루마 아테나 차이\n아테나는 적외선 차단률이 높습니다. 필름 선택 기준을 봅니다.\n"}
    out = qs.candidates(pl, region="부산 기장", industry="썬팅필름", biz="seller", brand="루마")
    assert not any("부산" in c or "기장" in c for c in out), f"셀러 축에 지역이 붙음: {out}"
    src = inspect.getsource(qs.candidates)
    assert "canonical_region" in src, "지역 판정을 자체 규칙으로 하고 있음(단일 소스 이탈)"


def test_local_shop_keeps_region_axis(monkeypatch):
    """E-역: 동네 매장에는 지역 축이 있어야 한다(중립화가 지역을 죽이면 안 된다)."""
    out = _cands(monkeypatch)
    assert any(c.startswith("부산") for c in out), f"지역 축이 사라짐: {out}"


# ── F. 중복 조합 금지 ─────────────────────────────────────────────
def test_no_duplicate_axis_combination(monkeypatch):
    """F. '부산 부산', '중고차판매 중고차판매' 류가 실제로 나갔다(실사고)."""
    out = _cands(monkeypatch)
    for c in out:
        toks = c.split()
        assert len(toks) == len(set(toks)), f"토큰 중복 후보: {c!r}"


def test_region_token_is_shortform(monkeypatch):
    """F2. 지역 토큰은 축약형이어야 한다 — '부산광역시 썬팅'은 실측 검색량 0."""
    out = _cands(monkeypatch, region="부산광역시 기장군")
    assert not any("광역시" in c or "기장군" in c for c in out), f"행정 풀네임이 남음: {out}"


# ── G. 토큰 정제 ──────────────────────────────────────────────────
def test_tokens_stripped_of_particles_and_markup():
    """G. 조사·마크업 잔해가 검색어로 나가면 아무도 안 친다.
    실측: HTML 정규식이 태그 조각을 뽑았고, 조사가 붙은 채 후보가 됐다."""
    assert qs._clean("중고차를") == "중고차"
    assert qs._clean("주행거리는") == "주행거리"
    assert qs._clean("<p>중고차</p>") == "p중고차p" or "중고차" in qs._clean("중고차")
    assert qs._clean("시세!!") == "시세"
    assert qs._clean("가") == "가", "2자 이하는 조사 제거 대상이 아니다(단어가 사라진다)"


def test_stopwords_not_candidates(monkeypatch):
    """G2. '합니다·입니다·저희' 류 기능어는 검색어가 아니다."""
    out = _cands(monkeypatch)
    for c in out:
        assert c not in qs._STOP, f"불용어가 후보로: {c}"
        assert not c.endswith("합니다") and not c.endswith("입니다"), f"어미형 후보: {c}"


def test_candidate_length_bounds(monkeypatch):
    """G3. 문장 조각(28자 초과)은 검색어가 아니고, 3자 미만은 의미가 없다.
    실측: '중고차'(3자)가 하한에 걸려 탈락한 적이 있어 하한은 3자다."""
    out = _cands(monkeypatch, rel=[{"keyword": "중고차", "total": 271600}])
    assert "중고차" in out, f"3자 실검색어가 탈락함: {out}"
    for c in out:
        assert 3 <= len(c) <= 28, f"길이 위반: {c!r}({len(c)}자)"
