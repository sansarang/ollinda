"""
경쟁사 일일 자동 스캔(신규기능① PHASE 3) — APScheduler(BackgroundScheduler).
지연 import로 apscheduler 미설치 시 조용히 비활성(수동 트리거는 계속 동작).
인스턴스 1개(1 Replica) 전제라 중복 실행 우려 낮음. 재시작 시 잡 재등록.
"""
from __future__ import annotations

import logging
import os

_scheduler = None
LAST_RUN: dict = {}          # 잡 id → 마지막 실행 시각(ISO) — 배선이 실제로 도는지 실증용


def _mark(job_id: str) -> None:
    from datetime import datetime
    LAST_RUN[job_id] = datetime.utcnow().isoformat(timespec="seconds")


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
        # RSS 자동 매칭(파이프 A1 보조 경로) — 3시간마다 새 글 감지→자동 연결/확인 요청
        sch.add_job(_fresh_index_check, "cron", minute="*/30",
                    id="fresh_index", replace_existing=True)   # 발행 후 24h 집중 색인 체크(2-3)
        sch.add_job(_rss_autosync, "cron", hour="*/2", minute=20,
                    id="rss_autosync", replace_existing=True)
        # 🕳 빈자리 판정 갱신(2026-08-03) — 야간 정찰이 지면 지도를 채운 '뒤'에 돈다(새벽 4시 스캔 → 6시 판정).
        #   판정만 한다. 글감 편입·카드 노출은 사장님 화면에서 사장님 판단으로.
        sch.add_job(_gap_scan_all, "cron", hour=6, minute=10,
                    id="gap_scan_daily", replace_existing=True)
        # 🛡 야간 자가 스캔(2026-08-05) — 사장님보다 시스템이 먼저 발견한다.
        #   새벽 3시: 생성이 도는 낮 시간과 겹치지 않게, 지면 정찰(4시)보다도 앞에.
        #   ★ 크레딧 잔량을 먼저 보고 부족하면 탐지만 한다(R7) — 야간 작업이 크레딧을 말려
        #     아침 생성을 죽이는 것이 면역계가 만드는 새 사고다.
        sch.add_job(_immune_nightscan, "cron", hour=3, minute=0,
                    id="immune_nightscan", replace_existing=True)
        # 🎯 빈 질문 선점(2026-08-06) — 새벽 5시. 지면 정찰(4시) 뒤, 빈자리 판정(6시) 앞.
        #   빈자리는 시간이 지나면 남이 채운다 — 주기적으로 다시 훑어야 의미가 있다.
        sch.add_job(_vacantq_nightly, "cron", hour=5, minute=0,
                    id="vacantq_nightly", replace_existing=True)
        # 🧹 디스크 정리(2026-08-03) — 원본은 R2에 영구 보존되고 로컬은 캐시다. 새벽에 오래된 미디어 정리.
        sch.add_job(_disk_prune, "cron", hour=4, minute=40,
                    id="disk_prune_daily", replace_existing=True)
        # 🖼 파생본 데우기(2026-08-03) — 전 가게 자동. 가입자가 늘어도 손이 안 가야 한다.
        #   업로드 경로가 1차 보장선이고, 이건 그물(이관·복원·과거분 누락을 메운다).
        sch.add_job(_derive_warm, "cron", hour=5, minute=10,
                    id="derive_warm_daily", replace_existing=True)
        # 📡 주 1회 자율 정찰 보고(CLAUDE.md 자율 리서치 원칙) — 노출 실측 변화 + 제안을 운영자에게.
        sch.add_job(_autoscout_report, "cron", day_of_week="mon", hour=8, minute=30,
                    id="autoscout_weekly", replace_existing=True)
        sch.start()
        # 배포/재시작 직후 1회 소급 동기화(완전 자동 A) — 버튼 없이 등록 블로그 새 글을 즉시 추적
        import threading as _th
        _th.Timer(40, _rss_autosync).start()
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


