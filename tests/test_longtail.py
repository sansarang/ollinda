"""소재 롱테일 후보 생성 골든 (2026-08-13).

사고: 빈자리 정찰의 후보가 [이미 쓴 키워드]에서만 나오는 닫힌 루프여서, 지도에
대형 판(부산 중고차·썬팅 가격)만 쌓였다. 점수 산식을 이길 수 있는 쪽으로 고쳐도
재료가 대형 키워드뿐이라 효과가 없었다.
여기서 무는 것: ①문법×증명값으로 롱테일이 실제로 나온다 ②미증명 스키마 예시 토큰은
절대 나오지 않는다(유령 키워드) ③정찰 하한과 판정 하한이 다시 갈라지지 않는다.
"""
import types

from app.services import longtail as lt


def _t(**kw):
    d = {"id": "T1", "industry": "썬팅", "biz_type": "local", "region": "부산광역시 동구"}
    d.update(kw)
    return types.SimpleNamespace(**d)


# ── ① 문법 × 증명된 속성값 → 롱테일 ────────────────────────────────
def test_combos_makes_longtail_from_proven_attrs():
    out = lt.combos(_t(), ["EV5"], grammars=["{차종} {업종}", "{지역} {업종}"])
    assert "EV5 썬팅" in out, out
    assert "부산 썬팅" in out, out


def test_combos_without_attrs_makes_no_attribute_keyword():
    """속성값이 없으면 속성 조합은 만들지 않는다 — 빈칸이 유일한 폴백이다."""
    out = lt.combos(_t(), [], grammars=["{차종} {업종}"])
    assert not any("{" in k for k in out), out
    # 광역+업종 폴백만 남는다(기존 동작 보존)
    assert out == ["부산 썬팅 추천", "썬팅 추천"], out


def test_unsubstituted_placeholder_never_leaks():
    """지역이 없어도 '{지역} 썬팅' 같은 문자열이 검색어로 새 나가면 안 된다."""
    out = lt.combos(_t(region=""), ["EV5"], grammars=["{지역} {차종} {업종}"])
    assert all("{" not in k and "}" not in k for k in out), out
    assert "EV5 썬팅" in out, out


def test_wide_region_uses_spoken_form():
    """'부산광역시 썬팅'은 아무도 안 친다(실측 검색량 0)."""
    assert lt.wide_region("부산광역시 동구") == "부산"
    assert lt.wide_region("서울특별시 강남구") == "서울"
    assert lt.wide_region("") == ""
    # 구(區)만 있는 지역은 광역이 없으니 빈칸 — 억지로 채우지 않는다
    assert lt.wide_region("동구") == ""


def test_wide_region_do_form_is_stripped_as_is():
    """'경상남도'→'경상남'. 사람이 치는 말은 '경남'이라 검색량 관문에서 걸러진다 —
    여기서 임의로 줄임말을 만들면 canonical_region 규칙이 두 곳에 사는 셈이 된다.
    기존 동작(autoqueue와 동일)을 그대로 박제해 둔다."""
    assert lt.wide_region("경상남도 양산시") == "경상남"


def test_extra_tail_off_keeps_only_longtail():
    out = lt.combos(_t(), ["EV5"], grammars=["{차종} {업종}"], extra_tail=False)
    assert out == ["EV5 썬팅"], out


# ── ② 유령 키워드 방지 — 미증명 스키마 예시 토큰은 나오지 않는다 ──────
def test_proven_axis_values_rejects_unproven_schema_tokens(monkeypatch):
    """스키마 예시 차종(캐스퍼)은 사장님 실데이터에 없으면 후보가 될 수 없다.
    이것이 '딜러에게 없는 매물로 유령 글'이 나가던 계열의 입구다."""
    from app.services import gapscout as gs
    monkeypatch.setattr(gs, "owner_domain", lambda tid: {"tokens": {"EV5", "열차단"}})
    monkeypatch.setattr(gs, "_axis_tokens", lambda t: [
        {"axis": "차종", "tokens": ["EV5", "캐스퍼", "레이"]},
        {"axis": "필름·시공", "tokens": ["열차단", "유리막코팅"]},
    ])
    vals = lt.proven_axis_values("T1", _t())
    assert set(vals) == {"EV5", "열차단"}, vals


def test_proven_axis_values_empty_when_no_owner_data(monkeypatch):
    """실데이터가 없으면 빈 리스트 — 스키마 예시로 채우지 않는다(침묵 폴백 금지)."""
    from app.services import gapscout as gs
    monkeypatch.setattr(gs, "owner_domain", lambda tid: {"tokens": set()})
    monkeypatch.setattr(gs, "_axis_tokens", lambda t: [{"axis": "차종", "tokens": ["캐스퍼"]}])
    assert lt.proven_axis_values("T1", _t()) == []


# ── ③ 하한 드리프트 재발 방지 ────────────────────────────────────
def test_scan_floor_equals_gap_floor():
    """정찰 하한 > 판정 하한이면 롱테일은 지도에 들어오지도 못한다(2026-08-13 실사고).
    숫자를 각자 적지 말고 출처를 하나로 둔다."""
    from app.services import blogreach as br
    from app.services import gapscout as gs
    assert br._min_scan_volume() == gs.MIN_VOLUME


