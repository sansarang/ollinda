"""브리핑 매장/셀러 분기(C4) — 신호가 서로 다르게 나오는지 고정.

프로덕션 실측(2026-07-12): 셀러 샘플 계정(캠핑 폴딩박스) send-test → shop 축 신호,
매장 계정(썬팅) → 지역 축 신호. 여기서는 분기 로직을 결정적으로 회귀 고정한다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")
os.environ.setdefault("SHOPCAST_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SHOPCAST_DB", "/tmp/test_brief_seller.sqlite")

from unittest.mock import patch  # noqa: E402

from app import db  # noqa: E402
from app.services import briefing, diagnose, place  # noqa: E402

db.init_db()


def _seller():
    t = db.create_tenant("캠프기어", "캠핑 폴딩박스", "", biz_type="seller")
    return db.get_tenant(t.id)


def test_seller_gets_shop_signals_not_local():
    t = _seller()
    fake = {"mode": "seller", "scan_depth": 40, "estimated": False,
            "caught": [{"keyword": "캠핑 폴딩박스", "rank": 7, "volume": 3200, "status": "top"}],
            "missing": [{"keyword": "차박 수납박스", "rank": 0, "volume": 1900, "status": "missing"}]}
    with patch.object(diagnose, "diagnose_product_rank", lambda *a, **k: fake), \
         patch.object(place, "shop_top", lambda kw, limit=3: [{"name": "x", "mall": "박스나라", "price": 12900}]), \
         patch.object(diagnose, "diagnose_rank",
                      lambda *a, **k: (_ for _ in ()).throw(AssertionError("셀러가 지역 진단 호출"))):
        b = briefing.build_briefing(t, "free")
    assert b["kind"] == "shop_chase"
    assert "7위" in b["headline"] and "박스나라" in b["headline"]
    assert "캠핑 폴딩박스" not in b["headline"] + b["task"]   # (auto) 키워드 미노출
    # 매장 냄새 없음
    for bad in ("지역", "플레이스", "방문", "매장"):
        assert bad not in b["headline"] + b["task"], bad


def test_seller_missing_signal_and_honest_fallback():
    t = _seller()
    miss_only = {"mode": "seller", "scan_depth": 40, "estimated": False, "caught": [],
                 "missing": [{"keyword": "차박 수납박스", "rank": 0, "volume": 1900, "status": "missing"}]}
    with patch.object(diagnose, "diagnose_product_rank", lambda *a, **k: miss_only), \
         patch.object(place, "shop_top", lambda kw, limit=3: []):
        b = briefing.build_briefing(t, "free")
    assert b["kind"] == "shop_missing" and "1,900번씩" in b["headline"]   # (auto) 키워드 미노출 문구
    assert "차박 수납박스" not in b["headline"] + b["task"]               # 키워드 자체는 숨김
    # 조회 불가(estimated)면 신호를 지어내지 않고 steady 폴백(정직)
    with patch.object(diagnose, "diagnose_product_rank", lambda *a, **k: {"estimated": True}):
        b2 = briefing.build_briefing(t, "free")
    assert b2["kind"] in ("steady", "gap")


def test_local_store_keeps_existing_signals():
    t = db.create_tenant("루마썬팅", "썬팅", "부산 동구")
    with patch.object(diagnose, "diagnose_rank", lambda *a, **k: {"estimated": True}), \
         patch.object(diagnose, "diagnose_product_rank",
                      lambda *a, **k: (_ for _ in ()).throw(AssertionError("매장이 셀러 진단 호출"))):
        b = briefing.build_briefing(t, "free")
    assert b["kind"] in ("steady", "gap")
