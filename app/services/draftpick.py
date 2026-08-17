"""초안 선별 — 여러 각도로 써 보고 **가장 뜰 것 하나만** 남긴다.

왜 가능해졌나(2026-08-17):
  본문 원가가 오퍼스 $0.54 → Solar $0.003으로 떨어졌다. 편당 4원이다.
  대행사는 글 한 편에 사람이 붙어 편당 3~5만원이라 **버리는 게 비싸서** 못 고른다.
  우리는 3편 써도 12원이라 **버리는 게 사실상 공짜**다 — 그래서 고를 수 있다.

★ 3편을 다 올리는 게 아니다. 그건 저품질 양산이고 블로그가 죽는다(헌법 금지선).
  **발행 전에 골라서 1편만 남긴다.** 나머지는 버린다.

★ 점수는 코드가 낸다 — LLM 호출 0회.
  LLM에게 "어느 글이 나아?"를 물으면 그럴듯한 이유를 지어낸다. 우리가 파는 게
  판단이므로 판단은 잴 수 있는 것만으로 한다(board.judge와 같은 원칙).

배점 근거는 전부 오늘 실측이다:
  · 노린 질의 커버 — 네이버는 문단을 뽑아 노출한다(상위글 339개 대조, 84%가 질의별로 다른 대목).
    커버 0이면 그 글은 그 검색어에서 뽑힐 덩어리가 없다 → **결정적 항목**
  · 두꺼운 문단 — 뽑아갈 단위. 상위글 소제목 중간값 2개(잘게 쪼개면 단위가 사라진다)
  · 사진 뭉침 — 한곳에 몰리면 그 사이 본문이 없다는 뜻
  · 문단 편차 — 전부 같은 길이면 기계 티(실측: 발행글 106, 추론 끈 초안 55)
"""
from __future__ import annotations

import re
import statistics

#: 배점 — 합계 100. 커버리지가 절반을 차지한다(없으면 노출 자체가 안 된다).
W_COVER = 50
W_THICK = 20
W_SPREAD = 15
W_RHYTHM = 15

THICK_TARGET = 3      # 두꺼운 문단 이 개수까지 만점(실측 발행글 3개)
RHYTHM_GOOD = 90      # 문단 길이 표준편차 이 이상이면 리듬 만점(실측 발행글 106)


def _photo_stats(body: str) -> tuple:
    """(마커 수, 붙어 있는 곳 수) — [사진1][사진2]처럼 사이에 본문이 없는 지점."""
    n = len(re.findall(r"\[사진\d+\]", body or ""))
    bunched = len(re.findall(r"\[사진\d+\]\s*\[사진\d+\]", body or ""))
    return n, bunched


def score(body: str, plan: dict) -> dict:
    """초안 하나의 점수(0~100)와 항목별 내역. 잴 수 없는 것은 점수에 넣지 않는다."""
    from app.services import answerblock as ab

    cov = ab.query_coverage(body or "", plan or {})
    n_cov = sum(1 for c in cov if c.get("covered"))
    n_q = len(cov) or 1
    th = ab.thickness(body or "")
    paras = ab.paragraphs(body or "")
    lens = [len(p) for p in paras] or [0]
    n_photo, bunched = _photo_stats(body or "")

    s_cover = W_COVER * (n_cov / n_q)
    s_thick = W_THICK * min(1.0, th["n_thick"] / THICK_TARGET)
    s_spread = W_SPREAD * (1.0 if n_photo == 0 else max(0.0, 1.0 - bunched / max(1, n_photo)))
    s_rhythm = W_RHYTHM * min(1.0, (statistics.pstdev(lens) if len(lens) > 1 else 0) / RHYTHM_GOOD)

    total = round(s_cover + s_thick + s_spread + s_rhythm)
    return {"total": total, "chars": len(body or ""),
            "cover": f"{n_cov}/{n_q}", "cover_ok": n_cov == n_q,
            "thick": th["n_thick"], "photos": n_photo, "bunched": bunched,
            "rhythm": round(statistics.pstdev(lens) if len(lens) > 1 else 0),
            "parts": {"커버": round(s_cover), "두께": round(s_thick),
                      "사진분산": round(s_spread), "리듬": round(s_rhythm)}}


def pick(drafts: list, plan: dict) -> dict:
    """초안 목록에서 1등을 고른다. drafts=[{"angle":..,"body":..}, ...]

    ★ 커버리지를 모두 채운 초안이 하나라도 있으면 **그중에서만** 고른다.
      총점이 높아도 커버가 빈 글은 그 검색어에서 뽑히지 않는다 — 총점으로 뭉개면 안 된다.
    ★ 초안이 하나뿐이어도 점수는 매긴다(기록이 남아야 나중에 비교할 수 있다).
    """
    scored = []
    for d in (drafts or []):
        body = (d or {}).get("body") or ""
        if not body.strip():
            continue
        s = score(body, plan)
        scored.append({**{k: v for k, v in (d or {}).items() if k != "body"},
                       "body": body, "score": s})
    if not scored:
        return {"ok": False, "reason": "채점할 초안이 없다", "picked": None, "all": []}

    full = [s for s in scored if s["score"]["cover_ok"]]
    pool = full or scored
    pool.sort(key=lambda x: -x["score"]["total"])
    best = pool[0]
    why = (f"노린 질의를 모두 덮었고(커버 {best['score']['cover']}) 총점 {best['score']['total']}점"
           if best["score"]["cover_ok"]
           else f"질의를 다 덮은 초안이 없어 총점 우선 선택(커버 {best['score']['cover']})")
    return {"ok": True, "picked": best, "why": why,
            "dropped": [{"angle": s.get("angle"), "score": s["score"]["total"],
                         "cover": s["score"]["cover"]} for s in pool[1:]],
            "all": scored, "cover_full": len(full)}


def summary_line(res: dict) -> str:
    """사장님 화면용 — 주방 용어 금지. 몇 편을 써보고 골랐는지만 말한다."""
    if not res.get("ok"):
        return ""
    n = len(res.get("all") or [])
    if n <= 1:
        return ""
    return f"{n}가지 방향으로 써 보고 가장 잘 잡힐 것 하나를 골랐어요."
