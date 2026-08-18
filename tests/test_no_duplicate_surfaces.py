"""같은 것을 두 곳에서 말하지 않는다 — 중복 표면 골든.

2026-08-18 사장님:
  "최근 발행 확인건?? 이거는 각 컨텐츠 분석과 중복되지 않니?"

맞았다. 블로그 카드의 '최근 발행 확인' 목록이 보여주던 것은 전부 다른 화면에 있었다:
  제목·발행일 → 홈 목록(날짜별)  ·  순위/N일차 → 조사 카드 그래프
  '이 글, 왜 이렇게 썼냐면' → 조사 카드 조사 항목  ·  주간 리포트 → 순위 그래프

중복 표면은 단순히 지저분한 게 아니다 — **같은 사실을 두 곳에서 다르게 말하게 된다.**
한쪽만 고치면 다른 쪽이 옛 값을 계속 말한다(캡션 결함이 10회 재발한 것과 같은 계열).

그리고 표면을 걷어낼 때 **버튼만 옮기고 그 버튼을 움직이는 스크립트를 두고 오면**
눌러도 아무 일이 없다. 오늘 실제로 그럴 뻔했다(analystView).
"""
import inspect
import os
import re

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import main


def _code(fn) -> str:
    """주석을 뺀 코드만 — 주석에는 '왜 지웠는지'가 적혀 있어서 그대로 검사하면
    지운 이름이 주석에 남았다는 이유로 오탐이 난다(실제로 났다)."""
    out = []
    for line in inspect.getsource(fn).splitlines():
        t = line.strip()
        if t.startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def test_주간리포트는_사라졌다():
    """사장님 지시로 제거 — 발행글마다 순위 추이 그래프가 그 역할을 한다."""
    src = _code(main._blog_connect_card)
    assert "latest_weekly_report" not in src, "주간 리포트가 되살아났다"
    assert "주간 리포트 <span" not in src


def test_발행목록이_두_곳에_있지_않다():
    """'최근 발행 확인' 목록은 홈 목록·조사 카드와 겹쳐서 지웠다."""
    src = _code(main._blog_connect_card)
    assert "최근 발행 확인" not in src, "중복 목록이 되살아났다"
    assert "def _pub_row(" not in src


def test_진단_버튼이_조사카드에_있다():
    """겹치지 않던 셋([순위 추적]·[왜 안 뜨나요?]·발행글 링크)은 살려야 한다."""
    src = _code(main._research_card)
    assert "raceView(" in src, "순위 추적 버튼이 없다"
    assert "whyNot(" in src, "'왜 안 뜨나요? 진단' 버튼이 없다"
    assert "발행된 글 보기" in src, "발행된 글로 가는 링크가 없다"


def test_스크립트가_버튼과_같은_화면에_있다():
    """★ 오늘 실제로 당할 뻔했다.

    `analystView`는 /api/race 응답 HTML 안의 버튼이 부른다. 그 결과가 삽입되는 곳은
    조사 카드다. 그런데 함수 정의는 블로그 카드(홈)에 있었다 —
    버튼은 미리보기에, 정의는 홈에. 눌러도 아무 일이 없는 '죽은 자리'다.
    """
    card = _code(main._research_card)
    for fn in ("whyNot", "raceView", "analystView"):
        assert f"async function {fn}(" in card, \
            f"{fn} 정의가 조사 카드에 없다 — 버튼만 있고 동작이 없으면 죽은 자리다"


def test_스크립트가_두_번_정의되지_않는다():
    """같은 함수가 두 곳에 정의되면 나중 것이 이긴다 — 한쪽만 고치면 조용히 어긋난다."""
    src = inspect.getsource(main)
    for fn in ("whyNot", "raceView", "analystView"):
        n = src.count(f"async function {fn}(")
        assert n == 1, f"{fn}이 {n}번 정의됐다(중복 정의)"


def test_조사카드에_그려질_것이_없으면_아무것도_안_그린다():
    """빈 카드는 '조사했는데 결과가 없다'로 읽힌다."""
    class _Empty:
        id = ""
        tenant_id = ""
        payload = {}

    class _T:
        id = ""
        name = "x"
    assert main._research_card(_T(), _Empty()) == ""


