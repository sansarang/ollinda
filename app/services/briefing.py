"""
매일 아침 브리핑 — AI 사장님 파트너(브리핑 PHASE 1).
수동(사장님이 들어와야 일어남) → 능동(올린다가 먼저 "오늘 상황 + 할 일 딱 하나")으로.

심리 설계:
  ① 할 일은 딱 하나 — 신호들을 점수화해 최고 1개만, 나머지는 버린다(5개 주면 0개 함).
  ② 경쟁불안·손실 프레이밍은 '해결책과 함께'만 — 위협만 주고 끝내지 않는다.
  ③ "나머지는 제가 준비할게요" 파트너 톤 — 짐을 나눠 진다.

정직성: 실측 데이터(순위 스냅샷·경쟁사 스냅샷·발행 이력·실검색량)만 사용.
신호가 없으면 지어내지 않고 "오늘은 특별한 변화 없어요" 기본 브리핑.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from app import config, db

_log = logging.getLogger("shopcast.briefing")

# 신호별 기본 중요도(오늘 가장 임팩트 큰 것 1개 선정) — 높을수록 우선
_W_COMPETITOR = 90     # 경쟁사 역전/추격 = 손실 진행형(가장 급함)
_W_RANK_DOWN = 80      # 내 순위 하락
_W_GAP = 70            # 발행 공백(C-Rank 일관성 경고)
_W_MISSING_KW = 60     # 미노출 고검색량 키워드(선점 기회)
_W_RANK_UP = 50        # 순위 상승(굳히기)
_W_DEFAULT = 10


def _sig_competitor(t) -> dict | None:
    """경쟁사 위협 — 최근 스냅샷에서 경쟁사가 나보다 위거나 역전."""
    try:
        comps = db.list_competitors(t.id)
        for comp in comps:
            snaps = db.competitor_snapshots(comp["id"], limit=2)
            if not snaps:
                continue
            s = snaps[0]
            my, their = s.get("my_rank"), s.get("competitor_rank")
            if their and (not my or their < my):
                kw = s.get("keyword") or (comp.get("keywords") or [""])[0] or \
                     f"{t.region} {t.industry}".strip()
                return {"score": _W_COMPETITOR, "kind": "competitor",
                        "headline": f"'{esc_kw(kw)}'에서 {comp['name']}이(가) 사장님보다 위에 있어요"
                                    f"({their}위 vs {my or '미노출'}).",
                        "task": f"'{kw}' 겨냥 글 1편 — 사진 3장만 보내주세요",
                        "reason": "경쟁사가 먼저 자리를 잡으면 되찾는 데 몇 배 오래 걸려요. 오늘 한 편이면 추격이 시작돼요.",
                        "kw": kw, "angle": "review"}
    except Exception:
        pass
    return None


def _sig_rank_moves(t) -> list[dict]:
    """내 순위 변화(상승/하락) — rank_snapshots 실측 비교."""
    out = []
    try:
        from app.services import ranktrack
        for d in ranktrack.rank_deltas(t.id, limit=6):
            f, l = d.get("first"), d.get("last")
            if f is None or l is None:
                continue
            fv, lv = (f or 31), (l or 31)
            kw = d["keyword"]
            if lv > fv:                                # 하락
                out.append({"score": _W_RANK_DOWN + min(9, lv - fv), "kind": "rank_down",
                            "headline": f"'{kw}' 순위가 {f or '미노출'}위 → {l or '미노출'}위로 밀렸어요.",
                            "task": f"'{kw}' 새 글 1편 — 최근 작업 사진 3장만 보내주세요",
                            "reason": "새 글이 끊기면 네이버가 '활동이 식었다'고 봐요. 오늘 한 편이면 흐름을 되돌릴 수 있어요.",
                            "kw": kw, "angle": "review"})
            elif lv < fv and (l or 0) > 0:             # 상승
                out.append({"score": _W_RANK_UP + min(9, fv - lv), "kind": "rank_up",
                            "headline": f"'{kw}' 순위가 {f or '미노출'}위 → {l}위로 오르는 중이에요!",
                            "task": f"기세 굳히기 — '{kw}' 글 1편 더 (사진 3장이면 충분해요)",
                            "reason": "오르는 키워드에 글을 더하면 상위 안착이 훨씬 빨라져요. 지금이 제일 효율 좋은 타이밍이에요.",
                            "kw": kw, "angle": "howto"})
    except Exception:
        pass
    return out


def _sig_publish_gap(t, plan: str) -> dict | None:
    """발행 공백 — C-Rank 일관성 경고(실측: publish_activity)."""
    try:
        from app.services import pubcal
        act = db.publish_activity(t.id)
        gap = act.get("gap_days")
        if act.get("last_at") and gap is not None and gap >= config.REMIND_GAP_DAYS:
            wp = pubcal.week_plan(t, plan)
            topic = (wp["suggestions"][0]["topic"] if wp["suggestions"]
                     else f"{t.region} {t.industry}".strip())
            angle = (wp["suggestions"][0]["angle"] if wp["suggestions"] else "review")
            return {"score": _W_GAP + min(9, gap), "kind": "gap",
                    "headline": f"{gap}일째 새 발행이 없어요 (이번 주 {wp['done']}/{wp['target']}회).",
                    "task": f"오늘 '{topic}' 1편 — 사진 3장만 보내주세요",
                    "reason": "발행 간격이 벌어지면 쌓아둔 C-Rank 꾸준함 신호가 식어요. 한 편이면 페이스가 돌아와요.",
                    "kw": topic, "angle": angle}
    except Exception:
        pass
    return None


def _sig_missing_kw(t) -> dict | None:
    """미노출 고검색량 키워드 — 진단(실측 순위+실검색량) 기반 선점 기회."""
    try:
        from app.services import diagnose
        r = diagnose.diagnose_rank(t.industry, t.region, t.name)
        if r.get("estimated"):
            return None
        miss = sorted(r.get("missing") or [], key=lambda s: -(s.get("volume") or 0))
        if miss:
            kw, vol = miss[0]["keyword"], miss[0].get("volume") or 0
            vol_txt = f"월 {vol:,}회 검색되는데 " if vol else ""
            return {"score": _W_MISSING_KW + (5 if vol >= 1000 else 0), "kind": "missing_kw",
                    "headline": f"'{kw}' — {vol_txt}사장님 가게가 아직 안 보여요.",
                    "task": f"'{kw}' 선점 글 1편 — 사진 3장만 보내주세요",
                    "reason": ("그 검색이 지금은 전부 다른 가게로 가고 있어요. "
                               "먼저 자리 잡은 글이 오래 갑니다."),
                    "kw": kw, "vol": vol, "angle": "review"}
    except Exception:
        pass
    return None


def esc_kw(s: str) -> str:
    return (s or "").replace("<", "").replace(">", "")


def build_briefing(t, plan: str = "free") -> dict:
    """tenant의 '오늘의 브리핑 1건' — 신호 점수화 → 최고 1개만(딱 하나 원칙).
    반환: {kind, headline, task, reason, action_href, action_label, pass_href, score, date}."""
    signals: list[dict] = []
    for s in ([_sig_competitor(t)] + _sig_rank_moves(t)
              + [_sig_publish_gap(t, plan), _sig_missing_kw(t)]):
        if s:
            signals.append(s)
    if signals:
        best = max(signals, key=lambda x: x["score"])
    else:
        # 정직한 기본 브리핑 — 신호를 지어내지 않는다
        from app.services import pubcal
        wp = pubcal.week_plan(t, plan)
        topic = (wp["suggestions"][0]["topic"] if wp["suggestions"]
                 else f"{t.region} {t.industry}".strip() or "핵심 주제")
        best = {"score": _W_DEFAULT, "kind": "steady",
                "headline": "오늘은 순위·경쟁에 특별한 변화가 없어요 — 좋은 신호예요.",
                "task": f"꾸준함이 곧 순위예요 — '{topic}' 1편, 사진 3장이면 충분해요",
                "reason": "네이버 C-Rank는 '같은 주제 꾸준한 발행'을 가장 오래 기억해요.",
                "kw": topic, "angle": "review"}
    kw = best.get("kw") or ""
    best["action_href"] = (f"/me?target_kw={quote(kw)}&angle={best.get('angle', 'review')}&from=briefing"
                           if kw else "/me")
    best["action_label"] = "사진 보내고 시작하기"
    best["pass_href"] = "/api/briefing/pass"
    best["partner_note"] = "사진만 보내주시면 글·영상·발행 준비는 제가 할게요."
    best["date"] = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    return best


def get_or_create_today(t, plan: str = "free") -> dict:
    """오늘 브리핑 조회(있으면 재사용 — 1일 1회) 없으면 생성·저장."""
    today = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    cached = db.get_briefing(t.id, today)
    if cached:
        return cached
    b = build_briefing(t, plan)
    db.save_briefing(t.id, today, b)
    return b