def _fresh_index_check() -> None:
    """(색인 가속 2-3) 발행 후 첫 24시간은 30분 간격 집중 색인 확인 — 이후엔 기존 주기(race)로 복귀.
    실측만: 제목 블로그검색으로 URL 검출 시에만 indexed_at 기록. 외부 핑 없음(합법 수단 부재 — CHANGES 참고)."""
    import logging
    from app import db
    from app.services import blogrank
    try:
        for p in db.recent_unindexed_publishes(hours=24, limit=20):
            try:
                ok = blogrank.check_indexed(p.get("post_title") or "", p.get("published_url") or "")
                if ok:
                    db.mark_publish_indexed(p["piece_id"])
                    logging.getLogger("shopcast.index").info(
                        "[index] 색인 확인 piece=%s (발행 %s)", p["piece_id"], p.get("published_at"))
            except Exception:
                logging.getLogger("shopcast.index").exception("[index] 체크 실패 piece=%s", p.get("piece_id"))
    except Exception:
        logging.getLogger("shopcast.index").exception("[index] 집중 체크 실패")
    try:
        from app.services.ingest import video_watchdog
        video_watchdog()                    # 죽은 영상 잡 감지·1회 재시도(같은 30분 주기에 얹음)
    except Exception:
        logging.getLogger("shopcast.video").exception("[video-watchdog] 크론 실패")
    try:
        from app.services.ingest import photo_edit_sweep
        photo_edit_sweep()                  # 재시작으로 죽은 병렬 사진 보정 마무리(개인정보 마스킹 보증)
    except Exception:
        logging.getLogger("shopcast.ingest").exception("[photo-edit-sweep] 크론 실패")
    try:
        from app.services import lessons
        lessons.sweep()                     # 🧪 미노출 자동 개선 — 격차 진단→교훈 적재→검증(UI 0개)
    except Exception:
        logging.getLogger("shopcast.lessons").exception("[lessons] 크론 실패")
    try:  # 🌐 유입 경로 진단(2026-08-01) — 주제·이웃·플레이스·외부 통로(하루 1회, 새벽)
        import datetime as _dtb
        _nb = _dtb.datetime.utcnow()
        if _nb.hour == 19 and _nb.minute < 30:           # KST 새벽 4시 — 30분 잡이라 분까지 봐야 1회
            from app.services import blogreach
            blogreach.sweep()
    except Exception:
        logging.getLogger("shopcast.blogreach").exception("[blogreach] 크론 실패")
    try:  # 🔎 검색어 정찰(2026-08-01) — 발행 글이 실제로 잡히는 검색어 실측(자격증명 0·검색 API만)
        import datetime as _dtq
        _nq = _dtq.datetime.utcnow()
        if _nq.hour % 6 == 0 and _nq.minute < 30:        # 6시간마다(쿼터 보호) — 중복 실행 방지
            from app.services import queryscout
            queryscout.sweep()
    except Exception:
        logging.getLogger("shopcast.queryscout").exception("[queryscout] 크론 실패")
    try:  # 🗼 서버 자가진단 — 대시보드가 꺼져 있어도 이상을 텔레그램으로 통보(2026-07-29)
        from app.services import watchtower
        watchtower.check()
        import datetime as _dtw
        if 22 <= _dtw.datetime.utcnow().hour <= 23:      # KST 아침 7~8시 = UTC 22~23시
            watchtower.daily_summary()
    except Exception:
        logging.getLogger("shopcast.watchtower").exception("[watchtower] 크론 실패")


def _immune_nightscan() -> None:
    """🛡 야간 자가 스캔 — 산출물 표면을 원장 유형별로 훑는다.

    ★ 크레딧이 없으면 탐지만 하고 수선은 건너뛴다(R7). 자동 수정은 무비용 기계 수선뿐이고,
      재생성·코드 수정이 필요한 것은 진단서로 대기시킨다(자동 실행 금지).
    ★ 원장은 읽기만 한다 — 갱신은 배포 시점(safe-push)이 맡는다.
    """
    try:
        from app import llm as _llm
        from app.services.immune import nightscan as _ns
        # ★ 원장은 여기서 갱신하지 않는다 — 배포 이미지에 .git이 없어 build()가 0행을 낸다.
        #   원장 갱신은 배포 시점(safe-push)에 코드 트리에서 이뤄지고, 서버는 읽기만 한다.
        ok = not _llm.credit_out()
        r = _ns.run(allow_fix=ok)                     # 크레딧 없으면 탐지만
        logging.getLogger("shopcast.immune").info(
            "[immune] 야간 스캔 — 탐지 %d · 수선 %d · 진단서 %d%s",
            len(r.get("detected") or []), len(r.get("fixed") or []),
            len(r.get("diagnoses") or []), "" if ok else " (크레딧 없음 — 탐지만)")
    except Exception:
        logging.exception("[scheduler] 면역 야간 스캔 실패")


