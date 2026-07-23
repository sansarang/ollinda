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
    """셀러·병행 글 타깃 하드 규칙 — seo 단일 관문에 위임(같은 규칙 두 곳 사는 구조 제거)."""
    from app import seo as _seo
    return _seo.is_basic_region_kw(kw, getattr(t, "region", "") or "", getattr(t, "biz_type", "local") or "local")


def _skip_kw(t, kw: str) -> bool:
    """큐 적재 스킵 판정 — 오염 데이터 or 셀러·병행 기초지역."""
    return _bad_kw(kw) or _seller_kw_blocked(t, kw)


MIN_QUEUE_VOLUME = 100    # 큐 적재 최소 월검색량 — 기장(월20) 류 저볼륨 판 재발 방지(이중 차단)


def _seller_longtail_candidates(t) -> list:
    """셀러·병행 롱테일 후보 — 업종 스키마 search_grammar × (매물 컨텍스트 속성 | 스키마 속성 토큰).
    업종 무관 동적: 중고차=차종+중고, 캔들=향+캔들 등 스키마가 문법·속성을 공급(차량 하드코딩 제거).
    검색량 검증은 호출부(관문)에서. 광역+업종 폴백 항상 포함."""
    import re as _r
    from app.services import indschema as _isc
    ind0 = ((t.industry or "").replace("/", ",").split(",")[0] or "").strip()
    biz = (getattr(t, "biz_type", "local") or "local")
    wide = next((_r.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|도)$", "", tk)
                 for tk in (t.region or "").split()
                 if _r.search(r"(특별시|광역시|특별자치시|특별자치도|도)$", tk)), "")
    sch = _isc.get_schema(t.industry, biz)
    grammars = sch.get("search_grammar") or ["{속성} 추천", "{지역} {업종}"]
    # 속성 값: 매물 컨텍스트(실입력) 우선, 없으면 스키마 예시 토큰
    attrs, years = [], []
    for c in db.recent_inventory_context(t.id, limit=6):
        for v in (c.get("model"), c.get("car_class")):
            if v:
                attrs.append(v)
        if c.get("year"):
            years.append(c["year"])
    if not attrs:
        attrs = _isc.attribute_tokens(sch)[:6]
    out = []

    def _emit(g, subs):
        kw = g
        for ph, val in subs.items():
            kw = kw.replace("{" + ph + "}", val)
        kw = " ".join(_r.sub(r"\{[^}]*\}", "", kw).split())   # 미치환 플레이스홀더 제거
        if kw and len(kw) >= 3:
            out.append(kw)
    # 서열: 속성(+연식) 조합을 먼저(롱테일), 그다음 지역+속성, 마지막 광역+업종
    for a in attrs:
        for g in grammars:
            _emit(g, {"속성": a, "차종": a, "지역": wide, "업종": ind0, "의도": "추천", "연식": (years[0] if years else "")})
    _emit("{지역} {업종} 추천", {"지역": wide, "업종": ind0})
    _emit("{업종} 추천", {"업종": ind0})
    seen, uniq = set(), []
    for kw in out:
        k = " ".join(kw.split())
        if k and k not in seen:
            seen.add(k); uniq.append(k)
    return uniq


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
    # P4b — 스마트블록 세부주제(연관 키워드 근사, 월 100회+): 의도 유형으로 앵글 자동 정렬(2-3)
    try:
        if not db.writing_queue_rows(t.id, status="pending", limit=1):
            from app.services import smartblock as _sb
            ind0 = ((t.industry or "").replace("/", ",").split(",")[0] or "").strip()
            _wide = " ".join(tk for tk in (t.region or "").split()
                             if not __import__("re").search(r"(군|구|읍|면)$", tk))
            seeds = [x for x in (ind0, f"{_wide} {ind0}".strip()) if x]
            for st in _sb.subtopics(seeds, min_volume=MIN_QUEUE_VOLUME, limit=8):
                if _skip_kw(t, st["keyword"]):
                    continue
                if db.enqueue_writing(t.id, "P4", st["keyword"], _sb.angle_for(st["keyword"]),
                                      _reason(f"스마트블록 세부주제({st['intent']}형·월 {st['volume']}회 실측) 선점",
                                              vol=st["volume"])):
                    added["P4"] += 1
                    _log.info("[autoqueue] 적재 P4b(블록:%s) t=%s kw=%r vol=%s",
                              st["intent"], t.id, st["keyword"], st["volume"])
                    break                              # 블록 세부주제는 1건이면 충분(큐 다양성)
    except Exception:
        _log.exception("[autoqueue] P4b(스마트블록) 적재 실패 t=%s", t.id)
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
    # 트랙 B — 정보성 글(GEO/AI 브리핑 인용): 주간 상한 내에서 스키마 유래 질문형 주제 적재.
    #   source_type=R1(P4보다 뒤) → 매물·시공 글(P1~P4)이 항상 먼저 소비됨(트랙 A 우선순위 불변).
    try:
        from app.services import geo_track as _geo, indschema as _isc
        # ★ 실경험 게이트: owner_experience(사장 실제 Q&A) 없으면 트랙 B 미적재(저품질·일반론 글 원천 차단).
        #    안내는 대시보드가 담당(에러 아님). 트랙 A는 무관.
        if not db.has_owner_experience(t.id):
            _log.info("[autoqueue] 트랙B 보류 t=%s — 실경험(owner_experience) 미등록", t.id)
        elif db.info_track_count_since(t.id, days=7) < _geo.WEEKLY_INFO_CAP:
            _bizB = getattr(t, "biz_type", "local") or "local"
            _schB = _isc.get_schema(t.industry, _bizB)
            _expsB = db.list_owner_experience(t.id)          # 1순위 주제 = 사장 실경험 질문
            for tp in _geo.info_topics(t.industry, _bizB, _schB, region=t.region or "",
                                       desc=(getattr(t, "topic_axis", "") or ""), experiences=_expsB):
                kwB = _geo.select_info_keyword([tp["topic"]], t.region or "", t.industry, tenant_id=t.id)
                if not kwB or _bad_kw(kwB):        # 정보형은 비지역 → 기초지역 배제만(select_info_keyword 내부) 적용
                    continue
                if db.enqueue_writing(t.id, _geo.INFO_SOURCE, kwB, tp["angle"],
                                      _reason("트랙 B 정보성(AI 브리핑 인용 최적화) — 스키마 유래 질문형 주제"),
                                      content_type="info"):
                    added["B"] = added.get("B", 0) + 1
                    _log.info("[autoqueue] 적재 B(트랙B/info) t=%s kw=%r angle=%s", t.id, kwB, tp["angle"])
                    break                             # 리필 1회당 1건(누적은 주간 상한이 제어)
    except Exception:
        _log.exception("[autoqueue] 트랙 B(info) 적재 실패 t=%s", t.id)
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
            _ctype = (q.get("content_type") or "sell")   # sell=트랙A / info=트랙B(GEO)
            asset = db.create_asset(t.id, AssetType.IMAGE, paths[0], note)
            asset.target_kw = kw
            asset.angle = q["angle"] if q["angle"] in ("review", "howto", "price") else "review"
            asset.content_type = _ctype
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
            # ★ 게이트 체인(item 7) — 재생성이 일어나면 해당 게이트만이 아니라 그 트랙 '전체 체인'을
            #   처음부터 재통과해야 한다(길이 고치다 구조 깨진 글·구조 고치다 길이 빠진 글 차단). 상한 2회.
            #   트랙 A: 정직·사실 → 자수 → 모바일 규격 / 트랙 B: +G1~G6(GEO 구조+경험). 태그·오염은 조립 게이트.
            from app.services import geo_track as _geo
            _biz = getattr(t, "biz_type", "local") or "local"
            _cf, _chain_ok = [], False
            for _att in range(3):                       # 초기 1 + 재생성 2
                _cf = []
                gate = mass.industry_gate(prof, p.payload, _biz)          # 1) 정직·사실·품질
                if not gate["passed"]:
                    _cf.append(("honesty", autofix_instruction(p.payload.get("ranking_audit") or {}, "blog")
                                or "경험 문장과 구체 수치를 보강(날조 금지)"))
                ggate = None
                if _ctype == "info":                                     # 2) 트랙B GEO 구조+경험
                    ggate = _geo.geo_gate(p.payload)
                    p.payload["geo_gate"] = ggate
                    if not ggate["passed"]:
                        _cf.append(("geo", _geo.regen_instruction(ggate["fails"])))
                mg = seo.mobile_spec_gate(p.payload.get("body") or "", _ctype)   # 3) 자수 + 4) 모바일 규격
                p.payload["spec_gate"] = mg
                if not mg["passed"]:
                    if mg["below"]:
                        _cf.append(("spec_below", "글이 짧다. 글자수 채우려 같은 말 반복·일반론 부연·수식어 부풀리기 절대 금지. "
                                                  "매물 실값·검수 디테일·사장 경험 같은 '실제 정보'로만 보강. 채울 실정보 없으면 늘리지 마라."))
                    elif mg["above"]:
                        _cf.append(("spec_above", "글이 너무 길다. 핵심 유지하며 중복·군더더기를 압축(정보 손실 없이)."))
                    else:
                        _cf.append(("spec_mobile", "모바일 규격 위반: 긴 문단은 3~4줄(90~130자)로 쪼개고, 표는 2열 이하로. 내용 유지."))
                if not _cf:
                    _chain_ok = True
                    break
                _log.info("[autoqueue] 게이트체인 %d차 미달 t=%s kw=%r fails=%s", _att + 1, t.id, kw, [c[0] for c in _cf])
                if _att >= 2:                            # 재생성 상한(2회) 도달 — 전 체인 미통과
                    break
                try:
                    revise_piece(p, _cf[0][1])           # 첫 실패 사유로 재생성 → 다음 루프에서 전 체인 재검사
                except Exception:
                    _log.exception("[autoqueue] 체인 재생성 실패 t=%s", t.id)
            if not _chain_ok:                            # 상한 후에도 미통과 → 보류 + 사유 안내(발행 안 함)
                _reason = ",".join(c[0] for c in _cf)
                db.mark_writing(q["id"], "skipped", reason_append=f"게이트 체인 미통과: {_reason}")
                _log.warning("[autoqueue] 게이트체인 최종 미통과 → 보류(미발행) t=%s kw=%r %s", t.id, kw, _reason)
                _notice = ("글이 짧아요 — 사진이나 매물 정보를 더 올려주시면 정보가 풍부한 글로 만들어드려요"
                           if any(c[0] == "spec_below" for c in _cf) else "품질 기준 미달로 보류 — 사진·정보를 보강해 주세요")
                return {"ok": False, "chain_failed": True, "keyword": kw,
                        "fails": [c[0] for c in _cf], "notice": _notice}
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
