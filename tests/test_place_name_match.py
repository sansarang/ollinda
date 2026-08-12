"""가게명 매칭 계약 — 2026-08-12 실사고 박제.

사고: 사장님이 '초량루마썬팅'(지역명 붙여씀)으로 진단하면 네이버의 '루마썬팅 현대상사'와
포함관계가 성립하지 않아 실제 5위인데 '미노출'로 나왔다.
반대 방향 위험이 더 크다 — 짧은 업종어('썬팅')로 남의 가게를 내 가게로 판정하면 허위 노출 보고다
(헌법: 노출 판정은 자사 식별자 매칭만, 허위 양성보다 미표시가 낫다).
"""
import pytest

from app.services.place import _name_match as m

SAME = [
    ("초량루마썬팅", "루마썬팅 현대상사"),      # ★ 사고 케이스(붙여쓴 지역명)
    ("부산루마썬팅", "루마썬팅 현대상사"),
    ("루마썬팅", "루마썬팅 현대상사"),
    ("현대상사", "루마썬팅 현대상사"),
    ("루마썬팅 현대상사", "루마썬팅 현대상사"),
    ("스타벅스 서면점", "스타벅스 부산대점"),   # 지점 차이
    ("지벤트 초량점", "지벤트 서면점"),
]
DIFFERENT = [
    ("동선카", "루마썬팅 현대상사"),
    ("쌍둥이자동차광택", "루마썬팅 현대상사"),
    ("카누리", "루마썬팅 현대상사"),
    ("썬팅", "루마썬팅 현대상사"),              # ★ 업종어만으로 붙으면 허위 양성
    ("부산썬팅", "루마썬팅 현대상사"),
    ("강남미용실", "홍대미용실"),
    ("초량점", "지벤트 초량점"),                # 지점 표기만으로 붙으면 안 됨
]


@pytest.mark.parametrize("user,naver", SAME)
def test_same_store_matches(user, naver):
    assert m(user, naver), f"같은 가게인데 미매칭 — '{user}' 진단이 미노출로 오판된다"


@pytest.mark.parametrize("user,naver", DIFFERENT)
def test_different_store_never_matches(user, naver):
    assert not m(user, naver), f"남의 가게를 내 가게로 판정 — 허위 노출 보고('{user}' vs '{naver}')"


def test_rank_with_address_distinguishes_same_name(monkeypatch):
    """동명 가게 구분(2026-08-12 사장님 지시) — 주소를 주면 그 주소의 가게만 내 가게로 본다.
    주소 없이 이름만 같으면 남의 가게 순위를 내 순위로 보고하게 된다(허위 양성)."""
    from app.services import place
    fake = [
        {"name": "카앤바디", "address": "서울 강남구 테헤란로 1"},
        {"name": "카앤바디", "address": "부산 해운대구 센텀로 99"},   # 동명 다른 가게
    ]
    monkeypatch.setattr(place, "search", lambda kw, limit=5: fake)
    # 주소 없으면 첫 매칭(1위) — 남의 가게일 수 있다
    assert place.rank("서울 썬팅", "카앤바디") == 1
    # 내 가게 주소(부산)를 주면 2위로 정확히 잡힌다
    assert place.rank("서울 썬팅", "카앤바디", addr="부산 해운대구 센텀로 99") == 2
    # 목록에 없는 주소면 미노출(0) — 없는 순위를 지어내지 않는다
    assert place.rank("서울 썬팅", "카앤바디", addr="대구 중구 어딘가 1") == 0


def test_find_candidates_returns_address(monkeypatch):
    """후보 목록엔 사용자가 구분할 수 있게 주소가 반드시 있어야 한다."""
    from app.services import place
    monkeypatch.setattr(place, "search", lambda q, limit=5: [
        {"name": "카앤바디", "address": "서울 강남구 테헤란로 1", "category": "썬팅"},
        {"name": "카앤바디", "address": "부산 해운대구 센텀로 99", "category": "썬팅"},
    ])
    cands = place.find_candidates("카앤바디", "서울")
    assert len(cands) == 2
    assert all(c["address"] for c in cands), "주소 없는 후보 — 사용자가 내 가게를 구분 못 한다"
