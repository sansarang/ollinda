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
                      "result-", "iq-opt", "tz-grid", "hide-on-result")
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
    # ★ 2026-08-15: 말투를 사람 말로 다듬으면서 문구가 바뀌었다. 계약은 '정확한 문장'이 아니라
    #   '그 약속이 화면에 있는가'다 — 뜻으로 문다(문구를 못 바꾸게 하면 개선이 막힌다).
    assert "떨어지는 날" in h, "순위가 떨어지는 날이 문제라는 설명이 사라졌다"
    assert ("자동 발행은 하지 않아요" in h or "마음대로 올리지 않아요" in h), \
        "자동 발행 부인 약속이 사라졌다"
    # 경험 자산 — 지어내지 않음 병기
    assert ("한 번 답하면" in h or "한 번만 알려주시면" in h), "경험 자산 약속이 사라졌다"
    assert ("지어내지 않습니다" in h or "지어내지 않아요" in h or "쓰지 않습니다" in h), \
        "없는 것을 지어내지 않는다는 약속이 사라졌다"
    # 비밀번호 신뢰
    assert ("비밀번호는 받지 않습니다" in h or "비밀번호는 절대 안 받습니다" in h), \
        "비밀번호를 받지 않는다는 약속이 사라졌다"
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
    assert "가입 없이" in hero, "무료 진입 문구가 히어로에서 사라졌다"
    i_trial = hero.find("가입 없이")
    i_login = hero.find("/login/kakao")
    assert 0 <= i_trial < i_login, "가입 버튼이 무료 진단보다 위에 있다(마찰 큰 행동이 먼저)"


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


# ── 랜딩 재구성 계약 (2026-08-13 사장님 지시: 첫 화면에 전 과정 · 2번째는 무료 · 간단명료) ──
def test_first_screen_shows_the_whole_loop():
    """사장님 지시: 가입부터 글·영상 생성, 그 결과가 어떻게 실측되고, 다음에 무슨 글을
    쓰는지까지가 첫 화면에서 보여야 한다. 설명이 아니라 실물로."""
    f = landing._flow()
    assert ("사진만 올립니다" in f or "사진만 올려주세요" in f), "① 사진 단계 누락"
    assert ".mp4" in f and "실제 생성된 글" in f, "② 실제 영상·글 결과물 누락"
    assert "1위" in f and "실측" in f, "③ 발행 후 실측 단계 누락"
    assert "다음에 쓸 글" in f, "④ 다음 글감 단계 누락 — 루프가 끊긴다"
    assert (".mp4" in f), "② 실제 영상이 사라졌다"


def test_second_screen_is_free_trial_with_ai_preview():
    """2번째 화면은 '지금 내 가게로 무료로'다. 전체 생성은 실측 126초라 첫 방문자를
    못 잡는다 — 짧은 AI 호출로 '내 가게용 제목'을 먼저 보여준다."""
    t = landing._try()
    assert "가입 없이" in t and "무료" in t
    assert "instantTitles" in t and "/api/instant-titles" in t, "AI 즉석 제안이 빠졌다"
    assert "없는 가격·성능은 넣지 않았어요" in t, "AI 결과에 정직 고지가 없다"


def test_landing_is_not_cluttered():
    """랜딩이 18섹션·272문단이라 폰에서 20번 넘게 스크롤해야 끝났다(사장님: 너저분하다).
    섹션 수에 상한을 둔다 — 늘리려면 이 계약을 다시 논의해야 한다."""
    h = landing.render()
    n = len(re.findall(r"<section", h))
    assert n <= 12, f"섹션이 {n}개로 늘었다 — 랜딩이 다시 너저분해진다"


def test_removed_sections_kept_their_promises():
    """섹션을 지우면서 '약속'까지 지우면 안 된다. 꾸밈이 아니라 지키기로 한 계약이다."""
    h = _html()
    assert ("자동 발행은 하지 않아요" in h or "마음대로 올리지 않아요" in h), \
        "자동 발행 부인 약속이 사라졌다"
    assert "실제 화면 구성" in h, "목업 화면에 목업 표기가 사라졌다"
    assert ("한 번 답하면" in h or "한 번만 알려주시면" in h), "경험 자산 약속이 사라졌다"


