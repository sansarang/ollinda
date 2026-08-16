"""자사 콘텐츠 페이지(/guide) 골든.

박제 사유(2026-08-17): 외부 SEO 진단 — 사이트맵에 실질 콘텐츠가 홈 하나뿐이었고,
lastmod 5개 전부가 `landing.py` 파일 수정 시각이라 매일 "전부 바뀜"으로 신고됐다.
검색 노출을 파는 회사가 정작 자기 사이트에 색인할 내용이 없던 상태.

여기서 막는 재발:
  ① 가이드가 사이트맵에서 빠지는 것
  ② lastmod가 다시 한 값으로 뭉개지는 것(= 변경 신호 위조)
  ③ 페이지가 홈 제목·홈 canonical을 그대로 달아 중복 색인이 되는 것
  ④ 글이 얇아지는 것 — 상위 글 글자 중간값 실측(1,757자)의 절반 아래로 떨어지면 실패
"""
import os
import re

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import guides, landing


def _text(html: str) -> str:
    body = html.split("</h1>", 1)[-1]
    return re.sub(r"<[^>]+>", "", body)


def test_모든_가이드가_렌더되고_얇지_않다():
    assert len(guides.all_guides()) >= 5
    for g in guides.all_guides():
        html = landing.guide_page(g["slug"])
        assert html, g["slug"]
        n = len(_text(html).replace(" ", ""))
        # 상위 글 글자 중간값 1,757자(실측 kw_anatomy 29키워드). 절반 아래면 얇은 글이다.
        assert n >= 880, f"{g['slug']} 본문 {n}자 — 얇다"


def test_페이지마다_제목과_canonical이_다르다():
    """같은 제목·같은 canonical을 단 페이지 여럿은 검색엔진에게 한 페이지의 복제다."""
    titles, canons = set(), set()
    pages = [landing.guide_index()] + [landing.guide_page(g["slug"])
                                       for g in guides.all_guides()]
    for html in pages:
        t = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        c = re.search(r'<link rel=canonical href="(.*?)"', html).group(1)
        assert "사진만 올리면 글·영상까지" not in t, "홈 제목을 그대로 달았다"
        assert not c.rstrip("/").endswith(".kr"), f"홈 canonical을 그대로 달았다: {c}"
        titles.add(t)
        canons.add(c)
    assert len(titles) == len(pages) and len(canons) == len(pages)


def test_사이트맵에_가이드가_들어가고_lastmod가_한_값으로_뭉개지지_않는다():
    from fastapi.testclient import TestClient

    from app.main import app
    xml = TestClient(app).get("/sitemap.xml").text
    for g in guides.all_guides():
        assert f"/guide/{g['slug']}</loc>" in xml, g["slug"]

    lms = re.findall(r"<lastmod>(.*?)</lastmod>", xml)
    assert len(lms) >= 8
    # ★ 핵심: 전부 같은 날짜면 예전 결함이 돌아온 것이다(모든 페이지가 매일 바뀐다는 신고).
    assert len(set(lms)) >= 2, f"lastmod가 한 값으로 뭉개졌다: {set(lms)}"


def test_없는_slug는_404다():
    """빈 페이지를 200으로 주면 그것이 곧 얇은 색인이다."""
    from fastapi.testclient import TestClient

    from app.main import app
    assert TestClient(app).get("/guide/없는거").status_code == 404


def test_가이드는_링크로_이어져_있다():
    """봇은 링크·사이트맵에 없는 URL을 찍어보지 않는다(Yeti 404 0건 실측)."""
    assert "/guide" in landing._footer()
    html = landing.guide_page(guides.all_guides()[0]["slug"])
    others = [g["slug"] for g in guides.all_guides()[1:]]
    assert all(f"/guide/{s}" in html for s in others)
