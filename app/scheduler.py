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
        # 발행 리마인더(상위노출 PHASE 2) — 공백 N일이면 앱내+이메일(카톡 스텁), 매일 저녁
        sch.add_job(_publish_reminder, "cron", hour=18, minute=0,
                    id="publish_reminder", replace_existing=True)
        # 순위 자동추적(상위노출 PHASE 3) — tenant×타겟키워드 일일 스냅샷(아침, 스캔과 시차)
        sch.add_job(_rank_track, "cron", hour=7, minute=30,
                    id="rank_track_daily", replace_existing=True)
        # 아침 브리핑(브리핑 PHASE 2) — 매시 정각(05~12시), tenant별 설정 시각에 발송(1일 1회 락)
        sch.add_job(_morning_briefing, "cron", hour="5-12", minute=0,
                    id="morning_briefing", replace_existing=True)
        # 저녁 성과 피드백(브리핑 PHASE 4) — 20시
        sch.add_job(_evening_feedback, "cron", hour=20, minute=0,
                    id="evening_feedback", replace_existing=True)
        sch.start()
        _scheduler = sch
        logging.info("[scheduler] 경쟁사 일일 자동 스캔 등록(매일 %02d:00 KST)", hour)
        logging.info("[scheduler] 주간 블로그 리포트 등록(요일=%d %02d:10 KST)",
                     _cfg.WEEKLY_REPORT_DOW, _cfg.WEEKLY_REPORT_HOUR)
    except Exception:
        logging.exception("[scheduler] 기동 실패 — 자동 스캔 없이 계속")


def _morning_briefing() -> None:
    """매일 아침 브리핑 — 현재 KST 시각에 예약된 가게만(브리핑 PHASE 2)."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from app.services import briefing
        briefing.send_morning(datetime.now(ZoneInfo("Asia/Seoul")).hour)
    except Exception:
        logging.exception("[scheduler] 아침 브리핑 실패")


def _evening_feedback() -> None:
    """저녁 성과 피드백(브리핑 PHASE 4)."""
    try:
        from app.services import briefing
        briefing.send_evening()
    except Exception:
        logging.exception("[scheduler] 저녁 피드백 실패")


def _rank_track() -> None:
    """순위 자동추적(상위노출 PHASE 3) — 발행 전후 비교·학습 루프의 원천 데이터."""
    try:
        from app.services import ranktrack
        ranktrack.track_all()
    except Exception:
        logging.exception("[scheduler] 순위 자동추적 실패")


def _publish_reminder() -> None:
    """발행 공백 리마인더(상위노출 PHASE 2)."""
    try:
        from app.services import pubcal
        pubcal.remind_stale_tenants()
    except Exception:
        logging.exception("[scheduler] 발행 리마인더 실패")


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