def test_hero_has_shop_name_input_and_reuses_one_diagnosis_path():
    """2026-08-14 사장님 지시: 첫 화면에 상호 입력칸.
    남의 사례를 읽는 것과 내 가게 이름이 결과에 뜨는 것은 다른 일이다.
    ★ 단, 진단 로직은 한 벌만 산다 — 히어로는 값만 넘기고 판정은 rankCheck() 하나뿐이다
    (경로 규칙이 두 곳에 살면 그 자체가 결함)."""
    hero = landing._hero()
    assert 'id="rc_name"' in hero, "첫 화면 상호 입력칸이 없다"
    assert "rankCheck" in hero, "히어로가 진단을 실행하지 않는다"
    # ★ 2026-08-14 사장님 지적("입력했는데 아래로 내려가서 또 검색한다") —
    #   같은 일에 입력구가 둘이면 사람은 멈춘다. 진단 위젯은 페이지에 딱 한 벌만 산다.
    h = landing.render()
    assert h.count("async function rankCheck") == 1, "진단 함수가 두 벌로 늘었다"
    assert h.count("/api/rank-check") == 1, "진단 API를 두 곳에서 부른다(경로 이중화)"
    for _id in ("rc_name", "rc_region", "rc_ind", "rc_out", "rc_pick", "it_out"):
        assert h.count(f'id="{_id}"') == 1, f"{_id} 입력·출력구가 중복됐다"
    # 결과는 입력한 자리(히어로)에서 편다 — 다른 섹션으로 끌고 가지 않는다
    assert 'id="rc_out"' in hero, "결과가 입력한 자리에서 안 나온다"


def test_shop_name_only_promise_is_backed_by_resolution():
    """2026-08-14 사장님 지적: 첫 화면이 '상호만 넣으면 몇 위인지 확인해드려요'라고 약속하는데
    실제로는 빈손이 돌아왔다(headline이 자리표시자 '내 지역 업종', 잡은 것 0).

    원인: 진단 키워드는 [지역+업종]으로 만드는데 상호만 오면 둘 다 비어 키워드가 0개.
    약속을 지우거나, 지킬 수 있게 만들거나 둘 중 하나다 — 상호로 가게를 찾아 지역·업종을
    채우는 보완이 화면과 서버 양쪽에 있어야 한다(한쪽만 있으면 다른 경로에서 또 빈손).
    """
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    h = landing.render()
    assert "rcFillFrom" in h, "화면: 찾은 가게의 지역·업종을 채우는 보완이 없다"
    main = open(os.path.join(root, "app", "main.py"), encoding="utf-8").read()
    assert "상호로 보완" in main, "서버: 상호만 왔을 때의 보완이 없다"


def test_empty_result_message_does_not_ask_for_what_was_given():
    """상호를 넣었는데 '상호까지 입력하면…'이라고 답하면, 이미 한 일을 다시 시키는 것이다.
    빈손인 것보다 엉뚱한 안내가 더 나쁘다 — 왜 못 했는지를 사실대로 구분해 말한다."""
    from app.services import diagnose
    r = diagnose.diagnose_rank(industry="", region="", name="있을리없는가게이름ZZZ")
    assert "상호까지 입력하면" not in (r.get("subline") or ""), \
        "상호를 받고도 상호를 넣으라고 안내한다"
    r2 = diagnose.diagnose_rank(industry="", region="", name="")
    assert "상호" in (r2.get("subline") or ""), "상호가 없을 때는 상호를 요청해야 한다"


