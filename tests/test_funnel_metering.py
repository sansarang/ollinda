"""깔때기 계측 골든 (2026-08-15 사장님 지시).

왜: 방문 수만 세고 있었다. "8명 왔다"까지만 말할 수 있었고
  · 그중 몇이 사람인지 모른다(user-agent를 안 남겨 클라우드 스캐너가 섞였다)
  · 진단을 실제로 눌러본 사람이 0명인지 10명인지 모른다(행동 기록이 없었다)
그 상태로 랜딩을 계속 고치는 건 눈 감고 고치는 것이다.
trackEv는 구글 애널리틱스로만 보내고 서버에는 아무것도 안 남겼다.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_visit_records_user_agent_and_referrer():
    """IP만 저장하면 사후에 봇을 못 가른다. 봇도 기록한다 — 걸러내려면 남아 있어야 한다."""
    m = _src("app/main.py")
    i = m.find('db.claim_once(f"visit:')
    assert i > 0, "방문 기록 지점을 못 찾았다"
    around = m[max(0, i - 1200):i + 200]
    assert 'db.log_event("view"' in around, "방문에 행동 기록을 안 남긴다"
    assert "ua=_ua_raw" in around and "referer" in around, "user-agent·유입경로를 안 남긴다"


def test_event_endpoint_trusts_server_not_client():
    """IP·UA·유입경로는 서버가 붙인다 — 화면이 보낸 값을 믿으면 조작된다."""
    m = _src("app/main.py")
    i = m.find('@app.post("/api/ev")')
    assert i > 0, "/api/ev 가 없다"
    seg = m[i:i + 1600]
    assert "_client_ip(request)" in seg, "IP를 서버가 안 붙인다"
    assert "user-agent" in seg and "referer" in seg, "UA·유입경로를 서버가 안 붙인다"
    assert "ratelimit" in seg or "_rl.allow" in seg, "레이트리밋이 없다(로그 폭주)"


def test_trackev_sends_to_server_too():
    """기록 경로를 두 벌로 만들지 않는다 — 기존 trackEv 하나에 서버 전송을 더한다."""
    from app import landing
    h = landing.render()
    assert "/api/ev" in h, "서버로 행동을 안 보낸다"
    assert h.count("function trackEv") == 1, "기록 함수가 두 벌이다"
    assert "sendBeacon" in h, "이탈 직전 이벤트가 유실된다(sendBeacon 없음)"


def test_funnel_steps_are_instrumented():
    """깔때기의 각 단계가 계측돼야 어디서 끊기는지 안다."""
    from app import landing
    h = landing.render()
    for ev in ("diagnose_submit", "diagnose_result", "title_click",
               "signup_click", "demo_submit", "demo_exit"):
        assert ev in h, f"계측 누락: {ev}"


def test_ops_summary_reports_funnel_and_bot_split():
    """사령탑이 받아 그릴 수 있어야 한다 — DB를 매번 캐지 않는다."""
    m = _src("app/main.py")
    i = m.find('@app.get("/admin/ops-summary")')
    assert i > 0
    seg = m[i:i + 6000]
    assert '"funnel"' in seg and '"views_by_kind"' in seg, "깔때기·봇 분류를 안 돌려준다"
    assert "fmt_kst" in seg, "오늘 기준이 한국 날짜가 아니다"
