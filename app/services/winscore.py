"""
🎲 승산 스코어 — "이길 수 있는 판인가"를 글 쓰기 전에 계산(사장님 승인 2026-07-28).

노출은 보장 못 한다(C-Rank 절반) — 이건 '확률 추정'이며 근거(factors)를 함께 반환해 정직하게 보인다.
재료: 상위 글 노후도(열린 문) + 상위 글 해부 실측(도달 가능한 기준선인가) + 내 과거 실측 승률.
쓰임: 생성 payload 기록(win_score), 진단 /admin/win-score, (후속) 자동큐 우선순위.
"""
from __future__ import annotations

import logging

from app import db

_log = logging.getLogger("shopcast.winscore")


def _my_track(tenant_id: str) -> dict:
    """내 블로그 실측 전적 — 발행 글들의 최고 순위 분포."""
    top10 = top30 = total = 0
    try:
        for pub in db.list_blog_publishes(tenant_id, limit=30):
            kw = (pub.get("target_kw") or "").strip()
            if not kw:
                continue
            hist = [h["rank"] for h in db.rank_history(tenant_id, kw, kind="post") if h.get("rank")]
            hist += [h["rank"] for h in db.rank_history(tenant_id, kw, kind="blog_search") if h.get("rank")]
            if not hist:
                continue
            total += 1
            best = min(hist)
            if best <= 10:
                top10 += 1
            elif best <= 30:
                top30 += 1
    except Exception:
        pass
    return {"total": total, "top10": top10, "top30": top30}


def score(tenant_id: str, keyword: str) -> dict:
    """승산 0~100 + 근거. 데이터 없는 요소는 중립 점수(과신 금지)."""
    factors = []
    pts = 0
    # ① 상위권 노후도 — 낡을수록 최신성으로 열린 문(실전 검증 신호)
    try:
        from app.services import blogrank
        st = blogrank.top_staleness_days(keyword)
    except Exception:
        st = -1
    if st >= 180:
        pts += 30; factors.append(f"상위권 낡음({st}일) +30")
    elif st >= 90:
        pts += 20; factors.append(f"상위권 다소 낡음({st}일) +20")
    elif st >= 30:
        pts += 10; factors.append(f"상위권 보통({st}일) +10")
    elif st >= 0:
        pts += 3; factors.append(f"상위권 최신({st}일) +3 — 치열한 판")
    else:
        pts += 10; factors.append("노후도 미상 +10(중립)")
    # ② 상위 글 해부 — 우리가 도달 가능한 기준선인가(캐시만, 크롤 대기 없음)
    try:
        from app.services import bloganatomy
        an = bloganatomy.cached(keyword)
        if an is None:
            bloganatomy.ensure_async(keyword)
    except Exception:
        an = None
    if an:
        if an["avg_chars"] <= 2400:
            pts += 20; factors.append(f"상위 평균 {an['avg_chars']}자 — 기본 밴드로 도달 +20")
        elif an["avg_chars"] <= 3600:
            pts += 12; factors.append(f"상위 평균 {an['avg_chars']}자 — 경쟁 분량으로 도달 +12")
        else:
            pts += 4; factors.append(f"상위 평균 {an['avg_chars']}자 — 고분량 판 +4")
        if an["avg_imgs"] <= 16:
            pts += 10; factors.append(f"상위 평균 사진 {an['avg_imgs']}장 — 도달 가능 +10")
        else:
            pts += 4; factors.append(f"상위 평균 사진 {an['avg_imgs']}장 +4")
        if an["video_pct"] <= 40:
            pts += 10; factors.append(f"동영상 글 {an['video_pct']}% — 영상 첨부가 무기 +10")
        else:
            pts += 5; factors.append(f"동영상 글 {an['video_pct']}% +5")
        if an["table_pct"] <= 50:
            pts += 5; factors.append(f"표 있는 글 {an['table_pct']}% — 구조 우위 +5")
    else:
        pts += 20; factors.append("해부 데이터 예열 중 +20(중립)")
    # ②b 상대 전력(2026-08-01 사장님 승인 ③) — 상위 10개 글의 '블로그 계정' 수준(RSS 활동성).
    #   약체(방치·저활동)가 섞여 있으면 비집고 들어갈 틈 — 상위 블로거의 판 고르기 루틴.
    if an and an.get("blogs_checked"):
        _wk, _stg = an.get("weak_blogs", 0), an.get("strong_blogs", 0)
        if _wk >= 3:
            pts += 15; factors.append(f"상위권 약체 블로그 {_wk}개 — 열린 판 +15")
        elif _wk == 2:
            pts += 10; factors.append("상위권 약체 블로그 2개 +10")
        elif _wk == 1:
            pts += 5; factors.append("상위권 약체 블로그 1개 +5")
        elif _stg >= 7:
            factors.append(f"상위권 활발 블로그 {_stg}개 — 치열한 판 +0")
        else:
            pts += 3; factors.append("상위권 계정 보통 +3")
    # ③ 내 전적 — 실측 승률
    tr = _my_track(tenant_id)
    if tr["top10"] >= 2:
        pts += 25; factors.append(f"내 전적 top10 {tr['top10']}회 +25")
    elif tr["top10"] == 1:
        pts += 18; factors.append("내 전적 top10 1회 +18")
    elif tr["top30"] >= 1:
        pts += 10; factors.append(f"내 전적 top30 {tr['top30']}회 +10")
    elif tr["total"] == 0:
        pts += 8; factors.append("발행 이력 없음 +8(신생 중립)")
    else:
        pts += 4; factors.append("아직 30위 밖 위주 +4")
    out = {"keyword": keyword, "score": max(0, min(100, pts)), "factors": factors,
           "staleness_days": st, "anatomy": bool(an), "track": tr}
    _log.info("[winscore] %r → %d", keyword, out["score"])
    return out