def test_resolved_region_uses_spoken_form():
    """2026-08-14 실측: 상호 보완은 됐는데 지역이 '부산광역시 동구'로 들어가
    '부산광역시 썬팅'(월 20회)이라는, 아무도 치지 않는 말로 진단하고 있었다.
    지명은 canonical 관문(seo._kw_shorten)을 반드시 거친다 — 규칙이 두 곳에 살면 안 된다."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main = open(os.path.join(root, "app", "main.py"), encoding="utf-8").read()
    i = main.find("상호로 보완")
    assert i > 0, "상호 보완 블록이 없다"
    around = main[max(0, i - 1500):i + 500]
    assert "_kw_shorten" in around, "보완한 지역이 구어형 관문을 안 거친다"
    from app import seo
    assert seo._kw_shorten("부산광역시 동구") == "부산 동구"


def test_category_is_normalized_to_a_searchable_word():
    """2026-08-14 실측: 지도 카테고리 '광택전문'을 그대로 업종으로 써서
    '부산 광택전문'(월 20회)으로 진단했다. '부산 광택'은 250회 — 12배 차이다.
    카테고리는 업체 분류명이지 검색어가 아니다."""
    from app import seo
    assert seo.canonical_industry("광택전문") == "광택"
    assert seo.canonical_industry("썬팅,광택") == "썬팅"          # 첫 분류만
    assert seo.canonical_industry("자동차정비,수리") == "자동차정비"
    assert seo.canonical_industry("정비") == "정비"               # 과교정 금지
    assert seo.canonical_industry("") == ""                       # 없으면 빈칸


def test_client_does_not_reinvent_normalization():
    """지명·업종 가공 규칙은 서버 한 곳에만 산다. 화면은 use_region/use_industry를
    받아 쓰기만 한다 — 규칙이 두 곳에 살면 또 갈라진다(오늘 두 번 갈라졌다)."""
    h = landing.render()
    assert "use_region" in h and "use_industry" in h, "화면이 서버 값을 안 쓴다"
    i = h.find("function rcFillFrom")
    around = h[i:i + 900]
    assert "특별시" not in around and "split(/[,·" not in around, \
        "화면이 지명·업종 가공 규칙을 다시 구현했다"


def test_province_regions_use_city_name_only():
    """2026-08-14 실측 — 구리 카센터 180회 / 구리시 카센터 30회 /
    경기도 구리시 자동차정비 20회. 도 이름을 붙이면 아무도 안 친다.
    광역시는 붙여 쓰지만(부산 동구 썬팅=실검색) 도는 빼고 시 이름만 쓴다."""
    from app import seo
    assert seo._kw_shorten("경기도 구리시 카센터") == "구리 카센터"
    assert seo._kw_shorten("경상남도 양산시 미용실") == "양산 미용실"
    assert seo._kw_shorten("구리시 카센터") == "구리 카센터"
    # 광역시+구는 그대로 — 실제로 검색되는 조합이다
    assert seo._kw_shorten("부산광역시 동구 썬팅") == "부산 동구 썬팅"
    assert seo._kw_shorten("부산 동구 썬팅") == "부산 동구 썬팅"
    # 지역을 통째로 지우면 전국 키워드가 된다(소상공인이 못 이기는 판)
    assert seo._kw_shorten("경기도 카센터") == "경기 카센터"
    # 과교정 금지 — 지명 자리가 아닌 '…시'는 건드리지 않는다
    assert seo._kw_shorten("자동차정비 시공 가격") == "자동차정비 시공 가격"
    assert seo._kw_shorten("임시 점검") == "임시 점검"
    # ★ 통합 행정구역(2026-08-14 실측): '전남광주통합특별시'는 공식 명칭이지만 사람은 '광주'라 친다.
    #   광주 자동차정비 90회 · 광주 카센터 540회 vs 전남광주통합 자동차정비 20회.
    assert seo._kw_shorten("전남광주통합특별시 남구 자동차정비") == "광주 남구 자동차정비"
    assert seo._kw_shorten("전남광주통합특별시 카센터") == "광주 카센터"
    assert seo._kw_shorten("통합 관리") == "통합 관리"          # 지명이 아니면 건드리지 않는다


# ── 전환 구조 (2026-08-14 시장 조사 반영) ────────────────────────
def test_result_cta_is_personalized():
    """조사: 개인화된 CTA가 일반 CTA보다 +202%. 결과를 본 직후가 가장 뜨거운 순간인데
    그 자리에 일반 문구('이 업종으로 만들어보기')가 있었다.
    가게 이름과 '비어 있는 검색어'를 그대로 넣는다 — 글이 있는 사장님과 없는 사장님에게
    할 말도 달라야 한다."""
    h = landing.render()
    assert "_nm" in h and "_gap" in h, "결과 CTA에 가게 이름·검색어가 안 들어간다"
    assert "첫 글 받기" in h, "글이 없는 사장님용 문구가 없다"
    assert "잡는 글 받기" in h, "글이 있는 사장님용 문구가 없다"
    # ★ 2026-08-14 사장님 지시 — 버튼 하나로 바로 가입. 그리고 방금 알아낸 가게 정보를
    #   함께 넘겨야 가입 직후 "딱 3가지만 알려주세요"에서 약속이 끊기지 않는다.
    assert "무료 가입하고" in h, "가입 직행 CTA가 아니다"
    assert "/login/kakao?'+_q" in h or "_href" in h, "가입 링크에 가게 정보를 안 싣는다"
    for k in ("'nm'", "'rg'", "'ind'", "'ad'", "'blog'", "'kw'"):
        assert k in h, f"가입 링크에 {k}가 빠졌다 — 온보딩에서 다시 묻게 된다"


def test_email_capture_sits_right_after_result():
    """대화형 도구의 높은 전환은 '결과를 받아보시겠어요?' 지점에서 나온다(조사).
    진단은 봤는데 가입 안 하는 사람이 대다수 — 가입보다 마찰 낮은 회수로가 결과 바로
    아래 있어야 한다."""
    h = landing.render()
    assert "이 결과를 이메일로 받아두세요" in h, "결과 직후 이메일 회수가 없다"
    # ★ 소스 위치가 아니라 '화면에 그려지는 순서'로 재야 한다 — CTA 문구는 innerHTML 조립보다
    #   위에서 변수로 만들어지므로 소스 순서로 재면 늘 어긋난다(2026-08-14 오판).
    i0 = h.find("o.innerHTML=")
    assert i0 > 0, "결과 조립부를 못 찾았다"
    seg = h[i0:i0 + 3000]
    order = [seg.find(x) for x in ("d.headline", "'+_cta+'", "이 결과를 이메일로 받아두세요",
                                   "아직 결심 안 되셨으면")]
    assert all(v >= 0 for v in order), f"결과 구성요소가 빠졌다: {order}"
    assert order == sorted(order), \
        f"마찰 순서(결과→가입→이메일→출구)가 어긋났다: {order}"


def test_pricing_is_a_single_agency_offer():
    """2026-08-17 사장님 결정 — 대행 단일 상품으로 전환.

    왜: 월 12.9만원 SaaS는 시세의 1/3이면서 **고객이 도구를 배워야 하는** 구조였다.
      소상공인 사장님은 도구를 배울 시간이 없다 → 안 팔렸다(유료 1건).
      요금제를 3개 늘어놓고 고르게 하는 것 자체가 '배우라'는 요구다.
    """
    from app import config as _cfg
    h = landing.render()
    assert f"월 {_cfg.AGENCY_FROM:,}원" in h, "대행 가격이 안 보인다"
    assert "요금제 3가지" not in h, "옛 3단 요금표가 남아 있다"
    for gone in ("라이트", "스탠다드"):
        assert f">{gone}<" not in h, f"옛 플랜이 남아 있다: {gone}"
    # 시세 앵커는 유지 — 없으면 39만원이 비싸 보인다(근거 표기도 함께)
    assert "월 38~77만원" in h, "가격 앵커가 사라졌다"
    assert "공개 마켓 실판매가" in h, "앵커 근거 표기가 없다(정직 게이트)"
    i_anchor, i_price = h.find("월 38~77만원"), h.find(f"월 {_cfg.AGENCY_FROM:,}원")
    assert 0 < i_anchor < i_price, "앵커가 우리 가격보다 뒤에 있다"


def test_pricing_promises_no_learning():
    """대행의 핵심 약속 — 사장님은 아무것도 배우지 않는다."""
    h = landing.render()
    assert "사진만 보내시면" in h, "대행 약속(사진만)이 없다"
    assert "배우실 것은 없습니다" in h, "'배울 게 없다'는 약속이 없다"


def test_ai_titles_are_clickable_and_carry_that_title():
    """2026-08-14 사장님 지적 — AI 제목이 카드처럼 생겼는데 눌러도 아무 일도 안 일어났다
    (실측: onclick 없음 · href 없음 · cursor auto · 클릭해도 스크롤·URL 그대로).
    내 가게 이름이 든 제목을 방금 본 순간이 가장 뜨겁다 — 그 자리를 죽여두면 안 된다.
    각 제목은 '그 글부터 만들어달라'는 가입 링크여야 하고, 그 제목을 kw로 실어야 한다."""
    h = landing.render()
    assert "window.__signupHref" in h, "제목 카드가 가입 링크를 재사용하지 않는다"
    # ★ 2026-08-14 실측 — 세 제목이 전부 같은 kw를 실어 보냈다(기존 kw가 이미 있어 무시됨).
    #   3개를 보여주고 고르게 해놓고 선택이 반영 안 되면 보여준 의미가 없다.
    assert "searchParams.set('kw'" in h, "고른 제목이 링크에 반영되지 않는다(같은 키 중복)"
    assert "누르시면 그 글부터" in h, "누를 수 있다는 안내가 없다"
    # 카드가 <a>여야 한다 — div면 눌러도 아무 일도 안 일어난다
    i = h.find("d.titles.map")
    assert i > 0
    seg = h[i:i + 700]
    assert "<a href=" in seg and "cursor-pointer" in seg, "제목 카드가 여전히 클릭 불가다"


def test_result_screen_has_one_primary_cta_and_one_exit():
    """2026-08-14 실측 — 진단 결과 화면에 가입 버튼이 한 화면에 7개(모바일)·6개(PC)였다.
    오늘 결과 CTA를 새로 넣으면서 원래 있던 버튼을 안 지운 탓이다. 계속 더하기만 했다.

    업계 권장은 '페이지당 주요 CTA 하나'. 선택지가 많으면 사람은 고르는 대신 멈춘다.
    → 결과가 뜨면 중복 진입로는 감춘다(hide-on-result). 남는 것은
      [주 CTA 하나] + [아직 결심 못 한 사람의 출구 하나]뿐이다.
    """
    h = landing.render()
    # 감추는 장치가 살아 있는가
    assert "body.has-result .hide-on-result" in h, "결과 화면에서 중복 버튼을 감추는 규칙이 없다"
    assert "classList.add('has-result')" in h, "결과가 떠도 감추기가 발동하지 않는다"
    for frag in ("이미 마음 정하셨다면", "이미 회원이면"):
        i = h.find(frag)
        assert i > 0, frag
        # 그 블록이 hide-on-result 표식을 달고 있는지(앞쪽 태그에서 확인)
        assert "hide-on-result" in h[max(0, i - 700):i], f"'{frag}' 구역이 결과 화면에서 안 감춰진다"
    assert "sm:hidden hide-on-result" in h, "하단 고정 배너가 결과 화면에서 안 감춰진다"

    # 결과 안에는 주 CTA 1개 + 출구 1개만
    i = h.find("무료 가입하고")
    seg = h[i:i + 2600]
    assert seg.count("fillDemo()") == 1, "결과 안 '가입 없이' 출구가 하나가 아니다"
    assert "계정 만들고 바로 시작하기" not in h, "중복 가입 링크가 되살아났다"


def test_signup_entry_points_are_bounded():
    """진입로 총량에 상한을 둔다 — 하나씩 늘리다 보면 오늘처럼 7개가 된다."""
    import re as _re
    h = landing.render()
    # 화면에 글자로 보이는 '시작/가입' 버튼·링크 개수(정적 마크업 기준)
    n = len(_re.findall(r"<(?:a|button)[^>]*>(?:(?!</(?:a|button)>).)*?(?:무료 시작|구글로 시작|무료로 시작하기|가입)", h, _re.S))
    # 소스 기준 개수(JS 분기 포함). 지금 값에 못을 박는다 — 하나라도 더하면 실패한다.
    # 오늘 이 값이 조용히 늘어 결과 화면에 7개가 겹쳤다. 늘리기 전에 지울 것부터 찾아라.
    assert n <= 9, f"가입 진입로가 {n}개로 늘었다 — 늘리기 전에 지울 것부터 찾아라"


def test_no_internal_notes_leak_to_visitors():
    """2026-08-15 발견 — 손님 화면에 만드는 사람 메모가 그대로 떠 있었다:
    '(← 검색 유입 손님 공감 = 이탈 방지)'. 예시 글 안에 주석을 넣어두고 안 뺐다.
    화면에 보이는 것은 전부 손님에게 하는 말이어야 한다."""
    t = _visible_text()
    # 화살표 자체는 UI에 쓰인다 — '(← 설명)' 꼴의 메모 패턴만 막는다
    assert "(←" not in t, "내부 메모('(← …)')가 손님 화면에 노출됐다"
    for note in ("이탈 방지", "신뢰·체류", "TODO", "FIXME"):
        assert note not in t, f"내부 메모가 손님 화면에 노출됐다: {note!r}"


def test_speaker_is_the_shop_not_the_visitor():
    """헌법: 화자는 가게(파는 쪽), 청자는 손님. '내가 쓴 글'이라고 하면 화자가 손님이 된다.
    사장님께 말할 때는 '사장님 글'이라고 한다."""
    t = _visible_text()
    assert "내가 쓴 글" not in t, "화자가 뒤집혔다(사장님 화면인데 '내가')"


def test_case_study_shows_the_drop_not_just_the_peak():
    """2026-08-15 — 랜딩이 "8/9 1위"에서 끊겨 있었다. 그런데 그날 실측하니 그 글은 10위였다.
    당시 실측은 맞았지만 오늘 여는 사람에겐 틀린 정보다 — 사장님들은 검색해서 확인한다.

    ★ 그리고 떨어진 것까지 보여주는 게 우리가 파는 것과 맞다:
      우리 상품은 '1위를 만들어드립니다'가 아니라 '떨어지는 걸 잡아드립니다'다.

    ★ 골든은 **날짜가 아니라 뜻**을 문다(2026-08-17). 특정 날짜를 박아두면
      실측이 갱신될 때마다 깨지고, 사람을 옛 숫자에 묶는다.
    """
    t = _visible_text()
    assert "1위" in t, "성과 사례가 사라졌다"
    i_peak = t.find("1위")
    seg = t[i_peak:i_peak + 320]
    # 최고점 뒤에 '지금 값'이 함께 있어야 한다 — 최고점에서 끊으면 오늘 여는 사람에게 틀린 정보다
    assert ("내려" in seg or "떨어" in seg or "밀렸" in seg), "순위가 내려온 사실을 말하지 않는다"
    assert "확인" in t or "실측" in t, "언제 잰 값인지 밝히지 않는다"


def test_case_numbers_are_consistent_across_surfaces():
    """같은 사례가 히어로·흐름 두 곳에 나온다. 한쪽만 고치면 화면끼리 어긋난다
    (표면 하나만 고치는 것이 오늘까지 반복한 사고의 모양)."""
    h = landing.render()
    # 옛 주장이 어느 표면에도 남아 있으면 안 된다
    import re as _re
    vis = _re.sub(r"<!--.*?-->", " ", h, flags=_re.S)
    assert "발행 9일 만에" not in vis, "옛 사례 문구가 어딘가에 남아 있다"
    # 옛 숫자(우리 rank_snapshots와 어긋났던 값)가 남으면 안 된다 — 사장님들은 검색해서 확인한다
    for stale in ("12위", "8/9"):
        assert stale not in vis, f"기록과 어긋나던 옛 사례 숫자가 남아 있다: {stale}"
    assert vis.count("8/17") >= 2, "정정한 숫자가 한 표면에만 반영됐다(표면 하나만 고치기)"
    assert vis.count("루마썬팅 현대상사") >= 2, "사례 주체가 한 표면에만 있다"


def test_go_button_cancels_pending_auto_navigation():
    """사장님 실물 신고(2026-08-16): "지금 보러가기 했을 때 바로 이동해야 한다 — 한참 후 이동한다."

    원인은 내 방어 코드였다. '안 넘어간다'를 고치려고 3초 자동 이동 + 1.5초 새로고침을
    겹겹이 넣었는데, 버튼을 눌러 이동이 시작돼도 그 타이머들이 살아남아 다시 이동을 걸었다.
    → 누르는 순간 예약된 타이머를 전부 끄고 즉시 보낸다.
    """
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "main.py")
    src = open(p, encoding="utf-8").read()
    i = src.find("var go=document.getElementById('gGo')")
    assert i > 0, "완성 모달의 이동 버튼을 못 찾았다"
    seg = src[i:i + 700]
    assert "go.onclick" in seg, "버튼에 클릭 처리기가 없다(타이머를 못 끈다)"
    assert "clearTimeout(window.__goT)" in seg, "자동 이동 타이머를 안 끈다"
    assert "clearTimeout(window.__goR)" in seg, "새로고침 타이머를 안 끈다"
    assert "__goT=setTimeout" in src, "자동 이동 타이머에 손잡이가 없다(클릭이 끌 수 없다)"
