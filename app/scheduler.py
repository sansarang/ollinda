"""
경쟁사 일일 자동 스캔(신규기능① PHASE 3) — APScheduler(BackgroundScheduler).
지연 import로 apscheduler 미설치 시 조용히 비활성(수동 트리거는 계속 동작).
인스턴스 1개(1 Replica) 전제라 중복 실행 우려 낮음. 재시작 시 잡 재등록.
"""
from __future__ import annotations

import logging
import os

_scheduler = None


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if os.environ.get("SHOPCAST_DISABLE_SCHEDULER") == "1":
        logging.info("[scheduler] 비활성(SHOPCAST_DISABLE_SCHEDULER=1)")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:
        logging.info("[scheduler] apscheduler 미설치 → 자동 스캔 비활성(수동 트리거는 동작)")
        return
    try:
        hour = int(os.environ.get("SHOPCAST_SCAN_HOUR", "9"))
        sch = BackgroundScheduler(daemon=True, timezone="Asia/Seoul")
        sch.add_job(_daily_scan, "cron", hour=hour, minute=0,
                    id="competitor_daily", replace_existing=True)
        # 주간 성과 리포트(블로그등록 PHASE 4) — 블로그 연결 가게 대상, 월요일 아침
        from app import config as _cfg
        sch.add_job(_weekly_blog_report, "cron",
                    day_of_week=_cfg.WEEKLY_REPORT_DOW, hour=_cfg.WEEKLY_REPORT_HOUR, minute=10,
                    id="weekly_blog_report", replace_existing=True)
        sch.start()
        _scheduler = sch
        logging.info("[scheduler] 경쟁사 일일 자동 스캔 등록(매일 %02d:00 KST)", hour)
        logging.info("[scheduler] 주간 블로그 리포트 등록(요일=%d %02d:10 KST)",
                     _cfg.WEEKLY_REPORT_DOW, _cfg.WEEKLY_REPORT_HOUR)
    except Exception:
        logging.exception("[scheduler] 기동 실패 — 자동 스캔 없이 계속")


def _weekly_blog_report() -> None:
    """주간 성과 리포트 — 블로그 연결 가게 전체(블로그등록 PHASE 4)."""
    try:
        from app.services import weekly_report
        weekly_report.send_all()
    except Exception:
        logging.exception("[scheduler] 주간 블로그 리포트 실패")


def _daily_scan() -> None:
    """active 경쟁사 전체 자동 스캔(자동 benefit — 사용자 수동 한도와 무관)."""
    from app import db
    from app.services import competitor
    try:
        comps = db.list_competitors_all_active()
    except Exception:
        logging.exception("[scheduler] 경쟁사 목록 조회 실패")
        return
    ok = 0
    for comp in comps:
        try:
            t = db.get_tenant(comp["tenant_id"])
            if t:
                competitor.scan_competitor(t, comp)
                ok += 1
        except Exception:
            logging.exception("[scheduler] 경쟁사 스캔 실패 id=%s", comp.get("id"))
    logging.info("[scheduler] 일일 자동 스캔 완료 %d/%d", ok, len(comps))
