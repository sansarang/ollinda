"""🤖 크롤러 로그 골든 — UA를 믿지 않는 것이 핵심."""
import inspect

from app.services import botlog as B


def test_UA만_믿지_않는다():
    """UA가 'Yeti'라 주장해도 위조 가능하다 — IP 대역으로 검증한다(R2)."""
    assert B.verify_yeti("125.209.192.0") and B.verify_yeti("125.209.255.255")
    assert not B.verify_yeti("125.209.191.255") and not B.verify_yeti("125.210.0.1")
    assert not B.verify_yeti("") and not B.verify_yeti("nonsense")
    src = inspect.getsource(B.record)
    assert "yeti_verified" in src and "UA만 믿지 않는다" in src


def test_봇_종류를_나눠_센다():
    """텍스트 수집용과 크롬 렌더링용은 다른 일을 한다(R3)."""
    assert B.ua_kind("Mozilla/5.0 (compatible; Yeti/1.1; +http://naver.me/spd)") == "text"
    assert B.ua_kind("Mozilla/5.0 (compatible; Yeti/1.1) Chrome/120.0") == "render"
    assert B.ua_kind("Yeti-Mobile-image/1.0") == "image"


def test_사람_방문은_기록하지_않는다():
    """개인정보·용량 — 봇만 남긴다."""
    src = inspect.getsource(B.record)
    assert "_BOT.search(ua)" in src and "return" in src


def test_원본과_집계를_분리한다():
    """집계는 나중에 다시 할 수 있어야 한다(R4)."""
    assert '"a"' in inspect.getsource(B.record), "원본을 덮어쓴다"
    s = inspect.getsource(B.summary)
    for k in ("yeti_verified", "yeti_unverified", "yeti_by_path", "yeti_by_status", "yeti_daily"):
        assert k in s, f"집계 항목 누락: {k}"


def test_못_보는_것을_명시한다():
    """네이버 블로그 로그는 네이버 서버에 있다 — 숨기면 '다 봤다'는 착각을 준다."""
    assert "못 본다" in inspect.getsource(B.summary)
    assert "못 본다" in inspect.getsource(B)[:1200]


def test_기록_실패가_서비스를_막지_않는다():
    src = inspect.getsource(B.record)
    assert "except Exception" in src and "서비스를 막지 않는다" in src
    from app import main as m
    msrc = inspect.getsource(m.bot_access_log)
    assert "except Exception" in msrc and "call_next" in msrc
    assert msrc.index("call_next") < msrc.index("botlog"), "응답 전에 기록해 상태코드를 못 남긴다"
