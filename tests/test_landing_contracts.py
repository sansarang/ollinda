"""랜딩 마케팅·성능·정직 계약 골든 — 2026-08-09 랜딩 개선 6종 박제.

계약: ① 폰트는 dynamic-subset(3.8MB 전서브셋 금지) ② Tailwind는 셀프호스팅 CSS(CDN JIT 금지)
③ 파비콘 링크·실물 존재 ④ JSON-LD 가격은 config 실판매가(하드코딩 29000원 허위 봉합)
⑤ 영상 autoplay 금지(IO 재생) ⑥ 카톡 상담 문구는 실물 버튼(환경변수) 있을 때만
⑦ 이용약관 페이지·푸터 링크 ⑧ 통신판매업 번호는 발급 전 미표기(날조 금지)
⑨ GA는 환경변수 있을 때만 주입.
"""
import os
import re

from app import config, landing


def _html():
    return landing.render()


def test_font_is_dynamic_subset():
    h = _html()
    assert "pretendard-dynamic-subset" in h, "폰트가 dynamic-subset이 아니다(모바일 3.8MB 회귀)"
    assert "/static/pretendard.min.css" not in h


def test_tailwind_selfhosted_not_cdn():
    h = _html()
    assert "cdn.tailwindcss.com" not in h, "Tailwind CDN JIT(397KB JS) 회귀"
    assert '"/static/landing.css"' in h
    path = os.path.join(os.path.dirname(landing.__file__), "static", "landing.css")
    assert os.path.isfile(path) and os.path.getsize(path) > 10000, \
        "빌드된 landing.css 실물이 없다 — scripts/build-landing-css.sh 실행"


def test_rendered_tailwind_classes_covered_by_built_css():
    """렌더 HTML의 Tailwind 유틸리티가 빌드 CSS에 실재해야 한다(누락=화면 깨짐).
    인라인 <style>의 커스텀 클래스는 제외."""
    custom_prefixes = ("reveal", "card", "hero-", "rise", "baclip", "badiv",
                      "result-", "iq-opt", "tz-grid")
    h = landing.render() + landing.terms() + landing.privacy()
    css = open(os.path.join(os.path.dirname(landing.__file__), "static", "landing.css")).read()
    classes = set()
    for m in re.finditer(r'class=["\']([^"\']+)["\']', h):
        classes.update(m.group(1).split())
    missing = []
    for c in sorted(classes):
        if c.startswith(custom_prefixes) or not c:
            continue
        esc_css = re.sub(r"([:\[\]#./%])", r"\\\1", c)
        if "." + esc_css not in css:
            missing.append(c)
    assert not missing, f"빌드 CSS에 없는 클래스 {len(missing)}개: {missing[:10]}"


def test_favicon_links_and_assets():
    h = _html()
    for link in ("/favicon.svg", "/favicon.ico", "/apple-touch-icon.png"):
        assert link in h, f"파비콘 링크 누락: {link}"
    static = os.path.join(os.path.dirname(landing.__file__), "static")
    for f in ("favicon.svg", "favicon.ico", "apple-touch-icon.png"):
        assert os.path.isfile(os.path.join(static, f)), f"파비콘 실물 누락: {f}"


def test_jsonld_price_matches_config():
    h = _html()
    assert '"price":"29000"' not in h.replace(" ", ""), "하드코딩 허위 가격 회귀"
    assert str(config.PRICE_BASIC) in h and "AggregateOffer" in h


def test_video_no_autoplay_attr():
    for m in re.finditer(r"<video[^>]*>", _html()):
        assert "autoplay" not in m.group(0), "영상 autoplay 회귀(진입 즉시 1.2MB 다운로드)"


def test_kakao_chat_copy_only_with_real_button(monkeypatch):
    monkeypatch.delenv("KAKAO_CHANNEL_URL", raising=False)
    h = _html()
    assert "카카오톡 상담 버튼" not in h, "실물 없는 상담 버튼 안내(허위 카피) 회귀"
    monkeypatch.setenv("KAKAO_CHANNEL_URL", "https://pf.kakao.com/_test")
    h2 = _html()
    assert "카카오톡 상담 버튼" in h2 and "pf.kakao.com/_test" in h2


def test_terms_page_and_footer_links():
    h = _html()
    assert 'href="/terms"' in h, "푸터 이용약관 링크 누락"
    t = landing.terms()
    assert "이용약관" in t and "해지" in t and "환불" in t


def test_mail_order_no_only_when_issued(monkeypatch):
    monkeypatch.delenv("SHOPCAST_MAIL_ORDER_NO", raising=False)
    assert "통신판매업" not in _html(), "미발급 통신판매업 번호 표기(날조) 금지"
    monkeypatch.setenv("SHOPCAST_MAIL_ORDER_NO", "2026-경남양산-0000")
    assert "2026-경남양산-0000" in _html()


def test_ga_only_with_measurement_id(monkeypatch):
    monkeypatch.delenv("GA_MEASUREMENT_ID", raising=False)
    assert "googletagmanager" not in _html()
    monkeypatch.setenv("GA_MEASUREMENT_ID", "G-TEST123")
    h = _html()
    assert "googletagmanager" in h and "G-TEST123" in h


def test_measured_case_is_honest():
    """실측 사례 카드 — 실측 문구·면책 병기, 과거 목업 문구('3계단 상승' 등) 금지."""
    h = _html()
    assert "실측" in h and "부산 동구 썬팅업체" in h
    assert "3계단 상승" not in h, "미검증 상승 폭 주장 회귀"
    assert "개별 결과는" in h, "성과 사례 면책 문구 누락"


def test_cancel_policy_present():
    h = _html()
    assert "언제든 해지" in h, "요금 섹션 해지 안내 누락"
    assert any("해지는 어떻게" in q for q, _ in landing._QA), "FAQ 해지 항목 누락"
