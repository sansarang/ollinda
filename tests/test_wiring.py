"""
자율 운행 배선 박제(2026-08-03 공사 종료 라운드).

"돌 것이다"가 아니라 "돌고 있다"를 본다. 스케줄에서 빠지면 시스템이 조용히 멈춘다 —
아무도 모르고, 사장님만 "왜 안 되지"를 겪는다.
"""
from __future__ import annotations

import inspect

from app import scheduler


def test_autonomous_jobs_are_registered():
    """A. 지시 없이 돌아야 하는 것들이 스케줄에 실제로 등록되는가."""
    src = inspect.getsource(scheduler.start)
    for jid in ("competitor_daily", "rank_track_daily", "fresh_index", "rss_autosync",
                "gap_scan_daily", "disk_prune_daily", "autoscout_weekly"):
        assert f'id="{jid}"' in src, f"스케줄 누락: {jid}"


def test_gap_scan_runs_after_the_map_is_filled():
    """B. 빈자리 판정은 지면 지도가 채워진 '뒤'에 돌아야 한다.
    맥 야간 정찰이 04:00이므로 그보다 늦어야 한다 — 먼저 돌면 어제 지도로 판정한다."""
    src = inspect.getsource(scheduler.start)
    i = src.find('id="gap_scan_daily"')
    seg = src[max(0, i - 300):i]
    assert "hour=6" in seg, "정찰(04:00)보다 이른 시각이면 낡은 지도로 판정한다"


def test_gap_scan_judges_only():
    """C. 자동 실행되는 판정은 글감을 만들지 않는다 — 사람이 안 보는 사이 큐가 차면 안 된다."""
    src = inspect.getsource(scheduler._gap_scan_all)
    for forbidden in ("feed(", "enqueue", "writing_queue"):
        assert forbidden not in src, f"자동 판정이 글감을 건드린다: {forbidden}"


def test_weekly_report_reports_only():
    """D. 주 1회 자율 보고는 보고일 뿐이다 — 스스로 고치거나 발행하지 않는다."""
    src = inspect.getsource(scheduler._autoscout_report)
    for forbidden in ("publish", "feed(", "generate"):
        assert forbidden not in src, f"보고가 실행을 겸한다: {forbidden}"
    assert "add_notice" in src, "보고가 어디에도 남지 않는다"


def test_last_run_is_recorded():
    """E. 마지막 실행 시각을 남겨야 '돌고 있다'를 실증할 수 있다."""
    assert isinstance(scheduler.LAST_RUN, dict)
    for fn in (scheduler._gap_scan_all, scheduler._disk_prune, scheduler._autoscout_report):
        assert "_mark(" in inspect.getsource(fn), f"{fn.__name__}이 실행 흔적을 안 남긴다"


def test_push_gate_runs_goldens():
    """G. 골든이 실패해도 배포된 사고(2026-08-03) — 파이프라인 종료코드를 잘못 읽었다.
    사람 눈이 아니라 게이트가 막아야 한다."""
    import pathlib
    sh = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "safe-push.sh").read_text()
    assert "pytest tests/" in sh, "push 전에 골든을 돌리지 않는다"
    assert "exit 4" in sh, "골든 실패에도 push가 진행된다"
    assert "SHOPCAST_SKIP_TESTS" in sh, "긴급 우회 경로가 없다(있어야 하되 명시적이어야 한다)"


def test_wiring_endpoint_exposes_schedule_and_last_run():
    """F. 배선 상태는 화면에서 읽을 수 있어야 한다 — 코드를 열어봐야 알면 아무도 안 본다."""
    from app import main as _m
    src = inspect.getsource(_m.admin_wiring)
    for field in ("next_run", "last_run_utc", "trigger", "gowatch_configured", "local_cron"):
        assert field in src, f"배선 진단에 {field}가 없다"
    assert "노트북이 꺼지면" in src, "로컬 크론 의존을 경고하지 않는다"
