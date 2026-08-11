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


def test_refund_page_consistent_with_terms():
    """환불정책 전용 페이지(결제사 도메인 심사 요건) — 약관 제4조와 같은 정책이어야 한다."""
    h = _html()
    assert 'href="/refund"' in h, "푸터 환불정책 링크 누락"
    r = landing.refund()
    # 약관 제4조와 동일 정책 핵심 문구 — 두 페이지가 다른 말을 하면 심사 탈락 사유
    for key in ("7일 이내", "전액 환불", "다음 결제일부터 청구되지 않으며", "106-48-91586"):
        assert key in r, f"환불정책 핵심 문구 누락: {key}"
    assert "결제 후 7일 이내" in landing.terms(), "약관 환불 조항이 사라짐 — 환불정책과 불일치 위험"


def test_intro_promo_page():
    """홍보 유입 페이지(/intro) — 영상(캐시버전 포함)·CTA·계측이 전부 있어야 쪽지 홍보가 산다."""
    h = landing.intro()
    assert "/docs/intro.mp4?v=" in h, "소개 영상(버전 파라미터) 누락"
    # 주 CTA(무료로 시작하기)는 랜딩(/)으로 — 영상 본 사람을 제품 소개로 데려간다(2026-08-11)
    import re as _re
    assert _re.search(r'href="/"[^>]*>[^<]*무료로 시작하기', h), "주 CTA가 랜딩(/)으로 안 감"
    assert "trackEv" in h, "가입 계측 누락"
    assert "trackEv" in h, "계측 스크립트 누락 — 클릭 추적 없이는 홍보 효과를 잴 수 없다"


def test_biz_info_single_source():
    """사업자 표기(대표·번호·주소)는 BIZ_* 단일 소스 — 표면별 하드코딩이 한 곳만 고쳐지는
    사고를 냈다(2026-08-10: 푸터만 옛 주소 잔존). 옛 주소는 어느 표면에도 남으면 안 된다."""
    pages = {"landing": _html(), "terms": landing.terms(),
             "privacy": landing.privacy(), "refund": landing.refund()}
    for name, h in pages.items():
        assert "주남로" not in h and "영산대" not in h, f"{name}: 옛 사업자 주소 잔존"
    for name in ("landing", "terms", "privacy", "refund"):
        assert landing.BIZ_ADDR in pages[name], f"{name}: 사업자 주소 누락"
        assert landing.BIZ_REG_NO in pages[name], f"{name}: 사업자등록번호 누락"


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
    # gtag는 <head> 안 — body로 내려가면 서치콘솔 GA 소유확인이 깨진다(2026-08-10 실측)
    assert h.index("googletagmanager") < h.index("</head>"), "gtag가 head 밖으로 회귀"


def test_measured_case_is_honest():
    """실측 사례 타임라인 — 실측 날짜·순위(7/31 발행→8/2 12위→8/9 1위)만, 면책 병기,
    과거 목업 문구('3계단 상승' 등) 금지."""
    h = _html()
    assert "실측" in h and "부산 동구 썬팅업체" in h
    assert "7/31" in h and "12위" in h and "1위" in h, "실측 타임라인 누락"
    assert "3계단 상승" not in h, "미검증 상승 폭 주장 회귀"
    assert "개별 결과는" in h, "성과 사례 면책 문구 누락"


def test_marketing_sections_present_and_honest():
    """2026-08-09 재구성 계약 — 코드가 실제로 하는 일만 판다."""
    h = _html()
    # 빈자리 글감 — 실제 기능(gapscout) 소개 + UI 재현임을 명시
    assert "아직 이 질문에 답한 글이 없어요" in h and "실제 화면 구성" in h
    # 관측-적응 루프 — 자동 발행 부인(발행은 사장님) 문구 필수
    assert "떨어지는 날" in h and "자동 발행은 하지 않아요" in h
    # 경험 자산 — 지어내지 않음 병기
    assert "한 번 답하면" in h and "지어내지 않습니다" in h
    # 비밀번호 신뢰
    assert "비밀번호는 받지 않습니다" in h
    # 약한 기능-개수 스탯 제거 상태 유지(실측 숫자 생기기 전까지)
    assert "개 채널 동시" not in h, "기능 개수 스탯 회귀 — 실측 숫자로만 부활"
    # 순위 보장 문구 금지(금지선)
    assert "무조건 1위" not in h.replace("\"무조건 1위\" 보장은 하지 않습니다", "")


def test_landing_media_assets():
    """2026-08-09 자산 재제작 계약 — 실물 존재·포스터 교체(검은 첫 화면 og.png 포스터 회귀 방지)."""
    h = _html()
    assert "/demo/short_poster.jpg" in h, "데모 영상 실프레임 포스터 회귀"
    root = os.path.join(os.path.dirname(landing.__file__), "..")
    poster = os.path.join(os.path.dirname(landing.__file__), "static", "demo", "short_poster.jpg")
    assert os.path.isfile(poster) and os.path.getsize(poster) > 10000
    guide = os.path.join(root, "assets", "docs", "ollinda_guide.pdf")
    intro = os.path.join(root, "assets", "docs", "ollinda_intro.mp4")
    assert os.path.getsize(guide) > 50_000, "제품설명서 실물 이상"
    assert os.path.getsize(guide) < 1_000_000, "제품설명서가 다시 비대해짐(구 3.4MB 회귀)"
    assert os.path.getsize(intro) > 2_000_000, "소개 영상 실물 이상"
    # 캐시 무효화 — 파일 교체 후 브라우저가 옛 파일을 재생한 실사고(2026-08-09)
    for u in ("/docs/guide.pdf?v=", "/docs/intro.mp4?v=", "/demo/local_short.mp4?v=",
              "/demo/short_poster.jpg?v="):
        assert u in h, f"미디어 버전 파라미터 누락: {u} — 교체해도 방문자가 옛 파일을 본다"


def test_cancel_policy_present():
    h = _html()
    assert "언제든 해지" in h, "요금 섹션 해지 안내 누락"
    assert any("해지는 어떻게" in q for q, _ in landing._QA), "FAQ 해지 항목 누락"
