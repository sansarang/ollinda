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


def _bad_kw(kw: str) -> bool:
    """쉼표·슬래시 든 키워드 = 업종 원문이 그대로 굳은 오염 데이터 — 검색어로 성립 안 하므로 큐에 안 넣는다."""
    return ("," in (kw or "")) or ("/" in (kw or ""))


def _basic_region_tokens(region: str) -> list:
    """기초지역(구·군·읍·면) 어간 — 예 '부산광역시 기장군' → ['기장']. 광역시(부산)는 제외 안 함."""
    import re as _r
    out = []
    for tok in (region or "").split():
        if _r.search(r"(군|구|읍|면)$", tok):
            core = _r.sub(r"(특별자치시|특별자치도|자치도|군|구|읍|면)$", "", tok)
            if len(core) >= 2:
                out.append(core)
    return out


def _seller_kw_blocked(t, kw: str) -> bool:
    """셀러·병행 글 타깃 하드 규칙 — 기초지역(구·군) 포함 키워드는 큐(글 타깃)에서 제외.
    (플레이스·순위 추적 키워드에는 유지 — 이 함수는 writing_queue 적재에만 적용.)
    광역시(부산)는 허용 — 셀러도 광역 단위 유입은 유효."""
    if (getattr(t, "biz_type", "local") or "local") not in ("seller", "hybrid"):
        return False
    kwf = (kw or "").replace(" ", "")
    return any(core in kwf for core in _basic_region_tokens(getattr(t, "region", "") or ""))


def _skip_kw(t, kw: str) -> bool:
    """큐 적재 스킵 판정 — 오염 데이터 or 셀러·병행 기초지역."""
    return _bad_kw(kw) or _seller_kw_blocked(t, kw)


MIN_QUEUE_VOLUME = 100    # 큐 적재 최소 월검색량 — 기장(월20) 류 저볼륨 판 재발 방지(이중 차단)


def _seller_longtail_candidates(t) -> list:
    """매물 컨텍스트(차종·연식·차급) → 셀러 롱테일 후보(우선순위 순).
    1순위 [차종+중고/연식], 2순위 [차급+중고], 3순위 [광역+차종/차급], 최후 [광역+업종](폴백).
    검색량 검증은 호출부에서. 컨텍스트 없으면 폴백만."""
    import re as _r
    ind0 = ((t.industry or "").replace("/", ",").split(",")[0] or "중고차").strip()
    wide = next((_r.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|도)$", "", tk)
                 for tk in (t.region or "").split()
                 if _r.search(r"(특별시|광역시|특별자치시|특별자치도|도)$", tk)), "")
    ctxs = db.recent_inventory_context(t.id, limit=6)
    p1, p2, p3 = [], [], []
    for c in ctxs:
        md, yr, cl = c.get("model", ""), c.get("year", ""), c.get("car_class", "")
        if md:
            p1 += [f"{md} 중고", f"{md} 중고차"]
            if yr:
                p1 += [f"{yr} {md} 중고", f"{yr}년식 {md}"]
            if wide:
                p3.append(f"{wide} {md} 중고")
        if cl:
            p2 += [f"{cl} 중고", f"{cl} 중고차 추천"]
            if wide:
                p3.append(f"{wide} {cl} 중고")
    fallback = [f"{wide} {ind0} 추천"] if wide else [f"{ind0} 추천"]
    # 우선순위·중복 제거
    seen, out = set(), []
    for kw in p1 + p2 + p3 + fallback:
        k = " ".join(kw.split())
        if k and k not in seen:
            seen.add(k); out.append(k)
    return out


def _reason(text: str, **meta) -> str:
    """reason 필드(내부 로그) 구조화 — 근거 카드가 파싱할 JSON. text에 기존 사람용 로그 유지."""
    import json
    return json.dumps({"text": text, **{k: v for k, v in meta.items() if v is not None}}, ensure_ascii=False)