def _vacantq_nightly() -> None:
    """🎯 빈 질문 훑기 — 실수요 질문 중 아직 답이 없는 자리를 찾아 글감 큐로.

    ★ 사장님은 사진만 올리면 된다. 목록만 만들면 제목을 옮겨 적어야 해서 노동이 는다.
    ★ 선점 검증도 함께 — 우리가 쓴 뒤 실제로 뜨는지 봐야 '쓰면 뜬다'가 검증된다.
    """
    try:
        from app import config as _cfg
        from app import db as _db
        from app.services.vacantq import feed as _fd, finder as _fn
        from app.services.vacantq import scan as _sc, suggest as _sg
        for tid in _cfg.PRODUCTION_TENANTS:
            t = _db.get_tenant(tid)
            if not t:
                continue
            mats = _fn.materials(tid)
            works = _fn.work_terms(mats, getattr(t, "region", "") or "")
            if not works:
                logging.getLogger("shopcast.vacantq").info(
                    "[vacantq] %s — 하는 일을 못 캤다(과거 글 부족). 건너뜀", tid[:8])
                continue
            seeds = _sg.seeds_for(works, getattr(t, "region", "") or "", mats.get("anchors"))
            cand = [x for x in _sg.expand(seeds[:4], depth=2)["rows"] if x["depth"] == 2]
            res = _sc.scan(cand[:12], limit=12)
            got = _fd.feed(tid, res["vacant"])
            logging.getLogger("shopcast.vacantq").info(
                "[vacantq] %s — 실수요 %d · 빈자리 %d · 큐 편입 %d%s",
                tid[:8], len(cand), res["n_vacant"], got["n_added"],
                " (차단)" if res.get("blocked") else "")
            try:
                v = _fd.verify_claims(tid)
                if v.get("checked"):
                    logging.getLogger("shopcast.vacantq").info(
                        "[vacantq] 선점 검증 %d건 중 %d건 떴다", v["checked"], v["won"])
            except Exception:
                logging.exception("[vacantq] 선점 검증 실패")
    except Exception:
        logging.exception("[scheduler] 빈 질문 훑기 실패")


def _rss_autosync() -> None:
    """RSS 폴링 자동 매칭(파이프 A1) — 발행 URL 붙여넣기를 잊어도 파이프라인이 이어지게."""
    try:
        from app.services import pipesync
        pipesync.auto_sync_all()
    except Exception:
        logging.exception("[scheduler] RSS 자동매칭 실패")


def _rank_track() -> None:
    """순위 자동추적(상위노출 PHASE 3) — 발행 전후 비교·학습 루프의 원천 데이터."""
    try:
        from app.services import ranktrack
        ranktrack.track_all()
    except Exception:
        logging.exception("[scheduler] 순위 자동추적 실패")
    try:      # 생존 신고(생존신고 P1·P2) — 발행 글 포스트 단위 색인·순위 일별 실측
        from app.services import race
        race.track_all_publishes()
    except Exception:
        logging.exception("[scheduler] 발행 글 실황 추적 실패")
    try:      # 트랙2 — gowatch 적응 큐 소비(관측 변화 → 개선 제안 카드). 자동 발행 0.
        from app.services import adapt_consume, gowatch_client
        if gowatch_client.configured():
            r = adapt_consume.consume_all()
            logging.info("[scheduler] gowatch 소비: %s", r)
    except Exception:
        logging.exception("[scheduler] gowatch 적응 소비 실패")
    try:      # 자동 글감 큐 적재(auto) — 스냅샷 갱신 직후 P1~P4 소스로 채움
        from app.services import autoqueue
        autoqueue.refill_all()
    except Exception:
        logging.exception("[scheduler] 글감 큐 적재 실패")
    try:      # 발행 슬롯 공백 자동 채움(auto) — 유료 플랜만, tenant당 1글
        from app.services import autoqueue
        autoqueue.slot_fill_all()
    except Exception:
        logging.exception("[scheduler] 슬롯 자동 채움 실패")


def _gap_scan_all() -> None:
    """🕳 전 가게 빈자리 판정 갱신 — 지면 지도가 채워진 뒤 판정만 한다(글감 편입 없음).
    판정이 최신이어야 사장님이 사진 올릴 때 그 자리를 노릴 수 있다(seo._gap_first)."""
    _mark("gap_scan_daily")
    try:
        from app import db
        from app.services import gapscout
        n = 0
        for t in db.list_tenants() or []:
            try:
                r = gapscout.scan(t.id if hasattr(t, "id") else t.get("id"), limit=30)
                n += len(r.get("gaps") or [])
            except Exception:
                continue
        logging.info("[scheduler] 빈자리 판정 갱신 — 총 %d건", n)
    except Exception:
        logging.exception("[scheduler] 빈자리 판정 실패")


