"""
🧪 미노출 자동 개선 루프(주방 전용 — UI 0개, 사장님 확정 설계 2026-07-26).

미노출 확정 글(발행 7일+, 순위 30위 밖/색인 실패)을 상위 글과 비교 진단해
'가게 단위 글쓰기 교훈'을 만들고, 이후 모든 생성(ingest)이 조용히 주입한다.
검증: 교훈 생성 이후 발행 글의 순위로 wins/fails 집계 — 효과 없으면 자동 폐기(retire).
사장님에게는 아무것도 안 보임 — 다음 글이 그냥 더 잘 될 뿐.

호출: scheduler._fresh_index_check(30분 크론)에서 sweep(). LLM 콜 상한: 스윕당 2건.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import db

_log = logging.getLogger("shopcast.lessons")

MAX_ANALYSES_PER_SWEEP = 2      # 스윕당 LLM 진단 상한(비용 가드)
MAX_ACTIVE = 5                  # 가게당 활성 교훈 상한(프롬프트 과적재 방지)
UNEXPOSED_DAYS = 7              # 발행 후 이 일수 지나도록 30위 밖이면 미노출 확정
RETIRE_FAILS = 3                # 교훈 적용 후 미노출 글 3건·성공 0건 → 폐기


def _publish_kw(pub: dict) -> str:
    """발행 글의 타깃 키워드 — blog_publishes.target_kw 우선, 피스 payload 폴백."""
    kw = (pub.get("target_kw") or "").strip()
    if kw:
        return kw
    try:
        p = db.get_piece(pub.get("piece_id") or "")
        return ((p.payload.get("target_keywords") or [""])[0] or "").strip() if p else ""
    except Exception:
        return ""


def _best_rank(tenant_id: str, kw: str) -> "int | None":
    hist = [h["rank"] for h in db.rank_history(tenant_id, kw, kind="post") if h.get("rank")]
    hist += [h["rank"] for h in db.rank_history(tenant_id, kw, kind="blog_search") if h.get("rank")]
    return min(hist) if hist else None


def _days_since(iso: str) -> int:
    try:
        return (datetime.utcnow() - datetime.fromisoformat((iso or "")[:19])).days
    except Exception:
        return 0


def _analyze_gap(tenant, pub: dict, kw: str, cause: str, existing: list[str]) -> str:
    """격차 진단(LLM 1콜) — 내 글 실측 스탯 + 상위 글 제목·요약 → 일반화된 교훈 한 문장.
    특정 매물·키워드 고정 금지(다른 소재 글에도 통하는 교훈만). 실패·무키 시 ''."""
    try:
        piece = db.get_piece(pub.get("piece_id") or "")
        title = (pub.get("post_title") or (piece.payload.get("title") if piece else "") or "").strip()
        body = (piece.payload.get("body") if piece else "") or ""
        n_imgs = len((piece.payload.get("image_paths") if piece else None) or [])
        stats = (f"글자수 {len(body)} · 사진 {n_imgs}장 · 표 {'있음' if '|' in body else '없음'} · "
                 f"FAQ {'있음' if '자주 묻는' in body else '없음'} · 제목 {len(title)}자")
        from app.services.blogrank import _search_blog
        top = _search_blog(kw, 10)
        top_lines = "\n".join(f"- {t['title']} — {t['description'][:60]}" for t in top[:8]) or "(조회 실패)"
        # 🔬 상위 글 해부 실측(제목·요약 너머 구조 지표 — 캐시만, 없으면 예열)
        try:
            from app.services import bloganatomy as _ba
            _an = _ba.cached(kw)
            if _an is None:
                _ba.ensure_async(kw)
            else:
                top_lines += (f"\n[상위 글 구조 실측] 평균 {_an['avg_chars']}자·사진 {_an['avg_imgs']}장·"
                              f"소제목 {_an['avg_heads']}개·표 {_an['table_pct']}%·동영상 {_an['video_pct']}%")
        except Exception:
            pass
        cause_txt = ("색인 자체가 안 됐다(검색에 등록 실패 — 유사문서·저품질 가능)" if cause == "not_indexed"
                     else "1페이지에 진입했다가 뚜렷하게 하락했다(사용자 체류·반응 부족 의심 — "
                          "서두 훅·끝까지 읽게 하는 장치·콘텐츠 충실도 관점으로 진단하라)" if cause == "dwell_drop"
                     else f"색인은 됐지만 {UNEXPOSED_DAYS}일 넘게 30위 밖이다")
        from app import llm
        v = llm.call(
            "너는 네이버 상위노출 분석가다. 우리 가게 블로그 글이 아래 키워드 검색에서 "
            f"{cause_txt}. 상위 글들과 비교해 원인을 추정하고, '다음 글부터 적용할 교훈' 1개를 만들어라.\n"
            f"[키워드] {kw}\n[가게 업종] {getattr(tenant, 'industry', '')}\n"
            f"[우리 글 제목] {title}\n[우리 글 실측] {stats}\n"
            f"[검색 상위 글 제목·요약]\n{top_lines}\n"
            f"[기존 교훈(중복 금지)] {' / '.join(existing) or '없음'}\n\n"
            "규칙: 이 가게의 '다른 소재 글'에도 통하는 일반 교훈만(특정 매물명·이번 키워드를 박지 마라). "
            "명령형 한 문장 20~60자. 기존 교훈과 겹치거나 확신이 없으면 '없음'만 출력. 교훈 한 줄만 출력.",
            max_tokens=100)
        lesson = " ".join((v or "").split()).strip().strip("\"'")
        if not lesson or lesson == "없음" or not (10 <= len(lesson) <= 90):
            return ""
        if any(lesson[:15] in e or e[:15] in lesson for e in existing):   # 근사 중복 방지
            return ""
        return lesson
    except Exception:
        _log.exception("[lessons] 격차 진단 실패 kw=%r", kw)
        return ""


def _sweep_tenant(tenant, budget: int) -> int:
    """미노출 확정 글 진단 → 교훈 적재. 남은 LLM 예산 반환."""
    done = db.lesson_piece_ids(tenant.id)
    n_active = len(db.active_lessons(tenant.id, limit=MAX_ACTIVE))
    for pub in db.list_blog_publishes(tenant.id, limit=20):
        pid = pub.get("piece_id") or ""
        if not pid or pid in done:
            continue
        if _days_since(pub.get("published_at") or "") < UNEXPOSED_DAYS:
            continue                                     # 아직 판정 전
        kw = _publish_kw(pub)
        if not kw:
            db.add_lesson(tenant.id, "", source_piece_id=pid, cause="no_kw", status="none")
            continue
        best = _best_rank(tenant.id, kw)
        if best is not None and best <= 30:
            # 🕐 체류 의심(2026 상위 유지 = 체류·반응, 실전 검증): 1페이지 진입 후 뚜렷한 하락 =
            #   네이버가 사용자 반응(체류·재이탈)을 보고 내리는 전형 패턴 → 교훈 대상.
            _hist = ([h["rank"] for h in db.rank_history(tenant.id, kw, kind="post") if h.get("rank")]
                     or [h["rank"] for h in db.rank_history(tenant.id, kw, kind="blog_search") if h.get("rank")])
            _cur = _hist[-1] if _hist else None
            if best <= 10 and _cur and _cur > 15 and (_cur - best) >= 5:
                cause = "dwell_drop"
            else:
                db.add_lesson(tenant.id, "", source_kw=kw, source_piece_id=pid,
                              cause="exposed", status="none")     # 노출·유지 — 교훈 불필요(마커만)
                continue
        else:
            cause = "not_indexed" if not pub.get("indexed_at") else "unexposed"
        if budget <= 0 or n_active >= MAX_ACTIVE:
            break                                        # 다음 스윕에서 계속(비용·과적재 가드)
        existing = [l["lesson"] for l in db.active_lessons(tenant.id, limit=MAX_ACTIVE)]
        lesson = _analyze_gap(tenant, pub, kw, cause, existing)
        budget -= 1
        db.add_lesson(tenant.id, lesson, source_kw=kw, source_piece_id=pid, cause=cause,
                      status=("active" if lesson else "none"))
        if lesson:
            n_active += 1
            _log.info("[lessons] 교훈 적재 t=%s kw=%r cause=%s: %s", tenant.id, kw, cause, lesson)
    return budget


def _validate_tenant(tenant) -> None:
    """교훈 생성 이후 발행 글의 실측 순위로 wins/fails 재계산 — 효과 없으면 폐기."""
    try:
        import sqlite3
        with db._conn() as c:
            db._ensure_lessons_table(c)
            rows = c.execute("SELECT * FROM tenant_lessons WHERE tenant_id=? AND status='active' "
                             "AND lesson!=''", (tenant.id,)).fetchall()
            lessons = [dict(r) for r in rows]
    except Exception:
        return
    if not lessons:
        return
    pubs = db.list_blog_publishes(tenant.id, limit=30)
    for l in lessons:
        wins = fails = 0
        for pub in pubs:
            if (pub.get("published_at") or "") <= (l.get("created_at") or ""):
                continue                                 # 교훈 이전 글은 제외
            kw = _publish_kw(pub)
            if not kw:
                continue
            best = _best_rank(tenant.id, kw)
            if best is not None and best <= 10:
                wins += 1
            elif _days_since(pub.get("published_at") or "") >= UNEXPOSED_DAYS and (best is None or best > 30):
                fails += 1
        status = "retired" if (fails >= RETIRE_FAILS and wins == 0) else "active"
        if wins != l.get("wins") or fails != l.get("fails") or status != "active":
            db.update_lesson_stats(l["id"], wins, fails, status)
            if status == "retired":
                _log.info("[lessons] 교훈 폐기(효과 없음 fails=%d) t=%s: %s", fails, tenant.id, l["lesson"])


def sweep() -> None:
    """30분 크론 진입점 — 블로그 연결 가게 전체를 진단·검증. LLM 콜은 스윕당 상한."""
    budget = MAX_ANALYSES_PER_SWEEP
    try:
        for t in db.list_tenants_with_blog():
            try:
                budget = _sweep_tenant(t, budget)
                _validate_tenant(t)
            except Exception:
                _log.exception("[lessons] 가게 스윕 실패 t=%s", getattr(t, "id", "?"))
    except Exception:
        _log.exception("[lessons] 스윕 실패")
    try:
        sweep_global()
    except Exception:
        _log.exception("[lessons] 전역 감점 교훈 스윕 실패")


# ── 🌐 전역 감점 교훈 루프(2026-08-01 사장님 승인 — '한 번에 80점' 3겹) ──────────────
# 원칙: 상수 0 — 반복 감점 '패턴'을 데이터에서 발견 → LLM이 업종·가게 무관 원칙으로 일반화 →
# 전 가게 생성에 주입 → 첫 통과율로 검증 → 효과 없으면 자동 폐기. 루마의 감점이 꽃집 글을 지킨다.

_GL_MAX_ACTIVE = 5
_GL_MIN_REPEAT = 3          # 7일 내 3회 반복돼야 '패턴'


def _ensure_global(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS global_lessons("
              "id TEXT PRIMARY KEY, pattern TEXT, lesson TEXT, created_at TEXT,"
              "wins INTEGER DEFAULT 0, fails INTEGER DEFAULT 0, status TEXT DEFAULT 'active')")


def _warn_pattern(w: str) -> str:
    """감점 문구 → 일반화된 패턴 키(숫자·꼬리 제거) — '텍스트 7문단 연속…' → '텍스트 N문단 연속'."""
    import re as _re
    head = (w or "").split("→")[0].strip()
    return _re.sub(r"\d+", "N", head)[:60]


def _recent_blog_payloads(days: int = 7, limit: int = 120) -> list:
    from datetime import datetime, timedelta
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    out = []
    try:
        for s in db.list_sets(limit=limit):
            try:
                blog = next((p for p in db.get_set_pieces(s["asset_id"]) if p.kind.value == "blog"), None)
                if blog and (getattr(blog, "created_at", "") or s.get("created", "")) >= since[:10]:
                    out.append(blog.payload or {})
            except Exception:
                continue
    except Exception:
        pass
    return out


def sweep_global() -> None:
    """반복 감점 패턴 → 전역 교훈 적재(스윕당 LLM 1콜 상한) + 첫통과율 검증·자동 폐기."""
    from collections import Counter
    from datetime import datetime
    import uuid
    with db._conn() as c:
        _ensure_global(c)
        rows = [dict(r) for r in c.execute("SELECT * FROM global_lessons").fetchall()]
    active = [r for r in rows if r["status"] == "active"]
    pls = _recent_blog_payloads()
    # ① 패턴 집계(감점 문구는 채점기가 만든 것 — 특정 가게 텍스트 아님)
    cnt: Counter = Counter()
    sample: dict = {}
    for pl in pls:
        for w in ((pl.get("ranking_audit") or {}).get("warnings") or []):
            k = _warn_pattern(w)
            if k:
                cnt[k] += 1
                sample.setdefault(k, w)
    known = {r["pattern"] for r in rows}
    # ② 신규 패턴 1개만 교훈화(스윕당 LLM 1콜 상한 — 비용 통제)
    if len(active) < _GL_MAX_ACTIVE:
        for k, n in cnt.most_common():
            if n < _GL_MIN_REPEAT or k in known:
                continue
            try:
                from app import llm as _llm
                lesson = (_llm.call(
                    "블로그 자동 생성에서 아래 감점이 여러 가게에 반복된다. 다음 글부터 이 감점을 예방할 "
                    "'생성 지시' 한 문장을 써라. 규칙: 특정 업종·가게·키워드 언급 금지(어느 가게에나 "
                    "적용되는 글쓰기 원칙만), 60자 이내, 명령형.\n"
                    f"[반복 감점] {sample.get(k, k)} (7일간 {n}회)",
                    max_tokens=100) or "").strip().strip('"')
                if 8 <= len(lesson) <= 90:
                    with db._conn() as c:
                        _ensure_global(c)
                        c.execute("INSERT OR REPLACE INTO global_lessons(id, pattern, lesson, created_at) "
                                  "VALUES(?,?,?,?)",
                                  (uuid.uuid4().hex[:12], k, lesson, datetime.utcnow().isoformat()))
                    _log.info("[전역교훈] 적재: %r ← %r(%d회)", lesson, k, n)
            except Exception:
                _log.exception("[전역교훈] 일반화 실패 %r", k)
            break                                       # 스윕당 1개만
    # ③ 검증: 교훈 이후 생성된 글의 '첫 통과'(80+ & 보정 0라운드) 실적으로 폐기 판단
    for r in active:
        wins = fails = 0
        for pl in pls:
            if (pl.get("created_at") or "9999") < r["created_at"]:
                continue
            sc = (pl.get("ranking_audit") or {}).get("score")
            rounds = (pl.get("score_gate") or {}).get("rounds", 0)
            if isinstance(sc, int):
                if sc >= 80 and not rounds:
                    wins += 1
                elif sc < 80:
                    fails += 1
        with db._conn() as c:
            if fails >= 4 and wins == 0:                # 효과 없음 — 자동 폐기(지식 청소)
                c.execute("UPDATE global_lessons SET status='retired', wins=?, fails=? WHERE id=?",
                          (wins, fails, r["id"]))
                _log.info("[전역교훈] 폐기: %r (win %d/fail %d)", r["lesson"], wins, fails)
            else:
                c.execute("UPDATE global_lessons SET wins=?, fails=? WHERE id=?", (wins, fails, r["id"]))


def global_note_block() -> str:
    """전 가게 공통 주입 블록 — note_block이 합쳐서 반환."""
    try:
        with db._conn() as c:
            _ensure_global(c)
            rows = c.execute("SELECT lesson FROM global_lessons WHERE status='active' "
                             "ORDER BY created_at DESC LIMIT 3").fetchall()
        if not rows:
            return ""
        return ("\n[글쓰기 공통 교훈 — 전 가게 감점 실측에서 배운 원칙] "
                + " / ".join(r["lesson"] for r in rows))
    except Exception:
        return ""


def note_block(tenant_id: str) -> str:
    """생성 주입용 교훈 블록 — ingest가 모든 생성에서 호출(조용한 반영, UI 없음)."""
    try:
        ls = db.active_lessons(tenant_id, limit=3)
        tb = ("" if not ls else
              ("\n[글쓰기 교훈 — 이 가게 글의 실측 분석] 아래 교훈을 이번 글에 자연스럽게 반영하라"
               "(교훈 문장 자체를 글에 옮기지 마라): "
               + " / ".join(l["lesson"] for l in ls)))
        return tb + global_note_block()                 # 🌐 전역 감점 교훈(2026-08-01)도 함께
    except Exception:
        return ""