def test_scan_floor_low_enough_for_local_demand():
    """'부산 기장 중고차'(월 80회) 같은 동네 실수요가 정찰에서 잘리면 안 된다."""
    from app.services import blogreach as br
    assert br._min_scan_volume() <= 80


# ── ④ 큐 경로가 같은 함수를 쓴다(규칙 이중화 방지) ─────────────────
def test_autoqueue_delegates_to_canonical_combiner(monkeypatch):
    from app.services import autoqueue as aq
    monkeypatch.setattr(aq.db, "recent_inventory_context",
                        lambda tid, limit=6: [{"model": "쏘렌토", "car_class": "SUV", "year": "2020"}])
    called = {}
    real = lt.combos

    def _spy(t, attrs, **kw):
        called["attrs"] = list(attrs)
        return real(t, attrs, **kw)

    monkeypatch.setattr(lt, "combos", _spy)
    out = aq._seller_longtail_candidates(_t(industry="중고차", biz_type="seller"))
    assert called.get("attrs") == ["쏘렌토", "SUV"], called
    assert out and all("{" not in k for k in out), out


# ── ⑤ 야간 정찰이 경로 때문에 전멸하지 않는다 ─────────────────────
def test_blocks_bootstraps_shopcast_path_without_env():
    """2026-08-13 사고: cron에 PYTHONPATH가 없어 blocks.scan이 ModuleNotFoundError로
    죽었고, 전 가게가 매일 조용히 건너뛰어져 지면 지도가 굶었다.
    경로는 환경변수가 아니라 모듈이 스스로 찾는다."""
    import os
    import subprocess
    import sys as _s
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    blocks_py = os.path.join(root, "app", "services", "scout", "blocks.py")
    # shopcast를 sys.path에 넣어주지 않고, 파일 경로로만 blocks를 적재한다(cron과 같은 조건).
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('b', {blocks_py!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "from app.services.scout import session\n"      # ← 부트스트랩이 없으면 여기서 죽는다
        "print('ok')\n"
    )
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.expanduser("~"), "SHOPCAST_HOME": root}
    r = subprocess.run([_s.executable, "-c", code], capture_output=True, text=True,
                       cwd=os.sep, env=env)
    assert r.returncode == 0 and "ok" in r.stdout, (r.stdout, r.stderr[-500:])


# ── ⑥ 지면 게이트 — 자리 없는 판에는 글을 쏘지 않는다 ─────────────
def _mk_surface(monkeypatch, verdicts: dict, fresh: bool = True):
    """kw → blog_surface 실측을 흉내낸다(checked_at 신선도 포함)."""
    from datetime import datetime, timedelta
    from app import seo
    from app.services import blogreach as brc
    ts = (datetime.utcnow() - timedelta(days=0 if fresh else 999)).isoformat()

    def _fake(tid, kw):
        if kw not in verdicts:
            return {}
        return {"blog_surface": verdicts[kw], "checked_at": ts}

    monkeypatch.setattr(brc, "blocks_for", _fake)
    return seo


def test_dead_surface_keyword_is_swapped_out(monkeypatch):
    """실측 사고: 올린다가 쓴 '부산 기장 중고차' 글은 블로그탭 6위였지만 그 키워드
    첫 화면에 블로그 블록이 없어 손님 눈에는 0이었다(블로그탭 순위는 착시)."""
    seo = _mk_surface(monkeypatch, {"부산 기장 중고차": False, "부산 기장 중고차판매": True})
    kw0, kws = seo._drop_dead_surfaces("부산 기장 중고차",
                                       ["부산 기장 중고차", "부산 기장 중고차판매"], "T1")
    assert kw0 == "부산 기장 중고차판매", (kw0, kws)
    assert kws[0] == "부산 기장 중고차판매"


def test_live_surface_keyword_is_kept(monkeypatch):
    seo = _mk_surface(monkeypatch, {"부산 동구 썬팅": True})
    kw0, _ = seo._drop_dead_surfaces("부산 동구 썬팅", ["부산 동구 썬팅"], "T1")
    assert kw0 == "부산 동구 썬팅"


def test_unmeasured_keyword_is_never_dropped(monkeypatch):
    """미측정(지도에 없음)은 '지면 없음'이 아니다 — 모른다고 버리면 신규 가게가 글을 못 쓴다."""
    seo = _mk_surface(monkeypatch, {})
    kw0, _ = seo._drop_dead_surfaces("새 키워드", ["새 키워드", "다른 키워드"], "T1")
    assert kw0 == "새 키워드"


def test_stale_measurement_is_not_used_as_evidence(monkeypatch):
    """낡은 판정으로 키워드를 버리지 않는다 — 노트북이 꺼져 있던 기간이 근거가 되면 안 된다."""
    seo = _mk_surface(monkeypatch, {"부산 기장 중고차": False}, fresh=False)
    kw0, _ = seo._drop_dead_surfaces("부산 기장 중고차", ["부산 기장 중고차", "대체"], "T1")
    assert kw0 == "부산 기장 중고차"


def test_all_dead_keeps_original_and_does_not_invent(monkeypatch):
    """전부 지면이 없으면 임의 키워드를 지어내지 않는다 — 사유만 남기고 그대로 간다."""
    seo = _mk_surface(monkeypatch, {"A": False, "B": False})
    kw0, kws = seo._drop_dead_surfaces("A", ["A", "B"], "T1")
    assert kw0 == "A" and kws == ["A", "B"]