def _derive_warm() -> None:
    """🖼 전 가게 파생본 데우기 — 누락분만 만든다(있으면 건너뛴다).
    화면이 요청하는 그 경로 기준으로 채운다(services/derived.py 단일 함수)."""
    _mark("derive_warm_daily")
    try:
        import os as _os
        from app import db
        from app.services import derived as _dv
        made = fail = 0
        for t in db.list_tenants() or []:
            tid = t.id if hasattr(t, "id") else t.get("id")
            for s0 in db.list_sets(tenant_id=tid, limit=200):
                imgs = []
                for p0 in db.get_set_pieces(s0.get("asset_id") or ""):
                    for x in ((p0.payload or {}).get("image_paths") or []):
                        if x and x not in imgs:
                            imgs.append(x)
                for x in imgs:
                    fn = _os.path.basename(x)
                    if _dv.has_thumb(tid, fn) and _dv.has_web(tid, fn):
                        continue
                    ok = _dv.make_thumb(tid, fn) and _dv.make_web(tid, fn)
                    made += 1 if ok else 0
                    fail += 0 if ok else 1
        logging.info("[scheduler] 파생본 데우기 — 생성 %d 실패 %d", made, fail)
    except Exception:
        logging.exception("[scheduler] 파생본 데우기 실패")


def _disk_prune() -> None:
    """🧹 오래된 미디어 정리 — 원본은 R2에 있고 로컬은 캐시다(설계). 실패는 로그로 남긴다."""
    _mark("disk_prune_daily")
    try:
        from app import db, main as _m
        freed = 0
        for t in db.list_tenants() or []:
            tid = t.id if hasattr(t, "id") else t.get("id")
            try:
                freed += _m._prune_old_media(tid, keep_recent=4)
            except Exception:
                continue
        logging.info("[scheduler] 디스크 정리 — %d개 제거", freed)
    except Exception:
        logging.exception("[scheduler] 디스크 정리 실패")


def _autoscout_report() -> None:
    """📡 주 1회 자율 정찰 보고(CLAUDE.md 자율 리서치 원칙).
    노출 실측 변화 + 빈자리 상위 + 열린 확인 카드를 운영자 공지로 남긴다.
    ★ 보고일 뿐 아무것도 자동 실행하지 않는다."""
    _mark("autoscout_weekly")
    try:
        from app import db
        from app.services import exposure, gapscout
        lines = []
        for t in db.list_tenants() or []:
            tid = t.id if hasattr(t, "id") else t.get("id")
            nm = getattr(t, "name", "") or (t.get("name") if isinstance(t, dict) else "")
            try:
                ex = exposure.summary(tid) or {}
                gaps = [g for g in gapscout.list_gaps(tid, domain="확실", limit=5)
                        if (g.get("score") or 0) > 0]
            except Exception:
                continue
            if not (ex or gaps):
                continue
            top = ", ".join(f"{g['keyword']}({g['volume']}회)" for g in gaps[:3]) or "없음"
            lines.append(f"{nm} — 빈자리 상위: {top}")
        if lines:
            db.add_notice("", "autoscout", "주간 자율 정찰\n" + "\n".join(lines[:20]))
        logging.info("[scheduler] 자율 정찰 보고 — %d개 가게", len(lines))
    except Exception:
        logging.exception("[scheduler] 자율 정찰 보고 실패")


def _publish_reminder() -> None:
    """발행 공백 리마인더(상위노출 PHASE 2)."""
    _mark("publish_reminder")
    try:
        from app.services import pubcal
        pubcal.remind_stale_tenants()
    except Exception:
        logging.exception("[scheduler] 발행 리마인더 실패")


def _weekly_blog_report() -> None:
    """주간 성과 리포트 — 블로그 연결 가게 전체(블로그등록 PHASE 4)."""
    _mark("weekly_blog_report")
    try:
        from app.services import weekly_report
        weekly_report.send_all()
    except Exception:
        logging.exception("[scheduler] 주간 블로그 리포트 실패")


def _daily_scan() -> None:
    """active 경쟁사 전체 자동 스캔(자동 benefit — 사용자 수동 한도와 무관)."""
    _mark("competitor_daily")
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