def test_브리핑은_완전히_사라졌다():
    """★ 2026-08-18 사장님: "브리핑은 의미 없다. 업체마다 나가는 날짜가 틀리다.
    이제 대행업체라는 것을 명심해라. 모든건 사장인 내가 직접 판단한다."

    대행에서는 발행 리듬을 시스템이 정하지 않는다. 화면 카드만 지우고 발송이 남으면
    사장님은 여전히 매일 아침 메일을 받는다 — 그래서 모듈·잡·라우트까지 전부 지웠다.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert not (root / "app" / "services" / "briefing.py").exists(), "브리핑 모듈이 되살아났다"
    sched = (root / "app" / "scheduler.py").read_text()
    for line in sched.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "briefing" not in line, f"브리핑 잡이 스케줄러에 남아 있다: {line.strip()[:60]}"
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not [p for p in paths if "briefing" in p], "브리핑 라우트가 남아 있다"


def test_자동_글준비는_죽지_않았다():
    """브리핑을 지우면서 같이 죽이면 안 되는 것 — autoqueue는 `briefing_on`을
    '자동 글 준비' 스위치로 읽는다(이름만 브리핑). 컬럼까지 지웠으면 준비가 멈춘다."""
    import inspect
    from app.services import autoqueue
    assert "briefing_on" in inspect.getsource(autoqueue), \
        "자동 글 준비 스위치가 사라졌다 — 브리핑과 함께 지워버린 것이다"


def test_목록은_접히고_날짜마다_색이_다르다():
    """사장님: "눌렀을때 펼쳐지게 / 펼쳤을때도 날짜별 색깔도 달리해라"."""
    import inspect
    src = inspect.getsource(main.my_dashboard)
    assert "<details id='myContent'" in src, "내 콘텐츠가 접히지 않는다"
    assert "_PALETTE" in src and "_PALETTE[_i % len(_PALETTE)]" in src, \
        "날짜별 색 순환이 없다"


def test_가입은_봉인됐다():
    """★ 2026-08-18 사장님: "사용자의 가입은 원하지 않는다.
    카카오 채널·전화·메일로 내가 직접 받는다."

    대행에서 가게는 우리 사이트에 회원가입하지 않는다.
    ★ 문을 닫는 일은 세 겹이었다 — 링크를 지우고(8/17 완료), 폼을 막고,
      **소셜 로그인 라우터를 내려야** 끝난다. 앞의 둘만 했을 때
      /login/kakao 가 307로 살아 있어 클릭 한 번에 계정이 생겼다.
    """
    paths = {getattr(r, "path", "") for r in main.app.routes}
    for dead in ("/login/kakao", "/login/google", "/login/naver",
                 "/login/kakao/callback", "/login/google/callback"):
        assert dead not in paths, f"소셜 가입 문이 다시 열렸다: {dead}"
    assert "/signup" in paths, "구 링크·북마크를 받아줄 자리는 남겨둔다(문의로 보냄)"


def test_가입_유도_문구가_남아있지_않다():
    """랜딩·데모의 CTA는 전부 문의로 모여야 한다 — 누를 곳이 가입이면 404다."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    bad = []
    for rel in ("app/main.py", "app/landing.py"):
        for i, line in enumerate((root / rel).read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if "/login/kakao" in line or "/login/google" in line or "/login/naver" in line:
                bad.append(f"{rel}:{i}")
    assert not bad, f"죽은 소셜 가입 링크가 화면에 남아 있다: {bad}"


def test_사장님_로그인은_살아있다():
    """가입을 막는 것과 로그인을 막는 것은 다르다 — 운영 화면 문까지 닫으면 안 된다."""
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/login" in paths and "/logout" in paths, "운영자 로그인이 사라졌다"
    assert "/admin/testaccount" in paths, "계정을 만들 안전망이 사라졌다"


def test_드립_메일은_꺼져_있다(monkeypatch):
    """★ 2026-08-18 사장님: "메일은 꺼라."

    실측이 이유다 — 리드 2,438건 중 **이메일이 3건(0.1%)**뿐이다.
    네이버 블로그는 이메일을 공개하지 않는다. 그래서 드립 대상이
    사장님 본인·테스트 계정 5개뿐이었고, 매일 사장님 메일함으로만 나갔다.

    ★ 끄는 일도 두 겹이다 — 자동 잡을 지우고 **수동 버튼도 잠가야** 끈 것이다.
      가입 봉인 때 배운 것과 같다(링크만 지우면 문은 열려 있다).
    """
    import inspect
    from app import scheduler
    src = inspect.getsource(scheduler.start)
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        assert "drip" not in line, f"드립 자동 발송이 되살아났다: {line.strip()[:60]}"
    # 수동 버튼은 명시적으로 켜야만 열린다(fail-closed)
    monkeypatch.delenv("OLLINDA_DRIP_ON", raising=False)
    r = main.admin_drip_run(dry=0)
    import json
    body = json.loads(bytes(r.body).decode())
    assert body["ok"] is False and body["sent"] == 0, "꺼둔 드립이 수동으로 나갔다"


def test_크레딧_확인_실패는_Sentry에_쌓지_않는다():
    """★ 2026-08-18 — 같은 에러 79건이 쌓여 진짜 에러가 묻혔다.

    `llm._probe_ok()`는 1분마다 haiku를 1토큰 찔러 '크레딧이 돌아왔나'를 본다.
    크레딧이 없으면 400이 나는 게 **정상**인데, Sentry 자동 계측이 그걸 전부 이슈로 올렸다.
    크레딧 소진은 watchtower가 이미 메일로 알린다 — 같은 사실을 79번 더 쌓을 이유가 없다.
    """
    import inspect
    src = inspect.getsource(main._init_sentry)
    assert "before_send" in src, "Sentry 필터가 사라졌다 — 소음이 다시 쌓인다"
    assert "credit balance is too low" in src, "크레딧 확인 실패를 거르지 않는다"
    # 알림 경로는 살아 있어야 한다(숨기는 게 아니라 옮긴 것)
    from app import llm
    assert "watchtower" in inspect.getsource(llm.note_credit_out), \
        "크레딧 소진 알림 경로가 사라졌다 — 이러면 진짜로 숨기는 것이다"
