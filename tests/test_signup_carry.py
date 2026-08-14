"""진단 결과를 가입 너머로 인계 (2026-08-14 사장님 지시).

사장님 지적: "가입하고 나면 가게 등록 페이지가 있다... 잘 생각해서 해야 한다."
맞다. 랜딩에서 상호·지역·업종·주소·블로그를 이미 찾아냈는데, 가입하면 `/me`가
"딱 3가지만 알려주세요"부터 띄운다. 방금 알아낸 것을 다시 묻는 셈이고
'가입하고 이 글 전체 받기' 약속도 거기서 끊긴다.
"""
import types

from app import signup_carry as sc


def _t(**kw):
    d = {"id": "T1", "name": "내 가게", "industry": "", "region": "", "address": "",
         "phone": "", "hours": "", "map_url": "", "naver_blog_url": ""}
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_pack_requires_shop_name():
    """상호 없이는 가게를 특정할 수 없다 — 싣지 않는다(엉뚱한 가게를 채우면 더 나쁘다)."""
    assert sc.pack({"rg": "부산 동구", "ind": "썬팅"}) == ""
    assert sc.pack({"nm": "초량 루마썬팅"}) != ""


def test_pack_unpack_roundtrip():
    raw = sc.pack({"nm": "초량 루마썬팅", "rg": "부산 동구", "ind": "썬팅",
                   "ad": "부산광역시 동구 중앙대로274번길 7-7", "blog": "ksmrnd1", "kw": "부산 썬팅"})
    d = sc.unpack(raw)
    assert d["nm"] == "초량 루마썬팅" and d["blog"] == "ksmrnd1" and d["kw"] == "부산 썬팅"


def test_unpack_broken_value_never_blocks_signup():
    """쿠키가 깨져도 가입이 막히면 안 된다 — 조용히 무시하고 평소 온보딩으로."""
    assert sc.unpack("{쓰레기") == {}
    assert sc.unpack("") == {}
    assert sc.unpack("[1,2,3]") == {}


def test_apply_fills_only_blanks(monkeypatch):
    """이미 값이 있으면 덮지 않는다 — 사장님이 직접 넣은 것이 우선이다."""
    calls = {}
    from app import db
    monkeypatch.setattr(db, "rename_tenant", lambda tid, n, i, r: calls.update(rename=(n, i, r)))
    monkeypatch.setattr(db, "update_tenant_profile", lambda *a: calls.update(prof=a))
    monkeypatch.setattr(db, "set_tenant_blog", lambda tid, u, b: calls.update(blog=(u, b)))
    t = _t(industry="이미있음")
    filled = sc.apply_to_tenant(t, {"nm": "새이름", "ind": "덮으면안됨", "rg": "부산 동구"})
    assert calls["rename"][1] == "이미있음", "기존 업종을 덮었다"
    assert "업종" not in filled and "가게 이름" in filled


def test_apply_uses_existing_write_paths(monkeypatch):
    """가게 정보를 쓰는 길을 새로 만들지 않는다 — 기존 함수만 쓴다(경로 이중화 금지)."""
    from app import db
    seen = []
    monkeypatch.setattr(db, "rename_tenant", lambda *a: seen.append("rename_tenant"))
    monkeypatch.setattr(db, "update_tenant_profile", lambda *a: seen.append("update_tenant_profile"))
    monkeypatch.setattr(db, "set_tenant_blog", lambda *a: seen.append("set_tenant_blog"))
    sc.apply_to_tenant(_t(), {"nm": "가게", "ind": "썬팅", "rg": "부산 동구",
                              "ad": "부산 어딘가", "blog": "myblog"})
    assert set(seen) == {"rename_tenant", "update_tenant_profile", "set_tenant_blog"}


def test_login_routes_attach_carry():
    """가입 버튼이 정보를 실어 보내지 않으면 온보딩에서 다시 묻게 된다."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("app/kakao.py", "app/google_auth.py"):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        assert "signup_carry" in src and "attach" in src, f"{rel}이 진단 정보를 안 싣는다"


def test_me_applies_and_discloses():
    """자동으로 채우되 무엇을 채웠는지 밝힌다(조용히 채우면 정직 게이트 위반)."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main = open(os.path.join(root, "app", "main.py"), encoding="utf-8").read()
    assert "apply_to_tenant" in main, "/me가 인계 정보를 안 쓴다"
    assert "채워뒀어요" in main, "무엇을 채웠는지 밝히지 않는다"
    assert "delete_cookie(_SC_COOKIE)" in main, "쓰고 나서 쿠키를 안 지운다"
