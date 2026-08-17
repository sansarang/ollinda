"""🎓 학습 에이전트 (L2) — 관측을 파라미터로 바꾸는 유일한 주체.

2026-08-17 사장님 지시: "발행하는 순간 데이터도 즉시 로직에 적용되어야 한다."
  그래서 크론만 기다리지 않는다. **발행 이벤트에서 바로 돈다.**

하는 일 셋:
  ① on_publish  — 발행 즉시: 그 글에 걸린 실험을 추적 대상으로 걸고, 계측값에서 즉시 배울 것을 배운다
  ② judge_ready — 3~5일 지난 실험을 순위로 판정(재평가 구간 실측 근거)
  ③ propose     — 계측값에서 다음 실험값을 제안

왜 이 구조인가:
  · 계측값(뭉침·커버율·배치 일치율)은 **발행 즉시** 알 수 있다 → ①에서 바로 학습
  · 순위는 3~5일 걸린다(자사 궤적 실측: 19위 진입 → 3일 뒤 1위) → ②에서 판정
  둘을 섞으면 안 된다. 즉시 알 수 있는 것을 3일 기다릴 이유가 없고,
  3일 걸리는 것을 즉시 판정하면 그건 추측이다.

★ 교란 제거 — 판 전체가 흔들린 날의 순위 변화는 판정에서 뺀다.
  2026-08-17 조사: Google 3월 업데이트에서 상위 10위 중 1/4이 100위 밖으로 나갔다.
  그런 날 우리 글이 떨어진 것을 '실험 실패'로 세면 학습이 오염된다.
"""
from __future__ import annotations

import logging

from app import db
from app.agents import LEARNER, journal, params

_log = logging.getLogger("shopcast.agents.learner")

#: 순위 판정까지 기다리는 일수 — 자사 궤적 실측(진입 순위 ≠ 최종 순위, 재평가 3~5일)
JUDGE_AFTER_DAYS = 4
#: 이긴 것으로 볼 순위. 1페이지 진입이 실질 성과선이다.
WIN_RANK = 10
#: 판이 흔들린 날로 보는 기준 — 같은 검색어 상위 10 중 이만큼이 바뀌면 교란으로 본다
TURBULENCE = 0.4


def _metrics(payload: dict) -> dict:
    """발행 즉시 알 수 있는 계측값만 뽑는다(순위는 여기 없다)."""
    pl = payload or {}
    pp = pl.get("photo_placement") or {}
    tc = pl.get("term_coverage") or {}
    cap = pl.get("photo_capped") or {}
    body = pl.get("body") or ""
    import re
    return {
        "chars": len(body),
        "photos": len(re.findall(r"\[사진\d+\]", body)),
        "bunched": len(re.findall(r"\[사진\d+\]\s*\[사진\d+\]", body)),
        "place_rate": pp.get("rate"),
        "term_pct": tc.get("pct"),
        "outsiders": len(pl.get("outsider_terms") or []),
        "uploaded": cap.get("uploaded"),
        "in_body": cap.get("in_body"),
    }


