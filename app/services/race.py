"""
내 글 생존 신고 — 발행 후 노출까지 실황 추적(생존신고 P1~P4).

발행~노출 사이 3~7일 깜깜이 구간을 실측 중계로 채운다:
색인 검출(P2) → 30위 내 첫 진입 → 일별 순위 이동(P1) → 다음 관문 안내(P3) + 경쟁 정찰(P4).
조회수는 측정 불가(네이버 전용) — 색인·순위·상위 글 발행일 등 공식 API로 확인되는 사실만 쓴다.

정직성: 순위 보장·과장 금지("가능성"까지만). 위치·기기·시점별 차이 명시.
스냅샷은 rank_snapshots(kind='post') — 포스트 URL 직접 대조라 블로그 단위 오탐이 없다.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import db

_log = logging.getLogger("shopcast.race")

HONEST_NOTE = ("순위는 위치·기기·시점에 따라 다르게 보일 수 있어요(실측 기준). "
               "노출을 보장하는 건 아니고, 실제 움직임을 그대로 보여드려요.")


def _kw_of(piece) -> str:
    if piece is None:
        return ""
    return ((piece.payload or {}).get("target_keywords") or [""])[0].strip()


def _kw_for(piece, publish: dict) -> str:
    """추적 키워드 — 올린다 글은 target_keywords, 외부 글은 publish.target_kw(자동 추출분)."""
    return _kw_of(piece) or ((publish or {}).get("target_kw") or "").strip()


def _days_since(ts: str) -> int:
    try:
        return max(0, (datetime.utcnow() - datetime.fromisoformat((ts or "")[:19])).days)
    except Exception:
        return -1


def track_publish(t, piece, publish: dict) -> dict:
    """발행 글 1건 실측(스케줄러·API 공용) — 색인 확인 + 포스트 순위 스냅샷(kind='post').
    반환 {indexed, rank}. 실패는 None으로 정직 표기."""
    from app.services import blogrank
    kw = _kw_for(piece, publish)
    url = (publish or {}).get("published_url") or ""
    title = (publish or {}).get("post_title") or ""
    pid = (publish or {}).get("piece_id") or (piece.id if piece else "")
    out = {"indexed": None, "rank": None}
    if not (kw and url and pid):
        return out
    if not title:
        # 수동 발행확인은 제목이 비어 있음 — RSS에서 실제 글 제목을 찾아야 색인 검사가 정확(오검출 방지)
        try:
            from app.services import blogsync
            bid = getattr(t, "blog_id", "") or blogsync.normalize_blog_id(url)
            for post in (blogsync.fetch_feed(bid).get("posts") or []) if bid else []:
                if blogrank._norm_post_url(post.get("link", "")) == blogrank._norm_post_url(url):
                    title = post.get("title") or ""
                    break
        except Exception:
            pass
        title = title or ((piece.payload or {}).get("title") if piece else "") or ""
    # 색인: 이미 확인됐으면 재조회 안 함(API 절약)
    if (publish or {}).get("indexed_at"):
        out["indexed"] = True
    else:
        idx = blogrank.check_indexed(title, url)
        out["indexed"] = idx
        if idx:
            db.mark_publish_indexed(pid)
    pr = blogrank.post_rank(kw, url)
    out["rank"] = pr.get("rank")
    if pr.get("rank") is not None:
        prev = db.get_prev_rank(t.id, kw, kind="post")
        db.save_rank_snapshot(t.id, kw, pr["rank"], kind="post")
        # 첫 페이지(10위) 첫 진입 → 축하 알림(성취감, P5) — 1회만
        if pr["rank"] and pr["rank"] <= 10 and (prev is None or prev == 0 or prev > 10):
            db.add_notice(t.id, "race",
                          f"'{kw}' 글이 블로그검색 {pr['rank']}위 — 첫 페이지에 진입했어요! 지금 굳히면 더 올라가요.")
    return out


def track_all_publishes(days: int = 45) -> dict:
    """스케줄러용 — 최근 발행 글 전부 일별 실측. 반환 {tracked}."""
    n = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if not t:
            continue
        for pub in db.list_blog_publishes(tid, limit=10):
            d = _days_since(pub.get("published_at") or "")
            if d > days:
                continue
            if d > 14 and datetime.utcnow().weekday() != 0:
                continue      # 비용 가드: 2주 지난 글은 주 1회(월요일)만 실측
            piece = db.get_piece(pub.get("piece_id") or "")   # 외부 글(rss_auto)은 piece 없음 — 그래도 추적
            try:
                track_publish(t, piece, pub)
                n += 1
            except Exception:
                _log.exception("[race] 추적 실패 piece=%s", pub.get("piece_id"))
    _log.info("[race] 발행 글 실황 추적 %d건", n)
    return {"tracked": n}


def _scout_line(kw: str, my_days: int) -> str:
    """경쟁 정찰(P4) — 상위 5개 글의 발행일 '사실'만으로 한 줄. 추측 서사 금지."""
    try:
        from app.services import blogrank
        top = blogrank.scout_top(kw, 5)
        ages = [x["age_days"] for x in top if x.get("age_days") is not None]
        if not ages:
            return ""
        a1 = next((x["age_days"] for x in top if x["rank"] == 1 and x.get("age_days") is not None), None)
        fresh = sum(1 for a in ages if a <= 14)
        if fresh >= 3:
            return (f"상위 {len(ages)}개 중 {fresh}개가 최근 2주 내 글 — 이 키워드는 지금 경쟁이 치열해요. "
                    "롱테일 키워드 병행을 권해요.")
        if a1 is not None and my_days >= 0 and a1 > 30:
            return f"지금 1위 글은 {a1}일 전 발행 — 내 글({my_days}일차)이 신선도에선 우위예요."
        if a1 is not None:
            return f"지금 1위 글은 {a1}일 전 발행이에요."
        return ""
    except Exception:
        return ""


def timeline(t, piece, publish: dict) -> dict:
    """생존 신고 타임라인 데이터(P3) — {kw, days, steps:[{status, title, detail}], scout, note}.
    steps: 발행 → 색인 → 첫 진입 → 현재 위치(이동) → 다음 관문."""
    kw = _kw_for(piece, publish)
    days = _days_since((publish or {}).get("published_at") or "")
    live = track_publish(t, piece, publish)          # 지금 실측(색인+오늘 순위 스냅샷 포함)
    hist = [h for h in db.rank_history(t.id, kw, kind="post", limit=60) if h.get("rank") is not None]
    pub_date = ((publish or {}).get("published_at") or "")[:10]
    hist = [h for h in hist if (h.get("checked_at") or "") >= pub_date]      # 발행일 이후만
    steps = []
    steps.append({"status": "ok", "title": f"발행 완료 · {pub_date or '날짜 미상'}",
                  "detail": f"오늘로 {days}일차예요." if days >= 0 else ""})
    # 색인
    idx_at = (publish or {}).get("indexed_at") or ""
    if idx_at or live.get("indexed"):
        steps.append({"status": "ok", "title": "네이버가 글을 받았어요 (색인 확인)",
                      "detail": "이제 순위 경쟁이 시작됐어요."})
    elif live.get("indexed") is False:
        steps.append({"status": "wait", "title": "네이버가 수집 중이에요 (보통 1~3일)",
                      "detail": "아직 검색 결과에 안 잡혀요 — 이 단계에선 기다리는 게 정상이에요."})
    else:
        steps.append({"status": "info", "title": "색인 확인 불가", "detail": "조회가 안 돼 다음 실측 때 다시 확인해요."})
    # 첫 진입
    first = next((h for h in hist if h["rank"] and h["rank"] >= 1), None)
    cur = live.get("rank")
    if first:
        fd = _days_since(first.get("checked_at") or "")
        steps.append({"status": "ok",
                      "title": f"30위 내 첫 진입 — {first['rank']}위",
                      "detail": f"{max(0, days - fd)}일차에 처음 잡혔어요." if days >= 0 and fd >= 0 else ""})
    # 현재 위치 + 이동
    if cur is None:
        steps.append({"status": "info", "title": "현재 순위 조회 불가", "detail": "잠시 후 다시 확인해요."})
    elif cur == 0:
        steps.append({"status": "wait", "title": "현재 31위 밖",
                      "detail": ("색인 직후엔 아래에서 시작해 올라오는 경우가 많아요 — 같은 주제 글이 쌓이면 빨라져요."
                                 if (idx_at or live.get("indexed")) else "색인되면 위치가 잡히기 시작해요.")})
    else:
        prev = next((h["rank"] for h in reversed(hist[:-1]) if h["rank"]), None)
        move = ""
        if prev and prev != cur:
            move = f" (어제 {prev}위 → 오늘 {cur}위 {'상승' if cur < prev else '하락'})"
        steps.append({"status": "ok" if cur <= 10 else "run",
                      "title": f"현재 블로그검색 {cur}위{move}",
                      "detail": ("첫 페이지에 있어요 — 지금 굳히기가 제일 효율 좋아요." if cur <= 10
                                 else "움직임이 보이는 구간이에요. 꾸준함이 답이에요.")})
        # 다음 관문
        if cur > 10:
            steps.append({"status": "gate", "title": "다음 관문: 10위 안 = 첫 페이지",
                          "detail": f"{cur - 10}계단 남았어요. 같은 키워드 글 1편이 가장 확실한 연료예요."})
        elif cur > 3:
            steps.append({"status": "gate", "title": "다음 관문: 3위 안 = 최상단",
                          "detail": "여기부턴 유지가 반이에요 — 주제 꾸준함을 지키세요."})
    return {"kw": kw, "days": days, "steps": steps,
            "scout": _scout_line(kw, days), "note": HONEST_NOTE,
            "history": [{"rank": h["rank"], "at": (h.get("checked_at") or "")[:10]} for h in hist][-14:]}
