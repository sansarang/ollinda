"""
자동 글감 큐(auto) — "AI가 결론 냈으면 AI가 실행한다".

기존 분석 결과(정체/미노출/굳히기/키워드 풀)를 글감으로 자동 적재(P1~P4)하고,
사진 업로드 시·발행 슬롯 공백 시 우선순위대로 소비해 기존 파이프라인(생성→품질 게이트→
유사문서 회피→스케줄 배분)을 그대로 태운다. 유저에게 키워드·승률을 보여주지 않는다.

정직성: 사진 없이 글을 지어내지 않는다(need_photos 상태 노출). 실측 근거는 reason(내부 로그)에.
우선순위: P1 정체 앵글 재도전 > P2 미노출 선점 > P3 굳히기(근소 격차 가점) > P4 키워드 풀 폴백.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from app import db

_log = logging.getLogger("shopcast.autoqueue")

REFILL_P2_MAX = 2
SIM_THRESHOLD = 0.45


# ── 적재(refill) — 순위추적 스냅샷 갱신 직후 / 큐 비었을 때 ─────────
def refill(t, plan: str = "free") -> dict:
    """기존 분석 산출물 → 큐 적재. 반환 {P1,P2,P3,P4} 적재 수."""
    from app.services import ranktrack
    added = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    # P1 — 정체 키워드 앵글 재도전(기존 처방 로직 출력 그대로)
    try:
        for s in ranktrack.stagnant_keywords(t.id, limit=2):
            if db.enqueue_writing(t.id, "P1", s["keyword"], s["retry_angle"],
                                  f"정체(스냅샷 {s['first']}→{s['last']}) — {s['prev_label']} 대신 {s['retry_label']} 재도전"):
                added["P1"] += 1
                _log.info("[autoqueue] 적재 P1 t=%s kw=%r angle=%s", t.id, s["keyword"], s["retry_angle"])
    except Exception:
        _log.exception("[autoqueue] P1 적재 실패 t=%s", t.id)
    # P2 — 미노출(놓치는) 키워드 선점(기존 진단 재사용, 매장/셀러 분기)
    try:
        from app.services import diagnose
        if (getattr(t, "biz_type", "local") or "local") == "seller":
            r = diagnose.diagnose_product_rank(t.industry, getattr(t, "brand_name", "") or t.name,
                                               getattr(t, "brand_name", "") or "")
        else:
            r = diagnose.diagnose_rank(t.industry, t.region, t.name)
        if not r.get("estimated"):
            miss = sorted(r.get("missing") or [], key=lambda s: -(s.get("volume") or 0))
            for s in miss[:REFILL_P2_MAX]:
                if db.enqueue_writing(t.id, "P2", s["keyword"], "review",
                                      f"미노출(월 {s.get('volume') or 0}회 실측) 선점"):
                    added["P2"] += 1
                    _log.info("[autoqueue] 적재 P2 t=%s kw=%r vol=%s", t.id, s["keyword"], s.get("volume"))
    except Exception:
        _log.exception("[autoqueue] P2 적재 실패 t=%s", t.id)
    # P3 — 잘 되는 키워드 굳히기(+근소 격차 가점: '○○만 넘으면 N-1위' → 프롬프트 반영 재료)
    try:
        for d in ranktrack.rank_deltas(t.id, limit=4):
            if d.get("dir") not in ("up", "enter") or not d.get("last"):
                continue
            reason = f"상승 굳히기({d.get('first') or '미노출'}→{d['last']}위, {d.get('kind')})"
            if d.get("kind") in ("blog", "place") and 2 <= d["last"] <= 5:
                try:
                    from app.services import place
                    det = place.rank_detail(d["keyword"], t.name)
                    if det.get("rival"):
                        reason += f" | 근소격차: '{det['rival']}'만 넘으면 {d['last'] - 1}위"
                except Exception:
                    pass
            if db.enqueue_writing(t.id, "P3", d["keyword"], "howto", reason):
                added["P3"] += 1
                _log.info("[autoqueue] 적재 P3 t=%s kw=%r reason=%s", t.id, d["keyword"], reason)
            break                                     # 굳히기는 1건이면 충분
    except Exception:
        _log.exception("[autoqueue] P3 적재 실패 t=%s", t.id)
    # P4 — 키워드 풀의 미사용 최고 승률(폴백 — 큐가 비지 않게)
    try:
        if not db.writing_queue_rows(t.id, status="pending", limit=1):
            batches = db.list_keyword_batches(t.id, limit=1)
            if not batches:
                from app import ratelimit
                if ratelimit.cache_get(f"aq:mine:{t.id}", 86400) is None:   # 풀 없으면 1일 1회 자동 발굴
                    ratelimit.cache_set(f"aq:mine:{t.id}", 1)
                    from app.services import mass
                    mass.mine(t)
                    batches = db.list_keyword_batches(t.id, limit=1)
            for b in batches:
                for it in b["items"]:
                    if it.get("top10") and (it.get("status") or "candidate") == "candidate":
                        if db.enqueue_writing(t.id, "P4", it["keyword"], "review",
                                              f"키워드 풀 최고 승률 {it.get('win')}%(예상·내부용) 미사용분"):
                            added["P4"] += 1
                            _log.info("[autoqueue] 적재 P4 t=%s kw=%r win=%s", t.id, it["keyword"], it.get("win"))
                        break
    except Exception:
        _log.exception("[autoqueue] P4 적재 실패 t=%s", t.id)
    return added


def refill_all() -> None:
    """스케줄러용 — 순위추적 직후 전 가게 큐 적재."""
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if t and (t.industry or "").strip():
            try:
                refill(t, u.get("plan") or "free")
            except Exception:
                _log.exception("[autoqueue] refill 실패 t=%s", tid)


# ── 소비(consume) — 큐 1건 → 글 생성(기존 파이프라인) ────────────
def _existing_kw_set(t) -> set:
    """이미 발행됐거나 준비 중인 글의 타겟 키워드 — 동일 키워드 큐 항목은 skip."""
    out = set()
    for pub in db.list_blog_publishes(t.id, limit=30):
        k = (pub.get("target_kw") or "").strip()
        if k:
            out.add(k)
        piece = db.get_piece(pub.get("piece_id") or "")
        if piece:
            k2 = ((piece.payload or {}).get("target_keywords") or [""])[0].strip()
            if k2:
                out.add(k2)
    for b in db.list_keyword_batches(t.id, limit=3):
        for it in b["items"]:
            if it.get("piece_id") and it.get("status") in ("ready", "needs_fix", "generating"):
                out.add(it["keyword"])
    for row in db.writing_queue_rows(t.id, status="done", limit=30):
        if row.get("target_keyword"):
            out.add(row["target_keyword"])
    return out


def photo_pool(t) -> list:
    """재사용 가능한 최근 사진 세트(디스크 존재분) — 없으면 [] (사진 없이 글 안 지음)."""
    for s in db.list_sets(tenant_id=t.id, limit=10):
        ps = db.get_set_pieces(s["asset_id"])
        for p in ps:
            paths = [x for x in (p.payload.get("image_paths") or []) if x and os.path.exists(x)]
            if paths:
                return paths
    return []


def _schedule_date(t) -> str:
    """발행 슬롯 배분 — 오늘 슬롯(하루 1~2개) 비었으면 오늘, 찼으면 다음 빈 날."""
    counts: dict = {}
    for b in db.list_keyword_batches(t.id, limit=5):
        for it in b["items"]:
            d = it.get("scheduled_date")
            if d and it.get("status") in ("ready", "needs_fix"):
                counts[d] = counts.get(d, 0) + 1
    for row in db.writing_queue_rows(t.id, status="done", limit=30):
        pass                                          # 큐 생성분은 batch에 없음 — piece 스케줄은 아래 별도 기록
    day = datetime.utcnow().date()
    for _ in range(14):
        if counts.get(day.isoformat(), 0) < 1:        # 자동 생성분은 하루 1개 기본
            return day.isoformat()
        day += timedelta(days=1)
    return day.isoformat()


def consume(t, files: list | None = None, plan: str = "free") -> dict:
    """큐 1건 소비 → 글 생성. files 없으면 photo_pool 재사용, 그것도 없으면 need_photos.
    반환 {ok, made?, keyword?, source?, need_photos?, empty?}."""
    from app.domain.models import AssetType, ContentKind
    from app.industries import resolve_industry
    from app.services.generate import generate_for
    from app.services.revise import autofix_instruction, revise_piece
    from app.services import mass
    from app import storage, vision
    if not db.writing_queue_rows(t.id, status="pending", limit=1):
        refill(t, plan)
    paths = []
    if files:
        paths = [storage.save_upload(data, name or "p.jpg", t.id) for data, name in files]
    else:
        paths = photo_pool(t)
    if not paths:
        return {"ok": False, "need_photos": True}
    existing = _existing_kw_set(t)
    for _ in range(4):                                # 같은 키워드 skip 후 다음 항목
        q = db.claim_writing(t.id)
        if not q:
            return {"ok": False, "empty": True}
        kw = q["target_keyword"]
        if kw in existing:
            db.mark_writing(q["id"], "skipped", reason_append="이미 같은 키워드 글 준비/발행됨")
            _log.info("[autoqueue] skip t=%s kw=%r (중복)", t.id, kw)
            continue
        _log.info("[autoqueue] 소비 %s t=%s kw=%r angle=%s reason=%s",
                  q["source_type"], t.id, kw, q["angle"], q["reason"])
        try:
            prof = resolve_industry(t.industry or "")
            note = ("[자동 글감] " + (q.get("reason") or ""))
            if "근소격차" in (q.get("reason") or ""):
                note += ("\n[경쟁 격차 공략] 바로 위 경쟁 글을 이기려면 그 글보다 더 구체적인 실측·경험·"
                         "사진 설명을 담아라. 같은 의도를 더 정확히 충족하는 글이 이긴다(비방 금지).")
            note += ("\n[배치 생성 — 유사문서 금지] 기존 글들과 도입·구성·소제목·사례를 완전히 다르게. "
                     "이 글은 오직 이 키워드 하나의 검색 의도에만 답한다.")
            analysis = vision.analyze_all(paths, t.industry) if paths else ""
            if analysis:
                note += f"\n[사진 분석] {analysis[:1500]}"
            asset = db.create_asset(t.id, AssetType.IMAGE, paths[0], note)
            asset.target_kw = kw
            asset.angle = q["angle"] if q["angle"] in ("review", "howto", "price") else "review"
            pieces = generate_for(t, asset, [ContentKind.BLOG], images=paths)
            if not pieces:
                raise RuntimeError("no pieces")
            p = pieces[0]
            # 유사문서 회피 — 최근 준비/발행 글들과 대조(기존 3-gram Jaccard)
            recent_bodies = []
            for s in db.list_sets(tenant_id=t.id, limit=6):
                for rp in db.get_set_pieces(s["asset_id"]):
                    if rp.kind.value == "blog" and rp.id != p.id and rp.payload.get("body"):
                        recent_bodies.append(rp.payload["body"])
            if any(mass.similarity(p.payload.get("body") or "", b) > SIM_THRESHOLD for b in recent_bodies[:6]):
                revise_piece(p, "기존 글과 구성이 비슷하다. 도입·소제목·사례·문단 순서를 전혀 다르게 다시 써라. "
                                f"타겟 키워드 '{kw}'와 검색 의도는 유지.")
            gate = mass.industry_gate(prof, p.payload, getattr(t, "biz_type", "local") or "local")
            if not gate["passed"]:                    # 품질 게이트 미달 자동 보완(기존)
                try:
                    revise_piece(p, autofix_instruction(p.payload.get("ranking_audit") or {}, "blog")
                                 or "경험 문장과 구체 수치를 보강")
                    gate = mass.industry_gate(prof, p.payload, getattr(t, "biz_type", "local") or "local")
                except Exception:
                    pass
            try:
                from app.services import tracklinks
                tracklinks.inject(t, p)
            except Exception:
                pass
            p.payload["auto_queue"] = q["source_type"]
            p.payload["gate"] = gate
            p.payload["scheduled_date"] = _schedule_date(t)
            db.save_piece(p)
            db.mark_writing(q["id"], "done", piece_id=p.id)
            _log.info("[autoqueue] 완료 %s t=%s kw=%r piece=%s gate=%s 예정=%s",
                      q["source_type"], t.id, kw, p.id, gate["score"], p.payload["scheduled_date"])
            return {"ok": True, "made": 1, "keyword": kw, "source": q["source_type"],
                    "piece_id": p.id, "asset_id": asset.id, "gate": gate["score"],
                    "scheduled_date": p.payload["scheduled_date"]}
        except Exception:
            _log.exception("[autoqueue] 생성 실패 t=%s kw=%r", t.id, kw)
            db.rollback_writing(q["id"])
            return {"ok": False, "error": "generate_failed"}
    return {"ok": False, "empty": True}


def state(t) -> dict:
    """홈 상태 — {pending, need_photos, due_ready}. 사진 없이 글 안 지음(정직)."""
    pending = len(db.writing_queue_rows(t.id, status="pending", limit=10))
    ready_unpub = 0
    for row in db.writing_queue_rows(t.id, status="done", limit=20):
        if row.get("piece_id") and not db.get_blog_publish(row["piece_id"]):
            ready_unpub += 1
    for b in db.list_keyword_batches(t.id, limit=3):
        for it in b["items"]:
            if it.get("piece_id") and it.get("status") in ("ready", "needs_fix") \
                    and not db.get_blog_publish(it["piece_id"]):
                ready_unpub += 1
    return {"pending": pending, "ready_unpub": ready_unpub,
            "need_photos": bool(pending and not ready_unpub and not photo_pool(t))}


def slot_fill_all() -> None:
    """스케줄러용 — 발행 슬롯 공백 + 준비 글 없음 + 사진 풀 있음 → 자동 1건 생성.
    플랜 쿼터를 존중(무료 남은 횟수 없으면 생성 안 함)."""
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        if (u.get("plan") or "free") == "free":
            continue      # 무료 플랜의 잔여 횟수를 자동 생성이 몰래 소모하지 않는다(비용·신뢰 가드)
        t = db.get_tenant(tid)
        if not (t and (t.industry or "").strip() and getattr(t, "briefing_on", 1)):
            continue
        try:
            st = state(t)
            if st["ready_unpub"] or not st["pending"]:
                continue
            if not photo_pool(t):
                continue                              # 사진 없으면 홈 need_photos 상태로만
            consume(t, None, u.get("plan") or "free")   # 하루 1회 잡 — tenant당 최대 1글
        except Exception:
            _log.exception("[autoqueue] slot_fill 실패 t=%s", tid)