def on_publish(tenant, piece) -> dict:
    """발행 즉시 — 실험 추적 등록 + 즉시 배울 것 학습. **크론을 기다리지 않는다.**"""
    pid = getattr(piece, "id", "") or ""
    tid = getattr(tenant, "id", "") or ""
    m = _metrics(getattr(piece, "payload", None) or {})
    journal.write(LEARNER, f"발행 감지 — 사진 {m['photos']}장·뭉침 {m['bunched']}곳"
                           f"·배치 {m['place_rate']}%·용어 {m['term_pct']}%",
                  why="발행 시점 계측 확보. 순위 판정은 %d일 뒤." % JUDGE_AFTER_DAYS,
                  kind="note", tenant_id=tid, piece_id=pid, data=str(m))

    # ① 즉시 학습 — 뭉침은 순위를 기다릴 필요가 없다. 0곳이면 그 조합이 옳았다는 뜻이다.
    acted = []
    try:
        if m["bunched"] == 0 and m["photos"] and m["in_body"] and m["uploaded"]:
            if m["uploaded"] > m["in_body"]:
                # 잘라서 뭉침 0을 얻었다 → 조금 더 넣어볼 여지가 있는지 실험
                from app.services import photocap as pc
                cur = params.get(f"photo:{tid}", "per_para", pc.PER_PARA)
                if params.propose(f"photo:{tid}", "per_para", round(cur * 1.15, 3), LEARNER,
                                  f"뭉침 0곳 — 사진을 더 넣어볼 여지 확인({m['in_body']}장)", pc.PER_PARA):
                    acted.append("per_para↑")
        elif m["bunched"] >= 3:
            from app.services import photocap as pc
            cur = params.get(f"photo:{tid}", "per_para", pc.PER_PARA)
            if params.propose(f"photo:{tid}", "per_para", round(cur * 0.8, 3), LEARNER,
                              f"뭉침 {m['bunched']}곳 — 사진 밀도를 낮춘다", pc.PER_PARA):
                acted.append("per_para↓")
    except Exception:
        _log.exception("[learner] 즉시 학습 실패")

    # ② 이 글에 실험값이 쓰였으면 추적 대상으로 건다
    try:
        params.mark_applied(f"photo:{tid}", pid, LEARNER)
    except Exception:
        pass

    if acted:
        journal.write(LEARNER, f"파라미터 실험 제안 — {', '.join(acted)}",
                      why="다음 생성 1건에만 적용된다. 실패하면 자동 복귀.",
                      kind="act", tenant_id=tid, piece_id=pid)
    return {"metrics": m, "acted": acted}


def _turbulent(tenant_id: str, keyword: str) -> bool:
    """그 판이 통째로 흔들렸는가 — 우리 탓과 남의 탓을 가른다."""
    try:
        from app.services import rivaltrack as rv
        tr = rv.trajectories(keyword) if hasattr(rv, "trajectories") else None
        if not tr:
            return False
        moved = sum(1 for t in tr if abs((t.get("delta") or 0)) >= 3)
        return bool(tr) and (moved / max(1, len(tr))) >= TURBULENCE
    except Exception:
        return False                      # 모르면 교란이 아니라고 본다(판정을 미루지 않는다)


def judge_ready(tenant) -> int:
    """재평가 구간이 지난 실험을 순위로 판정한다. 반환: 판정한 건수."""
    tid = getattr(tenant, "id", "") or ""
    n = 0
    try:
        from app.services import surfaces as sf
        pubs = db.list_blog_publishes(tid, limit=30)
        for pub in pubs:
            pid = pub.get("piece_id") or ""
            if not pid:
                continue
            open_trials = params.pending_for(pid)
            if not open_trials:
                continue
            from app.services.lessons import _days_since  # 기존 계산기 재사용(규칙 단일화)
            if _days_since(pub.get("published_at") or "") < JUDGE_AFTER_DAYS:
                continue
            kw = (pub.get("keyword") or "").strip()
            if not kw:
                continue
            if _turbulent(tid, kw):
                journal.write(LEARNER, f"판정 보류 — '{kw}' 판 전체가 흔들림",
                              why="교란된 날의 순위로 실험을 판정하면 학습이 오염된다.",
                              kind="note", tenant_id=tid, piece_id=pid)
                continue
            best = None
            for kind in sf.PRIORITY:
                r = db.latest_rank(tid, kw, kind) if hasattr(db, "latest_rank") else None
                if isinstance(r, int) and r >= 1:
                    best = r if best is None else min(best, r)
            won = bool(best and best <= WIN_RANK)
            for t in open_trials:
                res = params.judge(t["scope"], pid, won,
                                   note=f"{kw} {best if best else '미노출'}위")
                n += 1
                journal.write(LEARNER,
                              f"실험 판정 {'승' if won else '패'} — {t['name']}={t['value']}",
                              why=f"'{kw}' {best if best else '미노출'}위"
                                  + ({"promoted": " → 기본값으로 승격",
                                      "retired": " → 폐기하고 이전 값 복귀"}.get(res, "")),
                              kind="act" if res else "note", tenant_id=tid, piece_id=pid)
    except Exception:
        _log.exception("[learner] 판정 실패 t=%s", tid)
    return n