# ── 적재(refill) — 순위추적 스냅샷 갱신 직후 / 큐 비었을 때 ─────────
def refill(t, plan: str = "free") -> dict:
    """기존 분석 산출물 → 큐 적재. 반환 {P1,P2,P3,P4} 적재 수."""
    from app.services import ranktrack
    added = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    # P1b — 저CTR 재도전(CTR 4-3, 정체보다 우선 — 순위는 있는데 유입 0이면 처방은 '제목'): 1페이지(post rank≤10) 7일 이상인데 추적링크 유입 0 → 제목 매력 부족
    try:
        from datetime import date
        clicks = db.content_click_counts(t.id, days=30)
        for pub in db.list_blog_publishes(t.id, limit=10):
            pid = pub.get("piece_id") or ""
            piece = db.get_piece(pid) if pid else None
            if not piece:
                continue
            kw = (((piece.payload or {}).get("target_keywords") or [""])[0] or "").strip()
            if not kw or _skip_kw(t, kw):
                continue
            hist = [h for h in db.rank_history(t.id, kw, kind="post") if h.get("rank")]
            if not hist or not (hist[-1]["rank"] and hist[-1]["rank"] <= 10):
                continue
            first_top = next((h for h in hist if h["rank"] and h["rank"] <= 10), None)
            try:
                days = (date.today() - date.fromisoformat((first_top.get("checked_at") or "")[:10])).days
            except Exception:
                continue
            if days < 7 or clicks.get(pid[:16], 0) > 0:
                continue
            from app.services import ranktrack as _rt2
            ang = _rt2.next_angle(_rt2._last_angle(t.id, kw))
            if db.enqueue_writing(t.id, "P1", kw, ang,
                                  _reason(f"저CTR(1페이지 {days}일·추적 유입 0) — 제목 매력 부족 재도전",
                                          lowctr=True, last=hist[-1]["rank"], days=days)):
                added["P1"] += 1
                _log.info("[autoqueue] 적재 P1(저CTR) t=%s kw=%r rank=%s days=%s", t.id, kw, hist[-1]["rank"], days)
            break                                     # 저CTR 재도전은 1건이면 충분
    except Exception:
        _log.exception("[autoqueue] P1(저CTR) 적재 실패 t=%s", t.id)
    # P1 — 정체 키워드 앵글 재도전(기존 처방 로직 출력 그대로)
    try:
        for s in ranktrack.stagnant_keywords(t.id, limit=2):
            if _skip_kw(t, s["keyword"]):
                continue
            if db.enqueue_writing(t.id, "P1", s["keyword"], s["retry_angle"],
                                  _reason(f"정체(스냅샷 {s['first']}→{s['last']}) — {s['prev_label']} 대신 {s['retry_label']} 재도전",
                                          first=s["first"], last=s["last"], days=s.get("days"),
                                          prev=s["prev_label"], retry=s["retry_label"])):
                added["P1"] += 1
                _log.info("[autoqueue] 적재 P1 t=%s kw=%r angle=%s", t.id, s["keyword"], s["retry_angle"])
    except Exception:
        _log.exception("[autoqueue] P1 적재 실패 t=%s", t.id)
    # P2 — 미노출(놓치는) 키워드 선점(기존 진단 재사용, 매장/셀러 분기)
    try:
        from app.services import diagnose
        import re as _re2
        _biz = (getattr(t, "biz_type", "local") or "local")
        if _biz in ("seller", "hybrid"):
            # 병행·셀러: 지역 진단을 쓰되 기초지역(구·군) 제거한 '광역시'만 전달 → '부산 중고차' 수준(기장 배제).
            # 상품 진단(diagnose_product_rank)은 엉뚱한 지역/일반어를 뽑아 부적합.
            _wide = " ".join(tk for tk in (t.region or "").split()
                             if not _re2.search(r"(군|구|읍|면)$", tk))
            r = diagnose.diagnose_rank(t.industry, _wide, t.name)
        else:
            r = diagnose.diagnose_rank(t.industry, t.region, t.name)
        if not r.get("estimated"):
            miss = sorted((s for s in (r.get("missing") or []) if not _skip_kw(t, s["keyword"])),
                          key=lambda s: -(s.get("volume") or 0))
            for s in miss[:REFILL_P2_MAX]:
                if db.enqueue_writing(t.id, "P2", s["keyword"], "review",
                                      _reason(f"미노출(월 {s.get('volume') or 0}회 실측) 선점",
                                              vol=s.get("volume"))):
                    added["P2"] += 1
                    _log.info("[autoqueue] 적재 P2 t=%s kw=%r vol=%s", t.id, s["keyword"], s.get("volume"))
    except Exception:
        _log.exception("[autoqueue] P2 적재 실패 t=%s", t.id)
    # P3 — 잘 되는 키워드 굳히기(+근소 격차 가점: '○○만 넘으면 N-1위' → 프롬프트 반영 재료)
    try:
        for d in ranktrack.rank_deltas(t.id, limit=4):
            if d.get("dir") not in ("up", "enter") or not d.get("last") or _skip_kw(t, d["keyword"]):
                continue
            reason = f"상승 굳히기({d.get('first') or '미노출'}→{d['last']}위, {d.get('kind')})"
            _gap = False
            if d.get("kind") in ("blog", "place") and 2 <= d["last"] <= 5:
                try:
                    from app.services import place
                    det = place.rank_detail(d["keyword"], t.name)
                    if det.get("rival"):
                        reason += f" | 근소격차: '{det['rival']}'만 넘으면 {d['last'] - 1}위"
                        _gap = True
                except Exception:
                    pass
            if db.enqueue_writing(t.id, "P3", d["keyword"], "howto",
                                  _reason(reason, first=d.get("first"), last=d.get("last"), gap=_gap or None)):
                added["P3"] += 1
                _log.info("[autoqueue] 적재 P3 t=%s kw=%r reason=%s", t.id, d["keyword"], reason)
            break                                     # 굳히기는 1건이면 충분
    except Exception:
        _log.exception("[autoqueue] P3 적재 실패 t=%s", t.id)
    # P4a — 셀러·병행: 매물 컨텍스트 롱테일 우선(차종·연식·차급) + 검색량 검증(월 100회+). 큐 비었을 때.
    try:
        if ((getattr(t, "biz_type", "local") or "local") in ("seller", "hybrid")
                and not db.writing_queue_rows(t.id, status="pending", limit=1)):
            cands = [c for c in _seller_longtail_candidates(t) if not _skip_kw(t, c)]
            from app.services import searchad as _sa
            _measured = _sa.configured()
            vols = {}
            if _measured and cands:
                for vv in _sa.keyword_volumes(cands[:8], limit=80):
                    vols[(vv.get("keyword") or "").replace(" ", "")] = vv.get("total", 0)
            _placed = 0
            for kw in cands:
                if _placed >= 3:            # 셀러 롱테일은 차기 글감 3개까지(검색량 합격분)
                    break
                v = vols.get(kw.replace(" ", ""))
                # 검색량 실측: 월 100회 미만이면 스킵(저볼륨 판 이중 차단). 폴백('광역+업종')은 무측정도 허용(큐 안 비게).
                _is_fallback = kw.endswith("추천") and any(w in kw for w in (t.industry or "중고차").split())
                if _measured and v is not None and v < MIN_QUEUE_VOLUME and not _is_fallback:
                    continue
                if db.enqueue_writing(t.id, "P4", kw, "review",
                                      _reason(f"매물 롱테일{' (월 %d회 실측)' % v if v else ''} 선점",
                                              vol=v)):
                    added["P4"] += 1
                    _placed += 1
                    _log.info("[autoqueue] 적재 P4a(롱테일) t=%s kw=%r vol=%s", t.id, kw, v)
    except Exception:
        _log.exception("[autoqueue] P4a(롱테일) 적재 실패 t=%s", t.id)
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
                    if it.get("top10") and (it.get("status") or "candidate") == "candidate" and not _skip_kw(t, it["keyword"]):
                        if db.enqueue_writing(t.id, "P4", it["keyword"], "review",
                                              _reason(f"키워드 풀 최고 승률 {it.get('win')}%(예상·내부용) 미사용분",
                                                      win=it.get("win"), vol=it.get("volume"))):
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
    from app.services.mass import kst_today
    day = kst_today()
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
            if "제목 매력" in (q.get("reason") or ""):
                note += ("\n[제목 재도전 — 저CTR] 이전 글과 완전히 다른 스타일의 제목 후보를 뽑아라"
                         "(질문형/구체 숫자형/경험 고백형 등). 본문이 답할 수 있는 약속만 제목에 담아라.")
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
            # 발행 리듬(6-1): '내일 발행분'까지 선제 준비 — 미발행 준비 글 2개(오늘+내일) 버퍼 유지.
            # 하루 1회 잡이라 tenant당 최대 1글/일 생성은 그대로(비용 가드).
            if st["ready_unpub"] >= 2 or not st["pending"]:
                continue
            if not photo_pool(t):
                continue                              # 사진 없으면 홈 need_photos 상태로만
            consume(t, None, u.get("plan") or "free")   # 하루 1회 잡 — tenant당 최대 1글
        except Exception:
            _log.exception("[autoqueue] slot_fill 실패 t=%s", tid)
