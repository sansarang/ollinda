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


def test_diagnostic_lead_capture():
    """진단 결과 이메일 캡처(마케팅 A, 2026-08-11) — 비가입 방문자 리드를 잡는 마개."""
    h = landing.render()
    assert "리포트를 이메일로" in h, "리드 캡처 안내 누락"
    assert "rc_email" in h and "sendReport" in h, "이메일 입력·전송 누락"
    assert "/api/rank-report" in h, "리드 캡처 엔드포인트 연결 누락"
    from app import db
    assert db.save_landing_lead("lead@test.kr", "local|부산|카페|가게"), "리드 저장 실패"
    assert not db.save_landing_lead("bad-email"), "이메일 아닌 값이 저장됨"


def test_visit_bar_honest():
    """방문자 표시 — 일 방문 100명 넘을 때까지 숨김(사장님 지시), 노출 시 누적 실값(2026-08-11)."""
    assert "둘러봤" not in landing.render(5000, today=0), "일 방문 0인데 표시"
    assert "둘러봤" not in landing.render(5000, today=99), "일 방문 100 미만인데 표시"
    h = landing.render(5000, today=100)
    assert "5,000명" in h, "일 100 넘었는데 누적 미표시"


def test_intro_promo_page():
    """홍보 유입 페이지(/intro) — 영상(캐시버전 포함)·CTA·계측이 전부 있어야 쪽지 홍보가 산다."""
    h = landing.intro()
    assert "/docs/intro.mp4?v=" in h, "소개 영상(버전 파라미터) 누락"
    # 주 CTA(무료로 시작하기)는 랜딩(/)으로 — 영상 본 사람을 제품 소개로 데려간다(2026-08-11)
    import re as _re
    assert _re.search(r'href="/"[^>]*>[^<]*무료로 시작하기', h), "주 CTA가 랜딩(/)으로 안 감"
    assert "trackEv" in h, "계측 스크립트 누락 — 클릭 추적 없이는 홍보 효과를 잴 수 없다"
    # 서이추 영상 링크가 도착하는 곳 — 문의 연락처(메일·전화)가 보여야 전환된다(2026-08-11)
    assert landing.CONTACT_EMAIL in h, "인트로에 문의 메일 누락"
    assert landing.BIZ_PHONE in h, "인트로에 연락처 누락"


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


# ── 랜딩 정직·구성 계약 (2026-08-13 사장님 지시 1~4순위) ─────────────
def _visible_text():
    """사용자 눈에 실제로 보이는 텍스트만(스크립트·태그 제외)."""
    import html as _h
    h = landing.render()
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    return _h.unescape(re.sub(r"<[^>]+>", " ", h))


def test_no_fabricated_social_proof():
    """유료 고객 0명인데 '가장 인기'는 날조된 사회적 증거다(헌법: 날조 금지).
    실제로 팔린 뒤 데이터로 붙일 배지 — 그 전엔 구성만 사실대로 말한다."""
    t = _visible_text()
    assert "가장 인기" not in t, "요금제에 근거 없는 '가장 인기' 배지가 다시 붙었다"


def test_no_fabricated_customer_count():
    """0→37로 세는 '이 콘텐츠 보고 온 손님'은 우리에게 없는 실적이다.
    작게 (예시)를 달아도 화면에 남는 인상은 '37명이 왔다'는 실적이다."""
    h = landing.render()
    assert "data-count='37'" not in h and 'data-count="37"' not in h, \
        "가짜 유입 숫자 애니메이션이 되살아났다"
    assert "이 콘텐츠 보고 온 손님" not in _visible_text()


def test_proof_is_above_the_fold():
    """사장님이 가장 먼저 묻는 것은 '진짜 되나?'다. 실측 1위 사례가 첫 화면에 있어야 한다.
    (실측 2026-08-13: 증거가 79번째 문단에 있었고 방문 40명 중 클릭 0명)"""
    hero = landing._hero()
    assert "1위" in hero, "히어로에 실측 결과가 없다 — 증거가 다시 아래로 내려갔다"
    assert "실측" in hero, "실측 표기가 없다(허위 양성 방지 문구 포함)"


def test_primary_cta_is_no_signup_trial():
    """첫 행동은 '가입'이 아니라 '체험'이다 — 가입 없이 되는 미리보기가 있는데
    버튼이 묻혀 한 달 넘게 아무도 안 썼다(마지막 사용 2026-07-11)."""
    hero = landing._hero()
    assert "가입 없이" in hero, "무료 체험 진입이 히어로에서 사라졌다"
    i_trial = hero.find("가입 없이")
    i_login = hero.find("/login/kakao")
    assert 0 <= i_trial < i_login, "가입 버튼이 체험 버튼보다 위에 있다(마찰 큰 행동이 먼저)"


def test_no_kitchen_jargon_on_landing():
    """헌법: 사장님 화면에 주방 용어 금지. PAS·롱테일·실검색량은 만드는 사람 말이다.
    C-Rank·D.I.A.+는 '네이버를 안다'는 신호라 소량 허용하되 늘어나면 실패시킨다."""
    t = _visible_text()
    for word in ("PAS", "롱테일", "실검색량"):
        assert word not in t, f"주방 용어 '{word}'가 사장님 화면에 노출됐다"
    assert t.count("C-Rank") <= 2, "C-Rank 노출이 늘었다 — 전문용어는 최소로"


def test_long_feature_lists_are_collapsed():
    """랜딩이 18섹션·272문단이라 폰에서 20번 넘게 스크롤해야 끝났다.
    기능 나열은 결정에 필요한 정보가 아니다 — 지우지 말고 접어서 궁금한 사람만 펴 보게."""
    h = landing.render()
    assert h.count("<details") >= 2, "긴 나열 섹션이 다시 펼쳐진 채로 돌아왔다"


def test_no_unverified_speed_promise_on_demo_cta():
    """2026-08-13 사장님 지적: 날조 배지 2건을 지운 직후, 그 자리에 '3초 만에 결과 보기'라는
    세 번째 거짓말을 내가 넣었다. 실측은 126초였고 보여주는 것도 완성본이 아닌 도입부였다.

    체험 버튼 주변에 '초' 단위 속도 약속을 두지 않는다 — 생성은 LLM·영상 대기라
    초 단위로 보장할 수 없다. 시간을 말하려면 실측 범위(분)로만 말한다.
    """
    import html as _h
    hero = re.sub(r"<!--.*?-->", " ", landing._hero(), flags=re.S)   # 주석은 화면에 안 보인다
    vis = _h.unescape(re.sub(r"<[^>]+>", " ", hero))
    i = vis.find("가입 없이")
    assert i >= 0, "체험 진입 문구가 사라졌다"
    around = vis[max(0, i - 200):i + 300]
    assert not re.search(r"\d+\s*초\s*(만에|안에|이면)", around), \
        f"체험 버튼이 초 단위 속도를 약속한다(실측 126초 — 지킬 수 없는 약속): {around[:120]!r}"


def test_demo_cta_states_what_is_actually_shown():
    """무료 체험이 주는 것은 완성본이 아니라 '블로그 글 도입부'다.
    받는 것을 부풀리면 열어본 사람이 더 크게 실망한다."""
    hero = landing._hero()
    assert "도입부" in hero, "체험이 무엇을 보여주는지(도입부)를 말하지 않는다"
    assert "가입 후" in hero, "완성본·영상이 가입 후라는 경계를 말하지 않는다"
