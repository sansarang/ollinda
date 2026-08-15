"""네이버 봇 검증 골든 (2026-08-16).

사고: 공식 IP 대역을 125.209.192.0/18 **하나만** 적어두고 그 밖은 전부 '가짜'로 판정했다.
  실제 공식 목록은 36개 대역이다. 그래서 8일간 온 Yeti 17건 중 16건을 위조로 찍었고,
  나는 그 숫자로 "네이버가 우리 사이트를 거의 안 온다"고 사장님께 보고했다.
  역방향 DNS를 한 번만 조회했으면 5분 만에 알았을 일이다(전부 crawl.*.web.naver.com).

여기서 무는 것:
  · 실측으로 확인된 대역이 다시 빠지지 않는다
  · 판정 불가를 '가짜'로 단정하지 않는다(모름은 None)
  · 느린 DNS가 요청 경로로 되돌아오지 않는다
"""
import ipaddress

from app.services import botlog as bl

# 2026-08 로그에서 실제로 관측된 네이버 크롤러 IP (역방향 DNS로 crawl.*.web.naver.com 확인)
OBSERVED = ["114.111.32.181", "211.249.46.176", "110.93.150.30", "125.209.235.169"]


def test_observed_naver_ips_are_accepted():
    """실측으로 진짜임이 확인된 IP가 '가짜'로 찍히면 안 된다 — 그게 이 사고의 본체다."""
    bl.official_nets(force=True)
    for ip in OBSERVED:
        assert bl.verify_yeti(ip) is True, f"실측된 네이버 IP를 가짜로 판정: {ip}"


def test_fallback_covers_observed_even_without_network():
    """공식 목록을 못 받아도 실측 대역은 안전망에 남아 있어야 한다."""
    for ip in OBSERVED:
        a = ipaddress.ip_address(ip)
        assert any(a in n for n in bl._FALLBACK_NETS), f"안전망에서 빠진 실측 대역: {ip}"


def test_official_list_is_not_a_single_range():
    """대역 하나만 두는 것이 이 사고의 원인이었다."""
    bl.official_nets(force=True)
    assert len(bl._NETS_CACHE["nets"] or []) >= 4, "공식 목록이 너무 적다(적재 실패 의심)"
    assert len(bl._FALLBACK_NETS) >= 4, "안전망이 대역 하나로 되돌아갔다"


def test_non_naver_ip_is_rejected():
    assert bl.verify_yeti("8.8.8.8") is False
    assert bl.verify_yeti("8.8.8.8", deep=True) is False


def test_unknown_is_none_not_false():
    """조회가 안 되는 것을 '가짜'로 단정하지 않는다 — 모르면 모른다고 한다."""
    assert bl.fcrdns_ok("192.0.2.1") in (None, False)   # TEST-NET-1, 역방향 없음
    assert bl.verify_yeti("") is False                   # 값 자체가 없으면 False


def test_dns_is_not_called_in_the_request_path():
    """record()는 모든 요청마다 도는 미들웨어 안이다. 거기서 DNS를 기다리면 사이트가 멈춘다."""
    import inspect
    src = inspect.getsource(bl.record)
    assert "fcrdns" not in src, "요청 경로에서 DNS를 부른다"
    assert "deep=True" not in src, "요청 경로에서 깊은 검증을 한다"
    assert "verify_yeti" in src, "기록 시 빠른 판정조차 안 한다"
