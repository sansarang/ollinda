"""사이트맵 = 봇의 진입로 골든 (2026-08-16).

실측 근거(자체 서버 로그 408건 중 Yeti 17건 전수):
  · 네이버는 매 세션 sitemap.xml을 가장 먼저 친다(4세션 중 3세션)
  · 404가 0건 — 사이트맵·링크에 없는 URL은 찍어보지도 않는다(AI봇은 404율 72~99%)
  · 이틀 주기 재방문. lastmod가 없으면 무엇이 바뀌었는지 판단할 근거가 없다

여기서 무는 것:
  · 공개 페이지가 사이트맵에서 빠지지 않는가(빠지면 네이버에게 존재하지 않는 페이지가 된다)
  · lastmod를 지어내지 않는가(매일 오늘 날짜를 찍는 것은 변경 신호 위조 — 정직 게이트)
  · 비공개 경로가 새지 않는가
"""
import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _xml() -> str:
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    return r.text


def _locs() -> list:
    return re.findall(r"<loc>(.*?)</loc>", _xml())


def test_public_pages_are_all_listed():
    """공개 페이지가 빠지면 네이버는 그 페이지를 영원히 모른다."""
    paths = {u.split("ollinda.kr", 1)[-1] or "/" for u in _locs()}
    for p in ("/", "/intro", "/privacy", "/terms", "/refund"):
        assert p in paths, f"공개 페이지가 사이트맵에 없다: {p} (현재: {sorted(paths)})"


def test_private_paths_never_leak():
    """사장님 개인 업로드 링크·관리 화면은 절대 실리면 안 된다."""
    joined = " ".join(_locs())
    for bad in ("/admin", "/me", "/u/", "/billing"):
        assert bad not in joined, f"비공개 경로가 사이트맵에 실렸다: {bad}"


def test_lastmod_is_present_and_not_todays_date():
    """lastmod는 실제 변경 시점이어야 한다. 매일 오늘 날짜를 찍으면 변경 신호 위조다."""
    import datetime
    xml = _xml()
    lm = re.findall(r"<lastmod>(.*?)</lastmod>", xml)
    assert lm, "lastmod가 없다 — 네이버가 이틀마다 와서 판단 근거 없이 돌아간다"
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in lm), lm
    # 파일 수정 시각 기반이므로 '항상 오늘'일 수 없다. 오늘이 나오면 소스가 오늘 바뀐 경우뿐.
    src_day = datetime.datetime.utcfromtimestamp(
        __import__("os").path.getmtime(__import__("app.landing", fromlist=["x"]).__file__)
    ).strftime("%Y-%m-%d")
    assert lm[0] == src_day, f"lastmod가 실제 변경 시점이 아니다: {lm[0]} != {src_day}"


def test_sitemap_is_valid_xml():
    import xml.etree.ElementTree as ET
    ET.fromstring(_xml())


def test_robots_points_to_sitemap():
    """사이트맵을 알려주지 않으면 진입로가 없다."""
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Sitemap:" in r.text and "sitemap.xml" in r.text
