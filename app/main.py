"""
shopcast 웹 MVP — 서버렌더(FastAPI).
흐름: 사장님 업로드(/u/{token}) → AI 캡션 생성 → 운영자 검수(/admin) → 인스타 발행(토큰 없으면 시뮬).
실행: uvicorn app.main:app --reload
"""
from __future__ import annotations

import os

import base64
import json
import logging
import secrets
import time
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from app import auth, storage
from app.kakao import make_router as kakao_router
from app.google_auth import make_router as google_router

from app import db, oauth, seo
from app.domain.models import Channel, ContentStatus
from app.industries import ACTIVE_INDUSTRIES, PROFILES
from app.registry import get_publisher
from app.services.ingest import ingest_upload
from app.services.publish import publish_and_record
from app.services.revise import autofix_instruction, revise_piece
from app.web.render import badge, esc, nav, page, shell, stat_card

# 상태 한글 라벨
STATUS_KO = {"draft": "검수대기", "approved": "승인됨", "rejected": "반려",
             "scheduled": "예약됨", "published": "발행완료", "failed": "실패"}
CHMAP = {"instagram": "인스타", "naver_blog": "네이버", "youtube": "유튜브", "x": "X"}
FREE_LIMIT = 2   # 가입자 무료 생성 횟수
# 오너(사장) 영구 무제한 라이선스 — 이 이메일들은 모든 한도 면제. env로 추가 가능.
OWNER_EMAILS = {e.strip().lower() for e in os.environ.get(
    "SHOPCAST_OWNER_EMAILS", "etetetetet5ea@kakao.com,etetet3ea1101@gmail.com").split(",") if e.strip()}


def _is_owner(user: dict | None) -> bool:
    return bool(user and (user.get("email") or "").lower() in OWNER_EMAILS)

# 구글 로고(4색 G) — 간편가입 버튼용
GOOGLE_SVG = ('<svg width="20" height="20" viewBox="0 0 48 48" class="inline-block align-middle">'
              '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 '
              '14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
              '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 '
              '5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
              '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 '
              '16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
              '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 '
              '2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>')


def _google_btn(label: str = "구글로 가입하기") -> str:
    return (f"<a href='/login/google' class='flex items-center justify-center gap-2 w-full py-3 rounded-xl "
            f"font-bold border border-slate-200 bg-white text-slate-700 mb-3 hover:bg-slate-50 shadow-sm'>"
            f"{GOOGLE_SVG} {label}</a>")


def _quota_block(owner: dict | None):
    """플랜별 생성 한도 초과 시 안내 HTML 반환, 통과면 None. owner 없음(대행 tenant)=무제한."""
    if not owner:
        return None
    if _is_owner(owner):                 # 사장님 영구 라이선스 = 무제한
        return None
    plan = owner.get("plan") or "free"
    if plan == "agency":
        return None
    up = ("<div class='bg-white rounded-2xl shadow-sm p-7 text-center max-w-md mx-auto'>"
          "<div class='text-4xl mb-2'>🎁</div>{t}"
          "<p class='text-slate-500 text-sm mb-4'>{m}</p>"
          "<a href='/#pricing' class='inline-block bg-indigo-600 text-white font-bold px-6 py-3 rounded-xl'>"
          "요금제 보기 (베이직 39,000 · 프로 79,000)</a></div>")
    if plan == "free":
        if (owner.get("free_used") or 0) >= FREE_LIMIT:
            return up.format(t=f"<h1 class='text-xl font-bold mb-1'>무료 {FREE_LIMIT}회를 모두 사용했어요</h1>",
                             m="프로는 무제한, 베이직도 매달 넉넉히 만들 수 있어요.")
        return None
    # 유료 플랜(self): 구독 활성 + 월 한도
    from app.services import pay
    from datetime import datetime
    sub = db.get_subscription(owner["id"])
    active = bool(sub and sub.get("status") == "active" and (sub.get("expires_at") or "") > datetime.utcnow().isoformat())
    if not active:
        return up.format(t="<h1 class='text-xl font-bold mb-1'>구독이 만료됐어요</h1>",
                         m="다시 결제하면 계속 이용할 수 있어요.")
    cap = pay.PLANS.get(plan, {}).get("monthly", 0)
    if cap and db.month_usage(owner["id"]) >= cap:
        return up.format(t=f"<h1 class='text-xl font-bold mb-1'>이번 달 한도({cap}건) 도달</h1>",
                         m="다음 달에 리셋됩니다. 더 필요하면 문의해 주세요.")
    return None


def _record_usage(owner: dict | None) -> None:
    if not owner:
        return
    plan = owner.get("plan") or "free"
    if plan == "free":
        db.incr_user_free(owner["id"])
    elif plan != "agency":
        db.incr_month_usage(owner["id"])


def _refund_usage(owner: dict | None) -> None:
    """생성 실패 시 선예약(_record_usage)한 사용량 원복(B7). db 함수가 0 미만으로 내려가지 않게 클램프."""
    if not owner:
        return
    plan = owner.get("plan") or "free"
    if plan == "free":
        db.incr_user_free(owner["id"], -1)
    elif plan != "agency":
        db.incr_month_usage(owner["id"], -1)


# ── 업로드 검증(B9) ──────────────────────────────────────
MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 사진 1장 최대 25MB
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp"}
_ALLOWED_IMG_CT = {"image/jpeg", "image/png", "image/webp", "image/heic",
                   "image/heif", "image/gif", "image/bmp"}


async def _read_image_uploads(photos, limit: int = 30) -> list[tuple[bytes, str]]:   # 안전 상한 30(사진 제한 해제 — 서버 부하 방지용, UI 비노출)
    """업로드 사진을 형식·크기 검증하며 읽는다. 이미지 아님/빈파일/초대형은 제외(B9)."""
    out: list[tuple[bytes, str]] = []
    for ph in (photos if isinstance(photos, list) else [photos] if photos else []):
        fn = getattr(ph, "filename", "") or ""
        if not fn:
            continue
        ext = os.path.splitext(fn)[1].lower()
        ct = (getattr(ph, "content_type", "") or "").lower()
        if ext not in _ALLOWED_IMG_EXT and ct not in _ALLOWED_IMG_CT:
            continue
        data = await ph.read()
        if not data or len(data) > MAX_UPLOAD_BYTES:
            continue
        out.append((data, fn))
        if len(out) >= limit:
            break
    return out

# OAuth 연결 지원 채널(자동 발행 가능한 것만)
CONNECTABLE = [Channel.INSTAGRAM, Channel.YOUTUBE, Channel.X]
CHANNEL_LABEL = {Channel.INSTAGRAM: "📷 인스타그램", Channel.YOUTUBE: "▶️ 유튜브", Channel.X: "𝕏 (트위터)"}

app = FastAPI(title="shopcast", version="0.3.0")


@app.middleware("http")
async def admin_basic_auth(request, call_next):
    """/admin* 운영자 보호(HTTP Basic). SHOPCAST_ADMIN_PASS 미설정 시 fail-closed로 /admin/* 전면 차단
    (/admin/cleanup·/admin/testaccount 등 파괴적·민감 라우트 무인증 노출 방지).
    사장님 업로드(/u/*)·OAuth 콜백·미디어는 공개 유지."""
    if request.url.path.startswith("/admin"):
        pw = os.environ.get("SHOPCAST_ADMIN_PASS")
        if not pw:
            # 운영자 비밀번호 미구성 = 관리자 영역 접근 차단(fail-closed).
            return Response("운영자 인증이 구성되지 않아 관리자 영역을 사용할 수 없습니다(SHOPCAST_ADMIN_PASS 미설정).",
                            status_code=503)
        user = os.environ.get("SHOPCAST_ADMIN_USER", "admin")
        ok = False
        auth = request.headers.get("authorization", "")
        if auth.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(auth[6:]).decode().partition(":")
                ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, pw)
            except Exception:
                ok = False
        if not ok:
            return Response("운영자 인증 필요", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="shopcast admin"'})
    resp = await call_next(request)
    # 보안 헤더(신뢰·SEO) — 모든 응답에 적용
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


@app.on_event("startup")
def _startup() -> None:
    # ⚠ 디스크 풀(SQLite 'disk I/O error')이어도 앱은 반드시 부팅해야 함 —
    #   /health·/admin/disk-sos가 응답해야 긴급 정리로 복구 가능(startup 크래시 시 컨테이너 exit → 사이트 전면 다운).
    try:
        db.init_db()
        if not db.list_tenants():       # 시작 업종 6종 데모 가게 시드
            for key in ACTIVE_INDUSTRIES:
                p = PROFILES[key]
                db.create_tenant(name=f"데모 {p.name}", industry=p.name, region="수원")
    except Exception:
        import logging
        logging.exception("[startup] DB 초기화 실패(디스크 풀 등) — 앱은 기동, /admin/disk-sos로 정리 필요")
    try:  # 💾 죽지 않는 잡 이어하기 — 재시작으로 죽은 생성(running 잡)을 스풀 입력으로 재개
        def _resume_jobs():
            import glob as _gl
            import logging as _lgj
            import time as _tj
            _tj.sleep(12)                        # 부팅 안정화 후(스케줄러·DB 준비)
            for j in db.pending_gen_jobs():
                try:
                    t = db.get_tenant(j["tenant_id"])
                    fps = sorted(_gl.glob(os.path.join(j["spool_dir"], "*")))
                    if not (t and fps):
                        db.finish_gen_job(j["id"], "failed")
                        continue
                    # 중복 창 방어(2026-07-29): 잡 기록 이후 블로그가 이미 만들어졌다면
                    # (피스 저장~완료 마킹 사이 크래시) 재실행 생략 — done 처리
                    _dup = False
                    try:
                        for _s3 in db.list_sets(tenant_id=t.id, limit=3):
                            _ps3 = db.get_set_pieces(_s3["asset_id"])
                            if any(p.kind.value == "blog" and str(getattr(p, "created_at", ""))[:19]
                                   >= (j.get("created_at") or "")[:19] for p in _ps3):
                                db.finish_gen_job(j["id"], "done", asset_id=_s3["asset_id"])
                                _dup = True
                                break
                    except Exception:
                        pass
                    if _dup:
                        continue
                    import json as _jj
                    meta = _jj.loads(j.get("meta") or "{}")
                    files2 = []
                    for fp in fps:
                        with open(fp, "rb") as f:
                            files2.append((f.read(), os.path.basename(fp)[3:] or "photo.jpg"))
                    _lgj.getLogger("shopcast.jobs").warning(
                        "[gen-job] 🔄 재시작 복구: job=%s tenant=%s 사진 %d장", j["id"][:8], t.name, len(files2))
                    db.set_gen_progress(t.id, "start", "이어서 만드는 중", "재시작 복구", 0.05, new=True)
                    from app.services.ingest import ingest_upload as _iu
                    made = _iu(t, files2, meta.get("note") or "", target_kw=meta.get("target_kw") or "",
                               angle=meta.get("angle") or "",
                               intake={}, pre_cleaned_idx=set(meta.get("pre_idx") or []) or None)
                    db.finish_gen_job(j["id"], "done" if made else "failed",
                                      asset_id=(made[0].asset_id if made else ""))
                    import shutil as _shj2
                    _shj2.rmtree(j["spool_dir"], ignore_errors=True)
                except Exception:
                    _lgj.getLogger("shopcast.jobs").exception("[gen-job] 복구 실패 job=%s", j.get("id"))
                    db.finish_gen_job(j.get("id") or "", "failed")
        import threading as _thj
        _thj.Thread(target=_resume_jobs, daemon=True).start()
    except Exception:
        import logging
        logging.exception("[startup] 잡 복구 스레드 시작 실패")
    try:                                # 경쟁사 일일 자동 스캔(apscheduler 미설치 시 graceful)
        from app import scheduler
        scheduler.start()
    except Exception:
        import logging
        logging.exception("[startup] 스케줄러 기동 실패 — 자동 스캔 없이 계속")


@app.get("/health")
def health() -> dict:
    # 배포 커밋 SHA 노출 — "내 수정이 반영됐나" 검증용(Railway가 주입하는 env). 미설정 시 unknown.
    _sha = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("SOURCE_COMMIT")
            or os.environ.get("GIT_COMMIT") or "unknown")[:12]
    return {"ok": True, "service": "shopcast", "version": app.version, "commit": _sha}


@app.get("/internal/published-posts")
def internal_published_posts(request: Request):
    """readview_v1 제공 — gowatch(별도 서비스)가 읽는 유일한 본체 뷰(발행 글 전수, 읽기 전용).
    GOWATCH_TOKEN 베어러 인증(미설정 시 fail-closed). 본체는 이 뷰만 노출, gowatch는 SELECT만."""
    tok = os.environ.get("GOWATCH_TOKEN")
    auth_h = request.headers.get("authorization", "")
    if not tok or auth_h != f"Bearer {tok}":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    from app import db as _db
    rows = _db.published_posts_view()
    dbg = {}
    if request.query_params.get("debug"):
        try:
            with _db._conn() as _c:
                dbg["blog_publishes_total"] = _c.execute("SELECT count(*) FROM blog_publishes").fetchone()[0]
                dbg["with_url"] = _c.execute("SELECT count(*) FROM blog_publishes WHERE published_url IS NOT NULL AND published_url<>''").fetchone()[0]
                dbg["sample"] = [dict(r) for r in _c.execute("SELECT piece_id,tenant_id,published_url,target_kw FROM blog_publishes LIMIT 5").fetchall()]
        except Exception as _e:
            dbg["err"] = repr(_e)[:200]
    return JSONResponse({"ok": True, "contract": "readview_v1", "count": len(rows), "posts": rows, "debug": dbg})


@app.get("/admin/gowatch/preview/{tenant_id}", response_class=HTMLResponse)
def admin_gowatch_preview(request: Request, tenant_id: str):
    """W3/D1~D3 실물 캡처용 — 로그인 없이 특정 tenant의 D2 배너·D1 카드·D3 관측표 렌더(운영자 전용)."""
    from app.services import dashboard_gowatch as _dg
    d2 = _dg.render_d2(tenant_id) or "<div class='text-xs text-slate-400 mb-4'>(D2 배너: 이상 없음 — 정상이면 배너 0)</div>"
    d1 = _dg.render_d1(tenant_id) or "<div class='text-xs text-slate-400 mb-4'>(D1 카드: 제안 없음)</div>"
    d3 = _dg.render_d3(tenant_id)
    page = ("<div class='max-w-2xl mx-auto px-4 py-6' style='font-family:system-ui'>"
            f"<div class='text-xs text-slate-400 mb-2'>gowatch 대시보드 프리뷰 · tenant={esc(tenant_id)}</div>"
            "<div class='text-sm font-bold text-slate-500 mb-1'>D2 상태 배너</div>" + d2 +
            "<div class='text-sm font-bold text-slate-500 mb-1'>D1 개선 제안 카드</div>" + d1 +
            "<div class='text-sm font-bold text-slate-500 mb-1 mt-4'>D3 관측 현황</div>"
            f"<div class='bg-white rounded-2xl border border-slate-100 p-4'>{d3}</div></div>")
    return HTMLResponse(page)


@app.post("/admin/restore-photo/{tenant_id}")
async def admin_restore_photo(tenant_id: str, basename: str = Form(...), photo: UploadFile = File(...)):
    """운영 복구(재보정 사고) — 백업본 바이트를 원래 basename으로 되돌리고 R2 재미러."""
    import re as _rn
    if not _rn.fullmatch(r"[A-Za-z0-9]+\.(jpg|jpeg|png|webp)", basename or ""):
        return JSONResponse({"ok": False, "error": "basename 형식"}, status_code=400)
    data = await photo.read()
    if not data or len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"ok": False, "error": "빈 파일/초과"}, status_code=400)
    from app import storage as _st
    d = os.path.join(_st.STORAGE_DIR, tenant_id)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, basename)
    with open(p, "wb") as f:
        f.write(data)
    key = _st.mirror_to_r2(p)
    return JSONResponse({"ok": True, "path": p, "r2": bool(key), "bytes": len(data)})


@app.get("/admin/resweep/{tenant_id}")
def admin_resweep(tenant_id: str, days: int = 2):
    """[봉인 2026-07-26] 재보정이 서류 사진을 파괴한 실측 사고 — 안전한 재설계 전까지 비활성."""
    return JSONResponse({"ok": False, "error": "resweep 봉인(서류 파괴 사고) — 재설계 필요"}, status_code=503)


def _admin_resweep_disabled(tenant_id: str, days: int = 2):
    import threading

    def _run():
        import logging
        from datetime import datetime, timedelta
        from app.services.ingest import _restore_media
        from app.media import photo_boost
        from app import storage as _st
        log = logging.getLogger("shopcast.resweep")
        n_sets = n_imgs = 0
        try:
            for s in db.list_sets(tenant_id=tenant_id, limit=50):
                pieces = db.get_set_pieces(s["asset_id"])
                blog = next((p for p in pieces if p.kind.value == "blog"), None)
                if not blog:
                    continue
                paths = _restore_media(tenant_id, blog.payload.get("image_paths") or [])
                if not paths:
                    continue
                n_sets += 1
                for p in paths:
                    try:
                        photo_boost.remove_overlay(p)
                        _st.mirror_to_r2(p)
                        n_imgs += 1
                    except Exception:
                        log.exception("[resweep] 실패 %s", p)
            log.warning("[resweep] 완료 t=%s sets=%d imgs=%d", tenant_id, n_sets, n_imgs)
        except Exception:
            log.exception("[resweep] 중단 t=%s", tenant_id)
    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"ok": True, "started": True})


@app.get("/admin/dedupe-blogs/{asset_id}")
def admin_dedupe_blogs(asset_id: str):
    """운영 수리 — 워치독 레이스로 생긴 중복 블로그 피스 제거(channel_status·사진 수·최신 우선 보존)."""
    from app.domain.models import ContentKind as _CKd
    pieces = db.get_set_pieces(asset_id)
    blogs = [p for p in pieces if p.kind == _CKd.BLOG]
    if len(blogs) < 2:
        return JSONResponse({"ok": True, "blogs": len(blogs), "removed": 0})
    blogs.sort(key=lambda b: (bool(b.payload.get("channel_status")),
                              len(b.payload.get("image_paths") or []),
                              str(getattr(b, "created_at", ""))), reverse=True)
    removed = []
    for dup in blogs[1:]:
        db.delete_piece(dup.id, dup.tenant_id)
        removed.append(dup.id[:8])
    keep = blogs[0]
    return JSONResponse({"ok": True, "kept": keep.id[:8],
                         "kept_imgs": len(keep.payload.get("image_paths") or []),
                         "removed": removed})


@app.get("/admin/lessons")
def admin_lessons(tid: str = "", run: str = ""):
    """운영 진단 — 가게 교훈 목록 + run=1이면 스윕 즉시 실행(크론 대기 없이 검증용)."""
    from app.services import lessons as _les
    if run == "1":
        _les.sweep()
    out = []
    try:
        import sqlite3 as _sq
        with db._conn() as c:
            db._ensure_lessons_table(c)
            q = ("SELECT * FROM tenant_lessons" + (" WHERE tenant_id=?" if tid else "")
                 + " ORDER BY created_at DESC LIMIT 30")
            rows = c.execute(q, ((tid,) if tid else ())).fetchall()
            out = [dict(r) for r in rows]
    except Exception:
        pass
    return JSONResponse({"ok": True, "n": len(out), "lessons": out})


@app.get("/admin/kw-intent")
def admin_kw_intent(kw: str = "", industry: str = "", biz: str = "seller", note: str = ""):
    """운영 진단 — 키워드-소재 의도 정합 게이트(seo.keyword_intent_ok) 단건 판정 확인용."""
    from app import seo as _seo
    return JSONResponse({"ok": True, "kw": kw,
                         "intent_ok": _seo.keyword_intent_ok(kw, industry, biz, "sell", note)})


@app.get("/admin/searcher-term")
def admin_searcher_term(industry: str = "", detail: str = ""):
    """운영 진단 — 업종명 → 손님이 실제로 검색하는 말(seo.searcher_term) 판정 확인.
    detail=1이면 후보별 검색량·문서수·기회지수까지(축약이 옳은지 눈으로 검증)."""
    from app import seo as _seo
    out = {"ok": True, "industry": industry, "term": _seo.searcher_term(industry)}
    if detail == "1":
        base = " ".join((industry or "").split())
        cands = [base] + [base[: -len(t)].strip() for t in _seo._SUPPLIER_TAIL
                          if base.endswith(t) and len(base) - len(t) >= 2]
        cands = list(dict.fromkeys([c for c in cands if len(c) >= 2]))
        from app.services import blogrank as _br
        from app.services import searchad as _sa
        vols = _sa.volume_map(cands) if _sa.configured() else {}
        rows = []
        for c in cands:
            v = int(vols.get(c.replace(" ", "")) or 0)
            try:
                d = int(_br.doc_count(c) or 0)
            except Exception:
                d = 0
            rows.append({"keyword": c, "volume": v, "docs": d,
                         "opportunity": round(v / max(d, 1), 6)})
        out["candidates"] = sorted(rows, key=lambda r: -r["opportunity"])
    return JSONResponse(out)


@app.post("/admin/dwell-test")
async def admin_dwell_test(request: Request):
    """운영 진단 — 발현률 게이트 단건 실행: body 텍스트를 받아 감사(missing)+보충(fixed) 결과 반환.
    form/body: text=본문, kw=키워드. 실제 생성 파이프라인과 동일 함수(_ensure_dwell_devices) 사용."""
    try:
        form = await request.form()
        text = str(form.get("text") or "")
        kw = str(form.get("kw") or "")
    except Exception:
        text, kw = "", ""
    if not text.strip():
        return JSONResponse({"ok": False, "error": "text 필요"}, status_code=400)
    from app.generators.text_claude import _audit_dwell_devices, _ensure_dwell_devices
    before_missing = _audit_dwell_devices(text)
    fixed_body, rep = _ensure_dwell_devices(text, kw)
    return JSONResponse({"ok": True, "before_missing": before_missing, "report": rep,
                         "after_missing": _audit_dwell_devices(fixed_body),
                         "body": fixed_body})


GEN_STALE_SEC = int(os.environ.get("SHOPCAST_GEN_STALE", "900"))
#   생성 잡 유령 판정 기준 — 실측 정상 소요 최대치(688초)의 약 1.3배. 단계마다 진행률을
#   찍으므로 이만큼 무갱신이면 스레드가 죽은 것이다(2026-08-02 실사고).


def _job_age(ts: str) -> float:
    """기록 시각 이후 경과 초. 값이 없거나 깨졌으면 무한대(=죽은 것으로 본다)."""
    from datetime import datetime as _d2
    try:
        return (_d2.utcnow() - _d2.fromisoformat(ts or "")).total_seconds()
    except Exception:
        return 1e9


def _tenant_is_demo(tid: str) -> bool:
    """tenants.is_demo 직접 조회 — Tenant 모델에 is_demo 필드가 없어 getattr은 항상 0(실측 버그 2026-07-31)."""
    try:
        with db._conn() as c:
            r = c.execute("SELECT is_demo FROM tenants WHERE id=?", (tid,)).fetchone()
        return bool(r and r["is_demo"])
    except Exception:
        return False


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # CWD 무관(배포 404 실측)
_DOCS = {"guide.pdf": ("assets/docs/ollinda_guide.pdf", "application/pdf", "올린다_제품설명서.pdf"),
         "intro.mp4": ("assets/docs/ollinda_intro.mp4", "video/mp4", "올린다_소개영상.mp4")}


@app.get("/docs/{name}")
def public_docs(name: str):
    """랜딩 다운로드 자료(제품설명서 PDF·소개 영상) — 화이트리스트 파일만(2026-07-31 사장님 지시)."""
    ent = _DOCS.get(name)
    if not ent:
        return HTMLResponse(status_code=404)
    path, mt, fname = ent
    ap = os.path.join(_REPO_ROOT, path)
    if not os.path.exists(ap):
        return HTMLResponse(status_code=404)
    return FileResponse(ap, media_type=mt, filename=fname)


@app.get("/admin/shop-perf")
def admin_shop_perf():
    """사령탑 가게별 성적(2026-08-01 사장님 지시) — 사용자에겐 비노출(주방 철학), 운영자만.
    발행 편수·10/30위 진입·진입률. winscore._my_track 재사용(단일 소스)."""
    from app.services import winscore as _ws
    out, seen = [], set()
    for s in db.list_sets(limit=200):
        tid = s.get("tenant_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        if _tenant_is_demo(tid):
            continue
        tr = _ws._my_track(tid)
        if not tr["total"]:
            continue
        out.append({"tenant": s.get("tenant"), "tid": tid[:8],
                    "published": tr["total"], "top10": tr["top10"], "top30": tr["top30"],
                    "rate10": round(100 * tr["top10"] / tr["total"])})
    out.sort(key=lambda r: (-r["published"], -r["rate10"]))
    return JSONResponse({"ok": True, "shops": out})


@app.api_route("/admin/global-lessons", methods=["GET", "POST"])
def admin_global_lessons(run: str = ""):
    """운영 진단 — 전역 감점 교훈(3겹 루프) 목록 + run=1이면 스윕 즉시 실행."""
    from app.services import lessons as _les
    if run == "1":
        _les.sweep_global()
    with db._conn() as c:
        _les._ensure_global(c)
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM global_lessons ORDER BY created_at DESC LIMIT 20").fetchall()]
    return JSONResponse({"ok": True, "lessons": rows})


@app.api_route("/admin/golden-refs", methods=["GET", "POST"])
async def admin_golden_refs(request: Request):
    """다업종 골든셋 레지스트리(4겹) — 코드 하드코딩 없이 운영 데이터로 관리.
    POST tenant_id·asset_id·label 등록 / GET 목록. 일괄 회귀는 /admin/quality-check-all."""
    with db._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS golden_refs(asset_id TEXT PRIMARY KEY,"
                  "tenant_id TEXT, label TEXT, added TEXT)")
        if request.method == "POST":
            form = await request.form()
            aid = str(form.get("asset_id") or "").strip()
            tid = str(form.get("tenant_id") or "").strip()
            lab = str(form.get("label") or "").strip()[:40]
            if aid and tid and db.get_tenant(tid):
                from datetime import datetime as _d
                c.execute("INSERT OR REPLACE INTO golden_refs VALUES(?,?,?,?)",
                          (aid, tid, lab, _d.utcnow().isoformat()))
        rows = [dict(r) for r in c.execute("SELECT * FROM golden_refs").fetchall()]
    return JSONResponse({"ok": True, "refs": rows})


@app.post("/admin/quality-check-all")
def admin_quality_check_all(request: Request):
    """다업종 골든셋 일괄 회귀(4겹) — 등록된 참조 세트 전부로 실생성·채점. 느림·과금(세트당 ~$1.3),
    프롬프트 개편 후 수동 실행용. 업종별 결과로 '한 업종만 좋아지는 편향'을 잡는다."""
    with db._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS golden_refs(asset_id TEXT PRIMARY KEY,"
                  "tenant_id TEXT, label TEXT, added TEXT)")
        refs = [dict(r) for r in c.execute("SELECT * FROM golden_refs").fetchall()]
    if not refs:
        return JSONResponse({"ok": False, "error": "golden_refs 비어 있음 — /admin/golden-refs로 등록"})
    out = []
    for r in refs:
        try:
            resp = admin_quality_check(request, tenant_id=r["tenant_id"], src_asset=r["asset_id"])
            import json as _j
            d = _j.loads(bytes(resp.body or b"{}"))
            out.append({"label": r.get("label"), "pass": d.get("pass"), "fail": d.get("fail")})
        except Exception as e:
            out.append({"label": r.get("label"), "error": repr(e)[:80]})
    return JSONResponse({"ok": True, "results": out})


@app.get("/admin/gap-scan")
def admin_gap_scan(tenant_id: str = "", limit: int = 40, comp: int = 1):
    """🕳 빈자리 판정 진단(2026-08-02, 1단계 읽기 전용) — 글감·화면 변화 0.

    '자리는 열려 있는데 우리가 아직 없는 검색어'를 점수순으로, 각 키워드가 사장님 영역인지
    (확실/인접/미지/제외) 근거와 함께 보여준다. 분류가 맞는지 사람이 먼저 판단하기 위한 표다.
    comp=0이면 경쟁 조회(문서 수·상위 글 나이)를 건너뛴다(호출 절약).
    """
    if not tenant_id:
        return JSONResponse({"ok": False, "error": "tenant_id 필요"}, status_code=400)
    from app.services import gapscout as _gs
    r = _gs.scan(tenant_id, limit=limit, with_competition=bool(comp))
    if r.get("gaps"):
        by = {}
        for g in r["gaps"]:
            by[g["domain"]] = by.get(g["domain"], 0) + 1
        r["by_domain"] = by
        r["proposable"] = [g for g in r["gaps"] if g["domain"] in ("확실", "인접") and g["score"] > 0]
    return JSONResponse(r)


@app.post("/admin/queue-gen")
def admin_queue_gen(tid: str = "", qid: int = 0, force: int = 0):
    """운영 진단 — 큐의 특정 글감(qid)으로 글을 뽑는다. 백그라운드 실행.
    평소 소비 순서(P1→…)는 그대로다. 지목은 여기서만 한다(검증·실측용)."""
    t = db.get_tenant(tid)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    from app.services import autoqueue as _aq
    import threading as _th

    def _bg():
        try:
            r = _aq.consume(t, plan="pro", only_id=qid, allow_done=bool(force))
            logging.getLogger("shopcast.autoqueue").warning("[queue-gen] %s", r)
        except Exception:
            logging.getLogger("shopcast.autoqueue").exception("[queue-gen] 실패 qid=%s", qid)
    _th.Thread(target=_bg, daemon=True).start()
    return JSONResponse({"ok": True, "tenant": t.name, "qid": qid})


@app.post("/admin/exp-add")
async def admin_exp_add(request: Request):
    """운영 지원 — 사장님이 대화로 주신 실경험 답변을 대신 등록(2026-08-02).
    화면 입력과 같은 저장 경로·같은 검증(길이)을 탄다. 내용은 사장님 말 그대로 넣는다."""
    body = await request.json()
    tid = (body.get("tenant_id") or "").strip()
    if not db.get_tenant(tid):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    saved, failed = [], []
    for it in (body.get("items") or []):
        q, a = (it.get("question") or "").strip(), (it.get("answer") or "").strip()
        (saved if db.save_owner_experience(tid, q, a) else failed).append(q[:40])
    return JSONResponse({"ok": True, "saved": saved, "failed": failed,
                         "total": len(db.list_owner_experience(tid, limit=50))})


@app.post("/admin/gap-feed")
def admin_gap_feed(tenant_id: str = "", limit: int = 3, dry: int = 1):
    """확실 영역의 빈자리를 글감 큐에 편입(2단계). dry=1이면 무엇이 들어갈지만 보여준다."""
    if not tenant_id:
        return JSONResponse({"ok": False, "error": "tenant_id 필요"}, status_code=400)
    from app.services import gapscout as _gs
    return JSONResponse(_gs.feed(tenant_id, limit=limit, dry=bool(dry)))


@app.post("/admin/gap-answer")
def admin_gap_answer(tenant_id: str = "", token: str = "", verdict: str = "", axis: str = ""):
    """사장님 확인 응답 기록 — '해요'(yes) / '안 해요'(no).
    yes는 분류만 올린다(글은 실사진·실경험이 있어야 쓴다). no는 영구 제외이자 되돌릴 수 있다."""
    from app.services import gapscout as _gs
    return JSONResponse(_gs.answer(tenant_id, token, verdict, axis))


@app.post("/admin/set-delete/{asset_id}")
def admin_set_delete(asset_id: str, tenant_id: str = ""):
    """운영 지원 — 잘못 만들어진 세트 정리(2026-08-03). 소유 검증을 그대로 탄다.
    묘비를 남겨 뒤늦게 끝난 스레드가 되살리지 못하게 한다(2026-08-01 부활 실사고)."""
    if not db.get_tenant(tenant_id):
        return JSONResponse({"ok": False, "error": "tenant_id 필요"}, status_code=400)
    before = len(db.get_set_pieces(asset_id))
    db.delete_set(asset_id, tenant_id)
    return JSONResponse({"ok": True, "asset_id": asset_id, "before": before,
                         "after": len(db.get_set_pieces(asset_id)),
                         "tombstoned": db.is_set_deleted(asset_id)})


def _tables_with(col: str) -> list:
    """그 컬럼을 가진 테이블 전부 — 테이블 목록을 손으로 적으면 반드시 빠뜨린다."""
    out = []
    with db._conn() as c:
        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            t = r["name"]
            if t.startswith("sqlite_"):
                continue
            cols = [x["name"] for x in c.execute(f"PRAGMA table_info({t})")]
            if col in cols:
                out.append(t)
    return sorted(out)


@app.post("/admin/migrate-tenant")
def admin_migrate_tenant(src: str = "", dst: str = "", dry: int = 1):
    """가게 이관 — src의 데이터를 dst로 옮긴다(2026-08-03).
    tenant_id 컬럼을 가진 전 테이블을 자동으로 훑는다(손으로 적으면 빠뜨린다).
    dry=1이면 무엇이 몇 건 옮겨질지만 센다."""
    if not (db.get_tenant(src) and db.get_tenant(dst)) or src == dst:
        return JSONResponse({"ok": False, "error": "src/dst 확인 필요"}, status_code=400)
    moved = {}
    with db._conn() as c:
        for t in _tables_with("tenant_id"):
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant_id=?", (src,)).fetchone()[0]
            if not n:
                continue
            moved[t] = n
            if not dry:
                try:
                    c.execute(f"UPDATE OR REPLACE {t} SET tenant_id=? WHERE tenant_id=?", (dst, src))
                except Exception as e:
                    moved[t] = f"{n}건 실패: {repr(e)[:80]}"
    return JSONResponse({"ok": True, "dry": bool(dry), "src": src, "dst": dst, "moved": moved})


@app.post("/admin/purge-except")
def admin_purge_except(keep_email: str = "", keep_tenants: str = "", dry: int = 1):
    """정리 — 남길 계정·가게만 두고 나머지를 지운다(2026-08-03 사장님 지시).
    ★ 되돌릴 수 없다. keep 목록이 비면 거부한다(전체 삭제 사고 방지).
    ★ dry=1 기본 — 무엇이 몇 건 지워질지 먼저 보여준다."""
    keep = [x.strip() for x in (keep_tenants or "").split(",") if x.strip()]
    if not (keep_email and keep):
        return JSONResponse({"ok": False, "error": "keep_email과 keep_tenants가 모두 필요합니다"},
                            status_code=400)
    for tid in keep:
        if not db.get_tenant(tid):
            return JSONResponse({"ok": False, "error": f"남길 가게가 실재하지 않음: {tid}"},
                                status_code=400)
    ph = ",".join("?" * len(keep))
    deleted = {}
    with db._conn() as c:
        for t in _tables_with("tenant_id"):
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant_id NOT IN ({ph})", keep).fetchone()[0]
            if n:
                deleted[t] = n
                if not dry:
                    c.execute(f"DELETE FROM {t} WHERE tenant_id NOT IN ({ph})", keep)
        n = c.execute(f"SELECT COUNT(*) FROM tenants WHERE id NOT IN ({ph})", keep).fetchone()[0]
        if n:
            deleted["tenants"] = n
            if not dry:
                c.execute(f"DELETE FROM tenants WHERE id NOT IN ({ph})", keep)
        nu = c.execute("SELECT COUNT(*) FROM users WHERE email<>?", (keep_email,)).fetchone()[0]
        if nu:
            deleted["users"] = nu
            if not dry:
                c.execute("DELETE FROM user_stores WHERE user_id IN "
                          "(SELECT id FROM users WHERE email<>?)", (keep_email,))
                c.execute("DELETE FROM users WHERE email<>?", (keep_email,))
    return JSONResponse({"ok": True, "dry": bool(dry), "keep_email": keep_email,
                         "keep_tenants": keep, "deleted": deleted})


@app.get("/admin/account-map")
def admin_account_map():
    """운영 진단(읽기 전용) — 계정별로 어느 가게(tenant_id)를 갖고 있고 콘텐츠가 몇 개인지.
    같은 이름의 가게가 여러 개라 '어느 것이 진짜 사장님 것인가'를 눈으로 가리기 위한 표."""
    out = []
    try:
        with db._conn() as c:
            rows = c.execute(
                "SELECT u.email, u.provider, s.tenant_id, t.name "
                "FROM user_stores s JOIN users u ON u.id=s.user_id "
                "LEFT JOIN tenants t ON t.id=s.tenant_id").fetchall()
    except Exception:
        try:
            with db._conn() as c:
                rows = c.execute(
                    "SELECT u.email, '' AS provider, s.tenant_id, t.name "
                    "FROM user_stores s JOIN users u ON u.id=s.user_id "
                    "LEFT JOIN tenants t ON t.id=s.tenant_id").fetchall()
        except Exception as e:
            return JSONResponse({"ok": False, "error": repr(e)[:200]}, status_code=500)
    for r in rows:
        tid = r["tenant_id"]
        try:
            n_sets = len(db.list_sets(tenant_id=tid, limit=100))
        except Exception:
            n_sets = -1
        out.append({"email": r["email"], "provider": r["provider"],
                    "tenant_id": tid, "tenant": r["name"], "sets": n_sets})
    out.sort(key=lambda x: (x["email"] or "", -(x["sets"] or 0)))
    return JSONResponse({"ok": True, "rows": out, "total": len(out)})


@app.get("/admin/harvest")
def admin_harvest(tenant_id: str = "", topic: str = ""):
    """🌾 경험 수확 실측(2026-08-03) — 묻기 전에 자사 기록에서 무엇을 캤는가.
    topic을 주면 그 주제로 인라인 질문을 물을지(ask) / 왜 안 묻는지도 함께 본다."""
    if not db.get_tenant(tenant_id):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    from app.services import gapscout as _gs, harvest as _hv
    _dg: dict = {}
    h = _hv.harvest(tenant_id, _diag=_dg)
    out = {"ok": True, "counts": h["counts"], "why": _dg,
           "owner": [i["text"][:120] for i in h["owner"][:12]],
           "fact": [i["text"] for i in h["fact"][:15]],
           "review": [i["text"][:100] for i in h["review"][:5]]}
    if topic:
        out["inline"] = _gs.inline_question(tenant_id, topic)
        out["note_block"] = _hv.as_note_block(tenant_id, topic)[:600]
    return JSONResponse(out)


@app.get("/admin/wiring")
def admin_wiring():
    """🔌 자율 운행 배선 실증(2026-08-03) — 무엇이 스케줄로 실제 도는가.
    [주기 / 마지막 실행 / 다음 예정]을 그대로 보여준다. '돌 것이다'가 아니라 '돌고 있다'를 본다."""
    from app import scheduler as _sc
    out, running = [], bool(getattr(_sc, "_scheduler", None))
    try:
        for j in (_sc._scheduler.get_jobs() if running else []):
            out.append({"id": j.id, "trigger": str(j.trigger),
                        "next_run": (j.next_run_time.isoformat() if j.next_run_time else None),
                        "last_run_utc": _sc.LAST_RUN.get(j.id)})
    except Exception:
        pass
    try:
        from app.services import gowatch_client as _gw
        gowatch = _gw.configured()
    except Exception:
        gowatch = None
    return JSONResponse({"ok": True, "scheduler_running": running, "jobs": out,
                         "gowatch_configured": gowatch,
                         "local_cron": ["nightly.py 04:00(지면 정찰)", "trackpub.py */30분(발행 추적)"],
                         "note": "local_cron은 맥북에서 돈다 — 노트북이 꺼지면 지면 지도가 낡는다"})


@app.get("/admin/kw-decide")
def admin_kw_decide(tenant_id: str = "", note: str = ""):
    """운영 진단 — 이 소재로 키워드가 어떻게 정해지는지 단계별로 보여준다(2026-08-02).
    빈자리 승격이 안 걸릴 때 어디서 끊겼는지 추측하지 않고 보기 위한 것."""
    t = db.get_tenant(tenant_id)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    from app import seo as _s
    from app.services import gapscout as _gs
    from app.strategies import resolve_strategy as _rs
    cands = _s.target_keywords(t.industry, t.region or "", note,
                               axis=_rs(t).keyword_axis, brand=t.brand_name or "") or []
    gaps = [g for g in _gs.list_gaps(tenant_id, domain="확실", limit=20) if (g.get("score") or 0) > 0]
    after_gap = _s._gap_first(list(cands), tenant_id, note)
    kw0, kws = _s.resolve_target_keyword(
        industry=t.industry or "", region=t.region or "", note=note,
        biz=(getattr(t, "biz_type", "local") or "local"), content_type="sell",
        brand=t.brand_name or "", keyword_axis=_rs(t).keyword_axis,
        tenant_id=tenant_id, prof_name=t.industry or "", verify_volume=False)
    return JSONResponse({"ok": True, "candidates": cands[:8],
                         "gaps_certain": [g["keyword"] for g in gaps],
                         "after_gap_first": after_gap[:8],
                         "gap_changed_order": after_gap[:1] != cands[:1],
                         "final_kw0": kw0, "final_kws": (kws or [])[:5]})


@app.get("/admin/gap-list")
def admin_gap_list(tenant_id: str = "", domain: str = "", limit: int = 30):
    """저장된 빈자리 판정 결과 조회(재조회 없이 표만 다시 본다)."""
    if not tenant_id:
        return JSONResponse({"ok": False, "error": "tenant_id 필요"}, status_code=400)
    from app.services import gapscout as _gs
    return JSONResponse({"ok": True, "gaps": _gs.list_gaps(tenant_id, domain, limit)})


@app.get("/admin/scout-plan")
def admin_scout_plan(tenant: str = "", limit: int = 30, ttl_days: int = 7, shops_only: str = ""):
    """🗺 지면 정찰 계획(2026-08-01 사장님 승인 ①) — 맥 야간 정찰기가 '오늘 훑을 키워드'를 받아간다.
    tenant 미지정이면 블로그 연결된 전 가게. 새 API 호출 0(이미 있는 키워드 데이터만 조합)."""
    from app.services import blogreach as _brc
    out = []
    if tenant:
        t = db.get_tenant(tenant)
        if not t:
            return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
        tenants = [t]
    else:
        # 🧹 정찰 대상 정리(2026-08-01 사장님 지시) — 첫 실행에서 6개 가게 중 4개가 헛돌았다.
        #   ① 데모 tenant 제외 ② 발행 이력 없는 빈 계정 제외(측정할 글이 없다)
        #   ③ 같은 블로그를 보는 tenant는 하나만 — 지면은 '블로그'의 속성이라 3번 훑을 이유가 없다
        #     (루마 tenant 3개가 같은 블로그를 각각 훑고 있었다). 발행이 많은 쪽을 대표로 삼는다.
        #   이메일 도메인으로 거르지 않는다 — @ollinda.guest는 카카오·구글 로그인에도 쓰여
        #   진짜 가입자를 잘라낼 수 있다. 판단 기준은 '실제 활동'이다.
        _by_blog: dict = {}
        for _t in db.list_tenants_with_blog():
            if _tenant_is_demo(_t.id):
                continue
            try:      # 활동량 = 발행 + 생성한 콘텐츠 세트.
                #   ★ '발행 이력'만 보면 안 된다 — 아직 발행 안 한 가게야말로 도와야 할 대상이다
                #     (실측: 주안모터스가 이 기준에 걸려 통째로 빠졌다). 만든 게 있으면 살아있는 가게다.
                _n = (len(db.list_blog_publishes(_t.id, limit=50) or []) * 10
                      + len(db.list_sets(tenant_id=_t.id, limit=20) or []))
            except Exception:
                _n = 0
            if _n <= 0:
                continue
            _bid = (getattr(_t, "blog_id", "") or "").strip().lower() or _t.id
            if _n > _by_blog.get(_bid, (0, None))[0]:
                _by_blog[_bid] = (_n, _t)
        tenants = [v[1] for v in _by_blog.values()]
    if shops_only == "1":       # 감시기·운영 도구용 — 계획 계산 없이 '살아있는 가게' 목록만
        return JSONResponse({"ok": True, "shops": [
            {"tenant_id": t.id, "tenant": t.name,
             "blog_id": (getattr(t, "blog_id", "") or "")} for t in tenants]})
    for t in tenants:
        try:
            kws = _brc.scout_plan(t.id, limit=limit, ttl_days=ttl_days)
        except Exception:
            kws = []
        if kws:
            out.append({"tenant_id": t.id, "tenant": t.name,
                        "blog_id": (getattr(t, "blog_id", "") or ""), "keywords": kws})
    return JSONResponse({"ok": True, "shops": out,
                         "total": sum(len(s["keywords"]) for s in out)})


@app.post("/admin/rank-kw-normalize")
def admin_rank_kw_normalize(dry: str = "1"):
    """🔤 순위 이력 키워드 표기 통일(1회 이관) — 공식 지명을 구어형으로.
    이력이 끊기지 않게 '행을 지우지 않고 keyword 값만 바꾼다'(PK가 자동증가 id라 병존 안전).
    dry=1이면 바꿀 목록만 반환(기본). 실제 반영은 dry=0."""
    from app import seo as _seo
    changes, applied = [], 0
    try:
        with db._conn() as c:
            rows = c.execute("SELECT DISTINCT tenant_id, keyword FROM rank_snapshots").fetchall()
            for r in rows:
                _old = r["keyword"] or ""
                _new = " ".join((_seo._kw_shorten(_old)).split())
                if _new and _new != _old:
                    changes.append({"tenant": (r["tenant_id"] or "")[:8], "from": _old, "to": _new})
                    if dry == "0":
                        c.execute("UPDATE rank_snapshots SET keyword=? WHERE tenant_id=? AND keyword=?",
                                  (_new, r["tenant_id"], _old))
                        applied += 1
    except Exception as e:
        return JSONResponse({"ok": False, "error": repr(e)[:120]}, status_code=500)
    return JSONResponse({"ok": True, "dry_run": dry != "0", "n": len(changes),
                         "applied": applied, "changes": changes[:30]})


@app.get("/admin/exposure")
def admin_exposure(tenant: str = ""):
    """운영 진단 — 노출 현황 요약(사장 화면에 그릴 데이터 그대로)."""
    from app.services import exposure as _ex
    if not db.get_tenant(tenant):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    return JSONResponse({"ok": True, **_ex.summary(tenant)})


@app.get("/admin/blocks-map")
def admin_blocks_map(tenant: str = ""):
    """🧱 지면 지도 — 이 가게 키워드별 통합검색 구성·블로그 지면 유무·내 노출(사령탑용)."""
    if not db.get_tenant(tenant):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    rows = []
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            for r in c.execute("SELECT * FROM kw_blocks WHERE tenant_id=? ORDER BY mine DESC, keyword",
                               (tenant,)).fetchall():
                _bb = [x for x in (r["blog_blocks"] or "").split("|") if x]
                rows.append({"keyword": r["keyword"],
                             "blocks": [x for x in (r["blocks"] or "").split("|") if x],
                             "blog_blocks": _bb,
                             "blog_surface": bool(_bb) or bool(r["mine"]),
                             "mine": bool(r["mine"]),
                             "checked_at": (r["checked_at"] or "")[:16]})
    except Exception:
        pass
    return JSONResponse({"ok": True, "n": len(rows),
                         "surface": sum(1 for r in rows if r["blog_surface"]),
                         "mine": sum(1 for r in rows if r["mine"]), "rows": rows})


@app.post("/admin/blocks-ingest")
async def admin_blocks_ingest(request: Request):
    """🧱 스마트블록 정찰 결과 수신(맥 로컬 insight/blocks.py) — 키워드별 통합검색 블록 구성 저장."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 필요"}, status_code=400)
    from app.services import blogreach as _brc
    return JSONResponse(_brc.blocks_ingest(str(body.get("tenant") or ""), body.get("rows") or []))


@app.get("/admin/blogreach")
def admin_blogreach(tenant: str = "", sweep: str = ""):
    """🌐 유입 경로 진단(2026-08-01 사장님 지시 B) — 검색 밖 통로(주제 설정·이웃 피드·발행 리듬·클립).
    sweep=1이면 전 가게 요약. 진단만 하고 자동 변경은 안 한다(계정 설정은 사람이)."""
    from app.services import blogreach as _brc
    if sweep == "1":
        return JSONResponse({"ok": True, "shops": _brc.sweep()})
    if not db.get_tenant(tenant):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    return JSONResponse(_brc.diagnose(tenant))


@app.get("/admin/queryscout")
def admin_queryscout(tenant: str = "", posts: int = 3, per: int = 10, debug: str = ""):
    """🔎 검색어 정찰 진단(①, 자격증명 0) — 발행 글이 어떤 검색어에서 잡히는지 실측.
    debug=1: 실행 없이 '생성된 후보 + 각 검색량'만 반환(후보 품질 점검용)."""
    from app.services import queryscout as _qs
    t = db.get_tenant(tenant)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    if debug == "1":
        from app.services import searchad as _sa
        out = []
        for pub in db.list_blog_publishes(tenant, limit=max(1, min(posts, 8))):
            p = None
            try:
                p = db.get_piece(pub.get("piece_id") or "")
            except Exception:
                pass
            pl = (p.payload if p else None) or {"title": pub.get("post_title") or ""}
            _dg: dict = {}
            cands = _qs.candidates(pl, region=getattr(t, "region", "") or "",
                                   industry=getattr(t, "industry", "") or "",
                                   biz=getattr(t, "biz_type", "local") or "local",
                                   brand=getattr(t, "brand_name", "") or "",
                                   search_kw=getattr(t, "search_kw", "") or "", _diag=_dg)
            vols = {}
            try:
                vols = _sa.volume_map(cands[:24])
            except Exception:
                pass
            out.append({"post": (pub.get("post_title") or "")[:40], "diag": _dg,
                        "candidates": [{"kw": c, "volume": vols.get(c.replace(" ", ""), 0)}
                                       for c in cands]})
        return JSONResponse({"ok": True, "region": getattr(t, "region", ""),
                             "industry": getattr(t, "industry", ""), "debug": out})
    return JSONResponse(_qs.scout(tenant, max_posts=max(1, min(posts, 8)),
                                  per_post=max(1, min(per, 14))))


@app.post("/admin/inflow-ingest")
async def admin_inflow_ingest(request: Request):
    """📊 유입 검색어 수집 반영(2026-08-01 사장님 지시 D) — 맥 로컬 수집기(insight/inflow.py)가 POST.
    블로그 channel_id로 가게를 찾아 ①이미 순위가 잡히는 검색어는 추적 편입 ②검색량 있는데 밖인 것은
    글감 큐 적재. 판단은 기존 신호(검색량·문서수) 재사용 — 하드코딩 0."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON 필요"}, status_code=400)
    from app.services import blogrank as _br
    from app.services import searchad as _sa
    from app.services import blogsync as _bs
    out = []
    for row in (body.get("rows") or [])[:20]:
        cid = _bs.normalize_blog_id(str(row.get("channel_id") or ""))
        t = next((x for x in db.list_tenants_with_blog()
                  if _bs.normalize_blog_id(getattr(x, "blog_id", "") or "") == cid), None)
        if not t:
            out.append({"channel": cid, "skip": "연결된 가게 없음"})
            continue
        kws = [str(k.get("keyword") or "").strip() for k in (row.get("keywords") or [])][:40]
        kws = [k for k in kws if 2 <= len(k) <= 40]
        tracked = queued = 0
        vols = {}
        try:
            vols = {v["keyword"].replace(" ", ""): v.get("total", 0)
                    for v in _sa.keyword_volumes(kws[:20], limit=60)}
        except Exception:
            pass
        for k in kws[:20]:
            vol = vols.get(k.replace(" ", ""), 0)
            try:
                r = _br.blog_rank(k, cid)
                rank = r.get("rank")
            except Exception:
                rank = None
            if isinstance(rank, int) and rank >= 1:      # 이미 노출 중 → 추적 편입(성과 관측)
                db.save_rank_snapshot(t.id, k, rank, kind="blog_search")
                tracked += 1
            elif vol >= 100:                             # 수요는 있는데 밖 → 글감 큐(기회)
                if db.enqueue_writing(t.id, "inflow", k, "review",
                                      f"실유입 검색어(월 {vol:,}회) — 아직 상위 미노출"):
                    queued += 1
        out.append({"tenant": t.name, "keywords": len(kws), "tracked": tracked, "queued": queued})
    return JSONResponse({"ok": True, "result": out})


@app.get("/admin/publish-pairs")
def admin_publish_pairs(tenant: str = ""):
    """운영 진단(읽기 전용) — 채점기↔실순위 검증용: 발행 글마다 (제목·URL·매칭·우리점수·타깃kw·최고순위).
    2026-08-01 사장님 지시 '올린다 글 vs 수동 글' 대조 분석 재료."""
    if not db.get_tenant(tenant):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    out = []
    for pub in db.list_blog_publishes(tenant, limit=50):
        row = {"title": (pub.get("post_title") or "")[:60], "url": pub.get("published_url"),
               "at": (pub.get("published_at") or "")[:10], "matched_by": pub.get("matched_by"),
               "match_score": pub.get("match_score"), "piece_id": (pub.get("piece_id") or "")[:8]}
        kw = ""
        try:
            p = db.get_piece(pub.get("piece_id") or "")
            if p is not None:
                pl = p.payload or {}
                row["our_score"] = (pl.get("ranking_audit") or {}).get("score")
                row["ours"] = bool(pl.get("gen_source") or pl.get("body"))   # 올린다 생성물 여부
                kw = (pl.get("target_kw") or (pl.get("target_keywords") or [""])[0] or "")
        except Exception:
            pass
        row["kw"] = kw
        if kw:
            ranks = [h["rank"] for h in db.rank_history(tenant, kw, limit=50)
                     if isinstance(h.get("rank"), int) and h["rank"] >= 1]
            row["best_rank"] = min(ranks) if ranks else None
        out.append(row)
    return JSONResponse({"ok": True, "n": len(out), "pairs": out})


@app.get("/admin/kw-region")
def admin_kw_region(kw: str = "", region: str = ""):
    """운영 진단 — 지역 정합 게이트 단건 판정(2026-08-01): 키워드가 가게 지역과 충돌하는가."""
    from app import seo as _seo
    if not (kw.strip() and region.strip()):
        return JSONResponse({"ok": False, "error": "kw·region 필요"}, status_code=400)
    return JSONResponse({"ok": True, "kw": kw, "region": region,
                         "conflict": _seo.region_conflict(kw, region)})


@app.post("/admin/score-gate/{asset_id}")
def admin_score_gate(asset_id: str):
    """운영 진단 — 발행 게이트(표면 수선 포함) 단독 재실행. 미달 글의 수선 효과 실측용."""
    blog = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 없음"}, status_code=404)
    from app.services import qualitycheck as _qc
    _before = ((blog.payload.get("ranking_audit") or {}).get("score"))
    r = _qc.score_gate(asset_id, source=(blog.payload.get("gen_source") or ""))
    blog2 = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
    return JSONResponse({"ok": True, "before": _before, "result": r,
                         "surface_pass": (blog2.payload.get("surface_pass") if blog2 else None),
                         "stops": (blog2.payload.get("score_gate_stops") if blog2 else None)})


@app.get("/admin/kw-supply")
def admin_kw_supply(hints: str = ""):
    """운영 진단 — 공급 신호 키워드 선정 확인(2026-08-01): 후보별 검색량·문서수·기회지수·광고경쟁·최종 선정."""
    from app.services import blogrank as _br
    from app.services import searchad as _sa
    hs = [h.strip() for h in (hints or "").split(",") if h.strip()]
    if not hs:
        return JSONResponse({"ok": False, "error": "hints=키워드,키워드 필요"}, status_code=400)
    vols = [v for v in _sa.keyword_volumes(hs, limit=80) if _sa._relevant(v["keyword"], hs)][:12]
    from app.services import datalab as _dl
    _gr = _dl.growth([v["keyword"] for v in vols])
    for v in vols:
        d = _br.doc_count(v["keyword"])
        v["docs"] = d
        v["opp"] = round(v["total"] / d, 4) if d and d > 0 else None
        v["trend"] = _gr.get(v["keyword"])             # 성장률(0.2=+20%) | None=중립/미설정
    return JSONResponse({"ok": True, "picked": _sa.sweet_spot_keywords(hs),
                         "candidates": sorted(vols, key=lambda v: -(v["opp"] or 0))})


@app.get("/admin/audit/{asset_id}")
def admin_audit(asset_id: str):
    """운영 진단 — 블로그 상위노출 채점 상세(점수·감점 사유·재작성 이력). '왜 80이 안 되나' 가시화."""
    blog = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 없음"}, status_code=404)
    pl = blog.payload or {}
    au = pl.get("ranking_audit") or {}
    return JSONResponse({"ok": True, "score": au.get("score"),
                         "warnings": au.get("warnings"), "checks": au.get("checks"),
                         "publish_blocked_score": pl.get("publish_blocked_score"),
                         "score_gate": pl.get("score_gate"),
                         "score_gate_stops": pl.get("score_gate_stops"),
                         "rewrite_job": pl.get("rewrite_job"),
                         "polish_job": pl.get("polish_job"),      # ⚡ 백그라운드 품질 보정 진행 상태
                         "target_keywords": pl.get("target_keywords"),
                         "battle_plan": pl.get("battle_plan"),   # 작전 감사(2026-08-01)
                         "title": pl.get("title"), "body_len": len(pl.get("body") or ""),
                         "body": pl.get("body"),                 # 운영 진단·전후 비교용(admin 전용)
                         "photos": len(pl.get("image_paths") or []),
                         "gen_source": pl.get("gen_source") or ""})   # 사진 묘사 원문(자막 재료 진단)


@app.get("/admin/users")
def admin_users():
    """운영 진단 — 가입자 명단(이메일 앞부분 마스킹·플랜·무료사용·가게명). '43명이 누구냐' 가시화(2026-07-31)."""
    out = []
    for u in db.list_users():
        em = (u.get("email") or "")
        t = None
        try:
            t = db.get_tenant(u.get("tenant_id") or "")
        except Exception:
            pass
        out.append({"email": (em[:3] + "***" + em[em.find("@"):]) if "@" in em else em[:6],
                    "owner": _is_owner(u), "plan": u.get("plan") or "free",
                    "free_used": u.get("free_used") or 0,
                    "tenant": getattr(t, "name", "") if t else "",
                    "demo_tenant": _tenant_is_demo(u.get("tenant_id") or ""),
                    "created": (u.get("created_at") or "")[:10]})
    out.sort(key=lambda r: r["created"], reverse=True)
    return JSONResponse({"ok": True, "n": len(out), "users": out})


@app.post("/admin/clear-gen-progress/{tenant_id}")
def admin_clear_gen_progress(tenant_id: str):
    """🧹 죽은 생성 잡 정리(운영 복구, 2026-08-02) — 스레드가 죽었는데 status가 running으로
    남아 화면엔 '생성 중', 배포 게이트는 영구 차단되던 문제.
    ★ 살아 있는 잡은 절대 건드리지 않는다 — 마지막 갱신 후 GEN_STALE_SEC 미만이면 거부한다."""
    pr = db.get_gen_progress(tenant_id) or {}
    if not pr:
        return JSONResponse({"ok": False, "error": "진행 기록 없음"}, status_code=404)
    age = _job_age(pr.get("updated_at"))
    if pr.get("status") == "running" and age < GEN_STALE_SEC:
        return JSONResponse({"ok": False, "error": f"아직 살아 있는 잡(마지막 갱신 {round(age)}초 전, "
                                                   f"기준 {GEN_STALE_SEC}초) — 정리 거부"},
                            status_code=409)
    db.set_gen_progress(tenant_id, pr.get("stage") or "", status="failed",
                        error=f"중단됨(마지막 갱신 {round(age)}초 전 — 운영자 정리)")
    return JSONResponse({"ok": True, "tenant_id": tenant_id, "idle_sec": round(age),
                         "was_stage": pr.get("stage")})


@app.post("/admin/set/{asset_id}/clear-video-status")
def admin_clear_video_status(asset_id: str):
    """🧹 낡은 영상 상태 정리(운영 복구) — 죽은 잡·지난 실패 기록이 화면에 '만드는 중'으로 남는 문제.
    영상 파일이 실제로 있는 채널만 done, 나머지는 not_requested로 되돌린다(요청 이력 초기화).
    글·사진·영상 파일 자체는 건드리지 않는다."""
    from app.domain.models import ContentKind as _CKv
    ps = db.get_set_pieces(asset_id)
    blog = next((p for p in ps if p.kind == _CKv.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    # ★ 쇼츠 파일 유무로 조각을 고르면 안 된다 — 네이버만 만든 세트는 video_path가 없다
    #   (같은 실수를 성공 판정에서도 했다). 조각은 종류로 고르고, 산출물은 각각 확인한다.
    _sh_all = [p for p in ps if p.kind == _CKv.SHORT]
    short = next((p for p in _sh_all if (p.payload or {}).get("video_path")), None) or (
        _sh_all[0] if _sh_all else None)
    _nv = ((short.payload.get("naver_video") or {}) if short else {})
    cs = dict(blog.payload.get("channel_status") or {})
    for ch, ok in (("shorts", bool(short and short.payload.get("video_path"))),
                   ("reels", bool(short and (short.payload.get("video_variants") or {}))),
                   ("naver", bool(_nv.get("path"))),
                   ("clip", bool((_nv.get("clip") or {}).get("path")))):
        cs[ch] = {"status": "done"} if ok else {"status": "not_requested"}
    db.update_piece_payload(blog.id, {"channel_status": cs, "video_job": {}})
    return JSONResponse({"ok": True, "channel_status": cs})


@app.post("/admin/set/{asset_id}/make-video")
def admin_make_video(asset_id: str, platforms: str = "naver"):
    """🎬 운영자 영상 요청 — 플랫폼을 지정해 즉시 실행(사용자 버튼과 동일 경로).
    워치독 자동 재시도를 끈 뒤(사장님 지시) registered 상태가 방치되던 문제의 운영 복구용.
    platforms=naver / naver,clip / shorts …"""
    ps = db.get_set_pieces(asset_id)
    if not ps:
        return JSONResponse({"ok": False, "error": "세트 없음"}, status_code=404)
    t = db.get_tenant(ps[0].tenant_id)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    from app.services.ingest import request_video_bundle as _rvb
    want = {w.strip() for w in (platforms or "").split(",") if w.strip()}
    ok, msg = _rvb(t, asset_id, want)
    return JSONResponse({"ok": ok, "error": msg, "want": sorted(want), "tenant": t.name})


@app.post("/admin/aiclip-unblock")
def admin_aiclip_unblock(tenant: str = ""):
    """🎬 AI 무빙 차단 마커 해제(운영 복구) — 검사 API가 죽었을 때 잘못 찍힌 .bad 마커를 지운다.
    실제 불량으로 찍힌 것도 함께 풀리므로, 해제 후 다음 생성에서 재검사된다(재과금 1회)."""
    import glob as _g
    base = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant or "")
    if tenant and not db.get_tenant(tenant):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    n = 0
    for _p in _g.glob(os.path.join(base, "**", "*.veoclip.bad"), recursive=True):
        try:
            os.remove(_p)
            n += 1
        except OSError:
            pass
    _un = len(_g.glob(os.path.join(base, "**", "*.veoclip.mp4.unverified"), recursive=True))
    return JSONResponse({"ok": True, "removed_markers": n, "unverified_clips": _un})


@app.post("/admin/credit-reset")
def admin_credit_reset():
    """💳 크레딧 충전 후 즉시 재개(운영 복구용) — 30분 자동 해제를 기다리지 않는다."""
    from app import llm as _llmr
    _was = _llmr.credit_out()
    _llmr.CREDIT_OUT_TS = 0.0
    return JSONResponse({"ok": True, "was_blocked": _was, "provider": _llmr.CREDIT_PROVIDER})


@app.get("/admin/busy")
def admin_busy():
    """배포 전 통합 점검(배포 규율 확장 2026-07-31) — 재시작에 죽는 진행 작업 전수:
    ①생성 진행률 running ②다시쓰기 running(10분 내) ③영상 잡 registered/running.
    busy=[]일 때만 배포. (실사고: 배포가 다시쓰기 스레드를 죽여 상태 고착+비용 낭비)"""
    from datetime import datetime as _d
    busy, ghosts = [], []
    seen_t = set()
    for s in db.list_sets(limit=40):
        tid, aid = s.get("tenant_id"), s.get("asset_id")
        if tid and tid not in seen_t:
            seen_t.add(tid)
            if _tenant_is_demo(tid):
                continue                               # 랜딩 데모 tenant — 티저가 진행률을 안 닫아 유령행 남음(실측)
            pr = db.get_gen_progress(tid) or {}
            if pr.get("status") == "running":
                # 유령 생성 잡 필터(2026-08-02 실측: 60%에서 991초 무갱신 — 스레드가 죽었는데
                #   status는 running으로 남아 배포가 영구히 막혔다). 다시쓰기·영상 잡에는 이미
                #   있던 장치가 생성에만 빠져 있었다. 기준은 '마지막 갱신 후 경과' — 단계마다
                #   갱신을 찍으므로, 한 단계가 통째로 멈춘 것을 잡는다.
                _stale = _job_age(pr.get("updated_at")) > GEN_STALE_SEC
                busy.append({"type": "gen", "tenant": s.get("tenant"), "stage": pr.get("stage"),
                             "idle_sec": round(_job_age(pr.get("updated_at"))),
                             "stale": _stale})
                if _stale:
                    busy.pop()                       # 표시는 아래 ghosts로, 배포는 막지 않는다
                    ghosts.append({"type": "gen", "tenant": s.get("tenant"),
                                   "stage": pr.get("stage"),
                                   "idle_sec": round(_job_age(pr.get("updated_at")))})
        if not aid:
            continue
        try:
            blog = next((p for p in db.get_set_pieces(aid) if p.kind.value == "blog"), None)
        except Exception:
            continue
        pl = (blog.payload or {}) if blog else {}
        if _rewrite_running(pl):
            busy.append({"type": "rewrite", "tenant": s.get("tenant"), "asset": aid[:8]})
        _pj = pl.get("polish_job") or {}               # ⚡ 백그라운드 품질 보정(2026-08-01)
        if _pj.get("status") == "running":
            try:
                _pa = (_d.utcnow() - _d.fromisoformat(_pj.get("ts", ""))).total_seconds()
            except Exception:
                _pa = 1e9
            if _pa < 900:                              # 15분 넘으면 죽은 잡 — 배포를 막지 않는다
                busy.append({"type": "polish", "tenant": s.get("tenant"), "asset": aid[:8]})
        vj = pl.get("video_job") or {}
        if vj.get("status") in ("registered", "running", "retrying"):
            try:      # 유령 잡 필터(실측: 7/24부터 'running' 잔존) — 2시간 넘으면 죽은 것
                _age = (_d.utcnow() - _d.fromisoformat(vj.get("ts", ""))).total_seconds()
            except Exception:
                _age = 1e9
            if _age < 7200:
                busy.append({"type": "video", "tenant": s.get("tenant"), "asset": aid[:8],
                             "stage": vj.get("stage", "")})
    from app import llm as _llmb
    return JSONResponse({"ok": True, "busy": busy, "safe_to_deploy": not busy,
                         "ghosts": ghosts,          # 죽은 것으로 판정 — 배포는 막지 않되 눈에는 보인다
                         "credit_out": _llmb.credit_out(),
                         "credit_provider": (_llmb.CREDIT_PROVIDER if _llmb.credit_out() else ""),
                         "ts": _d.utcnow().isoformat()})


@app.get("/admin/tts-test")
def admin_tts_test():
    """운영 진단 — 지금 이 서버에서 어떤 TTS 엔진이 실제로 동작하는지 확인(짧은 한 문장 실합성).
    ElevenLabs(JayK) 키 등록 검증용(2026-07-30). 비용: 수십 자 1회."""
    import tempfile
    from app.media import tts as _tts
    out = {"ok": True, "elevenlabs_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
           "voice_id": os.environ.get("ELEVENLABS_VOICE_ID", "")[:22] or "(기본값)",
           "gemini_key": bool(os.environ.get("GEMINI_API_KEY"))}
    with tempfile.TemporaryDirectory() as td:
        p, words = _tts.synthesize_timed("올린다 나레이션 엔진 점검입니다.", td)
        out["engine"] = ("elevenlabs+실측싱크" if (p and words) else
                         "elevenlabs/gemini(싱크없음)" if p else "없음(무음 영상)")
        out["audio_ok"] = bool(p and os.path.exists(p))
        out["word_timestamps"] = len(words or [])
        out["last_err"] = _tts.LAST_ERR[:200]
    return JSONResponse(out)


@app.get("/admin/aiclip-test")
def admin_aiclip_test(tenant: str = "", fname: str = ""):
    """운영 진단 — AI 카메라워크(Veo) 단건 실행: 저장된 사진 1장으로 생성+원본대조 QC까지.
    느림(1~3분)·과금(편당 약 900원) — 검증용으로만. 결과 클립은 /admin/media로 확인."""
    from app.media import ai_clip as _aic
    if not _aic.enabled():
        return JSONResponse({"ok": False, "error": "비활성(GEMINI_API_KEY 없음 또는 VEO_CLIP=0)"})
    t = db.get_tenant(tenant)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    path = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant, os.path.basename(fname))
    if not os.path.exists(path):
        return JSONResponse({"ok": False, "error": "사진 없음", "path_tried": os.path.basename(path)},
                            status_code=404)
    b = _aic.ClipBudget(max_new=1)
    clip = b.get(path)
    return JSONResponse({"ok": True, "stats": b.stats(),
                         "clip": f"/admin/media/{tenant}/{os.path.basename(clip)}" if clip else None,
                         "verdict": "통과(캐시/생성)" if clip else
                                    ("QC 탈락(켄번스 폴백)" if b.stats()["qc_fail"] else "생성 실패/상한")})


@app.post("/admin/gen-progress-close/{tenant_id}")
def admin_gen_progress_close(request: Request, tenant_id: str):
    """stale 'running' 진행률 잔상 수동 종료(운영 복구용) — 단건 재생성 경로가 남긴 잔상 정리.
    실제 실행 중 스레드에는 영향 없음(표시 행만 done 처리)."""
    if not db.get_tenant(tenant_id):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    pr = db.get_gen_progress(tenant_id) or {}
    db.set_gen_progress(tenant_id, pr.get("stage") or "done", pr.get("label") or "", "",
                        1.0, status="done")
    return JSONResponse({"ok": True, "closed_from": pr.get("status"), "label": pr.get("label")})


@app.get("/admin/anatomy")
def admin_anatomy(kw: str = "", force: str = ""):
    """🔬 상위 글 해부 진단 — 동기 크롤(키워드당 상위 5개, 저속)·구조 지표 집계 반환(원문 미저장).
    force=1: 캐시 무시하고 재해부(스키마 갱신·검증용)."""
    if not kw.strip():
        return JSONResponse({"ok": False, "error": "kw 필요"}, status_code=400)
    from app.services import bloganatomy
    if force == "1":
        try:
            with db._conn() as c:
                c.execute("DELETE FROM kw_anatomy WHERE keyword=?", (" ".join(kw.split()),))
        except Exception:
            pass
    return JSONResponse({"ok": True, "anatomy": bloganatomy.anatomize(kw.strip())})


@app.get("/admin/win-score")
def admin_win_score(tenant_id: str = "", kw: str = ""):
    """🎲 승산 스코어 진단 — 쓰기 전 '이길 수 있는 판인가' 근거 포함 반환."""
    if not (tenant_id and kw.strip() and db.get_tenant(tenant_id)):
        return JSONResponse({"ok": False, "error": "tenant_id·kw 필요"}, status_code=400)
    from app.services import winscore
    return JSONResponse({"ok": True, **winscore.score(tenant_id, kw.strip())})


@app.api_route("/admin/quality-check", methods=["GET", "POST"])
def admin_quality_check(request: Request, tenant_id: str = "", src_asset: str = "", video: int = 0):
    """📏 품질 회귀 검사(골든세트) — src_asset 세트의 사진으로 '실제 생성'을 동기 실행하고
    산출물을 결정적 규칙으로 채점(services.qualitycheck). video=1이면 네이버 영상까지 온디맨드 요청.
    배포 후 이걸 돌려 품질 후퇴를 사장님보다 먼저 발견한다(사장님=QA 구조 종식, 2026-07-28)."""
    t = db.get_tenant(tenant_id)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    _sp = db.get_set_pieces(src_asset)
    _src = next((p for p in _sp if (p.payload or {}).get("image_paths")), None)
    if not _src:
        return JSONResponse({"ok": False, "error": "src_asset에 사진 없음"}, status_code=404)
    from app.services.ingest import _restore_media, ingest_upload, request_video_bundle
    paths = _restore_media(t.id, list(_src.payload.get("image_paths") or [])[:8])
    if not paths:
        return JSONResponse({"ok": False, "error": "골든 사진 복원 실패"}, status_code=500)
    files = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                files.append((f.read(), os.path.basename(p)))
        except Exception:
            continue
    try:
        made = ingest_upload(t, files, "[품질검사 골든세트]", intake={})
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "traceback": traceback.format_exc()[-1500:]}, status_code=500)
    aid = made[0].asset_id if made else ""
    from app.services import qualitycheck as _qc
    rep = _qc.run_checks(aid) if aid else {"pass": 0, "fail": 1, "checks": [{"name": "생성 실패", "ok": False}]}
    if video and aid:
        try:
            request_video_bundle(t, aid, ["naver"])
            rep["video_requested"] = True
        except Exception as e:
            rep["video_requested"] = repr(e)[:120]
    rep["ok"] = True
    return JSONResponse(rep)


@app.api_route("/admin/remask/{asset_id}", methods=["GET", "POST"])
def admin_remask(asset_id: str, apply: int = 0, file: str = ""):
    """PII 재마스킹 진단·복구(임시번호판 미탐 실사고 후속). apply=0 드라이런 — 사본에서 탐지만 실행해
    per-photo 판정 로그 반환(원본 불변). apply=1 실제 마스킹(모자이크 추가만 — 파괴적 처리 없음).
    file=파일명 지정 시 그 사진만."""
    import shutil as _sh
    import tempfile
    _sp = db.get_set_pieces(asset_id)
    _bl0 = next((p for p in _sp if (p.payload or {}).get("image_paths")), None)
    if not _bl0:
        return JSONResponse({"ok": False, "error": "사진 있는 피스 없음"}, status_code=404)
    from app.services.ingest import _restore_media
    paths = _restore_media(_bl0.tenant_id, list(_bl0.payload.get("image_paths") or []))
    if file:
        paths = [p for p in paths if os.path.basename(p) == file]
    from app.media import photo_boost as _pb
    out = []
    for p in paths:
        _n0 = len(getattr(_pb, "_MASK_LAST_LOG", []))
        if apply:
            n = _pb.mask_personal_info(p)
            out.append({"file": os.path.basename(p), "masked": n,
                        "log": list(getattr(_pb, "_MASK_LAST_LOG", []))[_n0:]})
        else:
            with tempfile.NamedTemporaryFile(suffix=os.path.splitext(p)[1] or ".jpg", delete=False) as tf:
                cp = tf.name
            try:
                _sh.copyfile(p, cp)
                n = _pb.mask_personal_info(cp)
                out.append({"file": os.path.basename(p), "would_mask": n,
                            "log": list(getattr(_pb, "_MASK_LAST_LOG", []))[_n0:]})
            finally:
                try:
                    os.unlink(cp)
                except Exception:
                    pass
    return JSONResponse({"ok": True, "apply": bool(apply), "n": len(out), "photos": out})


@app.get("/admin/lead-scout")
def admin_lead_scout(region: str = "", biz: str = "", n: int = 20, mode: str = "place",
                     kw: str = ""):
    """🎯 영업 리드 정찰(공식 검색 API만).
    mode=place : 지역 업체 + 블로그 활동 상태(기존)
    mode=blog  : 키워드로 '블로그를 실제 운영하는 사장님'을 찾는다(전국·업종 무관) —
                 글은 쓰는데 뜸하거나 오래된 블로거 = 올린다가 가장 필요한 사람."""
    if mode == "blog":
        import re as _rb
        from datetime import datetime as _dtb
        from app.services.blogrank import _search_blog as _sbb, configured as _cfb
        q = (kw or f"{region} {biz}").strip()
        if not q:
            return JSONResponse({"ok": False, "error": "kw 또는 region+biz 필요"}, status_code=400)
        if not _cfb():
            return JSONResponse({"ok": False, "error": "네이버 API 키 미설정"}, status_code=503)
        rows, seen = [], set()
        for it in _sbb(q, min(max(n * 2, 20), 100)):
            bid = (it.get("bloggerlink") or "").rstrip("/").split("/")[-1]
            nm = _rb.sub(r"<[^>]+>", "", it.get("bloggername") or "").strip()
            if not bid or bid in seen:
                continue
            seen.add(bid)
            pd, age = it.get("postdate") or "", None
            try:
                age = (_dtb.utcnow() - _dtb.strptime(pd, "%Y%m%d")).days
            except Exception:
                pass
            # 우선순위: 오래 방치(needs help) > 최근(경쟁 강함). 30일↑ 방치 = 최우선
            pri = (age if age is not None else 60)
            rows.append({"name": nm or bid, "blog_id": bid,
                         "blog": f"https://blog.naver.com/{bid}",
                         "last_title": _rb.sub(r"<[^>]+>", "", it.get("title") or "")[:60],
                         "blog_last": pd, "blog_age_days": age, "priority": pri,
                         "phone": "", "address": "", "category": q})
            if len(rows) >= n:
                break
        rows.sort(key=lambda x: -x["priority"])
        return JSONResponse({"ok": True, "mode": "blog", "query": q, "n": len(rows), "leads": rows})
    import re as _rl
    from datetime import datetime as _dtl
    from app.services.blogrank import _search_blog as _sb, configured as _cfg2
    if not (region.strip() and biz.strip()):
        return JSONResponse({"ok": False, "error": "region·biz 필요"}, status_code=400)
    if not _cfg2():
        return JSONResponse({"ok": False, "error": "네이버 API 키 미설정"}, status_code=503)
    import os as _os, requests as _rq
    out, seen = [], set()
    _hdr = {"X-Naver-Client-Id": _os.environ["NAVER_CLIENT_ID"],
            "X-Naver-Client-Secret": _os.environ["NAVER_CLIENT_SECRET"]}
    for start in range(1, min(max(n, 5), 50) + 1, 5):
        try:
            r = _rq.get("https://openapi.naver.com/v1/search/local.json",
                        params={"query": f"{region} {biz}", "display": 5, "start": start},
                        headers=_hdr, timeout=8)
            items = r.json().get("items", []) if r.status_code == 200 else []
        except Exception:
            items = []
        for it in items:
            name = _rl.sub(r"<[^>]+>", "", it.get("title") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            blog, last, age = "", "", None
            try:
                b = _sb(name, 1)
                if b:
                    blog = b[0].get("bloggerlink") or ""
                    last = b[0].get("postdate") or ""
                    if len(last) == 8:
                        age = (_dtl.utcnow() - _dtl.strptime(last, "%Y%m%d")).days
            except Exception:
                pass
            # 우선순위: 블로그 없음(100) > 오래 방치(일수) > 활발(0)
            pri = 100 if not blog else (age if age is not None else 50)
            out.append({"name": name, "phone": it.get("telephone") or "",
                        "address": _rl.sub(r"<[^>]+>", "", it.get("roadAddress") or ""),
                        "category": _rl.sub(r"<[^>]+>", "", it.get("category") or ""),
                        "blog": blog, "blog_last": last, "blog_age_days": age, "priority": pri})
            if len(out) >= n:
                break
        if len(out) >= n:
            break
    out.sort(key=lambda x: -x["priority"])
    return JSONResponse({"ok": True, "region": region, "biz": biz, "n": len(out), "leads": out})


@app.api_route("/admin/watchtower", methods=["GET", "POST"])
def admin_watchtower(test: int = 0):
    """🗼 자가진단 수동 실행 — test=1이면 텔레그램 연결 테스트 메시지도 발송."""
    from app.services import watchtower as _wt
    out = {"ok": True, "telegram_configured": _wt.configured()}
    if test:
        out["test_sent"] = _wt.send("✅ 올린다 알림 연결 테스트 — 이 메시지가 보이면 정상입니다.")
    out["result"] = _wt.check()
    return JSONResponse(out)


@app.post("/me/feedback")
async def my_feedback(request: Request):
    """🗣 결과 화면 피드백 — 👍👎 + 한 줄. '취향'은 즉시 그 가게 교훈으로 반영(배포 불필요)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False}, status_code=401)
    form = await request.form()
    aid = str(form.get("asset_id") or "")[:64]
    vote = str(form.get("vote") or "")[:8]
    txt = str(form.get("text") or "").strip()[:400]
    t0 = db.get_tenant(getattr(u, "tenant_id", "") or "")
    if not t0:
        return JSONResponse({"ok": False}, status_code=404)
    db.add_feedback(t0.id, aid, vote=vote, text=txt)
    # 취향형(문체·길이·표현)은 그 가게 교훈으로 즉시 반영 — 다음 글부터 조용히 적용
    applied = False
    if txt and len(txt) >= 4:
        try:
            from app.generators.text_claude import _call_llm
            v = (_call_llm(
                "사장님이 AI가 쓴 글에 남긴 의견이다. 이게 '이 가게 글쓰기 취향'으로 바꿀 수 있는 "
                "것이면 다음 글부터 적용할 지시문 한 문장(20~60자, 명령형)으로 만들고, "
                "버그·오류·기능요청이면 NO 만 출력하라.\n"
                f"[의견] {txt}", model="claude-sonnet-5", max_tokens=100) or "").strip()
            v = " ".join(v.split())
            if v and "NO" not in v.upper() and 8 <= len(v) <= 90:
                db.add_lesson(t0.id, v, source_kw="", source_piece_id=f"fb:{aid[:8]}",
                              cause="user_feedback", status="active")
                applied = True
        except Exception:
            pass
    return JSONResponse({"ok": True, "applied": applied})


@app.post("/admin/feedback-purge")
def admin_feedback_purge(asset_id: str = "test-asset", tenant_id: str = ""):
    """🧹 테스트 피드백·교훈 정리 — asset_id(기본 test-asset) 피드백과 fb:test 교훈 삭제."""
    n_fb = n_ls = 0
    try:
        with db._conn() as c:
            db._ensure_feedback(c)
            cur = c.execute("DELETE FROM feedback WHERE asset_id=?" +
                            (" AND tenant_id=?" if tenant_id else ""),
                            ((asset_id, tenant_id) if tenant_id else (asset_id,)))
            n_fb = cur.rowcount or 0
            db._ensure_lessons_table(c)
            cur2 = c.execute("DELETE FROM tenant_lessons WHERE source_piece_id LIKE 'fb:%'" +
                             (" AND tenant_id=?" if tenant_id else ""),
                             ((tenant_id,) if tenant_id else ()))
            n_ls = cur2.rowcount or 0
    except Exception as e:
        return JSONResponse({"ok": False, "error": repr(e)[:120]}, status_code=500)
    return JSONResponse({"ok": True, "feedback_deleted": n_fb, "lessons_deleted": n_ls})


@app.post("/admin/feedback-test")
async def admin_feedback_test(request: Request):
    """🗣 고객 목소리 경로 진단 — 사용자 제출과 동일한 로직(분류→교훈 반영→기록)을 실행."""
    form = await request.form()
    tid = str(form.get("tenant_id") or "")
    txt = str(form.get("text") or "").strip()[:400]
    vote = str(form.get("vote") or "down")[:8]
    t0 = db.get_tenant(tid)
    if not t0:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    db.add_feedback(t0.id, "test-asset", vote=vote, text=txt)
    applied, lesson = False, ""
    if txt and len(txt) >= 4:
        try:
            from app.generators.text_claude import _call_llm
            v = (_call_llm(
                "사장님이 AI가 쓴 글에 남긴 의견이다. 이게 '이 가게 글쓰기 취향'으로 바꿀 수 있는 "
                "것이면 다음 글부터 적용할 지시문 한 문장(20~60자, 명령형)으로 만들고, "
                "버그·오류·기능요청이면 NO 만 출력하라.\n"
                f"[의견] {txt}", model="claude-sonnet-5", max_tokens=100) or "").strip()
            v = " ".join(v.split())
            if v and "NO" not in v.upper() and 8 <= len(v) <= 90:
                db.add_lesson(t0.id, v, source_kw="", source_piece_id="fb:test",
                              cause="user_feedback", status="active")
                applied, lesson = True, v
        except Exception as e:
            lesson = repr(e)[:80]
    return JSONResponse({"ok": True, "applied": applied, "lesson": lesson,
                         "note": "취향이면 교훈 반영(배포 불필요), 버그면 사령탑 표시"})


@app.get("/admin/feedback")
def admin_feedback(limit: int = 80, group: int = 1):
    """🗣 고객 목소리 집계(사령탑용) — 긴급/개선/패턴으로 분류. group=1이면 유사 의견 묶기."""
    rows = db.list_feedback(limit=limit)
    urgent, wish, quiet = [], [], []
    for r in rows:
        item = {"id": r.get("id"), "tenant": r.get("tenant"), "text": (r.get("text") or "")[:160],
                "vote": r.get("vote"), "signal": r.get("signal"),
                "at": (r.get("created_at") or "")[:16], "status": r.get("status")}
        if r.get("signal"):
            quiet.append(item)
        elif (any(k in (r.get("text") or "")
                  for k in ("안 돼", "안돼", "오류", "안나", "못", "이상", "별로", "실패", "틀"))
              or (r.get("vote") == "down"
                  and not any(k in (r.get("text") or "")
                              for k in ("좋", "마음에", "감사", "만족", "훌륭", "최고")))):
            urgent.append(item)
        else:
            wish.append(item)
    # 패턴: 같은 키워드가 3건 이상
    import re as _rf
    from collections import Counter as _C
    words = _C()
    for r in rows:
        for w in _rf.findall(r"[가-힣]{2,6}", (r.get("text") or "")):
            if w not in ("그리고", "하는", "해서", "합니다", "있는", "같아", "너무"):
                words[w] += 1
    pattern = [{"word": w, "n": n} for w, n in words.most_common(6) if n >= 3]
    return JSONResponse({"ok": True, "n": len(rows), "urgent": urgent[:12], "wish": wish[:12],
                         "quiet": quiet[:8], "pattern": pattern})


@app.get("/admin/biz-metrics")
def admin_biz_metrics(days: int = 30):
    """📊 사업 지표 단일 API(맥북 사령탑용, 읽기 전용) — 고객·매출·시스템 건강·마진.
    결제(구독) 데이터는 있으면 집계, 없으면 0 — 패들 연동 후 자동으로 채워진다."""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    since = (now - timedelta(days=days)).isoformat()
    users, subs, err = [], [], []
    try:
        users = db.list_users()
    except Exception:
        pass
    paid = trial = new_today = 0
    real_total = real_trial = 0                        # 실가입자(운영자·테스트 제외, 2026-07-31 사장님 지적)
    today = now.date().isoformat()
    for u in users:
        pl = (u.get("plan") or "free").lower()
        _em = (u.get("email") or "").lower()
        # 실측(2026-07-31): 43명 전원이 @ollinda.test/@ollinda.guest 개발 계정이었음 — 합성 도메인 제외
        _real = not (_is_owner(u) or _em.endswith("@ollinda.test") or _em.endswith("@ollinda.guest"))
        if _real and _tenant_is_demo(u.get("tenant_id") or ""):
            _real = False                              # 데모 tenant 소유 = 테스트 계정
        if pl in ("basic", "pro", "self", "agency"):
            paid += 1
        elif (u.get("free_used") or 0) > 0:
            trial += 1
            if _real:
                real_trial += 1
        if _real:
            real_total += 1
        if (u.get("created_at") or "")[:10] == today:
            new_today += 1
    # 구독·결제(테이블 있으면)
    revenue_mrr = 0
    pay_today = []
    try:
        with db._conn() as c:
            rows = c.execute("SELECT * FROM subscriptions").fetchall()
        from app import config as _cfg
        for r in rows:
            d = dict(r)
            if (d.get("status") or "") in ("active", "trialing"):
                revenue_mrr += (_cfg.PLANS.get(d.get("plan") or "", {}) or {}).get("price", 0)
            if (d.get("updated_at") or d.get("created_at") or "")[:10] == today:
                pay_today.append({"plan": d.get("plan"), "status": d.get("status"),
                                  "user": (d.get("user_id") or "")[:8]})
            subs.append(d)
    except Exception:
        pass
    # 시스템 건강 + 원가
    tenants, gen_fail, gen_run, cost_usd, cost_n, blocked = [], 0, 0, 0.0, 0, 0
    try:
        tenants = db.list_tenants()
    except Exception:
        pass
    heavy = []
    for t in (tenants or [])[:40]:
        try:
            p = db.get_gen_progress(t.id) or {}
            if p.get("status") == "failed":
                gen_fail += 1
                err.append({"tenant": t.name, "error": (p.get("error") or "")[:120]})
            elif p.get("status") == "running":
                gen_run += 1
            tc, tn = 0.0, 0
            for s in db.list_sets(tenant_id=t.id, limit=12):
                if (s.get("created") or "") < since[:10].replace("-", "-"):
                    pass
                for pc in db.get_set_pieces(s["asset_id"]):
                    if pc.kind.value != "blog":
                        continue
                    ac = (pc.payload or {}).get("api_cost") or {}
                    if ac.get("usd"):
                        tc += float(ac["usd"]); tn += 1
                    if (pc.payload or {}).get("publish_blocked_score"):
                        blocked += 1
            cost_usd += tc; cost_n += tn
            if tc > 0:
                heavy.append({"tenant": t.name, "usd": round(tc, 2), "sets": tn})
        except Exception:
            continue
    heavy.sort(key=lambda x: -x["usd"])
    # 잡 복구 이력
    jobs_pending = 0
    try:
        jobs_pending = len(db.pending_gen_jobs())
    except Exception:
        pass
    return JSONResponse({
        "ok": True, "ts": now.isoformat(),
        "customers": {"total_users": len(users), "paid": paid, "trial": trial,
                      "real_users": real_total, "real_trial": real_trial,   # 운영자·테스트 제외
                      "new_today": new_today, "tenants": len(tenants or [])},
        "revenue": {"mrr_krw": revenue_mrr, "subs": len(subs), "pay_today": pay_today},
        "health": {"gen_failed": gen_fail, "gen_running": gen_run, "errors": err[:5],
                   "jobs_pending": jobs_pending, "blocked_posts": blocked},
        "unit_economics": {"api_usd": round(cost_usd, 2), "sets": cost_n,
                           "avg_usd": round(cost_usd / cost_n, 3) if cost_n else 0,
                           "heavy": heavy[:3]},
    })


@app.post("/admin/set-signature/{tenant_id}")
async def admin_set_signature(request: Request, tenant_id: str):
    """블로그 서명 설정(form: text) — 빈 값이면 해제."""
    t = db.get_tenant(tenant_id)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    form = await request.form()
    txt = str(form.get("text") or "").strip()[:200]
    with db._conn() as c:
        c.execute("UPDATE tenants SET blog_signature=? WHERE id=?", (txt, tenant_id))
    return JSONResponse({"ok": True, "signature": txt})


@app.get("/admin/set-cost/{asset_id}")
def admin_set_cost(asset_id: str, regate: int = 0):
    """💰 세트 실측 비용·점수 조회(+regate=1이면 발행 게이트 재실행 — 봉인 세트 소액 재판정)."""
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 없음"}, status_code=404)
    if regate:
        from app.services import qualitycheck as _qcg
        _qcg.score_gate(asset_id, source=(blog.payload or {}).get("gen_source") or "")
        blog = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
    pl = blog.payload or {}
    return JSONResponse({"ok": True, "api_cost": pl.get("api_cost"),
                         "score": (pl.get("ranking_audit") or {}).get("score"),
                         "score_gate": pl.get("score_gate"),
                         "blocked": pl.get("publish_blocked_score")})


@app.get("/admin/kw-audit")
def admin_kw_audit(limit: int = 30):
    """키워드-소재 정합 전수 검사(캐스퍼/토레스 실사고 후속) — 세트별로
    ① 채널 간 target_kw 불일치 ② 키워드 고유 토큰이 사진 분석(note)에 없는데 본문에 등장(날조 신호)
    ③ 생성 시 기록된 subject_check 값(miss=게이트 불일치 판정)을 모아 반환."""
    import re as _re
    _COMMON = {"중고", "중고차", "판매", "추천", "후기", "가격", "비용", "시공", "매장", "전문", "업체"}
    out, checked = [], 0
    try:
        _sets = db.list_sets(limit=limit)
    except Exception:
        _sets = []
    for s in _sets:
        pieces = db.get_set_pieces(s["asset_id"])
        if not pieces:
            continue
        checked += 1
        a = db.get_asset(s["asset_id"])
        note = (getattr(a, "note", "") or "") if a else ""
        t = db.get_tenant(s["tenant_id"])
        _stop = set(_re.findall(r"[가-힣A-Za-z0-9]{2,}",
                                f"{getattr(t, 'region', '')} {getattr(t, 'name', '')} "
                                f"{getattr(t, 'industry', '')}" if t else "")) | _COMMON
        kws, subj, sus = {}, {}, []
        for p in pieces:
            pl = p.payload or {}
            tk = ((pl.get("target_kw") or "") or ((pl.get("target_keywords") or [""])[0] or "")).strip()
            if tk:
                kws.setdefault(tk, []).append(p.kind.value)
            if pl.get("subject_check"):
                subj[p.kind.value] = pl["subject_check"]
            body = (pl.get("body") or pl.get("text") or "")
            # ★ 대표 키워드만이 아니라 '키워드 목록 전체'를 검사 — 캐스퍼는 깊은 목록에서 유입(실측)
            _all_kw = " ".join([tk] + [str(k) for k in (pl.get("target_keywords") or [])]
                               + [str(k) for k in (pl.get("seo_keywords") or [])])
            toks = sorted({w for w in _re.findall(r"[가-힣A-Za-z0-9]{2,}", _all_kw)
                           if w not in _stop and w not in note and w in body})
            if toks and note:
                sus.append({"kind": p.kind.value, "tokens": toks})
        if len(kws) > 1 or sus or "miss" in subj.values():
            out.append({"asset_id": s["asset_id"], "tenant": s["tenant"], "created": s["created"],
                        "kws": {k: v for k, v in kws.items()}, "divergent": len(kws) > 1,
                        "suspect_tokens": sus, "subject_check": subj})
    return JSONResponse({"ok": True, "checked": checked, "flagged": len(out), "sets": out})


@app.get("/admin/sets-list")
def admin_sets_list(request: Request, tenant: str = "", limit: int = 20):
    """운영/검증용 — 최신 세트(asset_id·tenant·글수·생성) JSON. 콘티 검증 대상 선택용."""
    return JSONResponse({"ok": True, "sets": db.list_sets(tenant_id=tenant or None, limit=limit)})


@app.post("/admin/gowatch/consume")
def admin_gowatch_consume(request: Request):
    """gowatch 적응 큐 소비 1회 트리거(운영자/스케줄러/W3 E2E). 자동 발행 0 — 제안 카드만 생성."""
    from app.services import adapt_consume
    res = adapt_consume.consume_all()
    return JSONResponse({"ok": True, **res})


@app.post("/admin/gen-test/{tenant_id}")
async def admin_gen_test(request: Request, tenant_id: str, photos: list[UploadFile] = File(...),
                         note: str = Form(""), bg: int = Form(0)):
    """생성 다운 진단 — ingest_upload를 동기 실행해 예외를 즉시 반환(bg 스레드 삼킴 우회). 실물 재현.
    note: 사장님이 주신 실제 소재 메모(없으면 진단 표시). bg=1이면 백그라운드(사진이 많을 때)."""
    t = db.get_tenant(tenant_id)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    files = await _read_image_uploads(photos)
    _note = (note or "").strip() or "[진단 테스트]"
    if bg:
        import threading as _tgt

        def _run():
            try:
                ingest_upload(t, files, _note, intake={})
            except Exception:
                logging.getLogger("shopcast.ingest").exception("[gen-test] 실패 t=%s", tenant_id)
        _tgt.Thread(target=_run, daemon=True).start()
        return JSONResponse({"ok": True, "bg": True, "photos": len(files), "tenant": t.name})
    try:
        made = ingest_upload(t, files, _note, intake={})
        from app.services.generate import LAST_ERRORS as _LEd
        return JSONResponse({"ok": True, "pieces": [p.kind.value for p in made], "n": len(made),
                             "errors": dict(_LEd)})
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "traceback": traceback.format_exc()[-1500:]}, status_code=500)


@app.post("/admin/gen-pool/{tenant_id}")
def admin_gen_pool(tenant_id: str):
    """⏱ 생성 실측용 트리거(2026-08-01) — 그 가게가 이미 쓴 사진 풀을 그대로 재사용해 생성한다.
    같은 사진·같은 가게로 반복 측정해야 단계별 소요시간 비교가 의미를 갖는다(사진이 다르면
    분석 시간·본문 길이가 달라져 비교가 무너짐). 백그라운드 실행 — 사용자 경로와 동일."""
    t = db.get_tenant(tenant_id)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    from app.services.autoqueue import photo_pool
    paths = photo_pool(t)
    if not paths:
        return JSONResponse({"ok": False, "error": "재사용할 사진이 없습니다"}, status_code=409)
    files = []
    for _p in paths[:30]:
        try:
            with open(_p, "rb") as _f:
                files.append((_f.read(), os.path.basename(_p)))
        except Exception:
            pass
    if not files:
        return JSONResponse({"ok": False, "error": "사진 읽기 실패"}, status_code=409)

    import logging as _lgpool
    import threading as _thpool

    def _bg():
        try:
            ingest_upload(t, files, "[실측] 사진 풀 재사용 생성", intake={})
        except Exception:
            _lgpool.getLogger("shopcast.ingest").exception("[gen-pool] 실패 tenant=%s", tenant_id)
    _thpool.Thread(target=_bg, daemon=True).start()
    return JSONResponse({"ok": True, "photos": len(files), "tenant": t.name})


@app.get("/admin/gen-progress/{tenant_id}")
def admin_gen_progress(request: Request, tenant_id: str):
    """생성 진행/실패 진단 — 해당 tenant의 최신 생성 단계·에러(traceback 포함).
    ★ 미존재 tenant는 404 — 배포 게이트가 잘못된 ID로 'idle 착각' 후 push한 실사고(2026-07-27) 방지."""
    if not db.get_tenant(tenant_id):
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    return JSONResponse({"ok": True, "progress": db.get_gen_progress(tenant_id),
                         "duration_range": db.gen_duration_range()})


def _progress_payload(t) -> dict:
    """사용자용 진행률 — 단계·퍼센트·실측 범위·지연/실패 안내(정직한 표시). 숫자 남은시간 미표시."""
    pr = db.get_gen_progress(t.id) or {}
    rng = db.gen_duration_range()
    out = {"stage": pr.get("stage"), "label": pr.get("label") or "", "detail": pr.get("detail") or "",
           "pct": pr.get("pct"), "status": pr.get("status") or "idle"}
    # 실측 범위 문구(p25~p90) — 표본 충분할 때만. vision 병렬화 효과 즉시 반영.
    if rng:
        def _kr(sec):
            m, s = divmod(int(sec), 60)
            return (f"{m}분 {s}초" if s else f"{m}분") if m else f"{s}초"
        out["range_text"] = f"보통 {_kr(rng[0])}~{_kr(rng[1])} 걸려요"
    # 느림 사유(정직 안내) — ① AI 재시도(레이트리밋) 최근 발생 시 그 사유, ② p90 초과 시 일반 안내.
    #    사용자가 '뭘 하는지·왜 느린지' 알아 새로고침을 안 누르게.
    try:
        import time as _tm
        from app import llm as _llm
        if pr.get("status") == "running" and _llm.LAST_SLOW_TS and (_tm.time() - _llm.LAST_SLOW_TS) < 25:
            out["slow"] = _llm.LAST_SLOW_REASON or "AI 응답을 기다리는 중이에요"
    except Exception:
        pass
    try:
        from datetime import datetime
        started = pr.get("started_at")
        if not out.get("slow") and rng and started and pr.get("status") == "running":
            elapsed = (datetime.utcnow() - datetime.fromisoformat(started[:19])).total_seconds()
            if elapsed > rng[1]:
                out["slow"] = "평소보다 오래 걸리고 있어요 — 사진이 많거나 요청이 몰렸어요 (새로고침 안 하셔도 돼요)"
    except Exception:
        pass
    if pr.get("status") == "failed":
        out["error_note"] = "생성이 중단됐어요 — 다시 시도해 주세요"
    return out


@app.get("/me/gen-progress")
def me_gen_progress(request: Request):
    """사용자 홈 진행률 폴링 — 정직한 단계 표시(블로그·영상·재생성 공통 컴포넌트)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"status": "idle"})
    t = _ensure_user_tenant(u)
    return JSONResponse(_progress_payload(t))


@app.post("/me/video/make")
async def me_video_make(request: Request, asset_id: str = Form(""), platforms: str = Form(""),
                        hero: str = Form(""), photos: str = Form("")):
    """영상 온디맨드 — 홈에서 플랫폼(shorts·reels·naver) 골라 요청 → 백그라운드 렌더.
    hero: 영상 대표 사진 파일명(선택, 2026-07-31) — 만들기 시점에 골라도 반영(구세트 포함).
    빈값은 '기존 선택 유지', 'auto'는 'AI가 알아서'로 초기화.
    photos: 영상에 쓸 사진 선택(쉼표 파일명, 2026-07-31 사장님 지시) — 'all'/빈값=전체."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False, "error": "로그인이 필요해요"}, status_code=401)
    t = _ensure_user_tenant(u)
    a = db.get_asset(asset_id)
    if not a or getattr(a, "tenant_id", None) != t.id:
        return JSONResponse({"ok": False, "error": "내 콘텐츠가 아니에요"}, status_code=404)
    hero = os.path.basename((hero or "").strip())
    blog = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
    if blog:
        _names = {os.path.basename(p) for p in (blog.payload.get("image_paths") or [])}
        _upd = {}
        if hero == "auto":
            _upd["hero_photo"] = ""
        elif hero and hero in _names:
            _upd["hero_photo"] = hero
        _ph = (photos or "").strip()
        if _ph == "all":
            _upd["video_photos"] = []                  # 전체 사용(선택 해제)
        elif _ph:
            _sel = [os.path.basename(x.strip()) for x in _ph.split(",") if x.strip()]
            _sel = [x for x in _sel if x in _names]
            if _sel:
                _upd["video_photos"] = _sel
        if _upd:
            db.update_piece_payload(blog.id, _upd)
    from app.services.ingest import request_video_bundle
    ok2, err2 = request_video_bundle(t, asset_id, {x.strip() for x in platforms.split(",") if x.strip()})
    return JSONResponse({"ok": ok2, "error": err2})


@app.get("/me/video/photos")
def me_video_photos(request: Request, asset_id: str = ""):
    """영상 만들기 직전 사진 선택 UI용 — 세트 사진 목록(+현재 대표). 소유 검증."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False}, status_code=401)
    t = _ensure_user_tenant(u)
    a = db.get_asset(asset_id)
    if not a or getattr(a, "tenant_id", None) != t.id:
        return JSONResponse({"ok": False}, status_code=404)
    blog = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
    ips = (blog.payload.get("image_paths") if blog else None) or []
    photos = [{"name": os.path.basename(p), "url": f"/dl/{asset_id}/{os.path.basename(p)}"}
              for p in ips]
    # 🎬 영상 사진 상한을 화면에 알린다(2026-08-02 사장님 지적) — 상한은 서버가 이미 걸고
    #   있었는데(제작시간 때문에 9장) 화면은 17장을 고르게 두고 '17장으로 영상 만들기'라고 적었다.
    #   지키지 않을 약속을 화면에 쓰는 건 정직 게이트 위반이다. 상한은 파이프라인 단일 소스에서 읽는다.
    try:
        from app.services.ingest import _VIDEO_MAX_PHOTOS as _cap
    except Exception:
        _cap = 9
    return JSONResponse({"ok": True, "photos": photos,
                         "max_photos": int(_cap),
                         "video_photos": (blog.payload.get("video_photos") or []) if blog else [],
                         "hero": os.path.basename((blog.payload.get("hero_photo") or "")) if blog else ""})


@app.get("/me/video/status")
def me_video_status(request: Request, asset_id: str = ""):
    """영상 채널 상태 폴링(홈 카드) — shorts/reels/naver: not_requested·generating·done·failed."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False}, status_code=401)
    t = _ensure_user_tenant(u)
    a = db.get_asset(asset_id)
    if not a or getattr(a, "tenant_id", None) != t.id:
        return JSONResponse({"ok": False}, status_code=404)
    ps = db.get_set_pieces(asset_id)
    blog = next((p for p in ps if p.kind.value == "blog"), None)
    cs = (blog.payload.get("channel_status") or {}) if blog else {}
    vj = (blog.payload.get("video_job") or {}) if blog else {}
    return JSONResponse({"ok": True, "status": {
        ch: ((cs.get(ch) or {}).get("status") or "") for ch in ("shorts", "reels", "naver")},
        "job": {"status": vj.get("status") or "", "stage": vj.get("stage") or "",
                "error": (vj.get("error") or "")[:120]}})


@app.get("/admin/video-status/{asset_id}")
def admin_video_status(asset_id: str):
    """운영 진단 — 세트의 video_job·channel_status 원본(실패 사유 확인용)."""
    ps = db.get_set_pieces(asset_id)
    blog = next((p for p in ps if p.kind.value == "blog"), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    # 🔍 실패 사유 표면화(2026-08-01) — payload에는 남는데 어디서도 안 보여 원인 추적 때마다
    #   조각을 일일이 뒤져야 했다. 진단 경로에서 바로 읽히게 한다(쇼츠 조각 전부 나열 — 중복 감지용).
    _shorts = [p for p in ps if p.kind.value == "short"]
    _rows = []
    for _sp in _shorts:
        _nv = _sp.payload.get("naver_video") or {}
        _rows.append({"piece": _sp.id[:8],
                      "naver_ok": bool(_nv.get("path")),
                      "naver_note": _nv.get("_build_note") or "",
                      # 실제로 영상에 구워진 자막(진단에서 다른 산출물과 혼동하지 않게)
                      "naver_opening": _nv.get("opening") or "",
                      "naver_scenes": (_nv.get("scene_texts") or [])[:10],
                      # 화면-자막 짝(2026-08-02) — 일치 검증을 눈이 아니라 기록으로 한다
                      "naver_pairs": (_nv.get("scene_pairs") or [])[:12],
                      "naver_photo_locked": _nv.get("photo_locked"),
                      "naver_selling": _nv.get("selling") or {},   # 판매 문장 교체 결과·반려 사유
                      "naver_dur": _nv.get("duration_sec"),
                      "shorts_ok": bool(_sp.payload.get("video_path")),
                      "scene_note": (_sp.payload.get("_scene_note") or "")[:200],
                      "assemble_note": (_sp.payload.get("assemble_note") or "")[:200]})
    return JSONResponse({"ok": True, "video_job": blog.payload.get("video_job") or {},
                         "channel_status": blog.payload.get("channel_status") or {},
                         "has_short": bool(_shorts), "shorts_n": len(_shorts), "shorts": _rows})


@app.post("/admin/gowatch/seed-demo")
def admin_gowatch_seed_demo(request: Request, industry: str = "꽃집", keyword: str = "", region: str = ""):
    """W6 검증용 — 시드 아닌 업종의 모의 발행 데이터 생성(tenant+블로그 조각+발행기록). 동일 코드 경로 증명용.
    업종/키워드/지역은 파라미터(데이터) — 업종별 코드 분기 없음. 반환: publish_id·tenant_id."""
    from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus
    import uuid as _uuid
    kw = keyword or (industry + " " + (region or "")).strip()
    t = db.create_tenant(name=f"[데모]{industry}", industry=industry, region=region or "")
    pid = "demo_" + _uuid.uuid4().hex[:12]
    body = ("\n\n".join([
        f"{industry} 이용을 고민하는 분들이 가장 많이 묻는 것부터 정리했어요.",
        "첫째, 무엇을 준비해야 하는지. 처음이면 순서를 몰라 막막하실 텐데 단계별로 짚어드릴게요.",
        "둘째, 비용과 기간. 케이스마다 다르지만 대략의 기준을 알면 계획을 세우기 쉬워요.",
        "셋째, 자주 하는 실수. 미리 알면 피할 수 있는 부분을 사례로 담았어요.",
        "궁금한 점이 있으면 편하게 문의 주세요. 상황에 맞게 안내해 드릴게요.",
    ]))
    piece = ContentPiece(id=pid, tenant_id=t.id, asset_id="", channel=Channel.NAVER_BLOG,
                         kind=ContentKind.BLOG,
                         payload={"title": f"{kw} 준비할 때 꼭 알아야 할 것 3가지",
                                  "meta_description": f"{kw} 처음이라면 이 글부터",
                                  "body": body, "target_keywords": [kw]},
                         status=ContentStatus.PUBLISHED)
    db.save_piece(piece)
    url = f"https://blog.naver.com/demo_{industry}/{_uuid.uuid4().hex[:11]}"
    db.record_blog_publish(t.id, pid, url=url, matched_by="manual",
                           post_title=piece.payload["title"], target_kw=kw)
    return JSONResponse({"ok": True, "publish_id": pid, "tenant_id": t.id, "keyword": kw, "industry": industry})


@app.get("/me/observations", response_class=HTMLResponse)
def me_observations(request: Request):
    """D3 관측 현황 탭(리포트 하위) — 발행 글별 순위·지면·색인 표. null=측정 준비 중."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    from app.services import dashboard_gowatch as _dg
    body = _dg.render_d3(t.id)
    page = (
        "<div class='max-w-2xl mx-auto px-4 py-6'>"
        "<div class='flex items-center gap-2 mb-4'><a href='/me' class='text-slate-400 text-sm'>← 작업실</a>"
        "<h1 class='font-bold text-lg'>관측 현황</h1></div>"
        f"<div class='bg-white rounded-2xl border border-slate-100 p-4'>{body}</div></div>")
    return HTMLResponse(page)


@app.get("/me/proposal/{aid}", response_class=HTMLResponse)
def me_proposal(request: Request, aid: str):
    """D1 카드 '개선 글 보기' — 개선판 미리보기 + 복붙 키트 + 발행 완료(consumed)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    p = db.get_proposal(aid)
    if not p or p.get("tenant_id") != t.id:
        return HTMLResponse("<div class='p-6 text-slate-500'>제안을 찾을 수 없어요. <a href='/me' class='text-indigo-600'>작업실로</a></div>", status_code=404)
    card = p.get("card") or {}
    piece = db.get_piece(p.get("piece_id") or "") if p.get("piece_id") else None
    kit = ""
    if piece:
        pl = piece.payload or {}
        title = esc(pl.get("title") or "")
        bodytext = pl.get("body") or ""
        kit = (
            "<div class='mb-2 text-xs font-bold text-slate-500'>보강한 글 (수정 발행용 — 원 글 이력은 보존돼요)</div>"
            f"<input class='w-full border border-slate-200 rounded-xl px-3 py-2 text-sm font-bold mb-2' value=\"{title}\" readonly onclick='this.select()'>"
            f"<textarea class='w-full border border-slate-200 rounded-xl px-3 py-2 text-sm h-72' readonly onclick='this.select()'>{esc(bodytext)}</textarea>"
            "<div class='text-xs text-slate-400 mt-1'>탭해서 전체 선택 → 복사해서 블로그에 붙여넣고 발행하세요. (자동 발행은 하지 않아요)</div>")
    else:
        kit = "<div class='text-sm text-slate-600'>이 글을 보강해 다시 올리면 회복에 도움이 돼요.</div>"
    page = (
        "<div class='max-w-2xl mx-auto px-4 py-6'>"
        "<a href='/me' class='text-slate-400 text-sm'>← 작업실</a>"
        f"<h1 class='font-bold text-lg mt-2 mb-1'>{esc(card.get('headline') or '개선 제안')}</h1>"
        f"<div class='text-sm text-slate-500 mb-4'>{esc(card.get('sub') or '')}</div>"
        f"<div class='bg-white rounded-2xl border border-slate-100 p-4'>{kit}</div>"
        f"<form method=post action='/me/proposal/{esc(aid)}/done' class='mt-4'>"
        "<button class='w-full bg-emerald-600 text-white font-bold py-3 rounded-xl'>발행 완료 — 이 제안 닫기</button></form></div>")
    return HTMLResponse(page)


@app.post("/me/proposal/{aid}/done")
def me_proposal_done(request: Request, aid: str):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    from urllib.parse import quote as _q
    p = db.get_proposal(aid)
    if p and p.get("tenant_id") == t.id:
        db.mark_proposal(aid, "consumed")
        try:      # gowatch에 소비 확정 통지(gowatch가 자기 테이블에 기록 → PHASE3 회복 추적 대상)
            from app.services import gowatch_client
            gowatch_client.set_status(aid, "consumed")
        except Exception:
            pass
    return RedirectResponse("/me?ok=" + _q("제안을 닫았어요. 발행하신 글의 회복은 계속 지켜볼게요."), status_code=303)


@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    # 로그인 상태면 첫 화면 = 사용자 대시보드(작업실), 비로그인이면 마케팅 랜딩
    if auth.current_user(request):
        return RedirectResponse("/me", status_code=303)
    from app import landing
    return landing.render()


@app.get("/robots.txt")
def robots():
    base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
    body = (f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /me\nDisallow: /u/\n"
            f"Sitemap: {base}/sitemap.xml\n")
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap():
    base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
    urls = ["/", "/privacy"]
    items = "".join(f"<url><loc>{base}{u}</loc><changefreq>weekly</changefreq>"
                    f"<priority>{'1.0' if u == '/' else '0.5'}</priority></url>" for u in urls)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + items + '</urlset>')
    return Response(xml, media_type="application/xml")


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    from app import landing
    return landing.privacy()


app.include_router(kakao_router())
app.include_router(google_router())

_DEMO_HITS: dict = {}   # ip -> [timestamps] (무료 체험 rate limit)


# ── 대시보드 공통 스타일(대시보드 톤 A1) — 랜딩의 아이콘·톤 재사용(중복 정의 금지) ──
# 규칙: 보라 1색(#6366F1)·상승만 초록·흰 배경+#F9FAFB 구분·카드 흰+#E5E7EB+16px·아이콘 연보라 원형
def _ic(name: str, cls: str = "w-4 h-4") -> str:
    from app import landing as _l
    return _l._icon(name, cls)


def _icchip(name: str, tone: str = "indigo") -> str:
    from app import landing as _l
    return _l._icon_chip(name, tone)


_BTN = "bg-indigo-600 hover:bg-indigo-700 text-white font-bold rounded-xl transition"
_CARD = "bg-white border border-slate-200 rounded-2xl"


def _client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "") or "unknown")


def _is_dev_ip(ip: str) -> bool:
    """개발자 IP 예외 — SHOPCAST_DEV_IPS(콤마 구분)에 등록된 IP만 무료 한도 미적용.
    하드코딩 금지·환경변수 전용. 일반 사용자는 기존 한도 그대로(전체 무제한 금지)."""
    devs = {x.strip() for x in os.environ.get("SHOPCAST_DEV_IPS", "").split(",") if x.strip()}
    return bool(devs) and ip in devs


@app.get("/api/whoami")
def api_whoami(request: Request):
    """접속 IP 확인 — SHOPCAST_DEV_IPS에 넣을 값 확인용."""
    ip = _client_ip(request)
    return JSONResponse({"ip": ip, "dev": _is_dev_ip(ip)})


@app.post("/api/demo")
async def api_demo(request: Request, industry: str = Form(""), note: str = Form(""),
                   biz_type: str = Form("local"), marketplace: str = Form(""),
                   search_kw: str = Form(""), purpose: str = Form(""),
                   target_kw: str = Form(""), target_vol: str = Form(""),
                   confirmed: str = Form(""), vision_analysis: str = Form(""),
                   answers: str = Form(""), experience: str = Form(""),
                   photos: list[UploadFile] = File(None)):
    """랜딩 데모 — 미가입자는 '실제 생성 티저(흐리게)'로 가입 유도. 로그인 회원은 작업실로."""
    u = auth.current_user(request)
    _dev = _is_dev_ip(_client_ip(request))           # 개발자 IP — 무료 한도 미적용(env 등록 IP만)
    if u:                                            # 로그인 회원 → 작업실에서 실제 생성
        used = u.get("free_used") or 0
        free = (u.get("plan") or "free") == "free"
        if free and used >= FREE_LIMIT and not _dev:
            from app import config as _cfg
            return JSONResponse({"limit": True,
                                 "message": (f"무료 {FREE_LIMIT}회를 모두 사용했어요. 방금 만든 품질 그대로 계속하려면 "
                                             f"베이직 월 {_cfg.PRICE_BASIC:,}원 — 순위 성장 추적까지 열려요.")})
        left = (FREE_LIMIT - used) if free else None
        return JSONResponse({"go_dashboard": True,
                             "message": "내 작업실에서 사진을 올리면 바로 만들어드려요!"
                                        + (f" (무료 {left}회 남음)" if left is not None else "")})
    # 미로그인 → 실제 생성 후 '흐리게' 미리보기(티저)로 가입 유도
    if not (industry or "").strip():
        return JSONResponse({"require_signup": True, "message": "업종/상품을 입력하면 실제로 만들어 보여드려요!"})
    ip = _client_ip(request)
    if not _dev and db.demo_ip_count(ip) >= 2:       # 무료 미리보기 2회 → 그다음 가입 유도(개발자 IP 예외)
        return JSONResponse({"require_signup": True, "reason": "ip_limit",   # 프론트 명시 안내용
                             "message": "무료 미리보기 2회를 다 보셨어요! 가입하면 5채널 전부 + 영상까지 무료로 만들어드려요 🎁"})
    if not _dev:
        db.incr_demo_ip(ip)                          # 선예약(연타로 한도 우회 방지) — 실패 시 백그라운드에서 환불
    imgs = await _read_image_uploads(photos)
    full_note = (note or "").strip()
    if purpose.strip():                              # 목적 → 생성 프롬프트에 반영(글·영상 톤↑)
        full_note = (full_note + f" | 콘텐츠 목적: {purpose.strip()}").strip(" |")
    # 진단→생성 연결: 진단의 미노출 키워드가 넘어오면 그 키워드를 겨냥해 생성 + 손실 프레이밍(전환 PHASE 2)
    target_kw = (target_kw or "").strip()[:40]
    try:
        target_vol_n = max(0, int(float(target_vol or 0)))
    except Exception:
        target_vol_n = 0
    if target_kw:
        full_note = (full_note + f" | 타겟 키워드(미노출 진단): '{target_kw}' — 제목·첫문장에 자연스럽게 반영").strip(" |")
    # 스마트 입력(콘텐츠생성 PHASE 4) — 확인된 사진내용·질문답·경험을 생성에 구조 주입
    from app.services import smart_intake
    intake = {"confirmed": confirmed.strip()[:120],
              "analysis": (vision_analysis or "").strip()[:12000],
              "answers": smart_intake.parse_answers(answers),
              "experience": experience.strip()[:200]}
    _level = smart_intake.enrichment_level(intake["confirmed"], intake["answers"], intake["experience"])
    # 생성은 LLM 3~4콜로 60~150초 — 동기 응답은 Cloudflare 100초 한도에 잘려
    # '진행바만 돌고 결과 무소식'이 됨(버그1 원인①) → 백그라운드 잡 + 폴링으로 전환.
    import threading
    import time as _time
    import uuid as _uuid
    job = _uuid.uuid4().hex[:12]
    with _demo_jobs_lock:
        _demo_jobs[job] = {"status": "running", "ts": _time.time()}

    def _run_demo():
        try:
            from app.services import teaser as teaser_svc
            _t, _a, pieces, brief = teaser_svc.run_teaser(industry, biz_type, full_note, imgs, intake=intake)
            if not pieces:
                # 전 채널 실패 — generate_for가 개별 예외를 삼키므로, LLM 1회 프로브로
                # 진짜 원인(401/크레딧/429)을 끌어올려 분류(진단 가능하게). 무키면 더미라 통과.
                from app import llm as _llm
                _llm.call("ping", max_tokens=16)
                raise RuntimeError("no pieces")
            remaining = 2 if _dev else max(0, 2 - db.demo_ip_count(ip))
            html = _teaser_html(pieces, brief, _a, remaining,
                                target_kw=target_kw, target_vol=target_vol_n, enrichment=_level)
            with _demo_jobs_lock:
                _demo_jobs[job] = {"status": "done", "html": html, "ts": _time.time(),
                                   "tenant": _t}   # 이관 청구권용(가입 시 결과물 인계)
        except Exception as e:
            if not _dev:
                db.decr_demo_ip(ip)                  # 선예약 환불 — 실패는 한도 미소모(공정)
            import logging
            logging.exception("[teaser] 실패 job=%s", job)
            # 에러 분류(진단용) — 원인 유실 방지: 폴링 응답에 coarse 카테고리로 노출(상세는 로그)
            en, es = type(e).__name__, str(e).lower()
            if "authentication" in es or en == "AuthenticationError":
                cat = "auth"
            elif "credit" in es or "billing" in es or "purchase" in es:
                cat = "credit"
            elif en == "RateLimitError" or "rate_limit" in es or "429" in es:
                cat = "rate"
            elif en == "RuntimeError" and "no pieces" in es:
                cat = "no_pieces"
            else:
                cat = en[:40]
            with _demo_jobs_lock:
                _demo_jobs[job] = {"status": "error", "cat": cat, "ts": _time.time()}
    threading.Thread(target=_run_demo, daemon=True).start()
    return JSONResponse({"job": job})


_demo_jobs: dict = {}                 # job_id → {status: running|done|error, html} (1 replica 전제)
_demo_jobs_lock = __import__("threading").Lock()


@app.get("/api/demo/result/{job}")
def api_demo_result(job: str):
    """무료 생성 폴링 — 완료되면 teaser_html 반환(버그1: 무소식 금지, 실패도 명시)."""
    import time as _time
    with _demo_jobs_lock:
        # 오래된 잡 정리(30분+) — 메모리 누수 방지
        for k in [k for k, v in _demo_jobs.items() if _time.time() - v.get("ts", 0) > 1800]:
            _demo_jobs.pop(k, None)
        j = _demo_jobs.get(job)
    if not j:
        return JSONResponse({"error": "생성 정보를 찾지 못했어요. 다시 시도해 주세요.", "retry": True})
    if j["status"] == "running":
        return JSONResponse({"ready": False})
    if j["status"] == "error":
        cat = j.get("cat", "")
        msg = {"auth": "AI 생성 서비스 연결에 문제가 있어요 — 운영자가 확인 중이에요. 잠시 후 다시 시도해 주세요.",
               "credit": "AI 생성 서비스 점검 중이에요 — 운영자가 확인 중이에요. 잠시 후 다시 시도해 주세요.",
               "rate": "지금 생성이 몰렸어요. 1~2분 뒤 다시 시도해 주세요."}.get(
            cat, "생성에 문제가 있었어요. 잠시 후 다시 시도해 주세요.")
        return JSONResponse({"error": msg, "retry": True, "code": cat})
    _resp = JSONResponse({"ready": True, "teaser": True, "teaser_html": j.get("html", "")})
    if j.get("tenant"):   # 🎁 티저 이관(2026-07-31): 가입 시 이 결과물을 그대로 넘겨받는 청구권 쿠키
        _resp.set_cookie("demo_claim", j["tenant"], max_age=86400, samesite="lax",
                         secure=auth.cookie_secure())
    return _resp


def _img_thumb_data_uri(path, max_px: int = 640) -> str:
    """업로드 사진 → 작은 base64 썸네일(data URI). 로컬 없으면 R2에서 가져옴. 실패 시 ''."""
    try:
        from PIL import Image
        import io
        import base64
        data = None
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
        elif path:                                   # 로컬 삭제됨(R2 이관) → R2에서 다운로드
            from app import storage as _st
            if _st.r2_configured():
                import urllib.request
                key = os.path.relpath(path, _st.STORAGE_DIR).replace(os.sep, "/")
                url = os.environ["R2_PUBLIC_URL"].rstrip("/") + "/" + key
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=12).read()
        if not data:
            return ""
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _teaser_html(pieces, brief, asset_id, remaining: int = 0,
                 target_kw: str = "", target_vol: int = 0, enrichment: str = "bare") -> str:
    """미가입 무료 체험 결과 — '보여주되 다 주지 않는다'(전환 PHASE 1·2).
    블로그 글은 대부분 노출(품질 증명 = 미끼), 영상은 8초 워터마크 미리보기,
    5채널 중 2개(블로그+인스타)만 공개 — 완성본·다운로드·발행·전체 채널은 가입 뒤(훅).
    정직성: 잠긴 채널도 '실제로 생성됨'만 표기, 가짜 급함 없이 남은 무료 횟수만 표시."""
    import re as _re
    by = {p.kind.value: p for p in pieces}
    imgs = (next((p.payload.get("image_paths") for p in pieces
                 if p.kind.value == "blog" and p.payload.get("image_paths")), None)
            or next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or [])
    # ★ 블로그 피스 우선(실측 버그): 다듬기 병렬화로 저장 순서가 뒤섞여 X 피스(발행용 4장 제한)가
    #   먼저 잡히면 그리드·ZIP·재정렬이 전부 4장으로 좁아짐 — 16장 중 4장만 다운로드된 원인.
    thumbs = [x for x in (_img_thumb_data_uri(p) for p in imgs[:6]) if x]
    photos = (("<div class='flex gap-2 overflow-x-auto pb-1 mb-3'>"
               + "".join(f"<img src='{u}' class='h-24 w-24 object-cover rounded-lg flex-shrink-0'>" for u in thumbs)
               + "</div>") if thumbs else "")

    def card(label, badge, inner, hi=False, wide=False):
        """채널 카드 — 모바일: 가로 스와이프(80% 폭·스냅), 데스크탑: auto-fit 그리드(3~4열).
        flex-col + 마지막 요소 mt-auto로 같은 행 카드 높이·하단 CTA 정렬 통일.
        wide=True: 데스크탑에서 행 전체 폭 배너(잔여 칸 빈 공간 방지, 무료그리드 D1)."""
        ring = "border-2 border-indigo-300" if hi else "border border-slate-200"
        span = " md:col-span-full" if wide else ""
        return (f"<div class='bg-white {ring} rounded-2xl p-4 min-w-[80%] snap-center flex-shrink-0 "
                f"md:min-w-0 md:flex-shrink flex flex-col{span}'>"
                f"<div class='flex items-center justify-between mb-2'>"
                f"<span class='font-bold text-sm text-slate-700'>{label}</span>"
                f"<span class='text-[10px] font-bold text-indigo-500'>{badge}</span></div>{inner}</div>")

    def blur_lock(next_chunk: str, cta: str = "가입하면 전체 공개") -> str:
        """맛보기 경계(수정2) — 이어지는 내용을 블러로 보여주고 오버레이 CTA. '완성은 못 보게'.
        mt-auto: flex-col 카드에서 하단 고정 → 같은 행 카드들의 CTA 라인 정렬."""
        return ("<div class='relative mt-auto pt-1' aria-hidden='true'>"
                f"<div class='text-xs text-slate-400 whitespace-pre-wrap select-none pointer-events-none' "
                f"style='filter:blur(5px);max-height:88px;overflow:hidden'>{esc(next_chunk)}</div>"
                "<div class='absolute inset-0 flex items-center justify-center' "
                "style='background:linear-gradient(180deg,rgba(255,255,255,.25),rgba(255,255,255,.92) 80%)'>"
                f"<a href='/login/kakao' class='bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold "
                f"px-3.5 py-2 rounded-xl'>{cta} →</a></div></div>")

    cards = []
    # ① 영상 — 첫 카드·강조(최강 훅, 수정3). 병렬 렌더 + 기대감 진행표시, 8초 워터마크 미리보기.
    cards.append(card("유튜브 쇼츠 · 릴스", "8초 미리보기",
        f"<div id='tvid' data-a='{asset_id}'>"
        "<div class='py-5 text-center'>"
        "<div class='text-sm font-bold text-slate-700'>영상까지 자동으로 만들어지고 있어요</div>"
        "<div class='text-xs text-slate-400 mt-1'>첫 3초 훅 · 음성 나레이션 · 자막까지 — 완성되면 여기 바로 떠요</div>"
        "<div class='w-full h-1.5 bg-slate-100 rounded-full overflow-hidden mt-3'><div class='h-full bg-indigo-400' style='width:100%;animation:tvp 1.4s ease-in-out infinite'></div></div></div></div>"
        "<style>@keyframes tvp{0%,100%{opacity:.35}50%{opacity:1}}</style>"
        "<script>(function(){var el=document.getElementById('tvid');if(!el||el._p)return;el._p=1;var a=el.dataset.a,n=0;"
        "var iv=setInterval(async function(){n++;if(n>80){clearInterval(iv);el.innerHTML=\"<div class='text-slate-500 text-sm py-4 text-center'>영상은 가입 후 '내 작업실'에서 완성본으로 받을 수 있어요</div>\";return;}"
        "try{var r=await fetch('/api/demo/video/'+a);var d=await r.json();if(d.ready){clearInterval(iv);"
        "el.innerHTML='<video src=\"'+d.url+'\" controls autoplay muted loop playsinline class=\"w-full rounded-xl bg-black\" style=\"max-height:300px\"></video>'"
        "+'<div class=\"flex items-center justify-between mt-2\"><span class=\"text-xs text-slate-400\">완성본(전체 길이·워터마크 없음)은 가입 후</span>"
        "<a href=\"/login/kakao\" class=\"text-xs font-bold text-indigo-600\">완성본 받기 →</a></div>';}}catch(e){}},3000);})();</script>",
        hi=True))
    # ② 네이버 블로그 — 앞 ~32%만 선명(품질 증명), 이어지는 부분 블러+오버레이(수정2: 완성은 못 보게)
    blog = by.get("blog")
    if blog:
        body = _re.sub(r"\[사진\d+\]", "", blog.payload.get("body", "")).strip()
        cut = max(250, int(len(body) * 0.32))
        shown, hidden = body[:cut], body[cut:cut + 260]
        # 사장님 이야기 하이라이트(A2) — '내가 쓴 한 줄이 글이 됐다' 실감(전환 훅)
        _story = (blog.payload.get("owner_story") or "").strip()
        story_badge = (("<div class='bg-violet-50 border border-violet-100 rounded-lg px-2.5 py-1.5 mb-2'>"
                        "<span class='text-[10px] font-bold text-violet-500'>사장님 이야기가 글이 됐어요</span>"
                        f"<div class='text-xs text-violet-800'>“{esc(_story[:80])}”</div></div>") if _story else "")
        cards.append(card("네이버 블로그", "도입부 미리보기",
            f"<div class='font-bold text-slate-800 text-sm mb-1'>{esc(blog.payload.get('title',''))}</div>"
            + story_badge
            + f"<div class='text-slate-600 text-xs whitespace-pre-wrap max-h-36 overflow-hidden'>{esc(shown)}</div>"
            + blur_lock(hidden, "이어지는 본문은 가입 후")))
    # ③ 인스타그램 — 첫 훅만, 나머지 블러(수정2)
    cap = by.get("caption")
    if cap:
        txt = (cap.payload.get("text") or "").strip()
        cards.append(card("인스타그램", "훅 미리보기",
            f"<div class='text-slate-700 text-sm whitespace-pre-wrap'>{esc(txt[:110])}</div>"
            + blur_lock(txt[110:110 + 200])))
    # ④ 잠긴 채널 — 실제로 생성된 것만 '생성 완료'로 정직하게 표기(무료경계 PHASE 5)
    x_label = ("X (트위터) — 생성 완료, 가입하면 열려요" if by.get("x_post")
               else "X (트위터) — 가입 후 생성")
    locked_items = "".join(
        f"<div class='flex items-center gap-2 text-sm text-slate-500 py-1.5 border-b border-slate-100'>"
        f"<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.8' class='w-4 h-4 text-slate-400'>"
        f"<rect x='3' y='11' width='18' height='11' rx='2'/><path d='M7 11V7a5 5 0 0 1 10 0v4'/></svg>{t}</div>"
        for t in [x_label,
                  "인스타 캐러셀 카드 — 가입 후 생성",
                  "영상 완성본 + 피드 규격(1:1·4:5) — 가입 후",
                  "전체 다운로드(ZIP) · 네이버 발행 도우미 — 가입 후"])
    # 배너형(D1): 4카드가 3+1로 배치될 때 둘째 줄 빈 칸 방지 — 행 전체 폭 + 항목 2열
    cards.append(card("+ 나머지 채널", "가입하면 전부",
        f"<div class='md:grid md:grid-cols-2 md:gap-x-6'>{locked_items}</div>"
        "<div class='text-xs text-slate-400 mt-auto pt-2'>가입하면 5채널 전부 + 완성본 다운로드 (무료 2회)</div>",
        wide=True))

    # 모바일: 가로 스와이프 캐러셀(스냅) / 데스크탑: .tz-grid = auto-fit(minmax 280px) 3~4열 자동
    grid = ("<div class='tz-grid flex gap-3 overflow-x-auto snap-x snap-mandatory pb-2 -mx-1 px-1 mb-2'>"
            + "".join(cards) + "</div>"
            "<div class='md:hidden text-center text-[10px] text-slate-400 mb-3'>← 옆으로 넘겨서 채널별 결과 보기 →</div>")
    # 손실 프레이밍(전환 PHASE 2) — 진단의 미노출 키워드로 만든 글이면 실측 검색량 근거로
    loss = ""
    if target_kw:
        vol_txt = f" — 그 검색량(월 {target_vol:,}회)" if target_vol else ""
        loss = (f"<div class='bg-white border border-indigo-200 rounded-xl px-4 py-3 mb-3 text-sm text-slate-700'>"
                f"이 글은 진단에서 <b>미노출</b>로 나온 <b>'{esc(target_kw)}'</b>를 겨냥했어요. "
                f"지금 발행하면{vol_txt} 잡으러 갈 수 있어요.</div>")
    # 정보 부실 → 재생성 유도(전환 PHASE 6): "더 주면 이렇게 좋아져요" — 사실 기반(D.I.A.+ 근거)
    enrich_nudge = ""
    if enrichment == "bare" and remaining > 0:
        enrich_nudge = ("<div class='bg-white border border-slate-200 rounded-xl px-4 py-3 mb-3 text-sm'>"
                        "<div class='font-bold text-slate-700 mb-0.5'>이번 글은 사진만으로 만들었어요</div>"
                        "<div class='text-slate-500 text-xs mb-2'>가격·소요시간·경험 한 줄만 넣으면 네이버가 좋아하는 "
                        "'실제 경험 글'(D.I.A.+)이 돼서 훨씬 구체적으로 좋아져요.</div>"
                        "<button type=button onclick=\"var q=document.getElementById('d_questions');"
                        "var i=document.getElementById('d_ind');if(q&&i&&window.intakeQuestionsUI)intakeQuestionsUI(q,i.value,'local','','d_exp');"
                        "setTimeout(function(){var dt=q&&q.querySelector('details');if(dt)dt.open=true;},700);"   # 기본접힘 → 유도 시 펼침
                        "var t=document.getElementById('herodemo');if(t)t.scrollIntoView({behavior:'smooth',block:'center'});\" "
                        "class='w-full py-2.5 rounded-xl bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-sm font-bold transition'>"
                        f"정보 넣고 다시 만들기 (미리보기 {remaining}회 남음) →</button></div>")
    if remaining > 0:
        cta = (loss + enrich_nudge
               + "<a href='/login/kakao' class='block text-center py-3.5 rounded-xl font-extrabold mb-2' style='background:#FEE500;color:#191600'>"
               "이 글 전체 + 영상 + 5채널 받기 → 무료 가입</a>"
               f"<div class='text-center text-slate-500 text-sm'>가입하면 <b class='text-indigo-600'>무료 2회</b> 전체 생성 · "
               f"미리보기 <b class='text-indigo-600'>{remaining}회</b> 남음</div>")
    else:
        cta = (loss
               + "<div class='text-center text-slate-700 text-sm font-bold mb-2'>무료 미리보기 2회를 다 보셨어요 — 방금 그 품질 그대로, 가입하면 전체를 받아요</div>"
               "<a href='/login/kakao' class='block text-center py-3.5 rounded-xl font-extrabold mb-2' style='background:#FEE500;color:#191600'>카카오로 가입하고 전체 받기 (무료 2회)</a>"
               "<a href='/login/google' class='block text-center py-3 rounded-xl font-bold bg-white border border-slate-200 text-slate-700'>구글로 가입</a>")
    return ("<div class='bg-[#F9FAFB] border border-slate-200 rounded-2xl p-4'>"
            "<div class='text-center mb-1'><span class='inline-block bg-[#EEF2FF] text-indigo-600 text-[10px] font-bold px-2.5 py-1 rounded-full'>5채널 동시 생성</span></div>"
            "<div class='text-center text-slate-900 font-extrabold text-lg mb-1'>사진 한 장으로 이 모든 게 완성됐어요</div>"
            "<div class='text-center text-slate-500 text-xs mb-3'>영상·블로그·인스타·X·캐러셀 — 도입부 미리보기예요. 전체는 가입 후 무료 2회.</div>"
            + photos + grid + cta + "</div>")


def _make_demo_preview(vp: str) -> str | None:
    """데모 영상 → 첫 8초 + 워터마크 미리보기(전환 PHASE 1). 완성본은 가입 후.
    성공 시 preview 경로, 실패 None."""
    out = os.path.join(os.path.dirname(vp), "preview_" + os.path.basename(vp))
    if os.path.exists(out):
        return out
    import subprocess
    try:
        from app.generators.video import _font_path
        font = _font_path() or ""
        fontclause = f":fontfile='{font}'" if font else ""
        vf = (f"drawtext=text='올린다 미리보기':fontcolor=white:fontsize=46{fontclause}"
              ":box=1:boxcolor=black@0.45:boxborderw=16:x=(w-text_w)/2:y=140")
        tmp = out + ".tmp.mp4"
        r = subprocess.run(["ffmpeg", "-y", "-i", vp, "-t", "8", "-vf", vf,
                            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
                            "-c:a", "aac", "-movflags", "+faststart", tmp],
                           capture_output=True, timeout=90)
        if r.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, out)                 # 반쯤 쓰인 파일 서빙 방지
            return out
    except Exception:
        pass
    return None


@app.get("/api/demo/video/{asset_id}")
def demo_video_status(asset_id: str):
    """미가입 데모 영상 폴링 — 완성되면 '8초 워터마크 미리보기'만 제공(완성본은 가입 후)."""
    if not db.asset_is_demo(asset_id):
        return JSONResponse({"ready": False})
    for p in db.get_set_pieces(asset_id):
        vp = p.payload.get("video_path")
        if p.kind.value == "short" and vp and os.path.exists(vp):
            pv = os.path.join(os.path.dirname(vp), "preview_" + os.path.basename(vp))
            if os.path.exists(pv):
                return JSONResponse({"ready": True, "url": f"/d/{asset_id}/f/{os.path.basename(pv)}"})
            import threading
            threading.Thread(target=_make_demo_preview, args=(vp,), daemon=True).start()
            return JSONResponse({"ready": False})    # 다음 폴링에서 미리보기 서빙
    return JSONResponse({"ready": False})


@app.get("/d/{asset_id}/f/{fname}")
def demo_media(asset_id: str, fname: str):
    """데모(무료 체험) 미디어 — is_demo 자산만 공개 서빙.
    영상 완성본은 게이팅(전환 PHASE 1): mp4는 preview_* 미리보기만 공개."""
    import re
    if not db.asset_is_demo(asset_id) or not re.fullmatch(r"[A-Za-z0-9._-]+", fname):
        return HTMLResponse(status_code=404)
    if fname.lower().endswith(".mp4") and not fname.startswith("preview_"):
        return HTMLResponse("영상 완성본은 가입 후 '내 작업실'에서 받을 수 있어요.", status_code=403)
    pieces = db.get_set_pieces(asset_id)
    if not pieces:
        return HTMLResponse(status_code=404)
    path = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), pieces[0].tenant_id, fname)
    if not os.path.exists(path):
        from app import storage as _st
        r2 = _st.r2_media_url(pieces[0].tenant_id, fname)   # 로컬 정리됨 → R2에서 서빙
        return RedirectResponse(r2, status_code=302) if r2 else HTMLResponse(status_code=404)
    ext = fname.rsplit(".", 1)[-1].lower()
    mt = {"mp4": "video/mp4", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mt, filename=fname)


@app.get("/d/{asset_id}.zip")
def demo_zip(asset_id: str, request: Request):
    """데모 전체 ZIP(글+사진+영상) — 완성본 다운로드는 가입 필요(전환 PHASE 1)."""
    if not db.asset_is_demo(asset_id):
        return HTMLResponse(status_code=404)
    if not auth.current_user(request):
        return RedirectResponse("/login?next=/me", status_code=303)
    pieces = db.get_set_pieces(asset_id)
    if not pieces:
        return HTMLResponse(status_code=404)
    _blk = _contamination_block(pieces)
    if _blk:
        return _blk
    imgs = (next((p.payload.get("image_paths") for p in pieces
                 if p.kind.value == "blog" and p.payload.get("image_paths")), None)
            or next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or [])
    # ★ 블로그 피스 우선(실측 버그): 다듬기 병렬화로 저장 순서가 뒤섞여 X 피스(발행용 4장 제한)가
    #   먼저 잡히면 그리드·ZIP·재정렬이 전부 4장으로 좁아짐 — 16장 중 4장만 다운로드된 원인.
    _slug = _set_slug(pieces)
    _nv = _set_naver_video(pieces)
    entries = []
    for p in pieces:
        entries += _piece_pack_entries(p, imgs, prefix=f"{_ch_folder(p)}/", slug=_slug, nv=_nv)
    out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), pieces[0].tenant_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"demo_{asset_id[:8]}.zip")
    _write_zip(out, entries)
    return FileResponse(out, media_type="application/zip", filename="올린다_무료체험.zip")


# ══ 스마트 입력 엔진(콘텐츠생성 개선 PHASE 1~3) — 무료·유료 공용 ══
@app.get("/api/intake/questions")
def intake_questions(request: Request, industry: str = "", biz_type: str = "local", purpose: str = "",
                     hint: str = ""):
    """업종·목적 맞춤 스마트 질문 3~4개 + 경험 유도 1개(전부 선택 입력).
    미정의 업종(프리셋·캐시 없음)은 ensure_profile로 AI 프로필을 1회 생성해 캐시
    (industry_profiles 재사용) → 빵집 같은 업종도 맞춤 질문. 재요청은 LLM 0콜(캐시)."""
    from app.services import smart_intake
    from app.industries import resolve_industry, ensure_profile
    industry = (industry or "").strip()
    if not industry:
        return JSONResponse({"questions": [], "experience": smart_intake.EXPERIENCE_QUESTION,
                             "hint": "업종을 입력하면 맞춤 질문을 보여드려요"})
    # 상호명 입력 커버(버그2): '파리바게뜨'처럼 프로필 매칭 실패 시 사진 추측(hint)에서 업종 추론
    q_industry = industry
    if resolve_industry(industry).key == "generic" and (hint or "").strip():
        inferred = smart_intake.infer_industry_from_text(hint)
        if inferred:
            q_industry = inferred
    preparing = False
    if resolve_industry(industry).key == "generic":
        # 신규 업종 ~20초 지연 개선: 즉시 중립 질문 반환 + 프로필은 백그라운드 생성(방식 b).
        # 캐시 저장 후엔 같은 업종 재요청(같은 사용자 목적변경/재포커스 포함)부터 맞춤 질문 즉시.
        from app import ratelimit
        ip = (request.headers.get("cf-connecting-ip")
              or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "") or "unknown")
        if q_industry == industry and ratelimit.allow("intakeq:" + ip, 4, 20):
            preparing = _spawn_profile_gen(industry)   # 추론도 실패한 진짜 신규 업종만 AI 생성
    out = smart_intake.questions_for(q_industry, biz_type, purpose)
    if preparing:
        out["preparing_custom"] = True                 # (정보용) 맞춤 질문 준비 중 — 다음 조회부터 적용
    return JSONResponse(out)


_intake_gen_busy: set = set()                          # 동일 업종 동시요청 → LLM 중복 콜 방지
_intake_gen_lock = __import__("threading").Lock()


def _spawn_profile_gen(industry: str) -> bool:
    """ensure_profile을 백그라운드로 — 요청을 막지 않음. 이미 생성 중이면 스킵. 시작 여부 반환."""
    key = industry.strip().lower()
    with _intake_gen_lock:
        if key in _intake_gen_busy:
            return True                                # 이미 준비 중
        _intake_gen_busy.add(key)

    def _run():
        try:
            from app.industries import ensure_profile as _ep
            _ep(industry)                              # 성공 시 industry_profiles 캐시 저장
        except Exception:
            pass
        finally:
            with _intake_gen_lock:
                _intake_gen_busy.discard(key)
    import threading
    threading.Thread(target=_run, daemon=True).start()
    return True


# ── 사진 보정 선행(개선 ③) — 사진 선택 직후 원본을 선업로드받아 느린 정리(워터마크·개인정보)를
#    백그라운드로 미리 실행. '만들기' 시점엔 끝나 있어 생성 체감이 준다. 미완료·실패는 기존 경로 폴백(무해).
_INTAKE_STASH: dict = {}
import threading as _stash_th
_INTAKE_STASH_LOCK = _stash_th.Lock()
_INTAKE_STASH_SEM = _stash_th.BoundedSemaphore(
    max(1, int(os.environ.get("SHOPCAST_PHOTO_CONCURRENCY", "4"))))   # 동시 preclean 상한(vision 폭주 방지)


def _stash_dir() -> str:
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "intake_stash")
    os.makedirs(d, exist_ok=True)
    return d


def _stash_prune() -> None:
    """2시간 지난 stash 파일·엔트리 정리(디스크 누수 방지)."""
    import time as _tm
    now = _tm.time()
    try:
        d = _stash_dir()
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if now - os.path.getmtime(fp) > 7200:
                os.remove(fp)
    except Exception:
        pass
    with _INTAKE_STASH_LOCK:
        for k in [k for k, e in _INTAKE_STASH.items() if now - e.get("ts", 0) > 7200]:
            _INTAKE_STASH.pop(k, None)


@app.post("/api/intake/stash")
async def intake_stash(request: Request, token: str = Form(""), photo: UploadFile = File(...)):
    """사진 1장 선업로드 → 전체 해상도 JPEG 정규화 후 preclean(워터마크 제거+PII 마스킹)을 백그라운드 시작.
    반환 key를 폼 제출 시 stash_keys로 되돌려주면 업로드 핸들러가 보정본을 재사용한다."""
    u = auth.current_user(request)
    tenant = db.get_tenant(u["tenant_id"]) if (u and u.get("tenant_id")) else None
    if tenant is None and token.strip():
        tenant, _ = db.get_tenant_by_token(token.strip())
    if tenant is None:
        return JSONResponse({"ok": False}, status_code=401)
    from app import ratelimit
    ip = (request.headers.get("cf-connecting-ip")
          or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "") or "unknown")
    if not ratelimit.allow("stash:" + ip, 80, 600):
        return JSONResponse({"ok": False, "error": "rate"}, status_code=429)
    files = await _read_image_uploads([photo], limit=1)
    if not files:
        return JSONResponse({"ok": False}, status_code=400)
    _stash_prune()
    import time as _tm
    import uuid as _uu
    data, fname = files[0]
    key = _uu.uuid4().hex
    p = os.path.join(_stash_dir(), key + ".jpg")
    with _INTAKE_STASH_LOCK:
        _INTAKE_STASH[key] = {"path": p, "done": False, "cleaned": False,
                              "ts": _tm.time(), "tid": tenant.id}

    def _run():
        entry = _INTAKE_STASH.get(key) or {}
        try:
            with _INTAKE_STASH_SEM:
                # 전체 해상도 유지 JPEG 정규화(HEIC·회전 흡수) — 실패 시 cleaned=False로 두면
                # 제출 시 이 stash를 안 쓰고 원본 경로가 정상 처리(개인정보 마스킹 누락 봉쇄).
                import io as _io
                try:
                    from pillow_heif import register_heif_opener
                    register_heif_opener()
                except Exception:
                    pass
                from PIL import Image as _Im, ImageOps as _IOps
                im = _IOps.exif_transpose(_Im.open(_io.BytesIO(data))).convert("RGB")
                im.save(p, "JPEG", quality=92)
                from app.media import photo_boost as _pb
                _pb.preclean(p)
                entry["cleaned"] = True
        except Exception:
            logging.getLogger("shopcast.stash").exception("[stash] preclean 실패 key=%s", key[:8])
        finally:
            entry["done"] = True
    _stash_th.Thread(target=_run, daemon=True).start()
    return JSONResponse({"ok": True, "key": key})


@app.post("/api/intake/guess")
async def intake_guess(request: Request, industry: str = Form(""), purpose: str = Form(""),
                       photos: list[UploadFile] = File(None)):
    """사진 → AI 선추측(확인용 한 줄) + 분석 전문(PHASE 2). 무료·유료 공용.
    분석 전문은 hidden으로 되돌려받아 생성 시 vision 재호출을 생략(비용 1콜 유지).
    (vision-intent) 가게 맥락 주입 → ①무엇 + ②이 가게 관점 해석·확신도·의도 선택지."""
    from app import ratelimit
    ip = (request.headers.get("cf-connecting-ip")
          or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "") or "unknown")
    if not ratelimit.allow("intake:" + ip, 6, 30):     # 비전 콜 남용 방지
        return JSONResponse({"guess": "", "analysis": ""})
    files = await _read_image_uploads(photos)
    if not files:
        return JSONResponse({"guess": "", "analysis": ""})
    import tempfile
    import uuid as _uuid
    from app.services import smart_intake
    tmp = os.path.join(tempfile.gettempdir(), f"intake_{_uuid.uuid4().hex}")
    os.makedirs(tmp, exist_ok=True)
    paths = []
    try:
        # 축소·정규화(버그1 수정): 원본(수 MB·HEIC)을 그대로 vision에 보내면 다중 사진에서
        # 분석이 18초+ 걸리거나 실패 → 1280px JPEG로 변환(EXIF 회전 반영) 후 분석. 실패 시 원본.
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
        from PIL import Image as _Im, ImageOps as _IOps
        import io as _io
        # 전 장수 분석(사진분석 단일화) — 여기서 만든 per-photo [사진N] 전문을 생성이 재사용 → 생성 시 vision 0콜.
        # vision.analyze_all이 6장 청크 병렬이라 16장도 6장과 체감 지연 거의 동일(상한 30 = vision max).
        for i, (data, fname) in enumerate(files[:30]):
            p = os.path.join(tmp, f"g{i}.jpg")
            try:
                im = _Im.open(_io.BytesIO(data))
                im = _IOps.exif_transpose(im).convert("RGB")
                im.thumbnail((1280, 1280))
                im.save(p, "JPEG", quality=82)
            except Exception:
                ext = (os.path.splitext(fname or "")[1] or ".jpg")[:5]
                p = os.path.join(tmp, f"g{i}{ext}")
                with open(p, "wb") as f:
                    f.write(data)
            paths.append(p)
        # 가게 맥락(vision-intent 1-1·1-3): 로그인=프로필, 무료=입력 업종 텍스트, 미입력=""(해석 보류)
        _u = auth.current_user(request)
        _t = db.get_tenant(_u["tenant_id"]) if (_u and _u.get("tenant_id")) else None
        if _t and (_t.industry or "").strip():
            _bt = {"local": "매장형", "seller": "셀러형", "hybrid": "매장+온라인"}.get(
                (_t.biz_type or "local"), "매장형")
            ctx = f"{_t.name} · {_t.industry} · {_bt}"
            industry = industry.strip() or _t.industry
        else:
            ctx = f"업종/상품: {industry.strip()}" if industry.strip() else ""
        if purpose.strip():
            ctx = (ctx + f" · 이번 글 목적: {purpose.strip()[:30]}").strip(" ·")
        out = smart_intake.guess_from_photos(paths, industry.strip(), context=ctx)
        # tenant 학습 기본값(3-2·3-3): 같은 의도 연속 선택 시 묻지 않고 기본값 표시.
        # 단, 학습값이 이번 사진의 해석·선택지 어디에도 안 비치면(맥락-사진 불일치) 무시하고 다시 묻는다.
        if _t and out.get("confidence") == "low":
            _learned = db.default_intent(_t.id)
            # 불일치 감지(3-3): 학습값은 vision이 제시한 '긍정 후보(선택지)'와 겹칠 때만 적용.
            # 해석문 전체 매칭은 부정 표현("시공과 연결되지 않아요")의 토큰에 오탐 — 선택지로 한정.
            _hay = " ".join(out.get("choices") or [])
            import re as _re2
            _stop = {"이야기", "소개", "관련", "상품", "안내", "홍보"}
            _toks = [w for w in _re2.split(r"[\s·]+", _learned) if len(w) >= 2 and w not in _stop]
            if _learned and _toks and any(w in _hay for w in _toks):
                out["learned_intent"] = _learned
        return JSONResponse(out)
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


@app.get("/api/place/search")
def place_search(q: str = ""):
    """가게명 검색 → 정보 자동입력 후보(네이버 지역검색). 키 없으면 빈 목록."""
    from app.services import place
    return JSONResponse({"items": place.search(q), "configured": place.configured()})


@app.post("/api/rank-check")
async def api_rank_check(request: Request):
    """온보딩/랜딩 '내 가게 현재 순위 즉시진단'(결제 트리거, 성장 PHASE 1).
    업종+지역+상호 → 네이버 현재 순위 + CTA 프레임. 로그인 tenant면 baseline 저장."""
    from app.services import diagnose
    from app import ratelimit
    from app.config import RANK_RATE_PER_MIN, RANK_RATE_PER_HOUR, RANK_CACHE_TTL
    try:
        form = await request.form()
        industry = (form.get("industry") or "").strip()   # 셀러 모드에선 '상품 키워드'
        region = (form.get("region") or "").strip()
        name = (form.get("name") or "").strip()           # 셀러 모드에선 '스토어명'
        mode = (form.get("mode") or "").strip()           # ''(매장) | 'seller'
        brand = (form.get("brand") or "").strip()
    except Exception:
        industry = region = name = mode = brand = ""
    if mode == "seller":
        if not industry:
            return JSONResponse({"error": "상품 키워드를 입력해주세요."}, status_code=400)
    elif not (industry or name):
        return JSONResponse({"error": "업종 또는 상호를 입력해주세요."}, status_code=400)

    # ── 앞단 게이트 ① 동일 상호+지역 TTL 캐시 → 네이버 콜 자체를 절감(레이트리밋과 별개) ──
    ckey = f"{mode}|{industry}|{region}|{name}|{brand}".lower()
    cached = ratelimit.cache_get(ckey, RANK_CACHE_TTL)
    if cached is not None:
        return JSONResponse(cached)                      # 캐시 히트 = 네이버 콜 0 → 한도 미차감

    # ── 앞단 게이트 ② 캐시 미스(=네이버 호출 발생)만 IP 레이트리밋 ──
    ip = (request.headers.get("cf-connecting-ip")
          or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "") or "unknown")
    if not ratelimit.allow(ip, RANK_RATE_PER_MIN, RANK_RATE_PER_HOUR):
        return JSONResponse(
            {"error": "순위 진단이 잠깐 몰렸어요 🙏 1~2분 뒤 다시 눌러주시면 바로 열려요!"},
            status_code=429)

    if mode == "seller":
        result = diagnose.diagnose_product_rank(industry, name, brand)   # 쇼핑검색 40위 스캔
    else:
        result = diagnose.diagnose_rank(industry, region, name)
    # 진단→생성 연결(상위노출 PHASE 1): 미노출 키워드(검색량 큰 순) 상위 3개 = 타겟 콘텐츠 제안
    from app import config as _cfg
    from urllib.parse import quote as _q
    miss_sorted = sorted(result.get("missing") or [], key=lambda s: -(s.get("volume") or 0))
    result["targets"] = [
        {"keyword": s["keyword"], "volume": s.get("volume"),
         "make_href": "/me?target_kw=" + _q(s["keyword"])}
        for s in miss_sorted[:_cfg.TARGET_CONTENT_SUGGEST]]
    ratelimit.cache_set(ckey, result)                    # 같은 가게 반복 진단은 캐시로
    u = auth.current_user(request)
    if u and u.get("tenant_id"):
        diagnose.save_baseline(u["tenant_id"], result)   # before/after 기준점
    return JSONResponse(result)


# ══ 신규기능①: 경쟁사 추적기 ══
@app.post("/api/competitor/scan")
def competitor_scan_now(request: Request):
    """수동 스캔 트리거 — 등록 경쟁사 전부 즉시 조회(플랜 한도 차감, PHASE 3)."""
    from app import gating
    from app.services import competitor
    u = auth.current_user(request)
    blk = gating.check_limit(u, "competitor_scans")
    if blk:
        return JSONResponse(blk, status_code=(401 if blk.get("need_signup") else 402))
    t = _ensure_user_tenant(u)
    comps = db.list_competitors(t.id)
    if not comps:
        return JSONResponse({"error": "먼저 경쟁사를 등록해 주세요.", "empty": True}, status_code=200)
    scans = []
    for comp in comps:
        try:
            scans.append(competitor.scan_competitor(t, comp))
        except Exception:
            import logging
            logging.exception("[competitor] 수동 스캔 실패 id=%s", comp.get("id"))
    gating.consume(u, "competitor_scans")
    return JSONResponse({"scans": scans, "usage": gating.usage_summary(db.get_user(u["id"]), "competitor_scans")})


@app.post("/api/competitor")
async def competitor_add(request: Request):
    """경쟁사 등록 — competitors_max 검사(PHASE 4)."""
    from app import gating, config as _cfg
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "가입하면 경쟁사를 추적할 수 있어요!", "need_signup": True}, status_code=401)
    t = _ensure_user_tenant(u)
    limit = _cfg.plan_limit(u.get("plan") or "free", "competitors_max")
    if limit != -1 and db.count_competitors(t.id) >= limit:
        return JSONResponse({"error": f"등록 가능한 경쟁사 {limit}개를 다 쓰셨어요. 업그레이드하면 더 추가돼요!",
                             "upgrade": True, "cta": "요금제 업그레이드"}, status_code=402)
    form = await request.form()
    name = (form.get("name") or "").strip()
    region = (form.get("region") or t.region or "").strip()
    kws = [k.strip() for k in (form.get("keywords") or "").replace("\n", ",").split(",") if k.strip()]
    if not name:
        return JSONResponse({"error": "경쟁사 상호를 입력해 주세요."}, status_code=400)
    cid = db.create_competitor(t.id, name, region, kws)
    return JSONResponse({"ok": True, "id": cid,
                         "usage": gating.usage_summary(db.get_user(u["id"]), "competitor_scans")})


@app.get("/api/competitor/list")
def competitor_list(request: Request):
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"items": []})
    t = _ensure_user_tenant(u)
    return JSONResponse({"items": db.list_competitors(t.id)})


@app.post("/api/competitor/{cid}/delete")
def competitor_delete(cid: str, request: Request):
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    db.delete_competitor(cid, t.id)
    return JSONResponse({"ok": True})


@app.get("/api/competitor/report")
def competitor_report(request: Request):
    """내 순위 vs 경쟁사 최신 현황 + 역전/뒤처짐 경보(PHASE 4)."""
    from app.services import competitor
    from app import gating
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"cards": [], "alerts": [], "need_signup": True})
    t = _ensure_user_tenant(u)
    comps = db.list_competitors(t.id)
    rep = competitor.report(t, comps)
    rep["usage"] = gating.usage_summary(db.get_user(u["id"]), "competitor_scans")
    return JSONResponse(rep)


@app.get("/me/competitors")
def competitors_page(request: Request):
    """(UI 정리) 경쟁사 페이지 제거 — 경쟁 분석은 백엔드(자동 큐 P3 격차·브리핑 신호)에서만 동작."""
    return RedirectResponse("/me", status_code=303)


@app.post("/api/print/generate")
async def print_generate(request: Request):
    """인쇄물 생성 — 타입·항목·사진 → 렌더 → URL. print_items 한도 차감(PHASE 7)."""
    import asyncio
    import json as _json
    from app import gating
    from app.services import printable
    u = auth.current_user(request)
    blk = gating.check_limit(u, "print_items")
    if blk:
        return JSONResponse(blk, status_code=(401 if blk.get("need_signup") else 402))
    t = _ensure_user_tenant(u)
    form = await request.form()
    ptype = (form.get("type") or "menu").strip()
    if ptype not in printable.PRINT_TYPES:
        ptype = "menu"
    note = (form.get("note") or "").strip()
    try:
        items = _json.loads(form.get("items") or "[]")
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    # 사진(선택) — 저장 + 보정
    photo_path = ""
    ph = form.get("photo")
    if ph is not None and getattr(ph, "filename", ""):
        data = await ph.read()
        if data and len(data) <= MAX_UPLOAD_BYTES:
            photo_path = storage.save_upload(data, ph.filename, t.id)
            try:
                from app.media import photo_boost
                photo_boost.enhance_all([photo_path], t.industry, None)
            except Exception:
                pass

    _with_qr = (form.get("qr") or "1") != "0"          # 매장 QR 삽입 옵션(추적 P4, 기본 켬)
    res = await asyncio.to_thread(printable.generate, ptype, t, items, note, photo_path, "png", _with_qr)
    if not res.get("ok"):
        return JSONResponse({"error": res.get("error", "생성 실패")}, status_code=200)
    jid = db.save_print_job(t.id, ptype, res.get("path", ""), res.get("url") or "",
                            res.get("copy", {}).get("label", ""))
    gating.consume(u, "print_items")
    return JSONResponse({"ok": True, "id": jid, "download": f"/print/file/{jid}",
                         "label": res.get("copy", {}).get("label", ""),
                         "usage": gating.usage_summary(db.get_user(u["id"]), "print_items")})


@app.get("/api/print/list")
def print_list(request: Request):
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"items": []})
    t = _ensure_user_tenant(u)
    jobs = [{"id": j["id"], "label": j.get("label") or j.get("ptype"), "ptype": j.get("ptype"),
             "download": f"/print/file/{j['id']}", "created_at": j.get("created_at")}
            for j in db.list_print_jobs(t.id)]
    return JSONResponse({"items": jobs})


@app.get("/print/file/{jid}")
def print_file(jid: str, request: Request):
    """인쇄물 다운로드 — 소유권 확인 후 로컬/ R2 서빙(PHASE 7)."""
    j = db.get_print_job(jid)
    if not j:
        return HTMLResponse(status_code=404)
    u = auth.current_user(request)
    t = _ensure_user_tenant(u) if u else None
    if not (t and j.get("tenant_id") == t.id):
        return HTMLResponse("<p>권한이 없어요.</p>", status_code=403)
    path = j.get("path") or ""
    if path and os.path.exists(path):
        return FileResponse(path, filename=f"{j.get('label') or 'print'}.png")
    if j.get("url"):
        return RedirectResponse(j["url"], status_code=302)
    return HTMLResponse("<p>파일을 찾을 수 없어요.</p>", status_code=404)


def _print_block(t) -> str:
    """인쇄물 만들기(리뷰 QR·전단·POP) — 플레이스 섹션으로 일원화(UI 정리 1-2). 접힘 기본."""
    try:
        from app import gating
        from app.services import printable
        owner = db.get_user_by_tenant(t.id)
        usage = gating.usage_summary(owner, "print_items") if owner else {"limit": 0, "used": 0, "remaining": 0}
        used_label = ("무제한" if usage["limit"] == -1 else f"{usage['used']}/{usage['limit']}장")
        jobs = db.list_print_jobs(t.id, limit=8)
        type_opts = "".join(f"<option value='{k}'>{esc(v['label'])}</option>" for k, v in printable.PRINT_TYPES.items())
        made = "".join(
            f"<a href='/print/file/{j['id']}' target='_blank' class='flex items-center justify-between bg-white border border-slate-100 rounded-xl px-4 py-2.5 mb-2 hover:shadow-sm'>"
            f"<span class='text-sm text-slate-700'>🖨️ {esc(j.get('label') or j.get('ptype'))}</span>"
            f"<span class='text-xs text-indigo-600 font-bold'>다운로드 ↓</span></a>"
            for j in jobs) or "<div class='text-sm text-slate-400 text-center py-3'>아직 만든 인쇄물이 없어요.</div>"
        return (
            "<details class='mt-5 bg-slate-50 border border-slate-200 rounded-2xl p-4'>"
            "<summary class='cursor-pointer text-sm font-bold text-slate-700 select-none'>🖨️ 인쇄물 만들기 — 메뉴판·가격표·전단·POP "
            f"<span class='text-xs text-slate-400 font-normal'>(이번 달 {used_label} · 가격은 입력한 그대로)</span></summary>"
            "<div class='mt-3'>"
            f"<select id='p_type' class='w-full rounded-xl border px-3 py-2.5 mb-2 text-sm bg-white'>{type_opts}</select>"
            "<input id='p_note' placeholder='제목/이벤트 메모(선택, 예: 봄맞이 신메뉴)' class='w-full rounded-xl border px-3 py-2.5 mb-2 text-sm outline-none'>"
            "<div id='p_items'></div>"
            "<button type=button onclick='addRow()' class='text-xs text-indigo-600 font-bold mb-2'>+ 항목 추가</button>"
            "<label class='block text-xs text-slate-500 mb-1'>대표 사진(선택)</label>"
            "<input id='p_photo' type='file' accept='image/*' class='w-full text-xs mb-2'>"
            "<label class='flex items-center gap-2 text-xs text-slate-600 mb-3'>"
            "<input id='p_qr' type='checkbox' checked class='accent-indigo-600'> 매장 QR 넣기 — 손님이 찍으면 리포트에 '매장 QR' 유입으로 집계돼요</label>"
            "<button type=button onclick='genPrint()' class='w-full grad-btn text-white font-bold py-3 rounded-xl'>인쇄물 생성</button>"
            "<div id='p_msg' class='text-sm mt-2'></div>"
            "<div class='font-bold text-slate-700 mt-4 mb-2 text-sm'>내가 만든 인쇄물</div>" + made + "</div>"
            "<script>"
            "function addRow(){var d=document.getElementById('p_items');var r=document.createElement('div');r.className='flex gap-2 mb-2';"
            "r.innerHTML='<input class=\"pn flex-1 rounded-lg border px-3 py-2 text-sm\" placeholder=\"항목명\"><input class=\"pp w-28 rounded-lg border px-3 py-2 text-sm\" placeholder=\"가격\">';d.appendChild(r);}"
            "addRow();addRow();"
            "async function genPrint(){var msg=document.getElementById('p_msg');msg.textContent='생성 중… (10~20초)';"
            "var items=[];document.querySelectorAll('#p_items > div').forEach(function(row){var n=row.querySelector('.pn').value,p=row.querySelector('.pp').value;if(n)items.push({name:n,price:p});});"
            "var fd=new FormData();fd.append('type',document.getElementById('p_type').value);fd.append('note',document.getElementById('p_note').value);fd.append('items',JSON.stringify(items));"
            "var q=document.getElementById('p_qr');fd.append('qr',(q&&q.checked)?'1':'0');"
            "var ph=document.getElementById('p_photo').files[0];if(ph)fd.append('photo',ph);"
            "try{var r=await fetch('/api/print/generate',{method:'POST',body:fd});var d=await r.json();"
            "if(d.ok){msg.innerHTML='✅ 완성! <a href=\"'+d.download+'\" target=\"_blank\" class=\"text-indigo-600 underline font-bold\">다운로드</a>';setTimeout(function(){location.reload();},1200);}"
            "else{msg.textContent=d.error||'생성 실패';msg.className='text-sm mt-2 text-rose-500';if(d.upgrade)location.href='/#pricing';}}"
            "catch(e){msg.textContent='생성 실패 — 잠시 후 다시';}}"
            "</script></details>")
    except Exception:
        return ""


@app.get("/me/print")
def print_page(request: Request):
    """(UI 정리) 인쇄물 페이지 제거 — 리포트 > 플레이스 섹션의 '인쇄물 만들기'로 일원화."""
    return RedirectResponse("/me#place", status_code=303)


@app.get("/api/lookup")
def api_lookup(q: str = "", biz: str = ""):
    """가게 이름/상품 링크 하나로 자동 판별·입력. biz='seller'면 지역검색 건너뛰고 쇼핑검색.
    URL→셀러(상품 파싱) / 이름→지역검색(매장) / 없으면 쇼핑검색(셀러)."""
    from app.services import place, lookup
    q = (q or "").strip()
    if not q:
        return JSONResponse({"type": "none"})
    # A) URL 붙여넣기 → 셀러(상품 파싱 + 마켓 자동감지 + 검색어 자동생성)
    if q.startswith(("http://", "https://")):
        p = lookup.parse_url(q)
        name = (p.get("name") or "")[:60]
        return JSONResponse({"type": "seller", "name": name, "industry": name[:20],
                             "image": p.get("image", ""), "buy_url": q,
                             "market": _detect_market(q), "search_kw": _seller_search_kw(name),
                             "desc": (p.get("description") or "")[:120]})
    # 이름 → 지역검색(매장) — 단, 셀러로 선택했으면 건너뛰고 쇼핑검색으로
    local = place.search(q, limit=5) if biz != "seller" else []
    if local:
        from urllib.parse import quote as _q

        def _cand(it):
            region = _short_region(it.get("jibun") or it["address"])   # 시/구/동만
            # 플레이스 URL(best-effort) — 지역+상호로 검색해 정확한 곳으로 유도(동명업체 구분)
            map_q = ((region + " " + it["name"]).strip()) if region else it["name"]
            lat = lon = None
            try:                                                       # 네이버 좌표(mapx=경도, mapy=위도, *10^7)
                mx, my = float(it.get("mapx") or 0), float(it.get("mapy") or 0)
                if mx and my:
                    lon, lat = round(mx / 1e7, 7), round(my / 1e7, 7)
            except Exception:
                pass
            return {"name": it["name"], "industry": it["category"], "region": region,
                    "tel": it["tel"], "address": it["address"],
                    "map_url": "https://map.naver.com/p/search/" + _q(map_q),
                    "lat": lat, "lon": lon}
        cands = [_cand(it) for it in local]
        resp = dict(cands[0])
        resp["type"] = "local"
        if len(cands) > 1:                       # 동명·유사 업체 여러 곳 → 사용자가 선택
            resp["candidates"] = cands
        return JSONResponse(resp)
    # B) 지역 없음 → 쇼핑검색(셀러) — 마켓·브랜드·가격·검색어 자동 채움 + 여러 상품 후보
    shop = place.shop_search(q, limit=5)
    if shop:
        def _scand(it):
            brand = it.get("brand", "")
            return {"name": it["name"], "industry": it.get("category") or "",
                    "image": it.get("image", ""), "price": it.get("price", ""),
                    "mall": it.get("mall", ""), "brand": brand,
                    "market": _detect_market(it.get("mall", "")),
                    "search_kw": _seller_search_kw(it["name"], brand),
                    "buy_url": ""}    # 검색결과 링크는 남의 것 → 셀러가 자기 링크 직접 입력(URL 붙여넣기로만 자동)
        scands = [_scand(it) for it in shop]
        resp = dict(scands[0])
        resp["type"] = "seller"
        if len(scands) > 1:                        # 여러 상품 → 내 상품 선택
            resp["candidates"] = scands
        return JSONResponse(resp)
    # 셀러로 선택했는데 쇼핑검색도 없으면 → 상품명만이라도 셀러로 채움
    if biz == "seller":
        return JSONResponse({"type": "seller", "name": q, "industry": q[:20],
                             "search_kw": _seller_search_kw(q)})
    return JSONResponse({"type": "none", "configured": place.configured()})


@app.post("/api/contact")
async def api_contact(company: str = Form(""), manager: str = Form(""), phone: str = Form(""),
                      email: str = Form(""), message: str = Form("")):
    """랜딩 문의 — SMTP 설정 시 메일 발송, 항상 로그로 백업(리드 보존)."""
    to = "etetetetet5ea@kakao.com"
    body = f"[올린다 문의]\n상호:{company}\n담당:{manager}\n연락처:{phone}\n이메일:{email}\n내용:{message}"
    sent = False
    host, user, pw = (os.environ.get("SMTP_HOST"), os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"))
    if host and user and pw:
        try:
            import smtplib
            from email.mime.text import MIMEText
            msg = MIMEText(body)
            msg["Subject"] = f"[올린다 문의] {company}"
            msg["From"] = user
            msg["To"] = to
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
                s.starttls(); s.login(user, pw); s.send_message(msg)
            sent = True
        except Exception:
            sent = False
    try:
        d = os.environ.get("SHOPCAST_STORAGE", "storage")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "contacts.log"), "a") as f:
            f.write(body.replace("\n", " | ") + "\n")
    except Exception:
        pass
    return JSONResponse({"ok": True, "mailed": sent})


@app.get("/demo-upload/{name}")
def demo_upload(name: str):
    import re
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return HTMLResponse(status_code=404)
    path = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), "demo", name)
    if not os.path.exists(path):
        return HTMLResponse(status_code=404)
    return FileResponse(path)


# ── 회원가입/로그인 ───────────────────────────────────────
def _auth_page(title: str, inner: str) -> str:
    from app import landing
    return (landing._HEAD + "<div class='max-w-md mx-auto px-5 py-16'>"
            f"<a href='/' class='text-indigo-600 text-sm'>← 홈</a>"
            f"<h1 class='text-2xl font-extrabold mt-3 mb-6'>{esc(title)}</h1>{inner}</div>" + landing._FOOT)


@app.get("/signup", response_class=HTMLResponse)
def signup_get(from_: str = "", err: str = ""):
    msg = ""
    if err == "1":
        msg = "<p class='text-rose-500 text-sm mb-3 text-center'>이미 가입된 이메일이거나 입력이 비었어요.</p>"
    elif err == "2":
        msg = "<p class='text-rose-500 text-sm mb-3 text-center'>잠시 후 다시 시도해주세요.</p>"
    social = (_google_btn("구글로 가입하기")
              + "<a href='/login/kakao' class='block text-center mb-4 py-3 rounded-xl font-bold' "
              "style='background:#FEE500;color:#191600'>카카오로 3초 가입</a>"
              "<div class='flex items-center gap-2 my-4'><div class='flex-1 h-px bg-slate-200'></div>"
              "<span class='text-xs text-slate-400'>또는 이메일로 (인증 없이 바로)</span>"
              "<div class='flex-1 h-px bg-slate-200'></div></div>")
    form = (f"{msg}<form method=post action='/signup' class='space-y-3'>"
            "<input name=email type=email placeholder='이메일 (아이디로 사용)' required "
            "class='w-full border border-slate-200 rounded-xl p-3 outline-none focus:border-indigo-400'>"
            "<input name=pw type=password placeholder='비밀번호 (6자 이상)' minlength='6' required "
            "class='w-full border border-slate-200 rounded-xl p-3 outline-none focus:border-indigo-400'>"
            "<button class='w-full bg-indigo-600 hover:bg-indigo-700 text-white font-extrabold py-3 rounded-xl transition'>이메일로 가입하기</button></form>"
            "<p class='text-sm text-slate-400 mt-4 text-center'>이미 회원? <a href='/login' class='text-indigo-600 font-semibold'>로그인</a></p>")
    return _auth_page("가입하고 시작하기", social + form)


@app.post("/signup")
def signup_post(request: Request, email: str = Form(""), pw: str = Form("")):
    try:
        if not (email and pw) or db.get_user_by_email(email):
            return RedirectResponse("/signup?err=1", status_code=303)
        h, salt = auth.hash_pw(pw)
        u = db.create_user(email=email, pw_hash=h, salt=salt)
        resp = RedirectResponse("/me", status_code=303)
        resp.set_cookie(auth.COOKIE, auth.make_session(u["id"]), max_age=5184000, httponly=True, samesite="lax", secure=auth.cookie_secure())
        return resp
    except Exception as e:
        import traceback, logging
        logging.exception("[signup] 실패")
        if request.query_params.get("dbg") == os.environ.get("SHOPCAST_ADMIN_PASS", "_"):
            return HTMLResponse("SIGNUP_ERR " + repr(e) + "\n" + traceback.format_exc(), status_code=500)
        return RedirectResponse("/signup?err=2", status_code=303)


@app.get("/login")
def login_get(request: Request):
    # 로그인돼 있으면 작업실, 아니면 로그인 화면(카카오/구글)
    if auth.current_user(request):
        return RedirectResponse("/me", status_code=303)
    from app import landing
    err = ("<p class='text-rose-500 text-xs mb-2'>아이디 또는 비밀번호가 맞지 않아요.</p>"
           if request.query_params.get("err") else "")
    inner = (
        "<div class='min-h-screen flex items-center justify-center bg-slate-50 px-5'>"
        "<div class='bg-white rounded-3xl shadow-xl border border-slate-100 p-8 w-full max-w-sm text-center'>"
        f"<a href='/' class='inline-flex items-center gap-2 font-extrabold text-2xl mb-2'>{landing.LOGO}<span>올린다</span></a>"
        "<p class='text-slate-500 text-sm mb-6'>로그인하고 내 작업실로 이동하세요</p>"
        "<a href='/login/kakao' class='block text-center py-3.5 rounded-xl font-extrabold mb-2.5' style='background:#FEE500;color:#191600'>카카오로 시작하기</a>"
        "<a href='/login/google' class='block text-center py-3.5 rounded-xl font-bold border border-slate-200 hover:bg-slate-50 transition'>구글로 시작하기</a>"
        "<div class='flex items-center gap-2 my-4'><div class='flex-1 h-px bg-slate-100'></div>"
        "<span class='text-xs text-slate-400'>또는 아이디로</span><div class='flex-1 h-px bg-slate-100'></div></div>"
        f"{err}"
        "<form method='post' action='/login' class='space-y-2 text-left'>"
        "<input name='email' type='email' required placeholder='아이디(이메일)' autocomplete='username' "
        "class='w-full rounded-xl border border-slate-200 px-4 py-2.5 text-sm outline-none focus:border-indigo-400'>"
        "<input name='pw' type='password' required placeholder='비밀번호' autocomplete='current-password' "
        "class='w-full rounded-xl border border-slate-200 px-4 py-2.5 text-sm outline-none focus:border-indigo-400'>"
        "<button class='w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm transition'>로그인</button></form>"
        "<p class='text-sm text-slate-400 mt-4'>아직 회원이 아니신가요? <a href='/signup' class='text-indigo-600 font-semibold'>이메일로 회원가입</a></p>"
        "<a href='/' class='inline-block text-xs text-slate-400 mt-3 hover:text-slate-600'>← 홈으로</a>"
        "</div></div>")
    return HTMLResponse(landing._HEAD + inner + landing._FOOT)


@app.post("/login")
def login_post(email: str = Form(""), pw: str = Form("")):
    u = db.get_user_by_email(email)
    if not u or not auth.verify_pw(pw, u["salt"] or "", u["pw_hash"] or ""):
        return RedirectResponse("/login?err=1", status_code=303)
    resp = RedirectResponse("/me", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(u["id"]), max_age=5184000, httponly=True, samesite="lax", secure=auth.cookie_secure())
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/welcome", response_class=HTMLResponse)
def welcome(request: Request):
    u = auth.current_user(request)
    who = esc(u.get("email") or u.get("name") or "회원") if u else "회원"
    inner = (f"<div class='bg-white rounded-2xl border p-6 text-center'>"
             f"<div class='text-4xl mb-2'>🎉</div><p class='font-bold text-lg mb-1'>{who}님, 가입 완료!</p>"
             "<p class='text-slate-500 text-sm mb-4'>내 작업실에서 ① 가게 설정 ② 채널 연결 ③ 사진 올려 생성을 시작하세요.</p>"
             "<a href='/me' class='inline-block bg-indigo-600 text-white font-bold px-6 py-3 rounded-xl'>내 작업실로 가기 →</a></div>")
    return _auth_page("환영합니다", inner)


def _subscriber_page(title: str, inner: str, wide: bool = False) -> str:
    from app import landing
    mw = "max-w-6xl" if wide else "max-w-3xl"
    head = f"<h1 class='text-2xl font-extrabold mb-4'>{esc(title)}</h1>" if title else ""
    return (landing._HEAD + f"<div class='{mw} mx-auto px-5 py-10'>"
            "<div class='flex items-center justify-between mb-6'>"
            f"<a href='/' class='font-extrabold text-xl flex items-center gap-2'>{landing.LOGO}<span>올린다</span></a>"
            "<a href='/logout' class='text-sm text-slate-400'>로그아웃</a></div>"
            + head + inner + "</div>" + landing._FOOT)


def _ensure_user_tenant(u: dict):
    """구독자(user)에게 본인 가게(tenant)가 없으면 생성·연결. 활성 가게는 소유목록에도 등록."""
    tid = u.get("tenant_id")
    t = db.get_tenant(tid) if tid else None
    if t:
        db.link_store(u["id"], t.id)                # 기존 단일 가게도 다중가게 목록에 등록(마이그레이션)
        return t
    t = db.create_tenant(name="내 가게", industry="", region="", biz_type="local")  # 중립 기본명(닉네임 노출 방지)
    db.set_user_tenant(u["id"], t.id)
    db.link_store(u["id"], t.id)
    return t


@app.post("/me/store/add")
def store_add(request: Request):
    """새 가게 추가 등록 후 그 가게로 전환."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    _ensure_user_tenant(u)                          # 현재 가게 먼저 목록 등록
    db.add_store(u["id"])
    return RedirectResponse("/me?ok=새 가게를 추가했어요 — 가게 이름을 입력하고 자동 인식하세요", status_code=303)


@app.post("/me/store/switch")
def store_switch(request: Request, tenant_id: str = Form("")):
    """활성 가게 전환."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    db.switch_store(u["id"], tenant_id.strip())
    return RedirectResponse("/me", status_code=303)


@app.post("/me/store/cancel")
def store_cancel(request: Request):
    """가게 추가를 잘못 눌렀을 때 — 비어있는 새 가게면 삭제하고 이전 가게로 되돌림."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    if db.list_sets(tenant_id=t.id):                 # 콘텐츠가 있으면 실수 아님 → 그냥 전환만
        return RedirectResponse("/me?tab=content", status_code=303)
    db.delete_store(u["id"], t.id)                   # 비어있으면 삭제 + 이전 가게로
    return RedirectResponse("/me?ok=이전 가게로 돌아왔어요", status_code=303)


def _perf_report(tenant_id: str) -> str:
    """생성 콘텐츠 성과 요약 — 세트/채널 발행물/평균 상위노출 점수/타겟 키워드."""
    sets = db.list_sets(tenant_id=tenant_id, limit=200)
    if not sets:
        return ""
    scores: list = []
    kws: list = []
    channels: set = set()
    n_pieces = 0
    for s in sets:
        for p in db.get_set_pieces(s["asset_id"]):
            n_pieces += 1
            channels.add(p.channel.value)
            sc = (p.payload.get("ranking_audit") or {}).get("score")
            if isinstance(sc, (int, float)):
                scores.append(sc)
            for k in (p.payload.get("target_keywords") or []):
                if k and k not in kws:
                    kws.append(k)
    avg = round(sum(scores) / len(scores)) if scores else 0

    def _stat(icon, num, chip, label):
        return (f"<div class='rounded-2xl bg-white border border-slate-100 shadow-sm p-4'>"
                f"<div class='w-8 h-8 rounded-xl flex items-center justify-center text-base mb-2.5 {chip}'>{icon}</div>"
                f"<div class='text-4xl sm:text-5xl font-extrabold text-slate-900 leading-none tracking-tight'>{num}</div>"
                f"<div class='text-[11px] text-slate-400 mt-2 font-bold'>{label}</div></div>")
    stats = ("<div class='grid grid-cols-3 gap-3 mb-5'>"
             + _stat(_ic("package", "w-4 h-4"), len(sets), "bg-[#EEF2FF] text-indigo-600", "만든 세트")
             + _stat(_ic("grid", "w-4 h-4"), n_pieces, "bg-[#EEF2FF] text-indigo-600", "채널 발행물")
             + _stat(_ic("target", "w-4 h-4"), avg, "bg-[#EEF2FF] text-indigo-600", "평균 점수") + "</div>")
    kw_html = ""    # (auto) '노리는 키워드' 노출 제거 — AI 내부 재료
    # 🚀 before/after 순위 성장 카드 — 발행 후 자동 스냅샷 기반(성장 PHASE 2)
    ba = ""    # (auto) 키워드별 성장 나열 제거 — 글별 순위는 내 네이버 블로그에서
    return ("<div class='bg-white rounded-3xl border border-slate-100 shadow-sm hover:shadow-md transition-shadow p-5 sm:p-6 mb-5'>"
            "<h2 class='font-extrabold text-slate-900 mb-4 text-base'>성과 리포트</h2>"
            + ba + stats + kw_html
            + "<div class='mt-2'><button onclick='checkRank()' class='px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-600 text-xs font-bold rounded-xl transition'>키워드 순위 조회</button>"
            + "<div id='rankbox' class='mt-2'></div></div>"
            + "<script>async function checkRank(){var b=document.getElementById('rankbox');"
              "b.innerHTML='<span class=\"text-slate-400 text-xs\">조회 중…</span>';"
              "try{var r=await fetch('/me/rank');var d=await r.json();"
              "if(!d.configured){b.innerHTML='<span class=\"text-slate-400 text-xs\">네이버 키를 등록하면 순위 조회가 켜집니다.</span>';return;}"
              "if(!d.items||!d.items.length){b.innerHTML='<span class=\"text-slate-400 text-xs\">타겟 키워드가 아직 없어요.</span>';return;}"
              "b.innerHTML=d.items.map(function(it){var s=(it.rank===null)?'조회불가':(it.rank>=1?('네이버 지역 '+it.rank+'위 ✅'):'상위 5위 밖');"
              "return '<div class=\"flex justify-between border-b border-slate-100 py-1.5 text-sm\"><span class=\"text-slate-600\">'+it.kw+'</span><span class=\"font-bold text-slate-800\">'+s+'</span></div>';}).join('');"
              "}catch(e){b.innerHTML='<span class=\"text-rose-400 text-xs\">조회 실패</span>';}}</script>"
            + "<p class='text-xs text-slate-400 mt-3'>※ 순위는 참고용(위치·기기별로 달라요). 실시간 자동추적은 로드맵.</p></div>")


def _ensure_track_link(t):
    """가게 대표 목적지(플레이스/스토어) 추적 링크 — tracklinks 서비스로 위임(추적 P1에서 통합)."""
    from app.services import tracklinks
    return tracklinks.tenant_link(t)


@app.get("/me/qr/{code}.png")
def link_qr(code: str):
    """추적 링크 QR(오프라인→온라인 유입 측정)."""
    import io
    import qrcode
    from starlette.responses import Response as _Resp
    base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
    img = qrcode.make(f"{base}/r/{code}")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return _Resp(content=buf.getvalue(), media_type="image/png")


def _daily_action(t) -> dict:
    """능동 코칭 — '오늘의 액션 1개'. 상위노출 루프 기반 우선순위(상위노출 PHASE 6):
    ① 첫 콘텐츠 ② 발행 공백 ③ 정체 키워드 앵글 재도전 ④ 오르는 키워드 더 밀기 ⑤ 유입 성과 ⑥ 기본."""
    import datetime
    from urllib.parse import quote as _q
    sets = db.list_sets(tenant_id=t.id, limit=50)
    links = db.list_links(t.id)
    clicks = sum(int(l.get("clicks") or 0) for l in links)
    improving = []
    try:
        improving = db.improving_keywords(t.id)
    except Exception:
        pass
    if not sets:
        return {"emoji": "wand", "text": "첫 콘텐츠를 만들어보세요! 사진 한 장이면 5채널이 완성돼요.",
                "cta": "지금 만들기", "href": "/me"}
    # 마지막 콘텐츠 이후 경과일
    days = 0
    try:
        last = (sets[0].get("created") or "")[:10]      # list_sets created = KST 표시 문자열
        d0 = datetime.date.fromisoformat(last)
        from app.services.mass import kst_today as _kt
        days = (_kt() - d0).days
    except Exception:
        pass
    if days >= 3:
        return {"emoji": "calendar", "text": f"{days}일째 새 콘텐츠가 없어요. 꾸준함이 상위노출의 1순위예요 — 오늘 하나 올려요!",
                "cta": "새 콘텐츠 만들기", "href": "/me"}
    # 🔄 정체 키워드 — 앵글 바꿔 재도전(상위노출 PHASE 3·6)
    try:
        from app.services import ranktrack
        stag = ranktrack.stagnant_keywords(t.id, limit=1)
        if stag:
            s = stag[0]
            return {"emoji": "refresh", "text": f"‘{esc(s['keyword'])}’가 정체 중이에요. {s['retry_label']} 앵글로 바꿔 다른 검색블록을 노려봐요.",
                    "cta": "앵글 바꿔 만들기", "href": s["href"]}
    except Exception:
        pass
    if improving:
        k = improving[0]["keyword"]
        return {"emoji": "trend", "text": f"‘{esc(k)}’ 순위가 오르고 있어요! 이 기세로 하나 더 올리면 상위 굳히기 각이에요.",
                "cta": "이 키워드 더 밀기", "href": "/me?target_kw=" + _q(k)}
    if clicks > 0:
        return {"emoji": "target", "text": f"추적 링크 클릭 {clicks}회 — 콘텐츠가 실제 손님을 부르고 있어요. 계속 올려요!",
                "cta": "성과 보기", "href": "/me"}
    return {"emoji": "wand", "text": "오늘 콘텐츠 하나로 노출을 늘려보세요. 매주 2~3개가 상위노출의 정석이에요.",
            "cta": "만들기", "href": "/me"}


def _exposure_card(t) -> str:
    """"지금 네이버에서 사장님 가게가 보이는 곳" — 지면 4개 실측 카드(사장 언어).
    실측이 없으면 숫자를 지어내지 않고 '측정 준비 중'으로 둔다(정직 게이트 동일 원칙)."""
    try:
        from app.services import exposure as _ex
        d = _ex.summary(t.id)
    except Exception:
        return ""
    _s = d.get("surfaces") or {}
    _rows = []

    def _line(icon, label, body, tone="slate"):
        _rows.append(f"<div class='flex gap-2.5 items-start py-2 border-b border-slate-100 last:border-0'>"
                     f"<span class='text-base leading-6'>{icon}</span>"
                     f"<div class='min-w-0 flex-1'><div class='text-xs font-bold text-{tone}-500 mb-0.5'>{label}</div>"
                     f"<div class='text-sm text-slate-700 leading-relaxed'>{body}</div></div></div>")

    se = _s.get("search") or {}
    if se.get("state") == "none":
        _line("🔍", "통합검색", f"<span class='text-slate-400'>{esc(_ex.NOT_MEASURED)}</span>")
    else:
        _b = []
        for x in se.get("shown", []):
            # 블록명 미표시 — 귀속 검증 전까지 '첫 화면'까지만(허위 양성 방지, 2026-08-02).
            _b.append(f"<b class='text-emerald-700'>‘{esc(x['keyword'])}’</b> "
                      f"<span class='text-slate-500'>첫 화면에 보이는 중</span>")
        for k in se.get("waiting", [])[:3]:
            _b.append(f"‘{esc(k)}’ <span class='text-amber-600'>자리는 있는데 아직</span>")
        for k in se.get("no_room", [])[:2]:
            _b.append(f"‘{esc(k)}’ <span class='text-slate-400'>이 검색어는 블로그 자리가 없어요</span>")
        _line("🔍", f"통합검색 · 검색어 {se.get('n_measured', 0)}개 확인",
              "<br>".join(_b) or "아직 보이는 곳이 없어요")

    pl = _s.get("place") or {}
    if pl.get("state") == "measured":
        _b = []
        for it in pl.get("items", []):
            _r = it.get("rank")
            _txt = (f"<b class='text-emerald-700'>{_r}위</b>" if _r else
                    "<span class='text-slate-400'>상위 5곳 밖</span>")
            _b.append(f"‘{esc(it['keyword'])}’ {_txt}"
                      + (f" <span class='text-slate-400'>· {esc(it['delta'])}</span>" if it.get("delta") else ""))
        _line("📍", "플레이스(지도)", "<br>".join(_b))
    else:
        _line("📍", "플레이스(지도)", f"<span class='text-slate-400'>{esc(_ex.NOT_MEASURED)}</span>")

    br = _s.get("briefing") or {}
    if br.get("state") == "shown":
        _line("🤖", "AI 브리핑", "‘" + "’, ‘".join(esc(k) for k in br.get("items", [])) +
              "’ <b class='text-emerald-700'>브리핑 지면에서 확인됐어요</b>"
              " <span class='text-slate-400'>(블록 정확도 검증 중)</span>")
    elif br.get("state") == "waiting":
        _line("🤖", "AI 브리핑", "‘" + "’, ‘".join(esc(k) for k in br.get("items", [])) +
              "’ <span class='text-amber-600'>브리핑 자리는 있는데 아직</span>")
    else:
        _line("🤖", "AI 브리핑", f"<span class='text-slate-400'>{esc(_ex.NOT_MEASURED)}</span>")

    _line("🌐", "웹문서", f"<span class='text-slate-400'>{esc(_ex.NOT_MEASURED)}</span>")
    _when = esc((d.get("measured_at") or "").replace("T", " "))
    return ("<div class='bg-white rounded-2xl shadow-sm p-5 mb-4'>"
            "<div class='flex items-baseline justify-between mb-1'>"
            f"<h2 class='font-extrabold text-slate-900'>지금 네이버에서 {esc(d.get('shop',''))}가 보이는 곳</h2>"
            + (f"<span class='text-[11px] text-slate-400'>{_when} 확인</span>" if _when else "")
            + "</div><div class='mt-1'>" + "".join(_rows) + "</div></div>")


def _place_kit_card(t) -> str:
    """📍 플레이스 채우기 — 코드가 할 수 있는 것(상세설명 초안)과 사장님이 하실 단계 안내.
    실제 스마트플레이스 입력·인증은 사람 몫이므로, 초안은 복붙용으로 주고 순서를 안내한다."""
    if (getattr(t, "biz_type", "local") or "local") == "seller":
        return ""                                   # 전국 셀러는 지도 지면이 대상이 아니다
    try:
        from app.services import place_opt as _po
        draft = _po.description_draft(t)
        steps = _po.PLACE_STEPS
    except Exception:
        return ""
    if not draft:
        return ""
    _steps = "".join(
        f"<div class='flex gap-2 items-start py-1.5'>"
        f"<span class='shrink-0 w-5 h-5 rounded-full bg-indigo-100 text-indigo-700 text-[11px] "
        f"font-bold grid place-items-center'>{i}</span>"
        f"<div class='text-sm'><b class='text-slate-800'>{esc(lab)}</b> "
        f"<span class='text-slate-500'>{esc(desc)}</span></div></div>"
        for i, (lab, desc) in enumerate(steps, 1))
    _id = "pldraft"
    return ("<div class='bg-white rounded-2xl shadow-sm p-5 mb-4'>"
            "<h2 class='font-extrabold text-slate-900 mb-1'>플레이스(지도) 채우기</h2>"
            "<p class='text-xs text-slate-500 mb-3'>지도 순위는 글이 아니라 등록정보가 정합니다 — "
            "여기부터 채우는 게 검색 노출의 가장 빠른 길이에요.</p>"
            + _steps
            + "<div class='mt-3 text-xs font-bold text-slate-500 mb-1'>상세설명 초안 (복사해서 붙여넣기)</div>"
            + f"<pre id='{_id}' class='text-[13px] leading-relaxed text-slate-700 bg-slate-50 "
              f"rounded-xl p-3 whitespace-pre-wrap'>{esc(draft)}</pre>"
            + f"<button type='button' onclick=\"navigator.clipboard.writeText("
              f"document.getElementById('{_id}').innerText);this.textContent='복사됨!';\" "
              "class='mt-2 w-full px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-900 "
              "text-white text-sm font-bold transition'>상세설명 복사</button></div>")


@app.get("/me", response_class=HTMLResponse)
def my_dashboard(request: Request, ok: str = "", err: str = "", gen: str = ""):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    # 🎁 티저 이관(전환 개선 ②, 2026-07-31 사장님 승인): 미가입 체험에서 만든 결과물을 가입 즉시
    #   '내 작업실'에 그대로 — 재생성 없이 데모 tenant를 통째로 인계(사진·글·점수 전부 보존).
    #   조건: 청구권 쿠키 + 대상이 아직 데모 + 내 가게가 비어 있을 때만(기존 콘텐츠 덮어쓰기 방지).
    import re as _re_cl
    _claim = (request.cookies.get("demo_claim") or "").strip()
    if _claim and _re_cl.fullmatch(r"[0-9a-f-]{16,64}", _claim):
        try:
            _dt = db.get_tenant(_claim)
            if _dt is not None and _tenant_is_demo(_claim):
                _mine = db.get_tenant(u.get("tenant_id") or "") if u.get("tenant_id") else None
                if _mine is None or not db.list_sets(tenant_id=_mine.id, limit=1):
                    _nm = ((_dt.name or "").replace("미리보기", "").strip() or "내 가게")
                    with db._conn() as _c:
                        _c.execute("UPDATE tenants SET is_demo=0, name=? WHERE id=?", (_nm, _claim))
                    db.set_user_tenant(u["id"], _claim)
                    db.link_store(u["id"], _claim)
                    u["tenant_id"] = _claim
                    ok = ok or "미가입 체험에서 만든 콘텐츠를 그대로 가져왔어요 — 아래 '내 콘텐츠'에서 완성본을 확인하세요!"
        except Exception:
            logging.getLogger("shopcast.demo").exception("[demo-claim] 이관 실패")
    t = _ensure_user_tenant(u)
    tok = db.tenant_token(t.id)
    inp = "w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm"
    banner = ""
    if ok:
        banner = f"<div class='bg-emerald-50 text-emerald-700 p-3 rounded-xl mb-4 text-sm'>✅ {esc(ok)}</div>"
    if err:
        banner = f"<div class='bg-rose-50 text-rose-600 p-3 rounded-xl mb-4 text-sm'>⚠️ {esc(err)}</div>"
    if gen:   # 생성 중 — 실제 단계 진행률 폴링(정직한 표시: 남은시간 숫자 금지, 단계·실측범위·지연/실패 안내)
        _base_n = len(db.list_sets(tenant_id=t.id))
        banner = (
            "<div id='genprog' class='bg-indigo-50 border border-indigo-100 rounded-2xl p-4 mb-4'>"
            "<div class='flex items-center gap-3'>"
            "<div class='w-6 h-6 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin flex-shrink-0'></div>"
            "<div class='flex-1 min-w-0'>"
            "<div id='gp_label' class='font-bold text-sm text-slate-900'>콘텐츠를 만들고 있어요</div>"
            "<div id='gp_detail' class='text-xs text-indigo-500'>준비 중…</div></div></div>"
            "<div class='mt-3 h-2 bg-indigo-100 rounded-full overflow-hidden'>"
            "<div id='gp_bar' class='h-full bg-indigo-600 rounded-full transition-all duration-500' style='width:6%'></div></div>"
            "<div id='gp_range' class='text-[11px] text-slate-400 mt-1'></div>"
            "<div id='gp_slow' class='text-xs text-amber-600 mt-1'></div></div>"
            f"<script>(function(){{var base={_base_n},n=0;"
            "function $(i){return document.getElementById(i);}"
            "var iv=setInterval(async function(){n++;"
            "try{"
            "var d=await (await fetch('/me/gen-progress')).json();"
            "if(d.label)$('gp_label').textContent=d.label;"
            "$('gp_detail').textContent=(d.detail||'');"
            "if(d.pct!=null)$('gp_bar').style.width=Math.max(6,Math.min(99,Math.round(d.pct*100)))+'%';"
            "$('gp_range').textContent=(d.range_text||'');"
            "$('gp_slow').textContent=(d.slow||'');"
            "if(d.status==='failed'){clearInterval(iv);$('genprog').innerHTML="
            "\"<div class='font-bold text-sm text-rose-600'>생성이 중단됐어요</div>"
            "<div class='text-xs text-slate-500 mt-1'>사진 수를 줄이거나 잠시 후 다시 시도해 주세요.</div>"
            "<a href='/me' class='inline-block mt-2 bg-indigo-600 text-white text-xs font-bold px-3 py-1.5 rounded-lg'>다시 시도</a>\";return;}"
            "var cd=await (await fetch('/me/sets/count')).json();"
            "if(cd.n>base){clearInterval(iv);location.href='/me?ok='+encodeURIComponent('콘텐츠가 완성됐어요! 아래에서 확인하세요');return;}"
            "}catch(e){}"
            "if(n>160){clearInterval(iv);location.reload();return;}"   # 안전 상한(~8분) — 진짜 멈춤 방지(가짜 2분 새로고침 폐지)
            "},3000);})();</script>")
    # 트랙2 대시보드 — D2 상태 배너(이상 시만) + D1 개선 제안 카드(이벤트 시만)를 최상단에.
    try:
        from app.services import dashboard_gowatch as _dg
        banner = _dg.render_d2(t.id) + _dg.render_d1(t.id) + banner
    except Exception:
        pass
    # 🔍 노출 현황 — 사장님 화면의 1번 숫자(CLAUDE.md 최상위 기준: 발행량이 아니라 노출 상태)
    exposure_card = _exposure_card(t) + _place_kit_card(t)
    # ① 가게/스토어 설정
    bopts = "".join(f"<option value='{k}'{' selected' if (t.biz_type or 'local') == k else ''}>{lab}</option>"
                    for k, lab in [("local", "동네 매장(방문 유도)"), ("seller", "온라인 셀러(구매 유도)"),
                                   ("hybrid", "매장+온라인")])
    mkopts = "".join(f"<option value='{k}'{' selected' if (t.marketplace or '') == k else ''}>{v}</option>"
                     for k, v in [("", "마켓 선택(셀러)"), ("coupang", "쿠팡"), ("11st", "11번가"),
                                  ("smartstore", "스마트스토어"), ("gmarket", "지마켓"), ("self", "자사몰")])
    store_form = (
        f"<form method=post action='/me/store' class='grid sm:grid-cols-2 gap-2'>"
        f"<input id=sf_name name=name value=\"{esc(t.name)}\" placeholder='상호/브랜드 *' required class='{inp}'>"
        f"<input id=sf_industry name=industry value=\"{esc(t.industry)}\" placeholder='업종/상품 * (예: 카페, 캠핑 폴딩박스)' required class='{inp}'>"
        f"<input id=sf_region name=region value=\"{esc(t.region)}\" placeholder='지역 (매장)' class='{inp}'>"
        f"<select name=biz_type class='{inp} font-semibold'>{bopts}</select>"
        f"<input id=sf_phone name=phone value=\"{esc(t.phone)}\" placeholder='전화 (매장)' class='{inp}'>"
        f"<input id=sf_address name=address value=\"{esc(t.address)}\" placeholder='주소 (매장)' class='{inp}'>"
        f"<select name=marketplace class='{inp}'>{mkopts}</select>"
        f"<input name=brand_name value=\"{esc(t.brand_name)}\" placeholder='브랜드명 (셀러)' class='{inp}'>"
        f"<input name=search_kw value=\"{esc(t.search_kw)}\" placeholder='검색어 유도 (쿠팡 등)' class='{inp}'>"
        f"<input name=buy_url value=\"{esc(t.buy_url)}\" placeholder='상세페이지/스토어/제휴 링크' class='{inp}'>"
        f"<input name=map_url value=\"{esc(t.map_url)}\" placeholder='네이버 플레이스 URL (매장)' class='{inp}'>"
        "<button class='bg-indigo-600 text-white font-bold py-2.5 rounded-xl sm:col-span-2'>저장</button></form>"
        "<p class='text-xs text-slate-400 mt-1 sm:col-span-2'>링크를 넣으면 글 끝에 <b>클릭 링크</b>로 자동 삽입돼요 (블로그·유튜브·X는 바로 클릭, 인스타는 프로필 안내).</p>"
        "<p class='text-xs text-slate-400 mt-2'>매장이면 글 끝에 지도·연락처, 셀러면 구매 링크/검색어로 자동 전환됩니다.</p>")
    # 온보딩용 최소 폼(필수 3개만 — 나머지는 나중에 설정에서). 셀러/동네매장 = 큰 토글로 명확히.
    _bt = (t.biz_type or "local")

    def _bopt(val, emoji, label, desc):
        sel = "peer-checked:border-indigo-600 peer-checked:bg-indigo-50 peer-checked:text-indigo-700"
        return (f"<label class='cursor-pointer'>"
                f"<input type=radio name=biz_type value='{val}'{' checked' if _bt == val else ''} class='peer sr-only'>"
                f"<div class='border-2 border-slate-200 rounded-xl p-3 text-center transition {sel}'>"
                f"<div class='text-2xl'>{emoji}</div><div class='font-bold text-sm mt-1'>{label}</div>"
                f"<div class='text-[11px] text-slate-400 mt-0.5'>{desc}</div></div></label>")
    biz_toggle = ("<div class='mt-1'><div class='text-xs font-semibold text-slate-500 mb-1'>사업형태 *</div>"
                  "<div class='grid grid-cols-3 gap-2'>"
                  + _bopt("local", _ic("store", "w-6 h-6 mx-auto text-indigo-600"), "동네 매장", "방문·예약 유도 · 지도/연락처")
                  + _bopt("seller", _ic("package", "w-6 h-6 mx-auto text-indigo-600"), "온라인 셀러", "구매링크·상품 키워드")
                  + _bopt("hybrid", "🔁", "둘 다", "방문+온라인 판매 병행")
                  + "</div></div>")
    search_box = (
        "<div class='bg-indigo-50 rounded-xl p-3 mb-3'>"
        "<div class='text-xs font-bold text-indigo-700 mb-1'>가게 이름으로 검색하면 자동 입력돼요 (타이핑 최소)</div>"
        "<div class='flex gap-2'>"
        f"<input id=place_q placeholder='가게 이름으로 검색' class='{inp} flex-1'>"
        "<button type=button onclick='placeSearch()' class='px-4 bg-indigo-600 text-white rounded-xl font-bold text-sm whitespace-nowrap'>검색</button></div>"
        "<div id=place_results class='mt-2 space-y-1'></div></div>")
    place_js = (
        "<script>"
        "async function placeSearch(){var q=document.getElementById('place_q').value.trim();if(!q)return;"
        "var b=document.getElementById('place_results');b.innerHTML='<div class=\"text-xs text-slate-400\">검색 중…</div>';"
        "try{var r=await fetch('/api/place/search?q='+encodeURIComponent(q));var d=await r.json();"
        "if(!d.items||!d.items.length){b.innerHTML='<div class=\"text-xs text-slate-400\">'+(d.configured?'결과가 없어요. 아래에 직접 입력해 주세요.':'검색 준비 중 — 아래에 직접 입력해 주세요.')+'</div>';return;}"
        "window.__pl=d.items;b.innerHTML=d.items.map(function(it,i){return '<button type=button onclick=\"pickPlace('+i+')\" class=\"block w-full text-left bg-white border rounded-lg p-2 text-sm hover:bg-indigo-50\"><b>'+it.name+'</b> <span class=\"text-xs text-slate-400\">'+(it.category||'')+'</span><br><span class=\"text-xs text-slate-400\">'+(it.address||'')+'</span></button>';}).join('');"
        "}catch(e){b.innerHTML='<div class=\"text-xs text-rose-400\">검색 실패</div>';}}"
        "function pickPlace(i){var it=(window.__pl||[])[i];if(!it)return;"
        "document.getElementById('sf_name').value=it.name||'';"
        "document.getElementById('sf_industry').value=it.category||'';"
        "var reg=(it.address||'').split(' ').slice(0,2).join(' ');"
        "document.getElementById('sf_region').value=reg;"
        "document.getElementById('sf_address').value=it.address||'';"
        "document.getElementById('sf_phone').value=it.tel||'';"
        "document.getElementById('place_results').innerHTML='<div class=\"text-xs text-emerald-600 font-bold\">✓ '+(it.name||'')+' 정보가 채워졌어요</div>';}"
        "</script>")
    store_form_min = (
        search_box +
        "<form method=post action='/me/store' class='space-y-3'>"
        f"<div><div class='text-xs font-semibold text-slate-500 mb-1'>상호/브랜드 *</div>"
        f"<input id=sf_name name=name value=\"{esc(t.name)}\" placeholder='가게 이름' required class='{inp}'></div>"
        f"<div><div class='text-xs font-semibold text-slate-500 mb-1'>업종 또는 파는 상품 *</div>"
        f"<input id=sf_industry name=industry value=\"{esc(t.industry)}\" placeholder='예: 카페, 썬팅, 캠핑 폴딩박스' required class='{inp}'></div>"
        f"<input type=hidden id=sf_region name=region value=\"{esc(t.region)}\">"
        f"<input type=hidden id=sf_address name=address value=\"{esc(t.address)}\">"
        f"<input type=hidden id=sf_phone name=phone value=\"{esc(t.phone)}\">"
        + biz_toggle
        + "<div class='mt-1'><div class='text-xs font-semibold text-slate-500 mb-1'>네이버 블로그 (선택 — 연결하면 발행확인·순위추적이 정확해요)</div>"
        f"<input name=naver_blog value=\"{esc(getattr(t, 'naver_blog_url', '') or '')}\" placeholder='https://blog.naver.com/내아이디 또는 아이디' class='{inp}'></div>"
        + "<button class='w-full bg-indigo-600 text-white font-bold py-3.5 rounded-xl text-base'>완료하고 시작하기 →</button></form>"
        "<p class='text-xs text-slate-400 mt-2'>검색하면 상호·업종·주소가 자동 입력돼요. 없으면 직접 입력하세요.</p>"
        + place_js)
    # ② 내 채널 연결
    connected = {a.channel: a for a in db.list_channel_accounts(t.id)}
    rows = ""
    for ch in CONNECTABLE:
        acc = connected.get(ch)
        if acc and acc.access_token_enc:
            state = "<span class='text-emerald-600 text-sm font-semibold'>✅ 연결됨</span>"
            btn = f"<a href='/me/connect/{ch.value}/start' class='px-3 py-1.5 bg-slate-200 rounded-lg text-xs'>다시 연결</a>"
        elif oauth.configured(ch):
            state = "<span class='text-slate-400 text-sm'>미연결</span>"
            btn = f"<a href='/me/connect/{ch.value}/start' class='px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs'>연결하기</a>"
        else:
            state = "<span class='text-amber-600 text-sm'>준비 중(앱 심사)</span>"
            btn = "<span class='text-xs text-slate-400'>곧 제공</span>"
        rows += (f"<div class='flex items-center justify-between bg-white rounded-xl border p-3 mb-2'>"
                 f"<div><b>{CHANNEL_LABEL[ch]}</b><br>{state}</div>{btn}</div>")
    channels = ("<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-5 mb-4'>"
                "<h2 class='font-bold mb-1'>② 내 채널 연결 (발행할 곳)</h2>"
                "<p class='text-xs text-slate-400 mb-3'>비밀번호 없이 공식 OAuth로 1회 허용 → 내 계정에 자동 발행. "
                "네이버는 공식 API가 없어 글을 완성해 드리면 직접 발행(반자동).</p>" + rows + "</div>")
    # ③ 콘텐츠 이력(세트 단위) → 각 항목 = 발행 소재(/kit)
    sets = db.list_sets(tenant_id=t.id, limit=50)
    _chan_icon = {k: _ic(v, "w-3.5 h-3.5 inline-block text-slate-500") for k, v in
                  {"instagram": "camera", "naver_blog": "pen", "x": "message", "youtube": "play",
                   "facebook": "check", "marketplace": "package"}.items()}
    if sets:
        _cards = []
        _ccounts = db.content_click_counts(t.id)         # 콘텐츠별 클릭 뱃지(추적 P2)
        _vol_budget = [3]                                 # 렌더당 searchad 미캐시 조회 상한(비용 가드)

        def _kw_volume_cached(kw: str):
            """월 검색량 — 일 1회 캐시(rx P3). 캐시 미스는 렌더당 3회까지만 실조회."""
            from app import ratelimit as _rl
            key = "kwvol:" + kw.replace(" ", "")
            v = _rl.cache_get(key, 86400)
            if v is not None:
                return v or None
            if _vol_budget[0] <= 0:
                return None
            _vol_budget[0] -= 1
            try:
                from app.services import searchad
                rows = searchad.keyword_volumes([kw])
                me = next((r for r in rows if (r.get("keyword") or "").replace(" ", "") == kw.replace(" ", "")), None)
                val = (me or {}).get("total") or 0
            except Exception:
                val = 0
            _rl.cache_set(key, val)
            return val or None

        def _expose_badge(ps):
            """글별 노출 배지(rx P3) — 저장된 실측 스냅샷·색인 상태만(렌더 시 네이버 콜 없음)."""
            blog_p = next((p for p in ps if p.kind.value == "blog"), None)
            if not blog_p:
                return ""
            kw = ((blog_p.payload.get("target_keywords") or [""])[0] or "").strip()
            if not kw:
                return ""
            hist = [h for h in db.rank_history(t.id, kw, kind="post") if h.get("rank") is not None] \
                or [h for h in db.rank_history(t.id, kw, kind="blog_search") if h.get("rank") is not None]
            pub = db.get_blog_publish(blog_p.id)
            vol = _kw_volume_cached(kw)
            vtxt = f" (월 {vol:,}회)" if vol else ""
            if hist:
                cur = hist[-1]["rank"]
                prev = hist[-2]["rank"] if len(hist) >= 2 else None
                if cur:
                    d = ("↑상승중" if prev and cur < prev else "↓하락" if prev and cur > prev else "")
                    body, cls = f"지금 {cur}위" + (f" {d}" if d else ""), ("text-emerald-700 bg-emerald-50" if cur <= 10 else "text-indigo-700 bg-indigo-50")
                else:
                    body, cls = "31위 밖", "text-slate-500 bg-slate-100"
            elif pub and not pub.get("indexed_at"):
                from app.services.whynot import _days_since as _ds
                body, cls = f"색인대기 {max(0, _ds(pub.get('published_at') or ''))}일차", "text-amber-700 bg-amber-50"
            elif pub:
                body, cls = "추적 시작 전", "text-slate-500 bg-slate-100"
            else:
                return ""
            return (f"<a href='/me#blog' title='실측 기준 · 위치·기기별 차이' "
                    f"class='inline-block text-[11px] font-bold px-2 py-0.5 rounded-full {cls}'>"
                    f"{esc(kw)}{vtxt} · {body}</a>")
        def _video_row(aid: str, ps) -> tuple[str, bool]:
            """(영상 온디맨드) 카드 내 플랫폼 선택·상태 행 — 반환: (HTML, 생성중 여부)."""
            _bp = next((p for p in ps if p.kind.value == "blog"), None)
            if not _bp:
                return "", False
            _csv = _bp.payload.get("channel_status") or {}
            _has_short_piece = any(p.kind.value == "short" for p in ps)
            chips, sel_any, gen_any = "", False, False
            for ch, lab in (("shorts", "숏폼"), ("reels", "릴스"), ("naver", "네이버")):
                stt = (_csv.get(ch) or {}).get("status") or ""
                if stt == "done" or (not stt and _has_short_piece):   # 구건(상태 기록 이전)은 피스 실재로 판정
                    chips += ("<span class='text-[10px] font-bold text-emerald-600 bg-emerald-50 "
                              f"px-1.5 py-0.5 rounded-full'>{lab} ✓</span>")
                elif stt == "generating":
                    gen_any = True
                    chips += ("<span class='text-[10px] font-bold text-indigo-500 bg-indigo-50 "
                              f"px-1.5 py-0.5 rounded-full animate-pulse'>{lab} 만드는 중…</span>")
                else:                                   # not_requested·failed·기록 없음 → 선택 가능
                    sel_any = True
                    _retry = " 다시" if stt == "failed" else ""
                    chips += ("<label class='text-[10px] font-bold text-slate-600 bg-slate-100 px-1.5 py-0.5 "
                              "rounded-full cursor-pointer inline-flex items-center gap-1'>"
                              # ★ 기본 해제(2026-08-01 사장님 지시) — 셋 다 켜져 있어 '영상 만들기'만
                              #   누르면 요청하지 않은 쇼츠·릴스까지 만들어졌다(API 크레딧 소모).
                              f"<input type='checkbox' name='vp_{aid}' value='{ch}' "
                              f"class='w-3 h-3 accent-indigo-600'>{lab}{_retry}</label>")
            btn = (("<button type='button' onclick=\"vdMake('" + aid + "')\" "
                    "class='text-[10px] font-bold text-white bg-slate-800 hover:bg-slate-900 "
                    "px-2 py-1 rounded-full transition'>🎬 영상 만들기</button>") if sel_any else "")
            row = (f"<div id='vd_{aid}'" + (f" data-vgenrow='{aid}'" if gen_any else "")
                   + " class='mt-1.5 flex flex-wrap items-center gap-1'>" + chips + btn + "</div>")
            return row, gen_any
        for s in sets:
            ps = db.get_set_pieces(s["asset_id"])
            _vrow, _ = _video_row(s["asset_id"], ps)
            _nclk = sum(_ccounts.get(p.id[:8], 0) for p in ps)
            _ebadge = _expose_badge(ps)
            # 진행 중 판정(삭제 잠금용) — 다시쓰기 running 또는 영상 잡 진행 중
            _bpl = next((p.payload or {} for p in ps if p.kind.value == "blog"), {})
            _vj_b = _bpl.get("video_job") or {}
            _vj_live = False
            if _vj_b.get("status") in ("registered", "running", "retrying"):
                try:      # 유령 잡 필터(admin/busy와 동일 기준) — 2시간 넘으면 죽은 것으로 본다.
                    from datetime import datetime as _dvb
                    _vj_live = (_dvb.utcnow() - _dvb.fromisoformat(_vj_b.get("ts", ""))).total_seconds() < 7200
                except Exception:
                    _vj_live = False          # ts 불명 = 구식 기록 → 잠그지 않는다(삭제 영구 봉인 방지)
            s["busy"] = bool(_rewrite_running(_bpl) or _vj_live)
            thumb = ""
            for p in ps:
                ips = p.payload.get("image_paths") or ([p.payload.get("image_path")] if p.payload.get("image_path") else [])
                thumb = next((f"/dl/{s['asset_id']}/{os.path.basename(im)}" for im in ips if im), "")
                if thumb:
                    break
            seen, badges = set(), ""
            for p in ps:
                ic = _chan_icon.get(p.channel.value, "•")
                if ic not in seen:
                    seen.add(ic)
                    badges += f"<span>{ic}</span>"
            thumb_html = (f"<img src='{thumb}' onerror=\"this.onerror=null;this.outerHTML='<div class=\\'w-14 h-14 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center text-2xl text-white flex-shrink-0\\'>✨</div>'\" class='w-14 h-14 rounded-xl object-cover flex-shrink-0 bg-slate-100'>" if thumb
                          else "<div class='w-14 h-14 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center text-2xl text-white flex-shrink-0'>✨</div>")
            _cards.append(
                "<div class='group flex items-center gap-3 p-2.5 rounded-2xl border border-slate-100 bg-white hover:shadow-md hover:border-indigo-200 hover:-translate-y-0.5 transition-all'>"
                + thumb_html
                + f"<div class='flex-1 min-w-0'><div class='flex items-center gap-1 text-base leading-none mb-1.5'>{badges}"
                + (f"<span class='ml-1 text-[11px] font-bold text-violet-600 bg-violet-50 px-2 py-0.5 rounded-full' "
                   f"title='올린다 추적링크 클릭 기준(조회수 아님)'>이 콘텐츠로 온 손님 {_nclk}명</span>" if _nclk else "")
                + "</div>"
                + f"<div class='text-xs text-slate-400 font-medium'>{esc(s['created'])} · {s['n']}채널</div>"
                + (f"<div class='mt-1'>{_ebadge}</div>" if _ebadge else "")
                + _vrow + "</div>"
                + f"<a href='/me?view={s['asset_id']}' class='px-3.5 py-2 bg-indigo-600 hover:bg-indigo-700 active:scale-[.98] text-white text-xs font-bold rounded-xl transition'>보기</a>"
                # 🔒 진행 중(다시쓰기·영상)에는 삭제 잠금 — 지웠는데 되살아나는 경험을 없앤다
                #   (2026-08-01 실사고: 삭제 6분 뒤 끝난 다시쓰기가 글을 되살렸다. 서버 쪽 묘비로
                #    이미 막히지만, 사장님이 헛수고하지 않도록 버튼 단계에서도 안내한다.)
                + (("<span class='px-1.5 py-2 text-slate-200 text-base' title='작업이 끝난 뒤 지울 수 있어요'>"
                    + _ic("xcircle", "w-4 h-4") + "</span>") if s.get("busy") else
                   (f"<form method=post action='/me/set/{s['asset_id']}/delete' onsubmit=\"return confirm('이 콘텐츠를 삭제할까요?')\">"
                    + "<button class='px-1.5 py-2 text-slate-300 hover:text-rose-500 text-base transition' title='삭제'>"
                    + _ic("xcircle", "w-4 h-4") + "</button></form>")) + "</div>")
        hist = ("<div class='grid sm:grid-cols-2 gap-3'>" + "".join(_cards) + "</div>"
                # 영상 온디맨드 — 요청(선택 플랫폼 전송) + 생성중 카드 폴링(끝나면 새로고침으로 ✓ 반영)
                "<script>"
                "async function vdMake(aid){"
                "var sel=[].slice.call(document.querySelectorAll(\"input[name=vp_\"+aid+\"]:checked\"))"
                ".map(function(x){return x.value;});"
                "if(!sel.length){alert('만들 플랫폼을 선택해 주세요');return;}"
                "window.vmPick(null,aid,sel.join(','));}"     # ⭐ 대표 사진 고르기 모달 경유(구세트 포함)
                "(function(){var rows=document.querySelectorAll('[data-vgenrow]');if(!rows.length)return;"
                "var iv=setInterval(async function(){var busy=false;"
                "for(var i=0;i<rows.length;i++){var aid=rows[i].getAttribute('data-vgenrow');"
                "try{var d=await (await fetch('/me/video/status?asset_id='+aid)).json();"
                "var st=(d&&d.status)||{};"
                "if(st.shorts==='generating'||st.reels==='generating'||st.naver==='generating')busy=true;"
                "}catch(e){busy=true;}}"
                "if(!busy){clearInterval(iv);location.reload();}},8000);})();"
                "</script>" + _VMPICK_JS)
    else:
        hist = "<p class='text-slate-400 text-sm py-6 text-center'>아직 만든 콘텐츠가 없어요. 위에서 사진 올려 만들어보세요.</p>"
    # ── 최초 1회 온보딩 vs 작동 대시보드 ──
    onboarded = bool((t.industry or "").strip())
    if not onboarded:
        _multi = len(db.list_user_stores(u["id"])) > 1
        # 안내+뒤로가기 통합 배너(A3: 중복 안내박스 하나로) — 뒤로가기는 다른 가게가 있을 때만
        _back = (("<form method=post action='/me/store/cancel' class='flex-shrink-0'>"
                  "<button class='text-xs font-bold text-indigo-600 bg-white border border-indigo-200 "
                  "rounded-xl px-3 py-2 hover:bg-indigo-50 transition whitespace-nowrap'>← 뒤로가기</button></form>")
                 if _multi else "")
        intro = ("<div class='flex items-center gap-3 bg-[#EEF2FF] text-indigo-700 p-4 rounded-2xl mb-4 text-sm'>"
                 f"{_ic('store', 'w-5 h-5 flex-shrink-0')}"
                 "<div class='flex-1'>"
                 + ("<b>새 가게</b>를 추가했어요. <b>딱 3가지</b>만 알려주세요. (30초 · 실수라면 뒤로가기)"
                    if _multi else "가입 완료! 시작하려면 <b>딱 3가지</b>만 알려주세요. (30초)")
                 + f"</div>{_back}</div>")
        card = (f"<div class='{_CARD} p-5'>"
                "<h2 class='font-bold mb-3'>내 가게/상품 정보</h2>" + store_form_min + "</div>")
        return _subscriber_page(f"{esc(t.name)} · 시작 설정", banner + intro + card)
    # 온보딩 완료 → 사진 올려 생성이 메인
    from app.services import pay as _pay
    _plan = u.get("plan") or "free"
    _pn = {"free": "무료", "basic": "베이직", "pro": "프로", "self": "프로", "agency": "대행"}.get(_plan, _plan)
    if _is_owner(u):
        _pn, _usage, _upbtn = "사장님", "무제한 · 영구 라이선스", ""
    elif _plan == "free":
        _usage = f"무료 {u.get('free_used') or 0}/{FREE_LIMIT}회"
        _upbtn = (f"<a href='/billing?plan=pro' class='ml-auto {_BTN} text-sm px-4 py-2'>업그레이드</a>")
    else:
        _cap = _pay.PLANS.get(_plan, {}).get("monthly", 0)
        _usage = f"이번달 {db.month_usage(u['id'])}" + (f"/{_cap}건" if _cap else "건(무제한)")
        _upbtn = ""
    plan_card = (f"<div class='{_CARD} p-4 mb-4 flex items-center gap-3'>"
                 f"{_icchip('shield')}"
                 f"<div><div class='text-xs text-slate-400'>내 플랜</div>"
                 f"<div class='font-bold text-slate-900'>{_pn} · {_usage}</div></div>{_upbtn}</div>")
    # 무료 소진 → 결제 유도(전환 PHASE 3) — 방금 만든 품질 근거 + 유료 기능 맛보기(사실만, 과장 없음)
    _upsell = ""
    if (not _is_owner(u)) and _plan == "free" and (u.get("free_used") or 0) >= FREE_LIMIT:
        from app import config as _cfg2
        _perks = "".join(
            f"<div class='flex items-center gap-2 text-sm text-slate-600 py-1'>"
            f"<span class='w-1.5 h-1.5 rounded-full bg-indigo-500 flex-shrink-0'></span>{p}</div>"
            for p in [f"콘텐츠 계속 생성 (베이직 월 8건 · 프로 무제한)",
                      "순위 성장 추적 — 발행 전후 '5위→2위' 자동 비교",
                      "경쟁사 추적 — 옆집 대비 내 순위 매일 자동 체크",
                      "블로그 발행 자동 확인 + 주간 성과 리포트"])
        _upsell = ("<div class='bg-white border-2 border-indigo-200 rounded-2xl p-5 mb-4'>"
                   "<div class='font-extrabold text-slate-900 mb-1'>무료 2회를 다 쓰셨어요</div>"
                   "<p class='text-sm text-slate-500 mb-3'>방금 만든 그 품질 그대로 계속 — "
                   f"<b class='text-slate-800'>베이직 월 {_cfg2.PRICE_BASIC:,}원</b>이면 이런 게 열려요.</p>"
                   + _perks +
                   "<div class='flex gap-2 mt-3'>"
                   "<a href='/billing?plan=basic' class='flex-1 text-center bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-3 rounded-xl transition'>베이직 시작</a>"
                   f"<a href='/billing?plan=pro' class='flex-1 text-center bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold py-3 rounded-xl transition'>프로 (월 {_cfg2.PRICE_PRO:,}원)</a></div>"
                   "<p class='text-xs text-slate-400 mt-2'>연 결제 시 약 30% 할인 · 언제든 해지 가능</p></div>")
    # 📈 성과 기반 업셀(전환 개선 ③, 2026-07-31 사장님 승인) — '한도 소진'보다 강한 트리거:
    #   추적 키워드가 상위 20위 안에 들어온 순간, 그 순위를 근거로 제안(손실 회피 프레임).
    _rank_hook = ""
    if (not _is_owner(u)) and _plan == "free" and not _upsell:
        try:
            _best = None
            for _kw0 in db.tracked_keywords(t.id, 5):
                _h0 = db.rank_history(t.id, _kw0, limit=30)
                _r0 = _h0[-1].get("rank") if _h0 else None
                if isinstance(_r0, int) and 1 <= _r0 <= 20 and (_best is None or _r0 < _best[1]):
                    _best = (_kw0, _r0)
            if _best:
                from app import config as _cfg3
                _rank_hook = (
                    "<div class='bg-white border-2 border-emerald-200 rounded-2xl p-5 mb-4'>"
                    f"<div class='font-extrabold text-slate-900 mb-1'>🎉 사장님 글이 '<span class='text-emerald-600'>{esc(_best[0])}</span>' "
                    f"검색 <span class='text-emerald-600'>{_best[1]}위</span>에 있어요</div>"
                    "<p class='text-sm text-slate-500 mb-3'>순위는 새 글이 계속 올라와야 유지되고 올라갑니다 — "
                    "여기서 멈추면 경쟁 가게 글이 자리를 채워요.</p>"
                    "<div class='flex gap-2'>"
                    f"<a href='/billing?plan=pro' class='flex-1 text-center bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-xl transition'>스탠다드로 계속 밀어올리기 (월 {_cfg3.PRICE_PRO:,}원)</a>"
                    f"<a href='/billing?plan=basic' class='flex-1 text-center bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold py-3 rounded-xl transition'>라이트 (월 {_cfg3.PRICE_BASIC:,}원)</a>"
                    "</div></div>")
        except Exception:
            pass
    _sname = t.name if (t.name and t.name not in ("카카오회원", "구글회원", "회원", "내 가게")) else ""
    greeting = ("<div class='mb-6'>"
                + (f"<div class='inline-flex items-center gap-1.5 bg-[#EEF2FF] text-indigo-700 text-sm font-bold px-3 py-1.5 rounded-full mb-3'>{_ic('store', 'w-3.5 h-3.5')} {esc(_sname)}</div>" if _sname else "")
                + "<div class='text-2xl sm:text-3xl font-bold text-slate-900 leading-tight'>사진만 올리면 "
                "<span class='text-indigo-600'>5채널 콘텐츠</span>가 완성돼요</div></div>")
    # 🎯 진단→생성 연결(상위노출 PHASE 1): ?target_kw=미노출키워드&angle=review|howto|price
    _tkw = (request.query_params.get("target_kw") or "").strip()[:40]
    _angle = (request.query_params.get("angle") or "").strip()
    _angle = _angle if _angle in ("review", "howto", "price") else ""
    _src = (request.query_params.get("from") or "").strip()      # briefing 원클릭 진입(PHASE 3)
    # 타겟 키워드 진입(놓치는 키워드/브리핑) 시 만들기 섹션으로 자동 스크롤 — 어디로 왔는지 헷갈림 방지
    _scrolljs = ("<script>window.addEventListener('load',function(){var b=document.getElementById('makebox');"
                 "if(b)b.scrollIntoView({behavior:'smooth',block:'start'});});</script>" if _tkw else "")
    upload_section = ("<div id='makebox' class='bg-white rounded-3xl border border-slate-100 shadow-sm p-6 sm:p-7'>"
                      "<div class='mb-5'><div class='text-lg font-extrabold text-slate-900'>콘텐츠 만들기</div>"
                      "<div class='text-sm text-slate-400'>가게 이름·사진만 있으면 끝</div></div>"
                      + _upload_form_html(t, tok, target_kw=_tkw, angle=_angle, src=_src) + "</div>" + _scrolljs)
    content = ("<div id='myContent' class='bg-white rounded-3xl border border-slate-100 shadow-sm p-5'>"
               "<h2 class='font-bold text-slate-900 mb-1'>내 콘텐츠</h2>"
               "<p class='text-xs text-slate-400 mb-3'>‘보기’를 누르면 결과가 나와요.</p>" + hist + "</div>")
    # ('주방은 보여주지 않는다') 통계 KPI 카드(만든 세트·발행물·평균 노출점수) 삭제 —
    # 자체 채점·개수는 허영 지표. 사장님에게 의미 있는 건 실측(순위·유입·문의)뿐.
    kw_card = ""    # (auto) '노리는 키워드' 카드 제거
    view = (request.query_params.get("view") or "").strip()
    tab = (request.query_params.get("tab") or "").strip()
    result_html = _result_html(u, view, back_href="/me?tab=content", back_label="◀ 내 콘텐츠") if view else None
    _sbadge = (f"<div class='inline-flex items-center gap-1.5 bg-indigo-50 text-indigo-700 text-sm font-bold px-3 py-1.5 rounded-full mb-4'>{esc(_sname)}</div>" if _sname else "")
    _fw = "bg-white rounded-3xl border border-slate-100 shadow-sm p-6 sm:p-8"
    # 사이드바 클릭 = 전체 폭 단일 패널 전환 (내 콘텐츠 / 리포트 / 결과 / 만들기)
    if result_html:                                        # 콘텐츠 결과 (전체 폭)
        active = "content"
        main_inner = _sbadge + f"<div class='{_fw}'>{result_html}</div>"
    elif tab == "content":                                # 내 콘텐츠 (전체 폭)
        active = "content"
        main_inner = (_sbadge + f"<div class='{_fw}'>"
                      "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>내 콘텐츠</h2>"
                      "<p class='text-sm text-slate-400 mb-5'>‘보기’를 누르면 결과가 크게 나와요.</p>" + hist + "</div>")
    elif tab == "report":                                 # ('주방은 보여주지 않는다') 리포트 탭 삭제
        # 결과는 홈 한 줄 + 증빙 접힘이 전부 — 구 링크·북마크는 홈으로.
        return RedirectResponse("/me", status_code=303)
    else:                                                 # ✨ 만들기 (기본) — 완성되면 여기(만들기 대시보드)에 결과 표시
        active = "create"
        _made = (request.query_params.get("made") or "").strip()
        _made_html = ""
        if _made:                                         # 방금 생성 완료 → 만들기 화면에 결과 인라인 표시(내콘텐츠엔 이미 저장됨)
            _rh = _result_html(u, _made, back_href="/me", back_label="＋ 새로 만들기 ↓")
            if _rh:
                _made_html = f"<div class='{_fw} mb-6'>{_rh}</div>"
        # 📝 블로그 미연결 유도(온보딩 완료자) — 연결하면 발행확인·순위매칭 정확
        _blog_nudge = ""
        if not getattr(t, "blog_id", ""):
            _blog_nudge = ("<div class='flex items-center gap-3 bg-emerald-50 border border-emerald-100 rounded-2xl p-4 mb-5'>"
                           "<span class='text-indigo-600'>" + _ic("pen", "w-5 h-5") + "</span>"
                           "<div class='flex-1 min-w-0 text-sm text-slate-700'><b>내 네이버 블로그를 연결</b>하면 "
                           "발행 여부 자동 확인 + 내 블로그 순위 추적이 정확해져요. (공개 RSS만 사용)</div>"
                           "<a href='/me#blog' class='flex-shrink-0 bg-emerald-600 text-white text-sm font-bold px-4 py-2 rounded-xl hover:bg-emerald-700 transition'>연결하기</a></div>")
        # 🔔 앱내 알림(발행 리마인더 등) — 보여주고 읽음 처리
        _notices = db.unread_notices(t.id)
        _notice_html = ""
        if _notices:
            _notice_html = "".join(
                "<div class='flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-3'>"
                "<span class='text-amber-500'>" + _ic("message", "w-5 h-5") + "</span>"
                f"<div class='flex-1 text-sm text-amber-800'>{esc(n.get('text') or '')}</div>"
                "<a href='/me' class='flex-shrink-0 bg-amber-500 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>오늘 만들기</a></div>"
                for n in _notices[:2])
            db.mark_notices_read(t.id)
        if _made_html:
            main_inner = _made_html + upload_section
        else:
            # '오늘 할 일'은 브리핑 카드 하나로 통합(온보딩 P3) — 기존 '오늘의 액션'(_daily_action)
            # 카드는 브리핑과 중복이라 제거. 신규 사장님은 시작 가이드가 다음 할 일을 안내.
            # 오늘 발행 예정(대량 P4) — "오늘 이 글 복붙 발행하세요" 반자동 안내
            _due_html = ""
            try:
                from app.services import mass as _mass
                _due = _mass.due_today(t)
                def _due_trust(d):
                    """오늘 카드 안 근거 카드(접힘) — 실패해도 카드 표시를 막지 않음."""
                    try:
                        _pc0 = db.get_piece(d.get("piece_id") or "")
                        return _trust_card_html(_pc0) if _pc0 else ""
                    except Exception:
                        return ""
                def _golden_line():
                    """골든타임 안내(3-2) — 정보 제공만, 버튼·설정 없음. 시각 경과 시 문구 전환."""
                    try:
                        from datetime import datetime as _dtg, timedelta as _tdg
                        from app.services import pubcal as _pc
                        g = _pc.golden_hour(t)
                        now_h = (_dtg.utcnow() + _tdg(hours=9)).hour
                        if now_h < g["hour"]:
                            why = (f"손님들이 가장 많이 찾아오는 시간({g['peak']}시) 직전이에요" if g["basis"] == "measured"
                                   else "손님들이 검색을 시작하기 직전이에요")
                            txt = f"오늘은 {g['hour']}시~{g['hour'] + 1}시 사이 발행이 제일 좋아요 — {why}."
                        else:
                            txt = "지금 바로 발행해도 좋아요."
                        return f"<div class='text-xs text-violet-600 mt-2'>{esc(txt)}</div>"
                    except Exception:
                        return ""
                _gl = _golden_line()
                _cards = []
                for d in _due:
                    if not d.get("asset_id"):
                        continue
                    _cards.append(
                        "<div class='bg-violet-50 border border-violet-200 rounded-2xl p-4 mb-5'>"
                        "<div class='flex items-center gap-3'>"
                        f"<span class='text-violet-500'>{_ic('calendar', 'w-5 h-5 flex-shrink-0')}</span>"
                        "<div class='flex-1 text-sm text-violet-800'>오늘 발행할 글이 <b>준비됐어요</b> — 복붙만 하면 돼요. 발행 후 주소는 자동 추적돼요.</div>"
                        f"<a href='/kit/{esc(d['asset_id'])}/naver' class='flex-shrink-0 bg-violet-600 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>발행 소재 열기</a></div>"
                        + (_gl if not _cards else "")          # 골든타임은 첫 카드에만(중복 방지)
                        + f"{_due_trust(d)}</div>")
                _due_html = "".join(_cards)
            except Exception:
                pass
            # ('주방은 보여주지 않는다' 다이어트) '사진 필요'·'N일 쉬었어요' 카드 삭제 —
            # 할 일 안내는 브리핑 카드 하나가 담당(잔소리·중복 금지). 발행 준비 카드만 유지(실제 할 일).
            # 결과 한 줄('주방은 보여주지 않는다') — 실측 요약 한 줄 + 증빙 접힘(영수증). 리포트 탭 대체.
            _conv_card = ""
            try:
                _cv = db.weekly_conversion(t.id)
                _srcs = ("블로그", "인스타", "당근", "기타", "모름")
                _src_btns = "".join(
                    f"<button name=source value='{s}' class='py-2 rounded-lg bg-slate-100 hover:bg-emerald-100 text-slate-700 text-xs font-bold'>{s}</button>"
                    for s in _srcs)
                # 상위노출 요약 + 증빙 rows — 블로그 글 순위 · 지도 순위 (전부 저장된 실측, 네트워크 0)
                _rrows, _exposed, _best = "", 0, None

                def _rrow(lab, val):
                    return (f"<div class='flex justify-between text-sm py-1.5 border-b border-slate-100'>"
                            f"<span class='text-slate-600 truncate pr-3'>{esc(lab)}</span>"
                            f"<span class='font-bold text-slate-800 whitespace-nowrap'>{val}</span></div>")
                try:
                    for _pub in db.list_blog_publishes(t.id, limit=8):
                        _pkw = (_pub.get("target_kw") or "").strip()
                        if not _pkw:
                            continue
                        _h = [h["rank"] for h in db.rank_history(t.id, _pkw, kind="post") if h.get("rank")]
                        _r = _h[-1] if _h else None
                        if _r:
                            _exposed += 1 if _r <= 10 else 0
                            _best = _r if (_best is None or _r < _best) else _best
                        _rrows += _rrow(f"글 · {_pkw}", f"{_r}위" if _r else "추적 중")
                    from app.services import place_opt as _po
                    for _pr in (_po.place_summary(t).get("place_ranks") or [])[:4]:
                        _r = _pr.get("rank")
                        if _r:
                            _exposed += 1 if _r <= 5 else 0
                            _best = _r if (_best is None or _r < _best) else _best
                        _rrows += _rrow(f"지도 · {_pr['keyword']}", f"{_r}위" if _r else "5위 밖")
                except Exception:
                    pass
                _expo = ((f" · 상위노출 <b class='text-indigo-600'>{_exposed}곳</b>"
                          + (f" <span class='text-slate-400'>(최고 {_best}위)</span>" if _best else ""))
                         if _exposed else "")
                _receipt = ((f"<details class='mt-1'><summary class='cursor-pointer text-xs text-slate-400 "
                             "hover:text-slate-600 list-none select-none'>자세히 →</summary>"
                             f"<div class='mt-2 pt-1 border-t border-slate-100'>{_rrows}</div></details>")
                            if _rrows else "")
                _conv_card = (
                    "<div class='bg-white rounded-2xl border border-slate-200 shadow-sm px-4 py-3 mb-5'>"
                    "<div class='flex items-center gap-3'>"
                    "<div class='flex-1 text-sm text-slate-700'>이번 주 "
                    f"발행 <b>{_cv['posts']}</b> · 클릭 <b class='text-indigo-600'>{_cv['clicks']}</b> · "
                    f"문의 <b class='text-emerald-600'>{_cv['inquiries']}</b>{_expo}</div>"
                    "<details class='relative'><summary class='cursor-pointer text-xs font-bold text-emerald-600 "
                    "hover:text-emerald-700 list-none select-none whitespace-nowrap'>＋ 문의 기록</summary>"
                    "<form method=post action='/me/inquiry' class='absolute right-0 top-7 z-10 bg-white border "
                    "border-slate-200 rounded-xl shadow-lg p-3 w-64'>"
                    f"<div class='grid grid-cols-5 gap-1 mb-2'>{_src_btns}</div>"
                    "<input name=memo maxlength=200 placeholder='메모(선택)' class='w-full border border-slate-200 rounded-lg px-2 py-1.5 text-xs'>"
                    "</form></details></div>"
                    + _receipt + "</div>")
            except Exception:
                _conv_card = ""
            # ('주방은 보여주지 않는다' 다이어트) 정보성 글 배너·발행 캘린더 카드 삭제 —
            # 정보성 글은 리포트의 도구 섹션에서 접근, 발행 리듬은 브리핑(할 일 1개)이 담당.
            # 시작 가이드는 첫 콘텐츠 전에만(온보딩 끝난 사장님에게 체크리스트 반복 금지).
            _guide = _guide_card(t) if not db.list_sets(tenant_id=t.id, limit=1) else ""
            _task = _due_html or _briefing_card(t, _plan)     # 오늘 할 일은 딱 1장(발행 준비 우선)
            # 🧰 도구 서랍(리포트 탭 대체) — 블로그 연결·플레이스 도구·매장 QR·실경험. 기본 접힘.
            _fw2 = "bg-white rounded-3xl border border-slate-100 shadow-sm p-6 sm:p-8"
            try:
                _tools = ("<details id='tools' class='mt-5 bg-white rounded-3xl border border-slate-200 shadow-sm'>"
                          "<summary class='cursor-pointer select-none list-none p-5 flex items-center gap-4'>"
                          "<span class='w-12 h-12 rounded-2xl bg-[#EEF2FF] flex items-center justify-center text-2xl flex-shrink-0'>🧰</span>"
                          "<span class='flex-1 min-w-0'>"
                          "<span class='block text-base font-extrabold text-slate-900'>도구</span>"
                          "<span class='block text-xs text-slate-400 mt-0.5'>블로그 연결 · 플레이스 · 매장 QR · 실경험 답변</span></span>"
                          "<span class='text-slate-300 text-xl'>▾</span></summary>"
                          "<div class='px-5 pb-5 space-y-5'>"
                          + _blog_connect_card(t, _fw2) + _place_card(t, _fw2) + _track_qr_box(t, _fw2)
                          + "<a href='/me/experience' class='block text-sm font-semibold text-slate-500 "
                          "hover:text-slate-700'>📝 사장님 실경험 답변 관리 →</a>"
                          "</div></details>"
                          "<script>if(['#tools','#blog','#place','#qr'].indexOf(location.hash)>=0)"
                          "{var _td=document.getElementById('tools');if(_td)_td.setAttribute('open','');}</script>")
            except Exception:
                _tools = ""
            main_inner = (greeting + _conv_card + _upsell + _rank_hook + _task + _notice_html
                          + _guide + _blog_nudge + upload_section
                          + "<div class='mt-5'></div>" + _store_info_card(t) + _tools)
    # 🆕 새로 추가한 '빈 새 가게'면 실수 대비 '뒤로가기(취소)' 배너
    if t.name == "새 가게" and len(db.list_user_stores(u["id"])) > 1 and not db.list_sets(tenant_id=t.id):
        _backban = ("<div class='flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-5'>"
                    "<span class='text-amber-500'>" + _ic("store", "w-5 h-5") + "</span>"
                    "<div class='flex-1 text-sm text-amber-800'><b>새 가게</b>를 추가했어요. 가게 이름을 넣고 자동 인식하세요. 잘못 누르셨나요?</div>"
                    "<form method=post action='/me/store/cancel'><button class='bg-white border border-amber-300 text-amber-700 text-sm font-bold px-4 py-2 rounded-xl hover:bg-amber-100 transition whitespace-nowrap'>← 뒤로가기</button></form></div>")
        main_inner = _backban + main_inner
    from app import landing
    # ('주방은 보여주지 않는다') 리포트 탭 삭제 — 결과는 홈 한 줄 + 증빙 접힘이 전부.
    _navitems = [("wand", "홈", "/me", "create"), ("book", "내 콘텐츠", "/me?tab=content", "content")]

    def _navlink(i, l, h, key):
        cls = ("bg-[#EEF2FF] text-indigo-700" if key == active
               else "text-slate-500 hover:bg-slate-50 hover:text-slate-900")
        return (f"<a href='{h}' class='flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold {cls} transition'>"
                f"{_ic(i, 'w-4 h-4 flex-shrink-0')}{l}</a>")

    # 🏪 다중 가게 전환기 + 가게 추가
    _stores = db.list_user_stores(u["id"])

    def _storeitem(st):
        on = (st.id == t.id)
        nm = esc(st.name) if getattr(st, "name", "") and st.name not in ("내 가게", "카카오회원", "구글회원") else "내 가게"
        cls = "bg-indigo-600 text-white" if on else "bg-slate-50 text-slate-600 hover:bg-slate-100"
        chk = "<span class='ml-auto text-xs'>✓</span>" if on else ""
        return (f"<form method=post action='/me/store/switch'><input type=hidden name=tenant_id value='{st.id}'>"
                f"<button class='w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-semibold {cls} transition text-left'>"
                f"{_ic('store', 'w-4 h-4 flex-shrink-0')}<span class='truncate'>{nm}</span>{chk}</button></form>")
    _storebox = ("<div class='mb-5'><div class='text-[11px] font-bold text-slate-400 px-2 mb-1.5'>내 가게</div>"
                 "<div class='space-y-1'>" + "".join(_storeitem(s) for s in _stores) + "</div>"
                 "<form method=post action='/me/store/add'>"
                 "<button class='w-full mt-1.5 flex items-center justify-center gap-1 px-3 py-2 rounded-xl text-sm font-bold text-indigo-600 border border-dashed border-indigo-200 hover:bg-indigo-50 transition'>＋ 가게 추가</button></form></div>")
    sidebar = ("<aside class='hidden lg:flex flex-col w-56 flex-shrink-0 border-r border-slate-100 bg-white p-4 sticky top-0 h-screen'>"
               f"<a href='/' class='flex items-center gap-2 font-extrabold text-lg mb-6 px-2'>{landing.LOGO}<span>올린다</span></a>"
               + _storebox
               + "<nav class='space-y-1'>" + "".join(_navlink(*n) for n in _navitems)
               + f"</nav><div class='mt-auto px-3 pt-4 border-t border-slate-100'><div class='text-xs text-slate-400 mb-1'>{_pn}</div>"
               "<a href='/logout' class='text-sm font-semibold text-slate-400 hover:text-slate-700'>로그아웃</a></div></aside>")
    _mobnav = ("<div class='flex lg:hidden items-center gap-2 mb-4 overflow-x-auto'>"
               + "".join(_navlink(*n) for n in _navitems)
               + "<a href='/logout' class='ml-auto text-sm text-slate-400 whitespace-nowrap'>로그아웃</a></div>")
    page = (landing._HEAD
            + "<div class='flex min-h-screen bg-[#F9FAFB]'>" + sidebar
            + "<main class='flex-1 min-w-0 px-5 sm:px-8 py-8'>"
            + "<div class='lg:hidden mb-3'>" + _storebox + "</div>" + _mobnav
            # 🔍 노출 현황을 최상단에(CLAUDE.md: 첫 화면 1번 숫자는 발행량이 아니라 노출 상태).
            #   콘텐츠 목록·발행 이력은 그 아래로 — 순서만 바꾸고 기존 화면은 그대로 둔다.
            + "<div class='max-w-[1400px]'>" + banner + exposure_card + main_inner + "</div></main></div>"
            + landing._FOOT)
    return HTMLResponse(page)


@app.post("/me/store")
def my_store(request: Request, name: str = Form(""), industry: str = Form(""), region: str = Form(""),
             biz_type: str = Form("local"), phone: str = Form(""), address: str = Form(""),
             marketplace: str = Form(""), brand_name: str = Form(""),
             search_kw: str = Form(""), buy_url: str = Form(""), map_url: str = Form(""),
             lat: str = Form(""), lon: str = Form(""), naver_blog: str = Form("")):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    db.rename_tenant(t.id, name, industry, region)
    db.update_tenant_profile(t.id, phone, address, t.hours, (map_url.strip() or t.map_url))
    db.update_tenant_classification(t.id, biz_type, marketplace, buy_url, search_kw, brand_name)
    if lat.strip() and lon.strip():                 # 자동인식 좌표 저장(사진 GPS 지오태그용)
        db.set_tenant_coords(t.id, lat, lon)
    if industry.strip():
        from app.industries import ensure_profile
        ensure_profile(industry.strip())
    # 온보딩에서 네이버 블로그(선택) 입력 시 — 검증 성공만 저장, 실패는 설정 저장은 유지하고 안내
    if naver_blog.strip() and not getattr(t, "blog_id", ""):
        from app.services import blogsync
        from urllib.parse import quote as _q
        v = blogsync.verify_blog(naver_blog)
        if v["ok"]:
            db.set_tenant_blog(t.id, v["url"], v["blog_id"])
            return RedirectResponse("/me?ok=" + _q(f"설정 저장 + 블로그 '{v['title'] or v['blog_id']}' 연결 완료!"),
                                    status_code=303)
        return RedirectResponse("/me?err=" + _q(f"설정은 저장했어요. 블로그는 연결 못했어요 — {v['error']}"),
                                status_code=303)
    # 온보딩 유도(블로그템플릿 PHASE 1): 매장형인데 고정정보가 비면 매장 정보 입력 권유
    t2 = db.get_tenant(t.id)
    if (biz_type or "local") != "seller" and t2 and not ((t2.address or "").strip() and (t2.phone or "").strip()):
        from urllib.parse import quote as _q2
        return RedirectResponse("/me?ok=" + _q2("설정 저장! 아래 '매장 정보'(주소·전화·영업시간·주차)까지 채우면 "
                                                "모든 블로그 글에 자동으로 들어가요"), status_code=303)
    return RedirectResponse("/me?ok=설정을 저장했어요", status_code=303)


@app.post("/me/blog")
def my_blog_connect(request: Request, blog: str = Form("")):
    """내 네이버 블로그 연결(블로그등록 PHASE 1) — URL/아이디 유연 입력 → 정규화 + RSS 실존 검증.
    빈 값 제출 = 연결 해제. 검증 실패 시 저장하지 않고 정직하게 안내."""
    from urllib.parse import quote as _q
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    from app.services import blogsync
    raw = (blog or "").strip()
    if not raw:                                      # 연결 해제
        db.set_tenant_blog(t.id, "", "")
        return RedirectResponse("/me?ok=" + _q("블로그 연결을 해제했어요"), status_code=303)
    v = blogsync.verify_blog(raw)
    if not v["ok"]:
        return RedirectResponse("/me?err=" + _q(v["error"]), status_code=303)
    db.set_tenant_blog(t.id, v["url"], v["blog_id"])
    msg = f"블로그 '{v['title'] or v['blog_id']}' 연결 완료! 이제 발행 확인·순위 매칭이 정확해져요"
    return RedirectResponse("/me?ok=" + _q(msg), status_code=303)


def _confirm_blog_publish(t, piece, url: str, matched_by: str, score: float = 1.0,
                          post_title: str = "", published_at: str = "") -> None:
    """발행 확인 + 자동 연쇄 — pipesync로 위임(파이프 A1·A2: pubDate 보정→생존신고 즉시→링크 안내)."""
    from app.services import pipesync
    pipesync.confirm_publish(t, piece, url, matched_by, score, post_title, published_at)


def _tenant_blog_pieces(tid: str, limit_sets: int = 30) -> list:
    """이 가게의 블로그 생성글(최신순)."""
    out = []
    for s in db.list_sets(tenant_id=tid, limit=limit_sets):
        for p in db.get_set_pieces(s["asset_id"]):
            if p.kind.value == "blog":
                out.append(p)
    return out


@app.post("/api/blog/check-published")
def api_blog_check_published(request: Request):
    """등록 블로그 RSS ↔ 올린다 생성글 매칭 → '실제 발행' 자동 확인(블로그등록 PHASE 2).
    임계 미달 매칭은 발행으로 만들지 않음(정직성) — 수동 확인 폼 병행."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    if not getattr(t, "blog_id", ""):
        return JSONResponse({"error": "먼저 내 네이버 블로그를 연결해 주세요.", "need_blog": True}, status_code=400)
    # 완전 자동 동기화와 동일 경로(pipesync) — 이 버튼은 '지금 새로고침' 보조일 뿐(주기 폴링이 기본)
    from app.services import blogsync, pipesync
    feed = blogsync.fetch_feed(t.blog_id)
    if not feed["ok"]:
        return JSONResponse({"error": "지금 블로그 확인이 어려워요. 잠시 후 다시 시도해 주세요."}, status_code=502)
    if not feed["exists"]:
        return JSONResponse({"error": "블로그를 찾지 못했어요. 연결을 다시 확인해 주세요."}, status_code=400)
    r = pipesync.auto_sync_tenant(t)
    n = r["auto"] + r["external"]
    return JSONResponse({"rss_posts": len(feed.get("posts") or []),
                         "found": [{"n": n}] if n else [], "synced": n})


@app.post("/me/blog/published")
def my_blog_published(request: Request, piece_id: str = Form(""), url: str = Form("")):
    """'발행함' 수동 확인 — 사용자가 발행 URL 붙여넣기(자동 매칭이 어려울 때 병행 경로)."""
    from urllib.parse import quote as _q
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    piece = db.get_piece(piece_id.strip())
    back = f"/kit/{piece.asset_id}/naver" if piece else "/me"
    if not piece or piece.tenant_id != t.id or piece.kind.value != "blog":
        return RedirectResponse("/me?tab=content&err=" + _q("내 블로그 글을 찾지 못했어요"), status_code=303)
    url = (url or "").strip()
    from app.services import blogsync
    if not blogsync.normalize_blog_id(url) or "blog.naver.com" not in url:
        return RedirectResponse(back + "?err=" + _q("네이버 블로그 글 주소를 붙여넣어 주세요 (예: https://blog.naver.com/아이디/글번호)"),
                                status_code=303)
    if getattr(t, "blog_id", "") and not blogsync.is_my_post_url(url, t.blog_id):
        return RedirectResponse(back + "?err=" + _q(f"등록된 블로그(blog.naver.com/{t.blog_id})의 글 주소가 아니에요"),
                                status_code=303)
    _confirm_blog_publish(t, piece, url, "manual")
    # 순위 추적 연계(태그 후보 등록) — 추적 볼륨 한도 내에서만(초과 시 스킵). 태그 상위 2~3개 중 검색형만.
    try:
        _existing = set(db.tracked_keywords(t.id, limit=20))
        _cap = 10                                          # 추적 볼륨 한도(기존 tracked_keywords 기본)
        if len(_existing) < _cap:
            import re
            from app.services import indschema as _isc2
            _tags = _blog_tags(t, piece)
            # 검색형 태그만(업종 일반태그·속성 토큰·지역 결합 4자+) 상위 2~3 — 스키마 기반(하드코딩 0)
            _schT = _isc2.get_schema(getattr(t, "industry", ""), getattr(t, "biz_type", "local") or "local")
            _mk = set()
            for _x in (_schT.get("general_tags") or []) + _isc2.attribute_tokens(_schT):
                _n = re.sub(r"[^가-힣A-Za-z0-9]", "", _x or "")
                if len(_n) >= 2:
                    _mk.add(_n)
            for _tk in (getattr(t, "region", "") or "").split():
                _s = re.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|시|군|구|도)$", "", _tk)
                if len(_s) >= 2:
                    _mk.add(_s)
            _cands = [tg for tg in _tags if len(tg) >= 4 and any(m in tg for m in _mk)][:3]
            for tg in _cands:
                if tg not in _existing and len(_existing) < _cap:
                    db.save_rank_snapshot(t.id, tg, None)  # null 랭크=등록만(다음 순위 크론이 확인)
                    _existing.add(tg)
    except Exception:
        pass
    return RedirectResponse(back + "?ok=" + _q("발행 기록 완료! 이 글의 순위 추적이 시작돼요"), status_code=303)


def _canonical_slug(tenant, blog) -> str:
    """★ 세트 확정 슬러그 — 발행 산출물 모든 명명(태그·에셋 파일명·폴더·zip·txt·영상)의 단일 소스.
    소스 = 세트 '제목'(사용자가 보고 선택하는 주제) — selected_title 우선, 없으면 title.
    제목이 이 세트의 실제 주제이자 폴더명(_safe_title)의 소스이므로, 제목에서 유도하면
    폴더=파일명이 구조적으로 일치한다. tenant-level 인벤토리 오염('레이')에 영향받지 않는다.
    제목 선택(PHASE B)이 바뀌면 슬러그도 자동으로 따라간다. 업종 불문 단일 규칙."""
    import re as _r
    from app.services import indschema as _isc
    pl = (getattr(blog, "payload", None) or {}) if blog else {}
    title = ((pl.get("selected_title") or pl.get("title") or "")).strip()   # 선택 제목이 권위
    kw = ((pl.get("target_keywords") or [""])[0] or "").strip()
    biz = getattr(tenant, "biz_type", "local") or "local"
    ind = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
    sch = _isc.get_schema(getattr(tenant, "industry", ""), biz)
    attr_vocab = _isc.attribute_tokens(sch)
    region = getattr(tenant, "region", "") or ""
    gen0 = (sch.get("general_tags") or [ind] or [""])[0] if (sch.get("general_tags") or ind) else ind

    def _wb(tok, text):                                  # 단어경계 매칭(앞이 한글이면 불일치)
        return bool(tok) and bool(_r.search(r"(?<![가-힣])" + _r.escape(tok), text or ""))

    # 이 세트의 주제 속성 토큰 = '제목'에 단어경계로 등장하는 스키마 속성(중고차=그랜저, 캔들=라벤더 등)
    title_attr = next((a for a in attr_vocab if _wb(a, title)), "")
    kw_norm = _r.sub(r"\s+", "", kw)
    kw_attr = next((a for a in attr_vocab if a and a in kw_norm), "")
    if title_attr:
        # 제목 주제가 명확 — 키워드가 제목과 정합하면 키워드 형태 유지, 어긋나면(레이 vs 그랜저) 제목 주제 채택
        base = kw_norm if (kw_attr and _wb(kw_attr, title)) else (title_attr + gen0)
    elif kw_attr and not _wb(kw_attr, title):
        # 제목엔 속성 없고 키워드에만 stale 속성 → 오염 의심, 지역+업종으로 안전 재구성
        base = seo._kw_shorten(region).replace(" ", "") + ind
    else:
        # 속성 토큰 없는 업종(카페 등) → 확정 키워드(제목 정합) 그대로, 없으면 지역+업종
        base = kw_norm or (seo._kw_shorten(region).replace(" ", "") + ind)
    base = _r.sub(r'[\\/:*?"<>|\s]', "", base).strip("_")[:30]
    return base or "사진"


def _canonical_keyword(tenant, blog) -> str:
    """★ 세트 확정 키워드(읽기 전용, 단일 소스) — 제목(주제) 유래. 슬러그와 동일 원칙.
    tenant-level 인벤토리·낡은 target_keywords 오염 무관. 본문·캡션·영상메타·태그가 전부 이걸 참조.
    반환은 읽기용 구('그랜저 중고차'). 업종 중립."""
    import re as _r
    from app.services import indschema as _isc
    pl = (getattr(blog, "payload", None) or {}) if blog else {}
    title = ((pl.get("selected_title") or pl.get("title") or "")).strip()
    kw = ((pl.get("target_keywords") or [""])[0] or "").strip()
    biz = getattr(tenant, "biz_type", "local") or "local"
    ind = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
    sch = _isc.get_schema(getattr(tenant, "industry", ""), biz)
    attr_vocab = _isc.attribute_tokens(sch)
    region = getattr(tenant, "region", "") or ""
    gen0 = (sch.get("general_tags") or [ind] or [""])[0] if (sch.get("general_tags") or ind) else ind

    def _wb(tok, text):
        return bool(tok) and bool(_r.search(r"(?<![가-힣])" + _r.escape(tok), text or ""))

    title_attr = next((a for a in attr_vocab if _wb(a, title)), "")
    kw_attr = next((a for a in attr_vocab if a and a in _r.sub(r"\s+", "", kw)), "")
    if title_attr:                                     # 제목 주제가 명확 → 정합 키워드 or 제목주제+업종
        return kw if (kw_attr and _wb(kw_attr, title)) else f"{title_attr} {gen0}".strip()
    if kw_attr and not _wb(kw_attr, title):            # 제목엔 없고 키워드만 stale 속성 → 안전(지역+업종)
        return f"{seo._kw_shorten(region)} {ind}".strip()
    return kw or f"{seo._kw_shorten(region)} {ind}".strip()


def _seo_photo_name(tenant, blog) -> str:
    """(이미지 SEO 5-1) 다운로드 파일명 — {확정슬러그}-{피사체}. 슬러그는 _canonical_slug 단일 소스
    (현재 세트 컨텍스트 정합 보장). 피사체는 사장님이 확인한 사진 내용에서만(없으면 생략 — 날조 금지)."""
    import re as _r
    base = _canonical_slug(tenant, blog)
    subject = ""
    m = _r.search(r"사진 내용\(사장님 확인[^)]*\):\s*([^\n]+)", (blog.payload or {}).get("gen_source") or "")
    if m:
        toks = [t for t in _r.findall(r"[가-힣A-Za-z0-9]{2,}", m.group(1)) if t not in ("사진", "모습", "장면")][:2]
        subject = "-".join(toks)
    _toks = [x for p in (base, subject) if p for x in p.split("-") if x]
    _toks = list(dict.fromkeys(_toks))
    _toks = [t for t in _toks if not any(t != o and t in o for o in _toks)]   # 부분 포함 dedupe(2-2)
    name = _r.sub(r"[^가-힣A-Za-z0-9\-]", "", "-".join(_toks))
    return name or "photo"


# 캡션 소스 계약(자막 사고와 동일 원칙): 렌더러는 vision 출력의 '묘사 값'만 받는다.
# 필드 라벨·분석 프리앰블·마크다운이 섞인 원문 전달 금지 — 아래 파서가 스트립, 스트립 후 공백이면
# 단건 재분석 폴백(기존 4-2a)이 그대로 이어받는다.
_CAP_LABEL = None    # 지연 컴파일 — vision 필드 라벨/프리앰블 패턴
_CAP_DOC = ("등록증", "계약서", "신분증", "면허증", "증명서", "서류", "기록부", "문서")   # 개인정보 위험 서류 키워드


def _clean_caption_desc(raw: str) -> str:
    """vision 원문 라인 → 순수 묘사 값. 마크다운 제거 + 필드 라벨 프리픽스 스트립 +
    프리앰블(분석 안내문) 검출 시 빈 값(→ 재분석 폴백行)."""
    import re as _r
    global _CAP_LABEL
    if _CAP_LABEL is None:
        _CAP_LABEL = _r.compile(r"^(무엇이 보이는가|보이는 것|해석|분석|촬영 팁|추천 활용|전체)\s*[:：]?\s*")
    s = _r.sub(r"[*_`#]+", "", raw or "").strip()                    # 마크다운 잔재 제거
    # ★ 내부 표기 제거(2026-08-01 사장님 지적) — vision이 붙이는 '[오버레이]' 같은 대괄호 토큰이
    #   캡션에 그대로 새어 네이버 이미지 설명에 박혔다. 캡션은 손님과 검색엔진이 읽는 글이다.
    s = _r.sub(r"\[[^\]]{1,20}\]", " ", s)
    s = _r.sub(r"^[\d)\-•\s.]+", "", s).strip()
    s = _CAP_LABEL.sub("", s).strip()
    s = _r.sub(r"^[:：\-\s]+", "", s).strip()
    if _r.search(r"(관점에서 분석|분석한 결과|분석입니다|분석하겠|다음과 같)", s):
        return ""                                                     # 프리앰블 문장 — 묘사 아님
    if not _r.search(r"[가-힣]{2,}", s):
        return ""                                                     # 내용어 없는 라인('(5/20).' 류 카운터) — 묘사 아님
    if _r.fullmatch(r"[\s()\d번째쨰,·\-—]+", s):
        return ""                                                     # 순서 표기만 남은 잔해('(12번째)') — 묘사 아님
    s = s.rstrip(".")
    if len(s) > 60:                                                   # 어절 경계 절단('…넓.' 류 잘림 방지)
        cut = s[:60]
        s = cut[:cut.rfind(" ")].rstrip(" ,·—-") if " " in cut else cut
    return s


def _caption_gate(text: str) -> str:
    """캡션 게이트(렌더 직전) — 내부 라벨·프리앰블·마크다운 잔재 검출 시 차단 사유 반환(자막 게이트 패턴 재사용)."""
    import re as _r
    if _r.search(r"[*`#]{2}|보이는가|관점에서 분석|분석한 결과|프롬프트|\[사진\d", text or ""):
        return "내부 라벨/프리앰블 잔재"
    return ""


def _body_core(body: str) -> str:
    """태그·오염 판정용 본문 '핵심부' — 관련글 링크·고정정보(CTA·지도·플레이스 안내) 섹션 제외.
    관련글의 타매물 링크·CTA 참조성 등장만으로 태그 승격/오염 판정되지 않게(현재 세트 기준만)."""
    import re as _r
    b = body or ""
    for pat in (r"##\s*함께\s*보면", r"📍\s*찾아오는", r"\[여기 네이버 지도", r"네이버에서\s*['\"]"):
        b = _r.split(pat, b)[0]
    return b


def _blog_tags(tenant, blog) -> list[str]:
    """(네이버 블로그 태그 자동 생성 — LLM 0, 코드 조합) 폼 실값·본문 근거 소스로 10~15개.
    소스 우선순위: 키워드 토큰 조합 → 현재 세트 매물(canonical 주제) → 업종 일반태그 → 지역태그.
    규칙: 최대 15개, 부분중복 dedupe, 금칙어 필터, 근거 없는 태그 금지. 태그 유래=현재 세트 컨텍스트∪본문 핵심부."""
    import re as _r
    pl = blog.payload or {}
    kws = pl.get("target_keywords") or []
    body = pl.get("body") or ""
    gen = pl.get("gen_source") or ""
    note = (gen + "\n" + body)                              # 근거(§a 키워드용) = 폼 입력(gen_source) + 본문
    _note_core = gen + "\n" + _body_core(body)             # §b 매물 속성용 = 핵심부(관련글·CTA 제외)
    region = seo._kw_shorten(getattr(tenant, "region", "") or "")
    ind = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()

    def _sq(s):                                             # 태그 정규화(붙여쓰기 관행 — 공백 제거, 특수문자 제거)
        return _r.sub(r"[^가-힣A-Za-z0-9]", "", (s or ""))

    cand: list[str] = []

    # a. 타깃 키워드 토큰 분해·조합
    kw0 = (kws[0] if kws else "").strip()
    kw_short = seo._kw_shorten(kw0)                        # 광역시·특별시 구어화
    def _drop_admin(t):                                    # 태그 토큰의 행정접미 제거(부산광역시→부산, 기장군→기장)
        return _r.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|시|군|구|도)$", "", t) or t
    kw_toks = [_drop_admin(t) for t in _r.findall(r"[가-힣A-Za-z0-9]{2,}", kw_short)]
    kw_toks = [t for t in kw_toks if len(t) >= 2]
    if kw_short:
        cand.append(_sq(kw_short))                         # 부산동구썬팅업체(광역시 제거)
        cand.append(_sq("".join(kw_toks)))                 # 부산동구썬팅업체(행정접미 제거형)
    if len(kw_toks) >= 2:
        cand.append(_sq(kw_toks[-2] + kw_toks[-1]))        # 뒤 2토큰 결합(썬팅업체)
        cand.append(_sq(kw_toks[0] + kw_toks[-1]))         # 지역+핵심(부산썬팅업체)
    cand += [_sq(t) for t in kw_toks if len(t) >= 2]

    # b. 매물/상품 속성(폼 실값·본문 근거만) — 업종 스키마 attribute_axes 토큰으로 인식(차량 하드코딩 0).
    #    정합은 반환 직전 tag_consistency_gate가 세트 컨텍스트 기준으로 최종 필터(비교언급 누수 차단).
    from app.services import indschema as _isc
    _bt3 = (getattr(tenant, "biz_type", "local") or "local")
    _sch3 = _isc.get_schema(getattr(tenant, "industry", ""), _bt3)
    _attr_vocab = _isc.attribute_tokens(_sch3)
    year = next(iter(_r.findall(r"(20\d{2}|19\d{2})", _note_core)), "")
    # ★ 태그 매물 속성 = '현재 세트 매물'(canonical 주제)만. 타매물·비교차종은 태그화 금지(그랜저 세트에 모닝 태그 금지).
    #   canonical(제목 유래) 우선 + 본문 핵심부·vision 등장 속성. 단어경계('플레이스'의 '레이' 배제).
    _canon_kw = _canonical_keyword(tenant, blog)
    _canon_attrs = [a for a in _attr_vocab if a and _r.search(r"(?<![가-힣])" + _r.escape(a), _canon_kw)]
    models = _canon_attrs or [w for w in _attr_vocab
                              if w and _r.search(r"(?<![가-힣])" + _r.escape(w), _note_core)][:1]
    for md in dict.fromkeys(models[:2]):
        cand.append(_sq(md))
        if year:
            cand.append(_sq(year + md))                    # 2019모닝 / 연식+속성
        cand.append(_sq(md + "중고") if ind and "중고" in ind else _sq(md))

    # c. 업종 일반태그 2~3개(스키마 general_tags) + 지역태그용 짧은 업종어
    _ind_short = ind
    for t in (_sch3.get("general_tags") or []):
        cand.append(_sq(t))
    _gt0 = (_sch3.get("general_tags") or [])
    if _gt0:
        _ind_short = _gt0[0]                               # 지역+업종 태그의 업종어(중고차·썬팅·카페)

    # d. 지역태그 — ★ canonical_region만 참조(프로필 주소 직접 추출 제거). 기초지역(기장)은 canonical에 있을 때만.
    #    canonical_region=''(셀러·hook=False)이면 지역태그 미생성(지역 토큰 표면 미주입).
    _creg_tag = pl.get("canonical_region")
    if _creg_tag is None:
        try:
            _hk = _sch3.get("allow_region_hook")
            _creg_tag = seo.canonical_region(getattr(tenant, "region", "") or "", _bt3,
                                             getattr(tenant, "industry", ""), allow_region_hook=_hk, verify_volume=False)
        except Exception:
            _creg_tag = ""
    for _rt in (_creg_tag or "").split():                 # '부산' 또는 '부산 기장' → 각 파트 + 업종
        if _rt and _ind_short:
            cand.append(_sq(_rt + _ind_short))            # 부산중고차 (+ 기장중고차: canonical에 기장 있을 때만)

    # 근거: 모든 후보는 소스 추출값(키워드 토큰·본문 차종/연식/제품명 화이트리스트·프로필 지역/업종)으로만
    # 구성되므로 날조가 원천적으로 불가(무사고 등 미기재 신뢰어는 애초에 추가 안 함).
    # 길이 + 금칙어(표시광고법) + 부분중복 dedupe(파일명 로직 재사용)만 적용.
    _ok = [t.strip() for t in cand if 2 <= len(t.strip()) <= 20]
    _ok = [t for t in _ok if not seo.hard_block_hits(t) and t not in seo.RISKY_EXPRESSIONS]
    seen = list(dict.fromkeys(_ok))                       # 정확 중복 제거(_sq로 공백차이도 흡수)
    # 근사 부분중복만 제거(길이차 1 이하 = '중고차들'⊂'중고차판매' 류) — 복합+구성요소(부산기장중고차/중고차판매)는
    # 태그 관행상 서로 다른 유입면이므로 둘 다 유지(파일명 dedupe보다 완화). 순서=소스 우선순위.
    deduped = [t for t in seen if not any(t != o and t in o and len(o) - len(t) <= 1 for o in seen)]
    # ── 태그 정합 게이트(전 업종 단일 규칙) — 속성 토큰은 '현재 세트' 컨텍스트(canonical 매물)∪본문 핵심부만.
    #    ★ tenant 인벤토리 전체가 아님 — 타매물(모닝) 토큰은 현재 세트 매물이 아니면 배제. 관련글·CTA 제외(_body_core).
    _ctx_vals = [a for a in _attr_vocab if a and _r.search(r"(?<![가-힣])" + _r.escape(a), _canon_kw)]
    _kept, _dropped = _isc.tag_consistency_gate(
        deduped[:15], _sch3, _ctx_vals, _note_core,        # 본문 근거=핵심부(관련글·CTA 제외)
        region=getattr(tenant, "region", "") or "", general_tags=_sch3.get("general_tags"))
    return _kept


def _photo_captions(tenant, blog, n: int) -> list[str]:
    """(이미지 SEO 5-2) 사진별 캡션 — 전수(N장=N개, 예외 없음)·정합(캡션[i]=image_paths[i]=본문[사진i]=
    파일명 i번=그리드 i번, 단일 인덱스)·중복 금지(동일 문구는 재생성/구분).
    인덱스 소스는 image_paths 하나. vision 실패 사진은 직접 재분석 1회 → 그래도 실패면 인접·순서 유래
    최소 캡션(빈 문자열·동일문구 채우기 금지) + 실패 로그. 서류·개인정보는 각 사진별 고유 캡션."""
    import re as _r
    import logging as _lg
    from app.services import indschema as _isc
    srcnote = (blog.payload or {}).get("gen_source") or ""
    kw = seo._kw_shorten(_canonical_keyword(tenant, blog))   # PHASE 1: 낡은 target_keywords 대신 canonical(제목 유래)
    imgs_ = (blog.payload or {}).get("image_paths") or []
    ind0 = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
    ind0 = seo.searcher_term(ind0) or ind0     # 손님이 쓰는 말로(캡션=이미지 검색 신호)
    _priv = _isc.get_schema(getattr(tenant, "industry", ""),
                            getattr(tenant, "biz_type", "local") or "local").get("privacy_patterns") or []
    _doc_risk = tuple(dict.fromkeys(list(_priv) + list(_CAP_DOC)))
    out, _patched, _fails = [], False, []
    kw_slots = {1, max(2, (n + 1) // 2), n} if n >= 3 else {1}       # 키워드 부착: 첫·중간·끝(도배 방지)
    # 🏷 상호 표기(2026-08-01 사장님 지적) — 이미지 검색은 캡션·파일명·주변 본문을 함께 읽는다.
    #   상호가 하나도 없으면 '어느 업체 사진인지' 식별이 안 된다. 단 전 장에 박으면 도배로 읽히므로
    #   대표(첫) 사진 1장에만 — 본문 상호 표기를 1회로 제한한 것과 같은 원리. 업종 무관 공통.
    _shop = " ".join((getattr(tenant, "name", "") or "").split())
    for i in range(1, n + 1):
        m = _r.search(rf"\[사진{i}\]\s*([^\n]+)", srcnote)
        raw_line = m.group(1) if m else ""             # 원문(서류·번호판 검사용)
        desc = _clean_caption_desc(raw_line)
        if len(desc) < 4:                              # 누락·공백 → 해당 image_paths[i] 직접 재분석 1회(정합 보장)
            try:
                from app import vision as _vz
                p_ = imgs_[i - 1] if i - 1 < len(imgs_) else ""
                one = (_vz.analyze(p_, ind0) or "").strip() if (p_ and os.path.exists(p_)) else ""
                first = next((l for l in one.splitlines() if len(_clean_caption_desc(l)) >= 6), "")
                _d2 = _clean_caption_desc(first)
                if len(_d2) >= 4:
                    desc, raw_line, _patched = _d2, first, True
                    srcnote += f"\n[사진{i}] {_d2}"
            except Exception:
                pass
        # 서류·개인정보 사진 — 민감정보 서술 금지 + 사진별 고유(인덱스). desc 비어도 원문 서류어로 판정.
        _is_doc = (any(k in (desc + " " + raw_line) for k in _doc_risk)
                   or bool(_r.search(r"\d{2,3}[가-힣]\s?\d{4}", raw_line + " " + desc)))
        if _is_doc:
            # ★ '(N번)' 제거(2026-08-01 사장님 지적) — 사진을 글 흐름에 맞게 재배치하면 이 번호가
            #   화면의 사진 번호와 어긋나 붙여넣을 때 헷갈린다(실측: 사진3인데 '(13번)').
            #   중복 구분은 아래 중복 처리에서 키워드로 한다. 손님이 읽는 문장에 내부 번호는 없어야 한다.
            out.append(f"{ind0} 확인 서류 사진".strip() if ind0 else "확인 서류 사진")
        elif len(desc) >= 4:
            if kw and i == 1 and _shop:
                out.append(f"{desc} — {kw}, {_shop}에서 직접 촬영했습니다.")
            elif kw and i in kw_slots:
                out.append(f"{desc} — {kw} 현장 사진입니다.")
            else:
                out.append(f"{desc}.")
        else:                                          # PHASE 2: 분석 실패 → 빈칸(키워드 때움 템플릿 금지 — 오염 원천).
            _fails.append(i)                            # UI가 '직접 적어주세요' 안내. 지어낸 캡션보다 빈칸(정직 게이트).
            out.append("")
    # 중복 금지 — 동일 문구는 사진 번호 접미로 결정적 구분(빈 재생성 대신 확정 구분)
    _seen = {}
    for _ix, _c in enumerate(out):
        _key = _r.sub(r"\s", "", _c)
        if not _key:
            continue                          # 빈칸은 중복 판정 대상이 아니다(직접 적어주세요 안내)
        if _key in _seen:
            # 중복 구분은 필요하지만 '(N번째)'는 사람이 읽는 캡션에 어울리지 않는다(사장님 지적).
            # 키워드를 덧붙여 자연스럽게 구분 — 이미지 검색에도 도움이 된다.
            out[_ix] = (f"{_c.rstrip('. ')} — {kw}" if kw else f"{_c.rstrip('. ')} 상세").strip() + "."
        else:
            _seen[_key] = True
    if _fails:
        _lg.getLogger("shopcast.caption").warning("[caption] vision 분석 실패 사진 %s/%d → 최소 캡션(전수 보장)", _fails, n)
    if _patched:
        try:
            blog.payload["gen_source"] = srcnote[:8000]
            db.save_piece(blog)
        except Exception:
            pass
    return out


def _content_photo_layout(tenant, blog):
    """글 내용에 맞춰 사진 재배치·재정렬(글 텍스트 불변 — 마커 위치·번호와 사진 순서만).
    반환 (new_body, order, caps): order=콘텐츠 흐름순 원본 사진 인덱스, caps=per-photo 캡션(원순서).
    다운로드·그리드·캡션이 이 order를 따르면 '아무 순서로 올려도 글 순서대로' 정렬됨.
    매칭=캡션↔문단 어절 겹침(결정적·레이트리밋 무관). per-photo 설명은 _photo_captions(정합·kit서 재분석)."""
    import re as _rl
    pl = blog.payload or {}
    imgs = pl.get("image_paths") or []
    n = len(imgs)
    body = pl.get("body") or ""
    if n <= 1:
        return body, list(range(n)), []
    try:
        caps = _photo_captions(tenant, blog, n)
    except Exception:
        caps = []
    # ★ 1차: 본문 마커 '등장순 재번호'(결정적·무조건 성립) — 생성기가 이미 의미 배치를 끝냈으므로
    #   kit은 재배치하지 않고 번호만 흐름순으로 바꾼다(표시=ZIP=네이버 단일 기준, 조건부 포기 없음).
    _seen_i = []
    for _mm in _rl.finditer(r"\[사진(\d+)\]", body):
        _iv = int(_mm.group(1))
        if 1 <= _iv <= n and _iv not in _seen_i:
            _seen_i.append(_iv)
    if len(_seen_i) >= max(2, n // 2):
        _order0 = _seen_i + [i for i in range(1, n + 1) if i not in _seen_i]   # 미등장은 뒤로
        _newnum = {orig: k + 1 for k, orig in enumerate(_order0)}
        _nb = _rl.sub(r"\[사진(\d+)\]",
                      lambda m: f"[사진{_newnum.get(int(m.group(1)), int(m.group(1)))}]", body)
        return _nb, [i - 1 for i in _order0], caps
    # 2차(구세트 폴백): 마커가 거의 없으면 기존 캡션↔문단 재매칭 경로
    if len([c for c in caps if (c or "").strip()]) < max(2, n // 2):   # 설명 태부족 → 원순서 유지(날조·오배치 금지)
        return body, list(range(n)), caps
    clean = _rl.sub(r"[ \t]*\[사진\d+\][ \t]*\n?", "", body).strip()
    paras = [p.strip() for p in _rl.split(r"\n\s*\n", clean) if p.strip()]
    if len(paras) < 2:
        return body, list(range(n)), caps

    def _tok(s):
        return {t for t in _rl.split(r"[^가-힣A-Za-z0-9]+", s or "") if len(t) >= 2}

    ptoks = [_tok(caps[i] if i < len(caps) else "") for i in range(n)]
    jtoks = [_tok(p) for p in paras]
    best_para = []
    for i in range(n):
        scored = [(len(ptoks[i] & jtoks[j]), -j) for j in range(len(paras))]
        mx = max(scored) if scored else (0, 0)
        if mx[0] == 0:                                    # 무겹침 → 원 순서 비례 균등 분산(뭉침 방지)
            best_para.append(min(len(paras) - 1, int(i * len(paras) / max(n, 1))))
        else:
            best_para.append(scored.index(mx))
    order = sorted(range(n), key=lambda i: (best_para[i], i))   # 콘텐츠 흐름순
    newnum = {orig: k + 1 for k, orig in enumerate(order)}      # 원인덱스 → 새 사진번호(오름차순)
    by_para = {}
    for i in range(n):
        by_para.setdefault(best_para[i], []).append(i)
    out = []
    for j, para in enumerate(paras):
        out.append(para)
        for i in sorted(by_para.get(j, []), key=lambda x: newnum[x]):
            out.append(f"[사진{newnum[i]}]")
    return "\n\n".join(out), order, caps


def _caption_box(tenant, blog, n: int, caps=None) -> str:
    """(이미지 SEO 5-2) 사진별 캡션 붙여넣기 박스 — 분석 없으면 렌더 생략.
    caps 전달 시 그대로 사용(그리드·다운로드와 동일 순서 유지); 미전달 시 _photo_captions로 산출."""
    if caps is None:
        caps = _photo_captions(tenant, blog, n)
    if not caps:
        return ""
    import re as _rg
    import logging as _lg

    def _clean(i, c):
        if not c or not c.strip():
            return ""                                    # PHASE 2: 빈칸(분석 실패) — UI가 '직접 적어주세요' 표시
        bad = _caption_gate(c)
        if bad:
            _lg.getLogger("shopcast.kit").warning("[캡션게이트] 사진%d 차단(%s): %r", i + 1, bad, c[:60])
            return ""
        return c

    # 전수 렌더 — 실패(빈칸) 사진은 '직접 적어주세요' 안내(침묵 생략 금지, 지어낸 캡션 금지)
    caps = [_clean(i, c) for i, c in enumerate(caps)]
    _fail_n = sorted(i + 1 for i, c in enumerate(caps) if not c)
    if _fail_n:
        _lg.getLogger("shopcast.kit").warning("[캡션] 분석 실패 사진 %s/%d → 빈칸+직접입력 안내(때움 금지)", _fail_n, n)
    rows = "".join(
        (f"<div class='flex items-start gap-2 py-1.5 border-b border-slate-100'>"
         f"<span class='text-xs font-bold text-slate-400 flex-shrink-0 mt-0.5'>사진{i + 1}</span>"
         + (f"<span class='flex-1 text-xs text-slate-600'>{esc(c)}</span>"
            f"<textarea id='cap{i}' class='hidden'>{esc(c)}</textarea>"
            f"<button type=button onclick=\"nvcp('cap{i}',this)\" class='flex-shrink-0 text-[11px] font-bold text-indigo-600'>복사</button>"
            if c else
            "<span class='flex-1 text-xs text-amber-500'>이 사진은 분석에 실패했어요 — 직접 한 줄 적어주세요</span>")
         + "</div>")
        for i, c in enumerate(caps))
    vis = [(i, c) for i, c in enumerate(caps) if c]
    doc_tip = ("<div class='text-[11px] text-amber-600 mt-1.5'>⚠ 서류 사진은 개인정보(성명·주소·차량번호)를 가리고 올리세요.</div>"
               if any("서류" in c for _, c in vis) else "")
    return ("<div class='mt-3 bg-slate-50 rounded-xl p-3'>"
            "<div class='text-xs font-bold text-slate-500 mb-1'>사진 캡션 (사진 아래 붙여넣기)</div>" + rows + doc_tip + "</div>")


def _index_label(pub: dict) -> str:
    """(색인 가속 2-4) 색인 상태 실측 라벨 — indexed_at/published_at 차이로 소요시간 계산. 추정 금지."""
    try:
        from datetime import datetime
        t0 = datetime.fromisoformat((pub.get("published_at") or "")[:19])
        t1 = datetime.fromisoformat((pub.get("indexed_at") or "")[:19])
        h = max(0, (t1 - t0).total_seconds()) / 3600
        took = (f"{int(h * 60)}분" if h < 1 else f"{h:.0f}시간") if h < 48 else f"{h / 24:.0f}일"
        return f"네이버가 글을 받았어요({took} 만에)"
    except Exception:
        return "네이버가 글을 받았어요"


def _trust_card_html(piece) -> str:
    """근거 카드(읽기 전용, 접힘 기본) — 홈 오늘 카드/발행 상세/리포트 공용(PHASE 3-4 단일 컴포넌트).
    글감 큐 연결(또는 mass 배치 생성) 없는 글은 카드 자체를 생략 — '근거 없음' 문구 노출 금지."""
    try:
        from app.services import trustcard
        item = db.find_writing_by_piece(piece.id)
        if not item:
            pl = piece.payload or {}
            _tkw = ((pl.get("target_kw") or "").strip()
                    or ((pl.get("target_keywords") or [""])[0] or "").strip())
            if pl.get("mass_batch") and _tkw:      # 배치(발굴) 생성분 — 기본 근거 템플릿
                item = {"source_type": "P4", "target_keyword": _tkw,
                        "angle": pl.get("angle") or "", "reason": ""}
        card = trustcard.render_trust_card(item)
        if not card:
            return ""
        _pub = None
        try:
            _pub = db.get_blog_publish(piece.id)
        except Exception:
            pass
        lines = "".join(f"<div class='text-sm text-slate-600 leading-relaxed mb-1'>{esc(l)}</div>"
                        for l in card["lines"])
        _idx = ""
        if _pub:
            if _pub.get("indexed_at"):
                _idx = f"<div class='text-xs text-emerald-600 mt-1'>{esc(_index_label(_pub))}</div>"
            else:
                _idx = "<div class='text-xs text-slate-400 mt-1'>네이버 접수 확인 중이에요</div>"
        publine = (f"<div class='text-sm font-semibold text-emerald-600 mt-2'>{esc(trustcard.PUBLISHED_LINE)}</div>{_idx}"
                   if _pub else "")
        return ("<details class='trustcard mt-2'>"
                f"<summary class='text-xs font-bold text-indigo-500 cursor-pointer select-none'>"
                f"\u25b8 {esc(card['title'])}</summary>"
                f"<div class='mt-2 bg-indigo-50/60 border border-indigo-100 rounded-xl p-3.5'>{lines}"
                f"<div class='text-xs text-slate-400 mt-2'>{esc(card['footer'])}</div>{publine}</div></details>")
    except Exception:
        import logging
        logging.getLogger("shopcast.trustcard").exception("[trustcard] 렌더 실패 piece=%s", getattr(piece, "id", ""))
        return ""


def _blog_connect_card(t, fw: str) -> str:
    """'내 네이버 블로그 연결' 카드 — 연결 전(입력 폼) / 연결 후(현황+해제)."""
    inp = "flex-1 border border-slate-200 rounded-xl px-3 py-2.5 text-sm"
    if getattr(t, "blog_id", ""):
        # 발행 일관성(RSS 실측, C-Rank 지속성) + 최신 주간 리포트(블로그등록 PHASE 4)
        cons_html = ""
        try:
            from app.services import blogsync as _bs
            from app import config as _cfg
            _feed = _bs.fetch_feed(t.blog_id)
            if _feed.get("ok") and _feed.get("exists"):
                _target = (getattr(t, "publish_schedule", 0) or 0) or _cfg.BLOG_WEEKLY_TARGET
                cons = _bs.posting_consistency(_feed["posts"], weekly_target=_target)
                # ('주방은 보여주지 않는다') 잔소리 삭제 — 숫자만 담백하게
                _pace = (f"<span class='text-emerald-600'>이번 주 {cons['this_week']}/{cons['weekly_target']}회 ✓</span>"
                         if cons["on_pace"] else f"이번 주 {cons['this_week']}/{cons['weekly_target']}회")
                _mx = max(cons["week_counts"] + [1])
                _bars = "".join(
                    f"<div class='flex flex-col items-center gap-1'><div class='w-7 rounded-t bg-emerald-400' "
                    f"style='height:{max(4, int(36 * n / _mx))}px'></div>"
                    f"<span class='text-[10px] text-slate-400'>{n}</span></div>"
                    for n in cons["week_counts"])
                _gap = (f" · 마지막 발행 {cons['days_since_last']}일 전" if cons["days_since_last"] is not None else "")
                cons_html = ("<div class='mt-4 bg-slate-50 rounded-2xl p-4'>"
                             "<div class='flex items-center justify-between mb-2'>"
                             f"<div class='text-sm font-bold text-slate-700'>실제 발행 현황(RSS 실측) — {_pace}</div>"
                             f"<div class='text-xs text-slate-400'>연속 {cons['streak_weeks']}주 발행{_gap}</div></div>"
                             f"<div class='flex items-end gap-2 h-14'>{_bars}</div>"
                             "<div class='text-[10px] text-slate-400 mt-1'>← 4주 전 · · 이번 주 →</div></div>")
        except Exception:
            pass
        _wr = db.latest_weekly_report(t.id)
        if _wr and _wr.get("data"):
            _d = _wr["data"]
            _rows2 = ""
            for c in (_d.get("rank_changes") or [])[:4]:
                _b = c.get("before") or "미노출"
                _a = c.get("after") or "미노출"
                _src = {"blog_search": "블로그탭", "place": "플레이스", "blog": "지역검색", "shop": "쇼핑검색"}.get(c.get("kind"), "")
                _up = (c.get("after") or 99) < (c.get("before") or 99) and c.get("after")
                _cls = "text-emerald-600" if _up else "text-slate-500"
                _rows2 += (f"<div class='flex justify-between text-sm py-1 border-b border-slate-100'>"
                           f"<span class='text-slate-600'>{esc(str(c.get('keyword', '')))} <span class='text-[10px] text-slate-400'>{_src}</span></span>"
                           f"<span class='font-bold {_cls}'>{_b} → {_a}{' ⬆️' if _up else ''}</span></div>")
            cons_html += ("<div class='mt-3 bg-indigo-50/50 rounded-2xl p-4'>"
                          f"<div class='text-sm font-bold text-slate-700 mb-1'>주간 리포트 <span class='text-xs text-slate-400 font-normal'>({esc(_wr.get('week') or '')})</span></div>"
                          + _rows2
                          + f"<p class='text-xs text-slate-500 mt-2'>{esc(_d.get('coaching') or '')}</p></div>")
        pubs = db.list_blog_publishes(t.id, limit=5)

        def _pub_row(p):
            # 발행 후 며칠(진단 P3) — 색인 대기(3일 미만)면 그 사실을 먼저 보여줌(불안 방지)
            from app.services.whynot import _days_since
            _d = _days_since(p.get("published_at") or "")
            _chip = (f"<span class='text-[11px] font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full whitespace-nowrap'>"
                     f"{_d}일차</span>" if _d >= 0 else "")
            # 생존신고 요약(파이프 A4) — 저장된 실측 스냅샷만(렌더 시 네이버 콜 없음)
            _pc = None
            try:
                _pc = db.get_piece(p.get("piece_id") or "")
                _pkw = ((((_pc.payload or {}).get("target_keywords") or [""])[0] or "").strip() if _pc
                        else (p.get("target_kw") or "").strip())   # 외부 글(rss_auto)은 자동 추출 키워드
                _ph = [h for h in db.rank_history(t.id, _pkw, kind="post") if h.get("rank") is not None] if _pkw else []
                if _ph:
                    _pr, _pp = _ph[-1]["rank"], (_ph[-2]["rank"] if len(_ph) >= 2 else None)
                    if _pr:
                        _ar = ("↑" if _pp and _pr < _pp else "↓" if _pp and _pr > _pp else "→")
                        _chip += (f"<span class='ml-1 text-[11px] font-bold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded-full whitespace-nowrap'>"
                                  f"{_pr}위 {_ar}</span>")
                    else:
                        _chip += "<span class='ml-1 text-[11px] font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full whitespace-nowrap'>31위 밖</span>"
                elif p.get("indexed_at"):
                    _chip += f"<span class='ml-1 text-[11px] font-bold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full whitespace-nowrap'>{_index_label(p)}</span>"
                elif _d >= 0 and _d < 1:
                    _chip += "<span class='ml-1 text-[11px] font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full whitespace-nowrap'>네이버 접수 확인 중이에요</span>"
            except Exception:
                pass
            _pid = esc(p.get("piece_id") or "")
            btn = (f"<button type=button onclick=\"whyNot('{_pid}',this)\" "
                   "class='text-[11px] font-bold text-indigo-600 border border-indigo-200 hover:bg-indigo-50 "
                   "px-2.5 py-1 rounded-lg transition whitespace-nowrap'>왜 안 뜨나요? 진단</button>"
                   if (_pid and _pc) else "")   # 진단은 글 품질(audit)이 있는 올린다 글만
            race_btn = (f"<button type=button onclick=\"raceView('{_pid}',this)\" "
                        "class='text-[11px] font-bold text-violet-600 border border-violet-200 hover:bg-violet-50 "
                        "px-2.5 py-1 rounded-lg transition whitespace-nowrap'>순위 추적</button>" if _pid else "")
            return (f"<div class='border-b border-slate-100 py-2'>"
                    f"<div class='flex items-center justify-between gap-2'>"
                    f"<a href='{esc(p.get('published_url') or '')}' target=_blank rel=noopener class='text-sm text-slate-700 font-medium truncate'>"
                    f"{esc(p.get('post_title') or (p.get('published_url') or '')[:50])}</a>"
                    f"<span class='flex items-center gap-1.5'>{_chip}{race_btn}{btn}</span></div>"
                    f"<div class='text-xs text-slate-400'>{esc(db.fmt_kst(p.get('published_at'), date_only=True))} · "
                    f"{'RSS자동' if p.get('matched_by') == 'rss' else '직접확인'}</div>"
                    # 근거 카드(trust PHASE 3-3) — 큐 연결 없는 글(외부 발행 등)은 자동 생략
                    + (_trust_card_html(_pc) if _pc else "")
                    + f"<div id='race_{_pid}'></div><div id='why_{_pid}'></div></div>")
        pub_rows = "".join(_pub_row(p) for p in pubs)
        pub_box = ((f"<div class='mt-4'><div class='text-xs font-bold text-slate-500 mb-1'>최근 발행 확인 {len(pubs)}건</div>{pub_rows}</div>")
                   if pubs else ("<p class='text-xs text-slate-400 mt-3'>블로그 등록됨 — 새 글은 <b class='text-slate-600'>자동으로</b> 감지해 추적해요"
                                 "(2시간 주기 · 방금 발행했다면 '지금 새로고침'). 여기엔 추적 중인 글이 표시돼요.</p>"))
        return (f"<div id='blog' class='{fw} mt-5'>"
                "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>내 네이버 블로그</h2>"
                f"<p class='text-sm text-slate-400 mb-3'>연결됨 · 새 글을 자동으로 감지해 색인·순위까지 추적해요 — 따로 누를 건 없어요.</p>"
                "<div class='flex items-center gap-3 flex-wrap'>"
                f"<a href='{esc(t.naver_blog_url)}' target=_blank rel=noopener "
                "class='inline-flex items-center gap-2 bg-emerald-50 text-emerald-700 font-bold text-sm px-4 py-2.5 rounded-xl'>"
                f"✅ blog.naver.com/{esc(t.blog_id)} ↗</a>"
                "<span class='text-xs font-bold text-emerald-700 bg-emerald-50 px-3 py-2 rounded-xl'>새 글 자동 추적 중 (2시간마다)</span><button type=button onclick='blogChk(this)' class='text-xs font-bold text-slate-500 border border-slate-200 hover:bg-slate-50 px-3 py-2 rounded-xl transition'>지금 새로고침</button>"
                "<span id='blogChkMsg' class='text-xs text-slate-400'></span>"
                "<form method=post action='/me/blog' class='ml-auto' onsubmit=\"return confirm('블로그 연결을 해제할까요? 발행 확인·순위 매칭이 꺼져요.')\">"
                "<input type=hidden name=blog value=''>"
                "<button class='text-xs text-slate-400 hover:text-rose-500 font-semibold'>연결 해제</button></form>"
                "</div>"
                # ('주방은 보여주지 않는다') 통계 교육 배너 삭제 — 딥링크 한 줄만
                + (f"<a href='https://admin.blog.naver.com/AnalyticsMainView.naver?blogId={esc(t.blog_id)}' "
                   "target=_blank rel=noopener class='inline-block text-xs font-semibold text-slate-400 "
                   "hover:text-slate-600 mt-2'>조회수·유입 검색어는 네이버 통계에서 ↗</a>")
                + cons_html + pub_box +
                "<script>async function blogChk(btn){var m=document.getElementById('blogChkMsg');m.textContent='확인 중…';btn.disabled=true;"
                "try{var r=await fetch('/api/blog/check-published',{method:'POST'});var d=await r.json();"
                "if(d.error){m.textContent=d.error;btn.disabled=false;return;}"
                "if(d.synced){m.textContent='✅ 새 글 '+d.synced+'건 추적 시작!';setTimeout(function(){location.reload();},900);}"
                "else{m.textContent='새 글 없음 — 이미 다 추적 중이에요 (RSS '+d.rss_posts+'건 대조).';btn.disabled=false;}"
                "}catch(e){m.textContent='확인 실패';btn.disabled=false;}}"
                # '왜 안 뜨나요?' 원클릭 진단(whynot P1~P3) — 결과는 해당 발행 항목 아래 삽입
                "async function whyNot(pid,btn){var box=document.getElementById('why_'+pid);if(!box)return;"
                "if(box.innerHTML){box.innerHTML='';btn.textContent='왜 안 뜨나요? 진단';return;}"
                "btn.disabled=true;btn.textContent='진단 중… (10초쯤)';"
                "try{var r=await fetch('/api/whynot/'+pid);var d=await r.json();"
                "if(d.error){box.innerHTML='<div class=\"text-xs text-rose-500 py-1\">'+d.error+'</div>';}"
                "else{box.innerHTML=d.html;btn.textContent='진단 닫기';}"
                "}catch(e){box.innerHTML='<div class=\"text-xs text-rose-500 py-1\">진단 실패 — 잠시 후 다시</div>';}"
                "btn.disabled=false;if(btn.textContent.indexOf('진단 중')>=0)btn.textContent='왜 안 뜨나요? 진단';}"
                # (auto) enrich 인라인 폼 제거 — 보강은 품질 게이트가 자동 수행
                # AI 순위 분석(분석가 P3) — 타임라인 안 버튼에서 호출
                "async function analystView(pid,btn){var box=document.getElementById('anl_'+pid);if(!box)return;"
                "if(box.innerHTML){box.innerHTML='';btn.textContent='왜 이 순위? AI 분석';return;}"
                "btn.disabled=true;btn.textContent='분석 중… (첫 분석은 30초쯤)';"
                "try{var r=await fetch('/api/analyst/'+pid);var d=await r.json();"
                "if(d.error){box.innerHTML='<div class=\"text-xs text-rose-500 py-1\">'+d.error+'</div>';btn.textContent='왜 이 순위? AI 분석';}"
                "else{box.innerHTML=d.html;btn.textContent='분석 닫기';}"
                "}catch(e){box.innerHTML='<div class=\"text-xs text-rose-500 py-1\">분석 실패 — 잠시 후 다시</div>';btn.textContent='왜 이 순위? AI 분석';}"
                "btn.disabled=false;}"
                # 생존 신고(생존신고 P3) — 발행→색인→진입→현재→다음 관문 타임라인
                "async function raceView(pid,btn){var box=document.getElementById('race_'+pid);if(!box)return;"
                "if(box.innerHTML){box.innerHTML='';btn.textContent='순위 추적';return;}"
                "btn.disabled=true;btn.textContent='실측 중…';"
                "try{var r=await fetch('/api/race/'+pid);var d=await r.json();"
                "if(d.error){box.innerHTML='<div class=\"text-xs text-rose-500 py-1\">'+d.error+'</div>';btn.textContent='순위 추적';}"
                "else{box.innerHTML=d.html;btn.textContent='추적 닫기';}"
                "}catch(e){box.innerHTML='<div class=\"text-xs text-rose-500 py-1\">실측 실패 — 잠시 후 다시</div>';btn.textContent='순위 추적';}"
                "btn.disabled=false;}"
                "</script></div>")
    return (f"<div id='blog' class='{fw} mt-5'>"
            "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>내 네이버 블로그 연결</h2>"
            "<p class='text-sm text-slate-400 mb-3'>네이버는 발행 API가 없어 직접 발행하시죠? "
            "블로그 주소를 등록하면 <b>실제 발행 확인 · 내 블로그 순위 추적</b>이 정확해져요.</p>"
            "<form method=post action='/me/blog' class='flex gap-2'>"
            f"<input name=blog placeholder='https://blog.naver.com/내아이디 또는 아이디만' class='{inp}'>"
            "<button class='px-5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-bold text-sm whitespace-nowrap'>연결</button></form>"
            "<p class='text-xs text-slate-400 mt-2'>공개 RSS(공식 제공)로만 확인해요 — 비밀번호·로그인이 필요 없어요.</p></div>")


@app.post("/api/blog/angle-variant")
async def api_blog_angle_variant(request: Request):
    """앵글 변형 생성(상위노출 PHASE 4) — 기존 블로그 글의 사진·소재를 재사용해
    다른 의도 앵글(후기형/방법형/가격형) 글을 생성 → 각기 다른 스마트블록 진입.
    plan 게이팅: angle_variants(config.PLAN_LIMITS)."""
    from app import gating
    u = auth.current_user(request)
    blk = gating.check_limit(u, "angle_variants")
    if blk:
        return JSONResponse(blk, status_code=(401 if blk.get("need_signup") else 402))
    t = _ensure_user_tenant(u)
    form = await request.form()
    piece_id = (form.get("piece_id") or "").strip()
    angle = (form.get("angle") or "").strip()
    if angle not in ("review", "howto", "price"):
        return JSONResponse({"error": "앵글은 review/howto/price 중 하나예요."}, status_code=400)
    piece = db.get_piece(piece_id)
    if not piece or piece.tenant_id != t.id or piece.kind.value != "blog":
        return JSONResponse({"error": "내 블로그 글을 찾지 못했어요."}, status_code=404)
    asset = db.get_asset(piece.asset_id)
    if not asset:
        return JSONResponse({"error": "원본 소재를 찾지 못했어요."}, status_code=404)
    asset.angle = angle
    tkw = (piece.payload.get("target_kw") or "").strip() or \
          ((piece.payload.get("target_keywords") or [""])[0] or "").strip()
    if tkw:
        asset.target_kw = tkw

    def _bg():
        try:
            from app.services.generate import generate_for
            from app.domain.models import ContentKind as _CK
            imgs = piece.payload.get("image_paths") or ([piece.payload.get("image_path")]
                                                        if piece.payload.get("image_path") else None)
            made = generate_for(t, asset, [_CK.BLOG], images=imgs)
            for p in made:
                p.payload["angle"] = angle
                p.payload["variant_of"] = piece.id
                p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value,
                                                               p.payload, source=asset.note)
                db.save_piece(p)
        except Exception:
            import logging
            logging.exception("[angle-variant] 생성 실패 piece=%s", piece_id)
    import threading
    threading.Thread(target=_bg, daemon=True).start()
    gating.consume(u, "angle_variants")
    lab = {"review": "후기형", "howto": "방법·과정형", "price": "가격·비용형"}[angle]
    return JSONResponse({"ok": True, "asset_id": piece.asset_id,
                         "msg": f"{lab} 앵글 글을 만들고 있어요 (20~40초). '내 콘텐츠'에서 확인하세요."})


@app.post("/me/store-info")
def my_store_info(request: Request, phone: str = Form(""), address: str = Form(""),
                  hours: str = Form(""), parking: str = Form(""), map_url: str = Form(""),
                  buy_url: str = Form(""), search_kw: str = Form("")):
    """매장 고정정보 저장(블로그템플릿 PHASE 1) — 한 번 입력 → 모든 블로그 글 마무리에 재사용."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    db.update_store_info(t.id, phone, address, hours, parking, map_url)
    if (buy_url.strip() or search_kw.strip()):     # 셀러 구매정보(있을 때만 갱신)
        db.update_tenant_classification(t.id, t.biz_type or "local", t.marketplace or "",
                                        buy_url.strip() or t.buy_url,
                                        search_kw.strip() or t.search_kw, t.brand_name or "")
    return RedirectResponse("/me?ok=매장 정보를 저장했어요 — 이제 모든 블로그 글에 자동으로 들어가요",
                            status_code=303)


def _store_info_card(t) -> str:
    """매장 정보 카드 — 한 번 입력하면 모든 글 마무리 고정정보 블록에 재사용."""
    inp = "w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm"
    seller = (getattr(t, "biz_type", "local") or "local") in ("seller", "hybrid")
    filled = sum(1 for v in (t.phone, t.address, t.hours, getattr(t, "parking", ""), t.map_url)
                 if (v or "").strip())
    seller_rows = ""
    if seller:
        seller_rows = (
            f"<input name=buy_url value=\"{esc(t.buy_url or '')}\" placeholder='구매 링크(스토어/상세페이지)' class='{inp}'>"
            f"<input name=search_kw value=\"{esc(t.search_kw or '')}\" placeholder='검색어 유도 (예: 쿠팡에서 폴딩박스)' class='{inp}'>")
    return (f"<details {'open' if filled < 2 else ''} class='bg-white rounded-3xl border border-slate-100 shadow-sm p-5 mb-5'>"
            f"<summary class='cursor-pointer select-none font-extrabold text-slate-900'>매장 정보 "
            f"<span class='text-xs text-slate-400 font-normal'>({filled}/5 입력됨 · 한 번 입력하면 모든 글에 자동 삽입)</span></summary>"
            "<p class='text-xs text-slate-400 mt-1 mb-3'>블로그 글 마무리 '찾아오는 길' 블록에 재사용돼요. "
            "지도는 텍스트가 아니라 네이버 <b>장소 컴포넌트</b>로 넣도록 발행 화면에서 안내해 드려요.</p>"
            "<form method=post action='/me/store-info' class='grid sm:grid-cols-2 gap-2'>"
            f"<input name=address value=\"{esc(t.address or '')}\" placeholder='주소' class='{inp} sm:col-span-2'>"
            f"<input name=phone value=\"{esc(t.phone or '')}\" placeholder='전화번호' class='{inp}'>"
            f"<input name=hours value=\"{esc(t.hours or '')}\" placeholder='영업시간 (예: 매일 10-21시, 월 휴무)' class='{inp}'>"
            f"<input name=parking value=\"{esc(getattr(t, 'parking', '') or '')}\" placeholder='주차 (예: 가게 앞 2대, 공영주차장 3분)' class='{inp}'>"
            f"<input name=map_url value=\"{esc(t.map_url or '')}\" placeholder='네이버 플레이스 URL' class='{inp}'>"
            + seller_rows +
            "<button class='bg-slate-900 hover:bg-slate-800 text-white font-bold py-2.5 rounded-xl sm:col-span-2 transition'>저장</button>"
            "</form></details>")


@app.post("/me/briefing-pref")
def my_briefing_pref(request: Request, hour: str = Form("8"), on: str = Form("1")):
    """아침 브리핑 설정 — 시각(05~12시)·on/off (브리핑 PHASE 2)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    try:
        db.set_briefing_pref(t.id, int(hour or 8), (on or "1") == "1")
    except Exception:
        pass
    return RedirectResponse("/me?ok=아침 브리핑 설정을 저장했어요", status_code=303)


@app.post("/api/briefing/pass")
def api_briefing_pass(request: Request):
    """'오늘은 패스' — 부담 없이 넘기기(브리핑 PHASE 3)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False}, status_code=401)
    t = _ensure_user_tenant(u)
    import datetime
    db.pass_briefing(t.id, datetime.datetime.utcnow().strftime("%Y-%m-%d"))
    return JSONResponse({"ok": True, "message": "오늘은 쉬어가요. 내일 아침에 다시 브리핑드릴게요!"})


@app.post("/api/briefing/send-test")
def api_briefing_send_test(request: Request):
    """본인 계정 한정 테스트 발송 — 실발송 경로(알림+이메일+카톡 스텁) 그대로, 시각·1일1회 락 무시.
    남용 방지: 로그인 필수(본인 tenant만) + IP 레이트리밋(시간 3회)."""
    from app import ratelimit
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False, "error": "로그인이 필요해요."}, status_code=401)
    if not ratelimit.allow("brieftest:" + _client_ip(request), 3, 3):
        return JSONResponse({"ok": False, "error": "테스트 발송은 시간당 3회까지예요."}, status_code=429)
    t = _ensure_user_tenant(u)
    if not (t.industry or "").strip():
        return JSONResponse({"ok": False, "error": "가게 설정(업종) 먼저 완료해 주세요."}, status_code=400)
    from app.services import briefing as _bf
    b = _bf.get_or_create_today(t, u.get("plan") or "free")
    text = _bf._briefing_text(t, b)
    db.add_notice(t.id, "briefing", f"오늘 아침 브리핑 — {b['headline']} 오늘 할 일: {b['task']}")
    mailed = False
    email = (u.get("email") or "")
    if email and not email.endswith((".guest", ".local")) and os.environ.get("SMTP_HOST"):
        try:
            from app.services.weekly_report import _send_email
            mailed = _send_email(email, "[올린다] 오늘 아침 브리핑 (테스트)", text)
        except Exception:
            pass
    _bf._send_kakao_stub(t, b)
    return JSONResponse({"ok": True, "kind": b.get("kind"), "headline": b.get("headline"),
                         "task": b.get("task"), "notice": True, "mailed": mailed})


@app.post("/admin/briefing/send-now")
def admin_briefing_now(hour: int = 0):
    """수동 트리거(테스트) — hour 미지정 시 현재 KST 시각."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services import briefing
    h = hour or datetime.now(ZoneInfo("Asia/Seoul")).hour
    return JSONResponse(briefing.send_morning(h))


@app.post("/me/topic-axis")
def my_topic_axis(request: Request, topic_axis: str = Form("")):
    """'전문 주제 축' 저장 — 이 블로그가 밀 핵심 주제/키워드군(C-Rank 주제 집중)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    db.set_topic_axis(t.id, topic_axis)
    return RedirectResponse("/me?ok=전문 주제 축을 저장했어요 — 발행 캘린더 제안에 반영돼요", status_code=303)


def _growth_card(t, fw: str) -> str:
    """순위 성장 그래프 + 코칭(상위노출 PHASE 3) — 잘 되는 키워드는 더 밀고, 정체는 앵글 재도전."""
    from app.services import ranktrack
    from urllib.parse import quote as _q
    deltas = ranktrack.rank_deltas(t.id)
    if not deltas:
        return ""
    _src_lab = {"blog_search": "블로그탭", "place": "플레이스", "blog": "지역검색", "shop": "쇼핑검색"}

    def _spark(history: list) -> str:
        """순위 미니 그래프 — 낮은 순위(1위)가 높은 막대. 0(미노출)은 최하 취급."""
        bars = ""
        for r in history[-8:]:
            v = 31 if not r else r
            h = max(4, int(34 * (31 - min(v, 31)) / 30))
            color = "bg-emerald-400" if r else "bg-slate-200"
            bars += f"<div class='w-2 rounded-t {color}' style='height:{h}px'></div>"
        return f"<div class='flex items-end gap-0.5 h-9'>{bars}</div>"

    rows = ""
    for d in deltas:
        f_lab = f"{d['first']}위" if d["first"] else "미노출"
        l_lab = f"{d['last']}위" if d["last"] else "미노출"
        badge = {"up": f"<span class='text-emerald-600 font-extrabold'>{f_lab} → {l_lab} ⬆️</span>",
                 "enter": f"<span class='text-emerald-600 font-extrabold'>미노출 → {l_lab} 진입 🎉</span>",
                 "down": f"<span class='text-rose-500 font-bold'>{f_lab} → {l_lab} ⬇️</span>",
                 "flat": f"<span class='text-slate-500 font-bold'>{l_lab} 유지</span>"}[d["dir"]]
        rows += ("<div class='flex items-center justify-between border-b border-slate-100 py-2.5 gap-3'>"
                 f"<div class='min-w-0'><div class='text-sm font-bold text-slate-700 truncate'>{esc(d['keyword'])} "
                 f"<span class='text-[10px] text-slate-400 font-normal'>{_src_lab.get(d['kind'], '')}</span></div>"
                 f"<div class='text-xs mt-0.5'>{badge}</div></div>" + _spark(d["history"]) + "</div>")
    # 코칭: 오른 키워드 = 더 밀기 / 정체 = 앵글 재도전
    coach = ""
    imp = db.improving_keywords(t.id)
    if imp:
        k = imp[0]["keyword"]
        coach += ("<a href='/me?target_kw=" + _q(k) + "' class='flex items-center justify-between bg-emerald-50 rounded-xl px-3.5 py-2.5 mt-3 hover:bg-emerald-100 transition'>"
                  f"<span class='text-sm text-slate-700'><b>'{esc(k)}'</b> 잘 되고 있어요 — 이 키워드 글 하나 더 밀어요</span>"
                  "<span class='text-xs font-bold text-emerald-600 whitespace-nowrap'>더 밀기 →</span></a>")
    for s in ranktrack.stagnant_keywords(t.id, limit=2):
        coach += (f"<a href='{s['href']}' class='flex items-center justify-between bg-amber-50 rounded-xl px-3.5 py-2.5 mt-2 hover:bg-amber-100 transition'>"
                  f"<span class='text-sm text-slate-700'><b>'{esc(s['keyword'])}'</b> 정체 중 — "
                  f"{s['prev_label']} 대신 <b>{s['retry_label']}</b> 앵글로 재도전</span>"
                  "<span class='text-xs font-bold text-amber-600 whitespace-nowrap'>앵글 바꿔 만들기 →</span></a>")
    return (f"<div class='{fw} mt-5'>"
            "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>순위 성장</h2>"
            "<p class='text-sm text-slate-400 mb-3'>자동 추적 스냅샷 기준 · 실측만 표시(참고용, 위치·기기별 차이)</p>"
            + rows + coach + "</div>")


def _place_card(t, fw: str) -> str:
    """📍 플레이스 최적화 카드(상위노출 PHASE 5) — 매장(local/hybrid)만.
    순위 요약 + 정보 완성도 체크리스트 + 리뷰 요청 키트(QR·문구)."""
    if (getattr(t, "biz_type", "local") or "local") not in ("local", "hybrid"):
        return ""
    from app.services import place_opt
    s = place_opt.place_summary(t)
    # 플레이스 순위 요약
    rank_rows = ""
    for r in s["place_ranks"][:4]:
        lab = f"{r['rank']}위" if r["rank"] else "5위 밖"
        chg = ""
        if r["prev"] is not None and r["rank"] is not None:
            cc, pp = (r["rank"] or 6), (r["prev"] or 6)
            chg = (" <span class='text-emerald-600 text-xs font-bold'>⬆️</span>" if cc < pp
                   else (" <span class='text-rose-500 text-xs font-bold'>⬇️</span>" if cc > pp else ""))
        rank_rows += (f"<div class='flex justify-between text-sm py-1.5 border-b border-slate-100'>"
                      f"<span class='text-slate-600'>{esc(r['keyword'])}</span>"
                      f"<span class='font-bold text-slate-800'>{lab}{chg}</span></div>")
    rank_box = (f"<div class='mb-1'>{rank_rows}</div>" if rank_rows
                else "<div class='text-sm text-slate-400'>지도 순위가 잡히면 여기 표시돼요</div>")
    # 체크리스트
    chk = ""
    for i in s["checklist"]:
        if i["done"] is True:
            ic, cls = "✅", "text-slate-500"
        elif i["done"] is False:
            ic, cls = "⬜", "text-slate-700 font-semibold"
        else:
            ic, cls = "·", "text-slate-600"
        _go = ("<a href='https://smartplace.naver.com/' target=_blank rel=noopener "
               "class='text-indigo-600 font-bold'> 네이버 플레이스 관리에서 하기 ↗</a>" if i["done"] is False else "")
        chk += (f"<details class='py-1.5 border-b border-slate-100'><summary class='cursor-pointer text-sm {cls} select-none'>"
                f"{ic} {esc(i['label'])} <span class='text-[11px] text-slate-400 font-normal'>— {esc(i['why'])}</span></summary>"
                f"<div class='text-xs text-slate-500 mt-1 pl-6'>{esc(i['how'])}{_go}</div></details>")
    # 리뷰 요청 키트
    rv = ""
    for idx, r in enumerate(s["reviews"]):
        rv += (f"<details class='bg-slate-50 rounded-xl px-3.5 py-2.5 mb-1.5'>"
               f"<summary class='cursor-pointer text-sm font-semibold text-slate-700 select-none'>{esc(r['where'])}</summary>"
               f"<div class='text-sm text-slate-600 whitespace-pre-wrap mt-2'>{esc(r['text'])}</div>"
               f"<textarea id='rv{idx}' class='hidden'>{esc(r['text'])}</textarea>"
               f"<button onclick=\"omCopy(document.getElementById('rv{idx}').value);this.textContent='✅ 복사됨'\" "
               "class='mt-2 px-3 py-1.5 bg-white border border-slate-200 text-slate-600 text-xs font-bold rounded-lg'>복사</button></details>")
    _tl = _ensure_track_link(t)
    qr = (f"<div class='flex items-center gap-3 mt-3 bg-indigo-50/60 rounded-xl p-3'>"
          f"<img src='/me/qr/{_tl['code']}.png' class='w-20 h-20 rounded-lg bg-white p-1 border border-slate-100' alt='QR'>"
          "<div class='text-xs text-slate-600'>이 QR을 카운터에 두면 손님이 바로 내 플레이스로 가요.<br>"
          f"<a href='/me/review-card.png' download class='text-indigo-600 font-bold'>⬇ 리뷰 요청 카드(인쇄용)</a> · "
          f"<a href='/me/qr/{_tl['code']}.png' download class='text-indigo-600 font-bold'>⬇ QR 저장</a></div></div>") if _tl else ""
    # ('주방은 보여주지 않는다') 실측 순위만 노출 — 체크리스트·리뷰키트·QR·인쇄물은 도구(접힘)로 강등.
    return (f"<div id='place' class='{fw} mt-5'>"
            "<h2 class='text-xl font-extrabold text-slate-900 mb-3'>지도 노출 순위</h2>"
            + rank_box
            + "<details class='mt-3'><summary class='cursor-pointer text-sm font-bold text-slate-500 select-none'>"
            f"🧰 플레이스 도구 — 체크리스트(정보 완성 {s['done']}/{s['known']})·리뷰 요청·QR·인쇄물</summary>"
            "<div class='mt-3 grid sm:grid-cols-2 gap-5'>"
            f"<div><div class='text-xs font-bold text-slate-500 mb-1'>정보 완성도 체크리스트</div>{chk}</div>"
            f"<div><div class='text-xs font-bold text-slate-500 mb-1'>리뷰 요청 키트</div>{rv}{qr}</div>"
            "</div>"
            + _print_block(t)
            + "</details></div>")


def _visitor_box(t) -> str:
    """손님 특성 요약(방문자 B1·B2) — 익명 집계만. '누가'는 모르고 알 수도 없다(명시)."""
    try:
        vs = db.visitor_stats(t.id, days=30)
    except Exception:
        return ""
    _ch_lab = {"naver_blog": "블로그", "instagram": "인스타", "marketplace": "판매글", "qr": "매장 QR", "x": "X"}
    if not vs.get("total"):
        return ("<div class='mt-5 pt-4 border-t border-slate-100'>"
                "<div class='text-sm font-bold text-slate-600 mb-1'>손님 특성 (익명)</div>"
                "<p class='text-sm text-slate-400'>아직 데이터가 없어요 — 추적링크·QR로 손님이 오면 "
                "기기·시간대·재방문 같은 특성이 여기 요약돼요. (개인을 식별하지 않아요)</p></div>")
    dv = vs.get("device") or {}
    dv_total = sum(dv.values()) or 1
    dv_txt = f"모바일 {round(100 * dv.get('mobile', 0) / dv_total)}%" if dv else ""
    bits = [b for b in [
        ("주로 " + dv_txt) if dv_txt else "",
        (vs.get("top_hour_band") and f"{vs['top_hour_band']}에 많이 와요"),
        (vs.get("top_channel") and f"{_ch_lab.get(vs['top_channel'], vs['top_channel'])} 유입이 1위"),
        (vs.get("top_region") and f"지역(국가 단위): {vs['top_region']}")] if b]
    rows = ("<div class='text-sm text-slate-700 font-semibold'>" + " · ".join(bits) + "</div>" if bits else "")
    visits = (f"<div class='text-sm text-slate-600 mt-1'>새 손님 <b class='text-violet-600'>{vs['new_visitors']}명</b> · "
              f"다시 온 손님 <b class='text-violet-600'>{vs['returning_visitors']}명</b>"
              + (f" · 글 보고 매장 QR까지 온 여정 <b class='text-violet-600'>{vs['journeys']}건</b>" if vs.get("journeys") else "")
              + "</div>")
    hot = ((f"<div class='flex items-center gap-3 bg-violet-50 border border-violet-100 rounded-xl px-3.5 py-2.5 mt-2'>"
            f"<div class='flex-1 text-sm text-violet-800'>이번 주 3번 이상 온 <b>관심 손님 {vs['hot_visitors']}명</b> — "
            "이벤트·새 소식 알릴 타이밍이에요.</div>"
            "<a href='/me' class='flex-shrink-0 bg-violet-600 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>소식 글 만들기</a></div>")
           if vs.get("hot_visitors") else "")
    return ("<div class='mt-5 pt-4 border-t border-slate-100'>"
            "<div class='text-sm font-bold text-slate-600 mb-1'>손님 특성 (익명)</div>"
            + rows + visits + hot
            + "<p class='text-[11px] text-slate-400 mt-2'>개인정보는 수집하지 않아요 — 익명 쿠키로 '같은 방문'만 구분해요"
              "(쿠키를 지우면 추적되지 않아요). 지역은 국가 단위까지만 봅니다.</p></div>")


def _track_qr_box(t, fw: str) -> str:
    """매장 QR·손님 추적(도구 서랍용, 압축판) — QR + 유입 수 + 링크. 상시 화면 아님."""
    _tl = _ensure_track_link(t)
    if not _tl:
        return ""
    _clicks = sum(int(l.get("clicks") or 0) for l in db.list_links(t.id))
    _base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
    _short = f"{_base}/r/{_tl['code']}"
    return (f"<div class='{fw}' id='qr'>"
            "<div class='text-sm font-bold text-slate-700 mb-2'>매장 QR — 명함·매장 앞에 붙이면 찍고 온 손님이 집계돼요</div>"
            "<div class='flex items-center gap-4 flex-wrap'>"
            f"<img src='/me/qr/{_tl['code']}.png' class='w-24 h-24 rounded-xl border border-slate-100 p-1 bg-white' alt='추적 QR'>"
            "<div class='flex-1 min-w-[200px]'>"
            f"<div class='text-2xl font-extrabold text-indigo-600'>{_clicks}<span class='text-sm text-slate-400 font-bold ml-1'>회 유입</span></div>"
            "<div class='mt-2 flex items-center gap-2'>"
            f"<input readonly value='{_short}' id='trkurl' class='flex-1 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-600'>"
            "<button type=button onclick=\"navigator.clipboard&&navigator.clipboard.writeText(document.getElementById('trkurl').value);this.textContent='✅'\" "
            "class='flex-shrink-0 bg-indigo-600 text-white text-sm font-bold px-3 py-2 rounded-lg'>복사</button></div>"
            f"<a href='/me/qr/{_tl['code']}.png' download='ollinda-qr.png' class='inline-block mt-2 text-xs font-bold text-indigo-500'>⬇ QR 이미지 저장</a>"
            "</div></div></div>")


def _ai_summary(t) -> str:
    """(auto 3-3) AI 활동 요약 한 줄 — 실측 집계만(이번 주 준비 글 수·1페이지 글 수). 키워드명 없음."""
    try:
        import datetime as _dt
        week_ago = (_dt.datetime.utcnow() - _dt.timedelta(days=7)).isoformat()
        with db._conn() as _c:
            n = _c.execute("SELECT COUNT(*) FROM content_pieces WHERE tenant_id=? AND kind='blog' "
                           "AND created_at >= ?", (t.id, week_ago)).fetchone()[0]
        from app.services import mass as _m
        ev = _m.evidence(t)
        m = ev.get("first_page") or 0
        if not (n or m):
            return ""
        return ("<div class='bg-violet-50 border border-violet-100 rounded-2xl px-4 py-3 mb-5 text-sm text-violet-800'>"
                f"이번 주 AI가 글 <b>{n}개</b>를 준비했고, 발행 글 중 <b>{m}개</b>가 1페이지에 있어요. "
                "<span class='text-violet-500 text-xs'>(실측 기준 · 다음 글감도 자동으로 준비 중)</span></div>")
    except Exception:
        return ""


def _guide_card(t) -> str:
    """첫 사용자 3스텝 온보딩(온보딩 P1) — ①첫 콘텐츠 ②네이버 발행 ③QR·링크 붙이기.
    완료 상태는 실데이터로 판정(체크 저장 불필요·정직). 다 하면 브리핑 안내, '다음에 하기'로 숨김."""
    if getattr(t, "guide_dismissed", 0):
        return ""
    try:
        s1 = bool(db.list_sets(tenant_id=t.id, limit=1))                      # 첫 콘텐츠
        s2 = bool(db.list_blog_publishes(t.id, limit=1))                      # 네이버 발행 확인
        s3 = sum(int(l.get("clicks") or 0) for l in db.list_links(t.id)) > 0  # 링크·QR 첫 유입
    except Exception:
        return ""
    dismiss = ("<form method=post action='/me/guide/dismiss' class='inline'>"
               "<button class='text-xs text-slate-400 underline'>다음에 하기</button></form>")
    if s1 and s2 and s3:
        # 전부 완료 — 축하 + 브리핑 안내 1회(닫으면 다시 안 뜸)
        return ("<div class='flex items-center gap-3 bg-emerald-50 border border-emerald-100 rounded-2xl p-4 mb-5'>"
                f"<span class='text-emerald-500'>{_ic('check', 'w-6 h-6')}</span>"
                "<div class='flex-1 text-sm text-emerald-800'><b>시작 3단계를 모두 마쳤어요!</b> "
                "이제 매일 아침 브리핑이 '오늘 뭘 할지' 챙겨드려요.</div>"
                "<form method=post action='/me/guide/dismiss'>"
                "<button class='flex-shrink-0 bg-emerald-500 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>확인</button></form></div>")

    def _step(done, num, label, href, cta):
        mark = (f"<span class='w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center flex-shrink-0'>{_ic('check', 'w-3.5 h-3.5')}</span>"
                if done else
                f"<span class='w-6 h-6 rounded-full bg-white border border-violet-200 text-violet-600 text-xs font-extrabold flex items-center justify-center flex-shrink-0'>{num}</span>")
        body = (f"<span class='text-sm {'text-slate-400 line-through' if done else 'text-slate-700 font-semibold'}'>{label}</span>")
        act = ("" if done else
               f"<a href='{href}' class='ml-auto flex-shrink-0 bg-violet-600 text-white text-xs font-bold px-3 py-1.5 rounded-xl'>{cta}</a>")
        return f"<div class='flex items-center gap-2.5 py-1.5'>{mark}{body}{act}</div>"
    done_n = sum([s1, s2, s3])
    return ("<div class='bg-violet-50 border border-violet-100 rounded-2xl p-4 mb-5'>"
            "<div class='flex items-center justify-between mb-1.5'>"
            f"<div class='text-sm font-extrabold text-violet-700'>올린다 시작 가이드 <span class='font-bold text-violet-400'>({done_n}/3)</span></div>"
            + dismiss + "</div>"
            + _step(s1, 1, "사진 올려 첫 콘텐츠 만들기", "/me#made", "만들기")
            + _step(s2, 2, "네이버 블로그에 발행하기", "/me?tab=content", "발행 소재 보기")
            + _step(s3, 3, "매장 QR·추적링크 붙이기 (손님 유입이 집계돼요)", "/me#qr", "QR 받기")
            + "</div>")


@app.get("/api/whynot/{piece_id}")
def api_whynot(piece_id: str, request: Request):
    """'왜 아직 안 뜨나요?' 원클릭 노출 진단 + 처방전(HTML 조각 반환).
    실측만: 순위 API·RSS·quality_audit·searchad. 보장 표현 금지(whynot.HONEST_NOTE)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    piece = db.get_piece(piece_id)
    if not piece or piece.tenant_id != t.id:
        return JSONResponse({"error": "글을 찾을 수 없어요."}, status_code=404)
    from app import ratelimit
    if not ratelimit.allow("whynot:" + t.id, 4, 12):   # 진단 1회 = 네이버·searchad 실콜 3~4회
        return JSONResponse({"error": "진단이 잠깐 몰렸어요 — 1~2분 뒤 다시 눌러주세요."}, status_code=429)
    from app.services import whynot
    d = whynot.diagnose(t, piece, db.get_blog_publish(piece_id))
    _icon = {"ok": ("check", "text-emerald-500"), "warn": ("help", "text-amber-500"),
             "fail": ("xcircle", "text-rose-500"), "info": ("clock", "text-slate-400")}
    rows = ""
    for ck in d["checks"]:
        ic, cls = _icon.get(ck["status"], ("info", "text-slate-400"))
        rows += (f"<div class='flex items-start gap-2 py-1.5 border-b border-slate-100'>"
                 f"<span class='{cls} mt-0.5 flex-shrink-0'>{_ic(ic, 'w-4 h-4')}</span>"
                 f"<div><div class='text-sm font-bold text-slate-700'>{esc(ck['title'])}</div>"
                 f"<div class='text-xs text-slate-500'>{esc(ck['detail'])}</div></div></div>")
    # (auto) 처방은 유저 버튼 없이 글감 큐가 자동 실행 — 문구만 결과로 보여준다
    rx = "".join(
        f"<div class='bg-indigo-50 border border-indigo-100 rounded-xl px-3.5 py-2.5 mt-2'>"
        f"<div class='text-sm text-slate-700'>{esc(p['text'])}</div>"
        "<div class='text-[11px] font-bold text-violet-500 mt-1'>→ AI가 다음 글감에 자동 반영해요 (따로 누를 건 없어요)</div></div>"
        for p in d["prescriptions"])
    head = ("이미 노출되고 있어요 — 굳히기가 다음 수예요" if d["exposed"]
            else f"'{esc(d['kw'])}'가 아직 안 뜨는 이유")
    html = (f"<div class='bg-white border border-slate-200 rounded-2xl p-4 mt-2'>"
            f"<div class='text-sm font-extrabold text-slate-800 mb-2'>{head}</div>"
            + rows
            + "<div class='text-xs font-bold text-slate-500 mt-3 mb-1'>AI가 이렇게 대응 중이에요</div>" + rx
            + f"<p class='text-[11px] text-slate-400 mt-2.5'>{esc(d['note'])}</p></div>")
    return JSONResponse({"ok": True, "html": html, "exposed": d["exposed"]})


@app.get("/api/race/{piece_id}")
def api_race(piece_id: str, request: Request):
    """생존 신고 타임라인(생존신고 P3) — 발행→색인→첫 진입→현재 위치→다음 관문 실황(HTML 조각)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    piece = db.get_piece(piece_id)
    pub = db.get_blog_publish(piece_id)
    if not pub or pub.get("tenant_id") != t.id or (piece and piece.tenant_id != t.id):
        return JSONResponse({"error": "발행 기록을 찾을 수 없어요."}, status_code=404)
    from app import ratelimit
    if not ratelimit.allow("race:" + t.id, 6, 20):   # 실측 1회 = 네이버 콜 2~3회
        return JSONResponse({"error": "실황 조회가 잠깐 몰렸어요 — 잠시 후 다시 눌러주세요."}, status_code=429)
    from app.services import race
    d = race.timeline(t, piece, pub)
    _st = {"ok": ("check", "text-emerald-500"), "run": ("arrowup", "text-indigo-500"),
           "wait": ("clock", "text-amber-500"), "gate": ("target", "text-violet-500"),
           "info": ("help", "text-slate-400")}
    rows = ""
    for i, s in enumerate(d["steps"]):
        ic, cls = _st.get(s["status"], ("help", "text-slate-400"))
        line = "" if i == len(d["steps"]) - 1 else "<div class='absolute left-[9px] top-6 bottom-0 w-0.5 bg-slate-100'></div>"
        rows += (f"<div class='relative flex items-start gap-2.5 pb-3'>{line}"
                 f"<span class='{cls} bg-white relative z-10 flex-shrink-0 mt-0.5'>{_ic(ic, 'w-[18px] h-[18px]')}</span>"
                 f"<div><div class='text-sm font-bold text-slate-700'>{esc(s['title'])}</div>"
                 + (f"<div class='text-xs text-slate-500'>{esc(s['detail'])}</div>" if s.get("detail") else "")
                 + "</div></div>")
    scout = (f"<div class='bg-slate-50 border border-slate-100 rounded-xl px-3.5 py-2.5 mt-1 text-xs text-slate-600'>"
             f"<b class='text-slate-700'>경쟁 정찰</b> · {esc(d['scout'])}</div>" if d.get("scout") else "")
    # 발행일 기준 미니 추이(있을 때만) — 낮은 순위(1위)가 높은 막대
    bars = ""
    if d.get("history"):
        cells = ""
        for h in d["history"]:
            v = h["rank"] or 31
            hh = max(4, int(40 * (31 - min(v, 31)) / 30))
            cells += (f"<div class='flex flex-col items-center gap-0.5'>"
                      f"<div class='w-2.5 rounded-t {'bg-indigo-400' if h['rank'] else 'bg-slate-200'}' style='height:{hh}px'></div>"
                      f"<span class='text-[9px] text-slate-400'>{esc(db.fmt_kst(h['at'], date_only=True)[5:])}</span></div>")
        bars = f"<div class='flex items-end gap-1.5 mt-2 overflow-x-auto'>{cells}</div>"
    html = (f"<div class='bg-white border border-slate-200 rounded-2xl p-4 mt-2'>"
            f"<div class='text-sm font-extrabold text-slate-800 mb-2.5'>'{esc(d['kw'])}' 순위 추적 — {d['days']}일차</div>"
            + rows + scout + bars
            + f"<button type=button onclick=\"analystView('{esc(piece_id)}',this)\" "
            "class='mt-2.5 text-[11px] font-bold text-violet-600 border border-violet-200 hover:bg-violet-50 "
            "px-2.5 py-1.5 rounded-lg transition'>왜 이 순위? AI 분석</button>"
            f"<div id='anl_{esc(piece_id)}'></div>"
            + f"<p class='text-[11px] text-slate-400 mt-2.5'>{esc(d['note'])}</p></div>")
    return JSONResponse({"ok": True, "html": html})


# ══ 대량 발행 — 승률 키워드 → 배치 생성 → 스케줄 → 증거(mass P1~P6) ══
@app.get("/me/mass")
def mass_page(request: Request):
    """(auto) 발굴·배치 UI 제거 — 키워드·승률은 AI 내부 재료. 엔진(mine/generate)은 큐가 내부 호출."""
    return RedirectResponse("/me", status_code=303)


@app.post("/api/mass/mine")
@app.post("/api/mass/mine")
async def api_mass_mine(request: Request, industry: str = Form("")):
    """승률 키워드 대량 발굴(P1) — searchad 실검색량+경쟁도+상위 글 나이. 실패=실패 표기(추정 금지)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    from app import ratelimit
    if not ratelimit.allow("massmine:" + t.id, 2, 4):   # 발굴 1회 = searchad ~12콜 + 검색 20콜
        return JSONResponse({"error": "발굴이 잠깐 몰렸어요 — 잠시 후 다시 시도해주세요."}, status_code=429)
    import asyncio
    from app.services import mass
    r = await asyncio.to_thread(mass.mine, t, industry)
    return JSONResponse(r, status_code=(200 if r.get("ok") else 400))


@app.post("/api/mass/generate")
async def api_mass_generate(request: Request):
    """배치 글 생성(P3) — 선택 승률 키워드 최대 5개, 백그라운드(유사문서 회피+업종 게이트+스케줄 배분)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    from app import ratelimit
    if not ratelimit.allow("massgen:" + t.id, 1, 3):
        return JSONResponse({"error": "배치 생성이 이미 진행 중이거나 몰렸어요 — 잠시 후 다시."}, status_code=429)
    form = await request.form()
    batch_id = (form.get("batch_id") or "").strip()
    kws = [k for k in form.getlist("keywords") if (k or "").strip()][:5]
    note = (form.get("note") or "").strip()[:200]
    if not (batch_id and kws):
        return JSONResponse({"error": "배치와 키워드를 선택해주세요."}, status_code=400)
    files = []
    for ph in form.getlist("photos"):
        if ph is not None and getattr(ph, "filename", ""):
            data = await ph.read()
            if data and len(data) <= MAX_UPLOAD_BYTES:
                files.append((data, ph.filename))
    from app.services import mass
    batch = db.get_keyword_batch(batch_id)
    if not batch or batch["tenant_id"] != t.id:
        return JSONResponse({"error": "배치를 찾을 수 없어요."}, status_code=404)
    matched = 0
    for it in batch["items"]:
        if it["keyword"] in set(kws):
            it["status"] = "generating"
            matched += 1
    if not matched:      # 전송 인코딩 문제 등으로 배치와 안 맞으면 정직하게 거절(유령 started 방지)
        return JSONResponse({"error": "선택한 키워드가 이 배치와 일치하지 않아요 — 페이지를 새로고침 후 다시 선택해주세요."},
                            status_code=400)
    db.save_keyword_batch(batch_id, t.id, batch["industry"], batch["items"])
    import threading
    threading.Thread(target=mass.generate_batch, args=(t, batch_id, kws, files, note), daemon=True).start()
    return JSONResponse({"ok": True, "started": matched,
                         "message": f"{len(kws)}개 글을 생성 중이에요 — 글당 1~2분, 끝나면 이 페이지에 스케줄과 함께 표시돼요."})


@app.get("/api/mass/batch/{bid}")
def api_mass_batch(bid: str, request: Request):
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    b = db.get_keyword_batch(bid)
    if not b or b["tenant_id"] != t.id:
        return JSONResponse({"error": "없음"}, status_code=404)
    return JSONResponse({"ok": True, "items": b["items"]})


@app.get("/api/analyst/{piece_id}")
def api_analyst(piece_id: str, request: Request):
    """AI 순위 분석가(분석가 P2) — 왜 이 순위·왜 1위가 아닌가·어떻게 이기나(HTML 조각).
    크롤링 없음: 검색API 제목·요약·발행일 + 공개 RSS 체급 + 업종 패턴 집계. 순위 변동 시에만 LLM 재분석."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    piece = db.get_piece(piece_id)
    pub = db.get_blog_publish(piece_id)
    if not pub or pub.get("tenant_id") != t.id or (piece and piece.tenant_id != t.id):
        return JSONResponse({"error": "발행 기록을 찾을 수 없어요."}, status_code=404)
    from app import ratelimit
    if not ratelimit.allow("analyst:" + t.id, 3, 10):   # 분석 1회 = 네이버 5~7콜 + LLM(캐시 시 0콜)
        return JSONResponse({"error": "분석이 잠깐 몰렸어요 — 잠시 후 다시 눌러주세요."}, status_code=429)
    from app.services import analyst
    d = analyst.analyze(t, piece, pub)
    rank_txt = f"{d['rank']}위" if d.get("rank") else "10위 밖"
    gap_rows = "".join(
        f"<div class='bg-indigo-50 border border-indigo-100 rounded-xl px-3.5 py-2.5 mt-2'>"
        f"<div class='text-[11px] font-bold text-indigo-500 mb-0.5'>실측: {esc(g['why'])}</div>"
        f"<div class='text-sm text-slate-700'>{esc(g['text'])}</div>"
        "<div class='text-[11px] font-bold text-violet-500 mt-1'>→ AI가 다음 글감에 자동 반영해요</div></div>"
        for g in d["gaps"])
    _sec = lambda title, body: ((f"<div class='mt-2.5'><div class='text-xs font-bold text-slate-500 mb-0.5'>{title}</div>"
                                 f"<div class='text-sm text-slate-700'>{esc(body)}</div></div>") if body else "")
    html = (f"<div class='bg-white border border-violet-200 rounded-2xl p-4 mt-2'>"
            f"<div class='text-sm font-extrabold text-slate-800'>AI 순위 분석 — '{esc(d['kw'])}' 현재 {rank_txt}</div>"
            + _sec("왜 이 순위까지 왔나", d.get("why_here"))
            + _sec("왜 1위가 아닌가", d.get("why_not_first"))
            + "<div class='text-xs font-bold text-slate-500 mt-3 mb-0.5'>어떻게 이기나 — AI가 자동 대응</div>" + gap_rows
            + f"<p class='text-[11px] text-slate-400 mt-2.5'>{esc(d['note'])}</p></div>")
    return JSONResponse({"ok": True, "html": html})


@app.post("/api/piece/{pid}/enrich")
async def api_piece_enrich(pid: str, request: Request, note: str = Form("")):
    """진단→처방 실행(rx P2): 품질 낮은 글을 audit 경고 기반 지시문 + 사장님 제공 실제 정보로
    재작성(보강). 효과 '보장' 없음 — 점수 전/후만 정직하게 보여준다."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"error": "로그인이 필요해요."}, status_code=401)
    t = _ensure_user_tenant(u)
    piece = db.get_piece(pid)
    if not piece or piece.tenant_id != t.id:
        return JSONResponse({"error": "글을 찾을 수 없어요."}, status_code=404)
    if piece.kind.value != "blog":
        return JSONResponse({"error": "블로그 글만 보강할 수 있어요."}, status_code=400)
    from app import ratelimit
    if not ratelimit.allow("enrich:" + t.id, 2, 6):     # 보강 1회 = LLM 1콜
        return JSONResponse({"error": "보강이 잠깐 몰렸어요 — 잠시 후 다시 시도해주세요."}, status_code=429)
    audit = (piece.payload or {}).get("ranking_audit") or {}
    before = audit.get("score")
    instr = autofix_instruction(audit, piece.kind.value) or "1인칭 실제 경험 문장과 구체 수치를 보강"
    note = (note or "").strip()[:200]
    if note:
        instr += (f"\n[사장님 제공 실제 정보 — 사실로 반영(최우선), 지어내기 금지] {note}")
    instr += "\n입력에 없는 가격·수치·스펙은 추가하지 마라."
    try:
        import asyncio
        await asyncio.to_thread(revise_piece, piece, instr)
    except Exception:
        return JSONResponse({"error": "보강 생성에 문제가 있었어요 — 잠시 후 다시."}, status_code=200)
    after = ((piece.payload or {}).get("ranking_audit") or {}).get("score")
    return JSONResponse({"ok": True, "before": before, "after": after,
                         "kit": f"/kit/{piece.asset_id}/naver",
                         "msg": (f"보강 완료 — 품질 {before}→{after}점. " if before is not None and after is not None
                                 else "보강 완료. ") + "발행 소재에서 확인하고 다시 발행해보세요."})


@app.post("/me/guide/dismiss")
def guide_dismiss(request: Request):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    db.dismiss_guide(t.id)
    return RedirectResponse("/me", status_code=303)


def _briefing_card(t, plan: str) -> str:
    """오늘의 브리핑 카드(브리핑 PHASE 5) — 능동 발송과 별개로 앱에서도 확인. 밝은 톤.
    게이팅: 전 플랜 제공(리텐션 목적 — 매일 들어올 이유가 곧 구독 유지)."""
    from app.services import briefing as _bf
    try:
        b = _bf.get_or_create_today(t, plan)
    except Exception:
        return ""
    if b.get("passed"):
        return (f"<div class='{_CARD} p-4 mb-5 flex items-center gap-3'>"
                f"{_icchip('checkcircle')}"
                "<div class='text-sm text-slate-500'>오늘 브리핑은 패스하셨어요 — 푹 쉬세요. "
                "내일 아침에 새 브리핑으로 찾아뵐게요.</div></div>")
    hour = int(getattr(t, "briefing_hour", 8) or 8)
    on = bool(getattr(t, "briefing_on", 1))
    hours_opts = "".join(f"<option value='{h}'{' selected' if h == hour else ''}>{h:02d}:00</option>"
                         for h in range(5, 13))
    pref = ("<details class='mt-3'><summary class='text-xs text-slate-400 cursor-pointer select-none'>"
            f"브리핑 설정 — 매일 {hour:02d}:00 · {'켜짐' if on else '꺼짐'}</summary>"
            "<form method=post action='/me/briefing-pref' class='flex items-center gap-2 mt-2'>"
            f"<select name=hour class='border border-slate-200 rounded-xl px-2.5 py-2 text-sm'>{hours_opts}</select>"
            f"<label class='text-sm text-slate-600 flex items-center gap-1.5'>"
            f"<input type=checkbox name=on value=1 {'checked' if on else ''}> 아침 브리핑 받기</label>"
            f"<button class='{_BTN} text-xs px-3 py-2'>저장</button></form></details>")
    # ('주방은 보여주지 않는다') 헤드라인 잔소리·이유·파트너 노트 삭제 — 할 일 한 문장 + 버튼만.
    return (f"<div class='bg-[#F5F3FF] border border-indigo-200 rounded-2xl p-5 mb-5'>"
            "<div class='flex items-center gap-2 mb-1.5'>"
            f"{_ic('message', 'w-4 h-4 text-indigo-600')}"
            "<span class='text-xs font-bold text-indigo-600'>오늘 할 일 딱 하나</span></div>"
            f"<div class='text-sm font-bold text-slate-900'>{esc(b.get('task', ''))}</div>"
            "<div class='flex items-center gap-2 mt-3'>"
            f"<a href='{b.get('action_href', '/me')}' class='{_BTN} text-sm px-4 py-2.5'>{esc(b.get('action_label', '시작하기'))}</a>"
            "<button type=button onclick=\"fetch('/api/briefing/pass',{method:'POST'}).then(r=>r.json())"
            ".then(d=>{location.reload();})\" class='text-xs text-slate-400 hover:text-slate-600 px-2'>오늘은 패스</button>"
            "</div>" + pref + "</div>")


def _calendar_card(t, plan: str) -> str:
    """발행 캘린더 카드(상위노출 PHASE 2) — 이번 주 진행률 + 남은 슬롯 제안 + 주제 축."""
    from app.services import pubcal
    wp = pubcal.week_plan(t, plan)
    # 진행률 도트(●=완료 ○=남음)
    dots = "".join("<span class='w-3.5 h-3.5 rounded-full bg-emerald-500 inline-block'></span>"
                   for _ in range(min(wp["done"], wp["target"])))
    dots += "".join("<span class='w-3.5 h-3.5 rounded-full bg-slate-200 inline-block'></span>"
                    for _ in range(wp["remaining"]))
    basis_note = "" if wp["basis"] == "published" else " <span class='text-[10px] text-slate-400'>(발행확인 전엔 생성 기준)</span>"
    sug_html = ""
    for s in wp["suggestions"][:3]:
        sug_html += (f"<a href='{s['href']}' class='flex items-center justify-between bg-white border border-slate-100 "
                     "rounded-xl px-3.5 py-2.5 mb-1.5 hover:border-indigo-300 hover:shadow-sm transition'>"
                     f"<div class='text-sm'><b class='text-slate-700'>{esc(s['topic'])}</b> "
                     f"<span class='text-xs text-indigo-500 font-bold'>{s['angle_label']}</span>"
                     f"<div class='text-[11px] text-slate-400'>{esc(s['why'])}</div></div>"
                     "<span class='text-xs font-bold text-indigo-600 whitespace-nowrap'>만들기 →</span></a>")
    axis = esc(getattr(t, "topic_axis", "") or "")
    inp = "flex-1 border border-slate-200 rounded-xl px-3 py-2 text-sm"
    axis_form = ("<details class='mt-2'><summary class='text-xs text-slate-400 cursor-pointer select-none'>"
                 f"전문 주제 축 {('· <b class=\"text-slate-600\">' + axis + '</b>') if axis else '설정(권장)'} — 같은 주제 꾸준함이 노출 신호</summary>"
                 "<form method=post action='/me/topic-axis' class='flex gap-2 mt-2'>"
                 f"<input name=topic_axis value=\"{axis}\" placeholder='예: 부산 썬팅, 열차단 필름 (쉼표로 여러 개)' class='{inp}'>"
                 "<button class='px-4 bg-slate-900 text-white rounded-xl text-xs font-bold'>저장</button></form></details>")
    return ("<div class='bg-white rounded-3xl border border-slate-100 shadow-sm p-5 mb-5'>"
            "<div class='flex items-center justify-between mb-2'>"
            f"<h2 class='font-extrabold text-slate-900'>발행 캘린더 · 이번 주 {wp['done']}/{wp['target']}{basis_note}</h2>"
            f"<div class='flex items-center gap-1'>{dots}</div></div>"
            f"<p class='text-xs text-slate-500 mb-3'>{esc(wp['coach'])}</p>"
            + sug_html + axis_form + "</div>")


@app.post("/me/place-news")
def my_place_news(request: Request):
    """플레이스 소식 3개 자동 생성 → 저장(붙여넣기용)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    try:
        from app.services import place_news
        for txt in place_news.generate(t, 3):
            db.add_place_news(t.id, txt)
        msg = "플레이스 소식 3개를 만들었어요! 아래에서 복사해 스마트플레이스 소식에 올리세요"
    except Exception:
        msg = "소식 생성 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요"
    return RedirectResponse(f"/me?ok={msg}", status_code=303)


@app.get("/me/rank")
def my_rank(request: Request):
    """순위 성과 조회 — 순위 + 지난 대비 변화(⬆️⬇️) + 경쟁 추월 대상(바로 위 가게)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"items": [], "configured": False})
    t = _ensure_user_tenant(u)
    from app.services import place
    kws: list = []
    for s in db.list_sets(tenant_id=t.id, limit=50):
        for p in db.get_set_pieces(s["asset_id"]):
            for k in (p.payload.get("target_keywords") or []):
                if k and k not in kws:
                    kws.append(k)
    # 발행 추적 키워드 자동 편입(완전자동 파이프) — 외부 글 target_kw + 순위 스냅샷 키워드.
    # 콘텐츠를 안 만들었어도 블로그만 연결하면 '키워드 순위'가 채워진다.
    for pub in db.list_blog_publishes(t.id, limit=10):
        k = (pub.get("target_kw") or "").strip()
        if k and k not in kws:
            kws.append(k)
    for k in db.tracked_keywords(t.id):
        if k and k not in kws:
            kws.append(k)
    # blog_id 연결 시: 블로그검색 결과에서 내 블로그 '정확 식별'(상호매칭 오탐 없음, 블로그등록 PHASE 3)
    bid = getattr(t, "blog_id", "") or ""
    _cites = {}                                     # PHASE 4: 키워드별 AI 브리핑 인용수(캡처 판독 저장분)
    for _c in db.blog_citations(t.id, limit=50):
        _ck = (_c.get("keyword") or "").strip()
        if _ck and _ck not in _cites:
            _cites[_ck] = _c.get("citation_count")
    items = []
    for k in kws[:5]:
        det = place.rank_detail(k, t.name)
        cur = det["rank"]
        prev = db.get_prev_rank(t.id, k)            # 오늘 이전 순위(변화 계산)
        db.save_rank_snapshot(t.id, k, cur)         # 오늘 순위 기록
        item = {"kw": k, "rank": cur, "prev": prev,
                "rival": det["rival"], "leader": det["leader"],
                "citation": _cites.get(k)}          # 순위/클릭과 함께 인용수 표시(없으면 null)
        if bid:
            from app.services import blogrank
            br = blogrank.blog_rank(k, bid)
            item["blog_rank"] = br["rank"]          # 1~30 | 0=미노출 | None=조회불가
            item["blog_prev"] = db.get_prev_rank(t.id, k, kind="blog_search")
            item["blog_url"] = br["url"]
            db.save_rank_snapshot(t.id, k, br["rank"], kind="blog_search")
        items.append(item)
    # (주방 철학, 2026-08-01 사장님 정정) 진입률 등 성적 지표는 사용자에게 비노출 —
    # 운영자 확인은 /admin/shop-perf(사령탑 패널)로.
    return JSONResponse({"items": items, "configured": place.configured(),
                         "blog_connected": bool(bid)})


@app.get("/me/experience", response_class=HTMLResponse)
def my_experience(request: Request, ok: str = "", err: str = ""):
    """트랙 B 실경험 원료 관리 — 사장이 실제 받는 질문·답변 Q&A 등록/수정/삭제.
    유효 Q&A 1건+ 있어야 정보성 글(트랙 B) 생성 시작(안내는 여기서). 트랙 A는 무관."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login?next=/me/experience", status_code=303)
    t = _ensure_user_tenant(u)
    exps = db.list_owner_experience(t.id)
    # 질문 예시(스키마 content_angles 유래 — 업종 하드코딩 0). 실패해도 안내는 나옴.
    hints = []
    try:
        from app.services import geo_track as _geo, indschema as _isc
        sch = _isc.get_schema(t.industry, getattr(t, "biz_type", "local") or "local")
        hints = [tp["topic"] for tp in _geo.info_topics(t.industry, getattr(t, "biz_type", "local") or "local",
                 sch, region=t.region or "")][:2]
    except Exception:
        pass
    # 🗣 선택 보조 상자(2026-08-03 사장님 지시로 격하) — 주 경로는 '생성 시점 인라인 질문'이다.
    #   여기서는 재촉하지 않는다: 뱃지·미답변 카운트·검색량·자리 같은 주방 표기 전부 없음.
    #   사장님은 결과만 보신다 — 왜 이 질문인지는 시스템이 알면 된다.
    _gapq = []
    try:
        from app.services import gapscout as _gs2
        _gapq = _gs2.questions(t.id, limit=2)
    except Exception:
        pass
    gapbox = ""
    if _gapq:
        _rows = "".join(
            f"<div class='border border-slate-100 rounded-xl p-3 mb-2'>"
            f"<div class='text-sm text-slate-700'>{esc(g['question'])}</div>"
            f"<button type=button onclick=\"document.getElementById('qf').value={json.dumps(g['question'], ensure_ascii=False)};"
            f"document.getElementById('af').focus();window.scrollTo({{top:document.getElementById('af').offsetTop-120,behavior:'smooth'}});\" "
            f"class='mt-2 px-3 py-1.5 bg-slate-100 text-slate-600 text-xs font-bold rounded-lg'>여기에 답하기</button>"
            f"</div>" for g in _gapq)
        gapbox = ("<div class='bg-white rounded-2xl border border-slate-200 shadow-sm p-5 mb-5'>"
                  "<div class='text-xs font-bold text-slate-400 mb-2'>이런 것도 한 줄 적어두시면 좋아요</div>"
                  + _rows + "</div>")
    sec = "bg-white rounded-2xl border border-slate-200 shadow-sm p-5 mb-5"
    status = (f"<div class='bg-emerald-50 text-emerald-700 rounded-xl px-4 py-3 text-sm mb-4'>"
              f"✅ 실경험 답변 {len(exps)}건 등록됨 — 정보성 글(네이버 AI 브리핑용)이 자동으로 시작됩니다.</div>"
              if exps else
              "<div class='bg-amber-50 text-amber-700 rounded-xl px-4 py-3 text-sm mb-4'>"
              "📝 <b>경험 답변을 등록하면 정보성 글이 시작됩니다.</b> 손님이 실제로 자주 묻는 질문과, "
              "사장님이 해주시는 답변을 적어주세요 — 이 내용이 네이버 AI가 인용하는 글이 됩니다.</div>")
    items = "".join(
        f"<div class='border border-slate-100 rounded-xl p-3 mb-2'>"
        f"<div class='text-sm font-bold text-slate-800'>Q. {esc(e['question'])}</div>"
        f"<div class='text-sm text-slate-600 mt-1 whitespace-pre-wrap'>A. {esc(e['answer'])}</div>"
        f"<form method=post action='/me/experience/delete' class='mt-2'><input type=hidden name=exp_id value='{e['id']}'>"
        f"<button class='text-xs text-rose-500 font-bold'>삭제</button></form></div>"
        for e in exps) or "<div class='text-sm text-slate-400 mb-2'>아직 등록된 답변이 없어요.</div>"
    ph = (f"예: {esc(hints[0])}" if hints else "예: 손님들이 가장 자주 묻는 질문")
    ph_a = "예: 저는 항상 ~를 먼저 봅니다. 특히 ~인 경우엔 ~하라고 안내드려요. (실무 내용만, 맞춤법 신경 안 쓰셔도 돼요)"
    inner = (
        "<a href='/me' class='inline-block text-sm text-slate-500 font-bold mb-2'>← 작업실</a>"
        "<h1 class='text-2xl font-extrabold text-slate-900 mb-1'>사장님 실경험 답변</h1>"
        "<p class='text-slate-400 text-sm mb-5'>손님이 자주 묻는 질문과 실제 답변을 적어주세요. "
        "사장님만 아는 현장 답변이 곧 네이버 AI 브리핑에 인용되는 글이 됩니다(맞춤법·문장력 불필요).</p>"
        + (f"<div class='bg-emerald-50 text-emerald-700 rounded-xl px-4 py-2 text-sm mb-3'>{esc(ok)}</div>" if ok else "")
        + (f"<div class='bg-rose-50 text-rose-600 rounded-xl px-4 py-2 text-sm mb-3'>{esc(err)}</div>" if err else "")
        + status
        + gapbox                                  # 🕳 빈자리가 기다리는 질문(맨 위 — 지금 할 일)
        + f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>등록된 Q&A</div>{items}</div>"
        + f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>답변 추가</div>"
        "<form method=post action='/me/experience/add' class='space-y-2'>"
        f"<input name=question id=qf maxlength=200 placeholder=\"{ph}\" required class='w-full border border-slate-200 rounded-xl px-3 py-2 text-sm'>"
        f"<textarea name=answer id=af rows=4 placeholder=\"{ph_a}\" required class='w-full border border-slate-200 rounded-xl px-3 py-2 text-sm'></textarea>"
        "<div class='text-[11px] text-slate-400'>답변은 최소 50자 — 실제로 해주시는 설명을 편하게 적어주세요.</div>"
        "<button class='px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold'>답변 등록</button>"
        "</form></div>")
    return HTMLResponse(_subscriber_page("사장님 실경험", inner))


@app.post("/me/experience/add")
async def my_experience_add(request: Request):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login?next=/me/experience", status_code=303)
    t = _ensure_user_tenant(u)
    from urllib.parse import quote as _q
    form = await request.form()
    q = (form.get("question") or "").strip()
    a = (form.get("answer") or "").strip()
    if db.save_owner_experience(t.id, q, a):
        return RedirectResponse("/me/experience?ok=" + _q("답변을 등록했어요 — 정보성 글에 반영됩니다"), status_code=303)
    return RedirectResponse("/me/experience?err=" + _q("답변은 최소 50자 이상 적어주세요(실무 내용만)"), status_code=303)


@app.post("/me/experience/delete")
async def my_experience_delete(request: Request):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login?next=/me/experience", status_code=303)
    t = _ensure_user_tenant(u)
    from urllib.parse import quote as _q
    form = await request.form()
    try:
        db.delete_owner_experience(int(form.get("exp_id") or 0), t.id)
    except Exception:
        pass
    return RedirectResponse("/me/experience?ok=" + _q("삭제했어요"), status_code=303)


@app.post("/me/set/{asset_id}/title")
async def my_set_title(request: Request, asset_id: str):
    """PHASE B — 제목 3안 선택 저장(selected_title). 발행물 전 명명(네이버 제목·복사·폴더·파일명·슬러그)이
    이 하나만 참조. 후보(title_options) 중 하나여야 저장(임의 주입 금지)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"ok": False}, status_code=401)
    pieces = _owned_pieces(u, asset_id)
    if not pieces:
        return JSONResponse({"ok": False}, status_code=404)
    form = await request.form()
    chosen = (form.get("title") or "").strip()
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    if not (blog and chosen):
        return JSONResponse({"ok": False, "error": "제목 없음"}, status_code=400)
    opts = [o for o in (blog.payload.get("title_options") or []) if o]
    if chosen not in opts and chosen != (blog.payload.get("title") or ""):
        return JSONResponse({"ok": False, "error": "후보 아님"}, status_code=400)
    blog.payload["selected_title"] = chosen            # 단일 소스(슬러그·파일명·폴더·복사 전부 참조)
    db.save_piece(blog)
    return JSONResponse({"ok": True, "selected_title": chosen})


@app.post("/me/inquiry")
async def my_inquiry(request: Request):
    """문의 출처 기록(초경량 3탭) — [문의 옴] → 출처(블로그/인스타/당근/기타/모름) + 메모 1줄(선택).
    전환 측정의 '문의 K건'. 개인정보 저장 안 함(출처·메모만)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login?next=/me", status_code=303)
    t = _ensure_user_tenant(u)
    from urllib.parse import quote as _q
    form = await request.form()
    src = (form.get("source") or "").strip()
    if db.save_inquiry(t.id, src, form.get("memo") or ""):
        return RedirectResponse("/me?ok=" + _q(f"문의 1건 기록됨({src}) — 전환 리포트에 반영돼요"), status_code=303)
    return RedirectResponse("/me?err=" + _q("출처를 선택해 주세요"), status_code=303)


@app.post("/me/citation-upload")
async def my_citation_upload(request: Request):
    """PHASE 4 — 크리에이터 어드바이저 통계 캡처 업로드 → AI 브리핑 인용수 판독·저장(vision).
    API 미제공 지표라 캡처 판독으로 편입. 판독 실패(숫자 안 보임)면 저장 안 함(날조 금지)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login?next=/me", status_code=303)
    t = _ensure_user_tenant(u)
    form = await request.form()
    ph = form.get("capture")
    if ph is None or not getattr(ph, "filename", ""):
        return RedirectResponse("/me?err=캡처 이미지를 선택해 주세요", status_code=303)
    from app.services import geo_track as _geo
    path = storage.save_upload(await ph.read(), ph.filename, t.id)
    res = _geo.read_citation_capture(path)
    cc = res.get("citation_count")
    if cc is None:
        return RedirectResponse("/me?err=캡처에서 인용수를 읽지 못했어요 — 'AI 브리핑 인용수'가 보이는 화면으로 다시 시도해 주세요", status_code=303)
    # 캡처에서 읽은 키워드(있으면)로 내 블로그 글 매칭 → piece_id 연결(없으면 키워드만 저장)
    kw = res.get("keyword") or ""
    pid = ""
    if kw:
        for s in db.list_sets(tenant_id=t.id, limit=30):
            for p in db.get_set_pieces(s["asset_id"]):
                if p.kind.value == "blog" and kw[:8] and kw[:8] in (p.payload.get("title") or ""):
                    pid = p.id
                    break
            if pid:
                break
    db.save_blog_citation(t.id, pid, kw or "(캡처)", cc)
    return RedirectResponse(f"/me?ok=AI 브리핑 인용수 {cc}회를 기록했어요 — 리포트에 반영됩니다", status_code=303)


@app.get("/admin/kit-verify")
def admin_kit_verify(tid: str = "", asset_id: str = "", inject: str = "", regen: str = ""):
    """(진단) 트랙 A 세트 재생성 → 전 표면 실물(제목·본문·태그·캡션·영상메타·슬러그·파일명) +
    PHASE 3 오염 게이트 판정. asset_id 주면 '그 세트의 실제 사진'으로 재생성(사진-세트 정합).
    미지정 시 tid의 photo_pool. inject=1이면 낡은 필드에 '레이 중고' 주입 → 게이트 차단 확인."""
    from app.services import generate as _gen
    from app.domain.models import AssetType as _AT, ContentKind as _CK
    _set_id = ""
    if asset_id:
        _sp = db.get_set_pieces(asset_id)
        _bl0 = next((p for p in _sp if p.kind.value == "blog"), None)
        if not _bl0:
            return JSONResponse({"ok": False, "error": "세트에 블로그 피스 없음"}, status_code=404)
        t = db.get_tenant(_bl0.tenant_id)
        paths = [x for x in (_bl0.payload.get("image_paths") or []) if x and os.path.exists(x)]  # 그 세트 실제 사진
        _set_id = asset_id
        if not paths:                                    # 로컬 소실 시 R2 복원
            try:
                from app.services.ingest import _restore_media
                paths = _restore_media(_bl0.tenant_id, _bl0.payload.get("image_paths") or [])
            except Exception:
                paths = []
    else:
        t = db.get_tenant(tid)
        if not t:
            return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
        try:
            from app.services.autoqueue import photo_pool as _pp
            paths = _pp(t)[:16]
        except Exception:
            paths = []
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    paths = paths[:20]
    if not paths:
        return JSONResponse({"ok": False, "error": "사진 없음(세트 사진 소실 가능)"}, status_code=409)
    note = "[자동 글감] 매물 실사진 세트"
    try:
        from app import vision                       # 이 모듈은 vision을 전역으로 들이지 않는다
        analysis = vision.analyze_all(paths, t.industry)
        if analysis:
            note += f"\n[사진 분석] {analysis[:2500]}"
    except Exception:
        pass
    asset = db.create_asset(t.id, _AT.IMAGE, paths[0], note)
    asset.content_type = "sell"
    from app.registry import get_generator as _gg
    blog = _gg(_CK.BLOG).generate(t, asset, paths)
    if not blog:
        return JSONResponse({"ok": False, "error": "생성 실패"}, status_code=500)
    # 오염 주입(V3): 낡은 필드에 '레이 중고' 강제 → 게이트가 잡는지
    if inject == "1":
        blog.payload["target_keywords"] = ["레이 중고"] + (blog.payload.get("target_keywords") or [])
        blog.payload["tags"] = ["레이중고", "레이"] + (blog.payload.get("tags") or [])   # 저장 태그 오염(키트 txt 경로)
        blog.payload["_inject_nv"] = {"path": "/tmp/x.mp4", "title": "레이 중고 핵심만 정리했어요",
                                      "hashtags": ["#레이중고"], "desc": "레이 중고 영상"}
    n_imgs = len(blog.payload.get("image_paths") or paths)
    caps = _photo_captions(t, blog, n_imgs)
    tags = _blog_tags(t, blog)
    slug = _canonical_slug(t, blog)
    canon = _canonical_keyword(t, blog)
    # 영상 메타 — inject면 주입한 stale, 아니면 canonical 유도(클린)
    _mock_short = type("P", (), {"kind": _CK.SHORT, "channel": type("C", (), {"value": "youtube"})(),
                                 "payload": {"naver_video": blog.payload.get("_inject_nv") or
                                             {"path": "/tmp/x.mp4", "title": f"{canon} 핵심만 정리했어요",
                                              "hashtags": [], "desc": ""}},
                                 "id": "mock", "tenant_id": t.id})()
    pieces = [blog, _mock_short]
    nv_disp = _nv_canonical(t, blog, _set_naver_video(pieces)) if inject != "1" else _set_naver_video(pieces)
    gate = _kit_contamination_gate(t, pieces)
    import re as _r
    def _scan(txt):
        return bool(_r.search(r"(?<![가-힣])레이", txt or ""))
    _title_v = blog.payload.get("selected_title") or blog.payload.get("title", "")
    surfaces_rey = {
        "제목": _scan(_title_v), "본문": _scan(blog.payload.get("body", "")),
        "태그": any(_scan(x) for x in (tags or [])), "캡션": any(_scan(x) for x in (caps or [])),
        "영상제목": _scan(nv_disp.get("title", "")), "해시태그": any(_scan(x) for x in (nv_disp.get("hashtags") or [])),
        "슬러그": _scan(slug),
    }
    # 지역 축 검증 — 기초지역(기장 등) 지명이 표면에 등장하나 + V2 근거(searchad 실측 비교)
    _cores = seo.basic_region_cores(getattr(t, "region", "") or "")
    _creg = blog.payload.get("canonical_region", "")
    _region_vols = {}
    try:
        from app.services import searchad as _sa
        _indv = ((t.industry or "").replace("/", ",").split(",")[0] or "").strip()
        _wide = seo._region_wide(getattr(t, "region", "") or "")
        _cands = [f"{_wide} {_indv}"] + [f"{c} {_indv}" for c in _cores]
        if _sa.configured() and _indv:
            _region_vols = {(_v.get("keyword") or ""): (_v.get("total") or 0)
                            for _v in _sa.keyword_volumes(_cands, limit=10)}
        _region_vols["_threshold"] = seo.REGION_MIN_VOLUME
    except Exception:
        pass
    def _rscan(txt):
        return [c for c in _cores if _r.search(r"(?<![가-힣])" + _r.escape(c), txt or "")]
    region_by_surface = {
        "제목": _rscan(_title_v), "본문(핵심)": _rscan(_body_core(blog.payload.get("body", ""))),
        "태그": _rscan(" ".join(tags or [])), "영상제목": _rscan(nv_disp.get("title", "")),
        "해시태그": _rscan(" ".join(nv_disp.get("hashtags") or [])),
    }
    return JSONResponse({"ok": True, "tenant": t.name, "set_id": _set_id, "n_photos": len(paths),
        "region_profile": getattr(t, "region", ""), "canonical_region": _creg, "basic_region_cores": _cores,
        "region_volumes_실측": _region_vols,
        "photo_basenames": [os.path.basename(p) for p in paths[:20]],
        "inventory_now": [{"model": c.get("model"), "class": c.get("car_class")} for c in db.recent_inventory_context(t.id, 6)],
        "canonical_keyword": canon, "slug": slug, "title": _title_v,
        "_diag_note": (note or "")[:600],                    # 진단: 생성 입력 note(캐스퍼 유입 추적)
        "_diag_target_kws": blog.payload.get("target_keywords"),   # 진단: 생성기가 확정한 키워드
        "_diag_search_kw": getattr(t, "search_kw", ""),
        "title_options": blog.payload.get("title_options"),
        "captions": caps, "tags": tags,
        "video": {"title": nv_disp.get("title"), "desc": nv_disp.get("desc"), "hashtags": nv_disp.get("hashtags")},
        "filenames_sample": [f"{slug}_{i}.jpg" for i in range(1, 4)] + [f"{slug}_네이버영상.mp4"],
        "레이_by_surface": surfaces_rey, "레이_total": sum(1 for v in surfaces_rey.values() if v),
        "기초지역_by_surface": region_by_surface,
        "기초지역_total": sum(len(v) for v in region_by_surface.values()),
        "contamination_gate": {"passed": gate["passed"], "violations": gate["violations"],
                               "ctx": gate["ctx"], "canonical_region": gate.get("canonical_region")}})


@app.get("/admin/overlay-test")
def admin_overlay_test(asset_id: str = "", tid: str = "", limit: int = 16):
    """(진단·A 검증) 세트 사진에 오버레이 탐지+제거를 '사본에서' 실행(원본 불변) →
    V1 기법 판정표 + V2 오탐율. 반환 per-photo {file,detected,type,coverage,action,restored} +
    summary{n, 탐지, 제거, 폴백, skip_b, 오탐후보}. 오탐후보=제거·인페인트 됐는데 코너 스탬프가 아닐 위험."""
    import shutil as _sh
    from app.media import photo_boost as _pb
    # 세트 사진 로드(kit-verify와 동일 경로)
    if asset_id:
        _sp = db.get_set_pieces(asset_id)
        _bl0 = next((p for p in _sp if p.kind.value == "blog"), None)
        if not _bl0:
            return JSONResponse({"ok": False, "error": "세트에 블로그 피스 없음"}, status_code=404)
        paths = [x for x in (_bl0.payload.get("image_paths") or []) if x and os.path.exists(x)]
        if not paths:
            try:
                from app.services.ingest import _restore_media
                paths = _restore_media(_bl0.tenant_id, _bl0.payload.get("image_paths") or [])
            except Exception:
                paths = []
    else:
        t = db.get_tenant(tid)
        if not t:
            return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
        try:
            from app.services.autoqueue import photo_pool as _pp
            paths = _pp(t)[:limit]
        except Exception:
            paths = []
    paths = [p for p in paths if p and os.path.exists(p)][:limit]
    if not paths:
        return JSONResponse({"ok": False, "error": "사진 없음"}, status_code=409)
    _scratch = "/tmp/overlay_test"
    os.makedirs(_scratch, exist_ok=True)
    def _thumb_b64(fp, box=None):
        try:
            import base64 as _b64
            import io as _io
            from PIL import Image as _Im, ImageOps as _IO, ImageDraw as _ID
            im = _IO.exif_transpose(_Im.open(fp)).convert("RGB")
            im.thumbnail((420, 420))
            if box:                                          # 탐지 bbox 빨간 테두리(전 이미지용)
                W, H = im.size
                d = _ID.Draw(im)
                d.rectangle([box.get("x0", 0) * W, box.get("y0", 0) * H,
                             box.get("x1", 0) * W, box.get("y1", 0) * H], outline=(255, 0, 0), width=3)
            buf = _io.BytesIO(); im.save(buf, "JPEG", quality=72)
            return "data:image/jpeg;base64," + _b64.b64encode(buf.getvalue()).decode()
        except Exception:
            return ""
    rows = []
    for i, p in enumerate(paths):
        cp = os.path.join(_scratch, f"c{i}.jpg")
        try:
            from app import vision as _vs
            det0 = _vs.detect_overlay(p)                     # kind·bbox 확보(원본 기준)
            _sh.copyfile(p, cp)
            rep = _pb.remove_overlay(cp, cp)     # 사본에서만 — 원본 세트 사진 불변
            rep["kind"] = det0.get("kind")
            if det0.get("present") and det0.get("type") == "a":
                _bx = {k: det0.get(k, 0) for k in ("x0", "y0", "x1", "y1")}
                rep["before"] = _thumb_b64(p, _bx)          # 전(빨간 박스)
                rep["after"] = _thumb_b64(cp)               # 후(게이트 통과 후 최종)
                try:                                        # raw 인페인트(게이트 전) — 품질 육안 판정용
                    from PIL import Image as _Im2, ImageOps as _IO2
                    import io as _io2, base64 as _b642
                    _o = _IO2.exif_transpose(_Im2.open(p)).convert("RGB")
                    _raw = _pb._cv_inpaint(_o, _bx, "telea")
                    if _raw is not None:
                        _raw.thumbnail((420, 420))
                        _bf = _io2.BytesIO(); _raw.save(_bf, "JPEG", quality=72)
                        rep["after_raw"] = "data:image/jpeg;base64," + _b642.b64encode(_bf.getvalue()).decode()
                except Exception:
                    pass
        except Exception as e:
            rep = {"action": "error", "err": str(e)[:80]}
        rep["file"] = os.path.basename(p)
        rows.append(rep)
    acts = [r.get("action") for r in rows]
    # cv2 미설치면 제거 자체가 no_cv2 — 그땐 오탐 측정 불가(탐지만 유효)
    return JSONResponse({"ok": True, "n": len(rows),
        "cv2": "미설치(no_cv2)" if any(a == "no_cv2" for a in acts) else "설치됨",
        "summary": {
            "탐지": sum(1 for r in rows if r.get("detected")),
            "완전제거": acts.count("inpainted"),
            "부분제거": acts.count("inpainted_partial"),
            "총제거오버레이수": sum(int(r.get("removed") or 0) for r in rows),
            "skip_전면b": acts.count("skip_type_b"),
            "skip_과대": acts.count("skip_large"),
            "무처리_none": acts.count("none"),
        },
        "photos": rows})


@app.get("/admin/contamination-scan")
def admin_contamination_scan(token: str = "레이", tid: str = "", limit: int = 300):
    """PHASE 0 — DB 전수 오염 스캔. 전 테이블·전 텍스트 컬럼에서 token을 단어 경계로 조회
    (앞이 한글이면 불일치 — '플레이스'의 '레이' 오탐 제외). tid 주면 그 tenant 관련만.
    반환 [{table, column, rowid, tenant, snippet}]."""
    import re as _r
    pat = _r.compile(r"(?<![가-힣])" + _r.escape(token))
    hits = []
    try:
        with db._conn() as c:
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            for tbl in tables:
                try:
                    cols = c.execute(f"PRAGMA table_info({tbl})").fetchall()
                except Exception:
                    continue
                txt_cols = [col[1] for col in cols if str(col[2]).upper() in ("TEXT", "") or "CHAR" in str(col[2]).upper()]
                if not txt_cols:
                    continue
                has_tid = any(col[1] == "tenant_id" for col in cols)
                idcol = "id" if any(col[1] == "id" for col in cols) else "rowid"
                try:
                    rows = c.execute(f"SELECT {idcol} AS _id, {'tenant_id,' if has_tid else ''}"
                                     f"{','.join(txt_cols)} FROM {tbl}").fetchall()
                except Exception:
                    continue
                for row in rows:
                    d = dict(row)
                    rtid = d.get("tenant_id", "")
                    if tid and rtid and rtid != tid:
                        continue
                    for col in txt_cols:
                        v = d.get(col)
                        if isinstance(v, str) and pat.search(v):
                            hits.append({"table": tbl, "column": col, "rowid": d.get("_id"),
                                         "tenant": (rtid or "")[:12], "asset_id": d.get("asset_id", ""),
                                         "snippet": _r.sub(r"\s+", " ", v)[:160]})
                            if len(hits) >= limit:
                                break
                    if len(hits) >= limit:
                        break
    except Exception as e:
        return JSONResponse({"ok": False, "error": repr(e)[:200]}, status_code=500)
    # 테이블.컬럼별 집계
    from collections import Counter
    by = Counter(f"{h['table']}.{h['column']}" for h in hits)
    return JSONResponse({"ok": True, "token": token, "total": len(hits),
                         "by_table_column": dict(by), "hits": hits})


@app.get("/admin/geo-topics")
def admin_geo_topics(industry: str = "", biz: str = "local", region: str = "", desc: str = ""):
    """(진단) 업종 스키마 유래 트랙 B 질문형 주제 도출 + 키워드 관문 통과 결과 — 사진 불필요.
    V1의 '4업종 주제 도출·업종 어휘 하드코딩 0' 검증용."""
    from app.services import geo_track as _geo, indschema as _isc, searchad as _sa
    sch = _isc.get_schema(industry, biz)
    topics = _geo.info_topics(industry, biz, sch, region=region, desc=desc)
    _sa_ok = _sa.configured()
    out = []
    for tp in topics:
        kw = _geo.select_info_keyword([tp["topic"]], region, industry, verify_volume=True)
        vol = None                                       # 검색량 실측(관문 경유값)
        if _sa_ok:
            try:
                vv = {(_v.get("keyword") or "").replace(" ", ""): _v.get("total", 0)
                      for _v in _sa.keyword_volumes([tp["topic"], kw], limit=20)}
                vol = vv.get((kw or "").replace(" ", ""), vv.get((tp["topic"] or "").replace(" ", ""), 0))
            except Exception:
                vol = None
        out.append({"topic": tp["topic"], "angle": tp["angle"], "gated_keyword": kw,
                    "measured_volume": vol, "searchad": _sa_ok})
    return JSONResponse({"ok": bool(topics), "industry": industry, "biz_type": biz,
                         "schema_axes": [a.get("axis") for a in (sch.get("attribute_axes") or [])],
                         "content_angles": sch.get("content_angles"),
                         "honesty_hooks": sch.get("honesty_hooks"), "topics": out})


@app.get("/admin/geo-gen")
def admin_geo_gen(tid: str = "", industry: str = "", biz: str = "local", region: str = "",
                  topic: str = "", nocache: str = "", exp_q: str = "", exp_a: str = ""):
    """(진단) 트랙 B 정보성 글 1건 생성 + GEO 게이트(G1~G6) — 신규업종·실경험 검증용.
    tid 지정 시 실가게(DB 실경험 사용), 미지정 시 industry/biz/region 합성 테넌트.
    exp_q/exp_a 주면 테스트 실경험 Q&A 주입(합성 테넌트용). 실경험 0이면 트랙 B 보류(안내, 에러 아님).
    ※ 제품 업종-적응 코드는 이 진단이 호출만 하며 불변."""
    from app.services import geo_track as _geo, indschema as _isc, generate as _gen
    from app.domain.models import AssetType as _AT, ContentKind as _CK, Tenant as _Tenant, Asset as _Asset
    t = db.get_tenant(tid) if tid else None
    if not t:
        if not industry:
            return JSONResponse({"ok": False, "error": "tid 또는 industry 필요"}, status_code=400)
        t = _Tenant(id=f"diag_{abs(hash(industry)) % 10**8}", name=f"진단_{industry}",
                    industry=industry, region=region, biz_type=biz)   # 합성(신규 업종 임의 테스트)
    biz = getattr(t, "biz_type", "local") or "local"
    # 실경험 원료: 실가게=DB, 합성=exp_q/exp_a 파라미터
    if tid:
        _exps = db.list_owner_experience(t.id)
    else:
        _exps = [{"id": 1, "question": exp_q, "answer": exp_a}] if (exp_a and len(exp_a.strip()) >= 50) else []
    if not _exps:
        return JSONResponse({"ok": True, "held": True, "reason": "no_experience",
                             "notice": "경험 답변을 등록하면 정보성 글이 시작됩니다",
                             "industry": t.industry, "biz_type": biz})   # 보류(에러 아님)
    sch = _isc.get_schema(t.industry, biz)
    if not topic:
        tps = _geo.info_topics(t.industry, biz, sch, region=t.region or "",
                               desc=(getattr(t, "topic_axis", "") or ""), experiences=_exps)
        if not tps:
            return JSONResponse({"ok": False, "error": "주제 도출 실패(LLM/스키마)"}, status_code=500)
        topic, angle = tps[0]["topic"], tps[0]["angle"]
    else:
        angle = "howto"
    kw = _geo.select_info_keyword([topic], t.region or "", t.industry, tenant_id=t.id) or topic
    try:
        from app.services.autoqueue import photo_pool as _pp
        paths = _pp(t)[:4]
    except Exception:
        paths = []
    note = f"[자동 글감·트랙B] {topic}"
    if paths:
        try:
            from app import vision                   # 이 모듈은 vision을 전역으로 들이지 않는다
            analysis = vision.analyze_all(paths, t.industry)
            if analysis:
                note += f"\n[사진 분석] {analysis[:1200]}"
        except Exception:
            pass
    asset = _Asset(id=f"diag_{abs(hash(topic)) % 10**8}", tenant_id=t.id, type=_AT.IMAGE,
                   path=(paths[0] if paths else ""), note=note)
    asset.target_kw = kw
    asset.angle = angle
    asset.content_type = "info"
    asset.owner_experience = _exps                       # 실경험 주입(생성기가 프롬프트·payload에 반영)
    try:
        from app.registry import get_generator as _gg
        p = _gg(_CK.BLOG).generate(t, asset, paths or [])   # 직접 호출 — 예외 표면화(진단)
    except Exception:
        import traceback as _tb
        return JSONResponse({"ok": False, "error": "생성 예외", "trace": _tb.format_exc()[-1200:]}, status_code=500)
    if not p:
        return JSONResponse({"ok": False, "error": "생성 실패(빈 결과)"}, status_code=500)
    gate = _geo.geo_gate(p.payload)
    if not gate["passed"]:
        try:
            from app.services.revise import revise_piece as _rev
            _rev(p, _geo.regen_instruction(gate["fails"]))
            gate = _geo.geo_gate(p.payload)
        except Exception:
            pass
    # 태그 정합 게이트(제품 파이프라인 그대로) — 최종 태그 + 게이트 제거 로그
    try:
        _final_tags = _blog_tags(t, p)                   # _blog_tags 내부에서 tag_consistency_gate 적용
    except Exception:
        _final_tags = p.payload.get("tags")
    _tg = {}
    try:
        _sch_t = _isc.get_schema(t.industry, biz)
        _av = _isc.attribute_tokens(_sch_t)
        _ctxv = [a for a in _av if a and a in (kw or "")]
        _kept, _dropped = _isc.tag_consistency_gate(list(p.payload.get("tags") or []), _sch_t, _ctxv,
                                                    p.payload.get("body") or "", region=t.region or "",
                                                    general_tags=_sch_t.get("general_tags"))
        _tg = {"kept": _kept, "dropped": _dropped}
    except Exception:
        pass
    return JSONResponse({"ok": True, "tenant": t.name, "industry": t.industry, "biz_type": biz,
                         "topic": topic, "keyword": kw, "angle": angle,
                         "title": p.payload.get("title"), "body": p.payload.get("body"),
                         "geo_gate": gate, "tags": _final_tags, "tag_gate": _tg,
                         "owner_experience_used": [{"q": e.get("question"), "a": e.get("answer")} for e in _exps],
                         "content_type": p.payload.get("content_type")})


def _kfont(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
              "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
              "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
              "/System/Library/Fonts/AppleSDGothicNeo.ttc"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    from PIL import ImageFont as _IF
    return _IF.load_default()


@app.get("/me/review-card.png")
def review_card(request: Request):
    """카운터 비치용 리뷰 요청 카드(이미지). 방문자 리뷰 유도."""
    u = auth.current_user(request)
    if not u:
        return HTMLResponse(status_code=403)
    t = _ensure_user_tenant(u)
    from PIL import Image, ImageDraw
    import io

    W = H = 1080
    img = Image.new("RGB", (W, H), (99, 102, 241))
    top = Image.new("RGB", (W, H), (236, 72, 153))
    mask = Image.new("L", (W, H))
    md = ImageDraw.Draw(mask)
    for y in range(H):
        md.line([(0, y), (W, y)], fill=int(255 * y / H))
    img.paste(top, (0, 0), mask)
    d = ImageDraw.Draw(img)

    def center(text, y, font, fill="white"):
        w = d.textbbox((0, 0), text, font=font)[2]
        d.text(((W - w) / 2, y), text, font=font, fill=fill)
    center("⭐⭐⭐⭐⭐", 150, _kfont(90))
    center("리뷰 남겨주세요", 300, _kfont(84))
    # 흰 박스
    d.rounded_rectangle([120, 470, W - 120, 780], radius=32, fill="white")
    center(esc(t.name) if False else t.name, 520, _kfont(60), fill=(30, 30, 40))
    center("네이버에서 검색 후", 630, _kfont(44), fill=(90, 90, 100))
    center(f"‘{t.name}’ 방문자 리뷰 ✍️", 700, _kfont(48), fill=(99, 102, 241))
    center("여러분의 한 줄 후기가 큰 힘이 됩니다 🙏", 850, _kfont(40))
    center("made by 올린다", 1000, _kfont(28), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@app.post("/me/link")
def my_link_create(request: Request, target: str = Form(""), label: str = Form("")):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    if target.strip():
        db.create_link(t.id, target.strip(), label.strip())
    return RedirectResponse("/me?ok=추적 링크를 만들었어요", status_code=303)


@app.get("/me/sets/count")
def my_sets_count(request: Request):
    """생성 중 폴링용 — 세트 개수(늘어나면 완료) + 최신 세트ID(결과 화면 이동용)."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"n": 0, "latest": ""})
    t = _ensure_user_tenant(u)
    sets = db.list_sets(tenant_id=t.id)
    return JSONResponse({"n": len(sets), "latest": (sets[0]["asset_id"] if sets else "")})


@app.get("/me/asset/{asset_id}/pieces")
def my_asset_pieces(request: Request, asset_id: str):
    """결과 화면 폴링용 — 이 세트의 채널(피스) 개수. 영상 완성되면 늘어남."""
    u = auth.current_user(request)
    if not u:
        return JSONResponse({"n": 0})
    pieces = _owned_pieces(u, asset_id)
    # 상태 서명 — 피스 수가 안 변해도(채널 재생성·상태 전이) 변화를 폴링이 감지하게(5채널 완전성 3-2)
    cs = next((p.payload.get("channel_status") for p in (pieces or [])
               if p.kind.value == "blog" and p.payload.get("channel_status")), {}) or {}
    sig = f"{len(pieces or [])}|" + ",".join(f"{k}:{(v or {}).get('status')}" for k, v in sorted(cs.items()))
    return JSONResponse({"n": len(pieces) if pieces else 0, "sig": sig})


@app.post("/me/set/{asset_id}/photos")
async def my_set_add_photos(request: Request, asset_id: str, photos: list[UploadFile] = File(...)):
    """(사진 제한 해제 4-1) 기존 세트에 사진 추가 — 새 세트가 아니라 같은 세트에 누적.
    추가분도 vision 분석(배치) 경유 → 슬롯 재배치(마커만, 글 텍스트 불변) + 캡션·파일명 자동 +
    영상 3종 재생성 예약. 정직성: 새 사진 캡션은 vision 확정 기반만(날조 0)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    pieces = _owned_pieces(u, asset_id)
    if not pieces:
        return HTMLResponse(_subscriber_page("접근 불가", "<p>내 콘텐츠가 아니에요.</p>"))
    tenant = db.get_tenant(pieces[0].tenant_id)
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    cur = max((p.payload.get("image_paths") or [] for p in pieces), key=len)
    files = await _read_image_uploads(photos, limit=max(0, 30 - len(cur)))
    if not (tenant and blog and files):
        return RedirectResponse(f"/kit/{asset_id}/naver?err=추가할 사진이 없어요(세트당 최대 30장)", status_code=303)
    new_paths = [storage.save_upload(d, fn or "photo.jpg", tenant.id) for d, fn in files]
    try:
        from app.media import photo_boost
        photo_boost.enhance_all(new_paths, tenant.industry, {"artist": tenant.name})
        for _p in new_paths:
            storage.mirror_to_r2(_p)
    except Exception:
        pass
    # vision 분석(새 사진만, 번호는 기존에 이어붙임 — gen_source 사실 채널 갱신)
    try:
        from app import vision as _vz
        import re as _re4
        ana = _vz.analyze_all(new_paths, tenant.industry) or ""
        ana = _re4.sub(r"\[사진(\d+)\]", lambda m: f"[사진{int(m.group(1)) + len(cur)}]", ana)
    except Exception:
        ana = ""
    all_paths = cur + new_paths
    from app.generators.text_claude import SLOT_RECOMMENDED, _ensure_photo_markers
    for p in pieces:
        if p.payload.get("image_paths") is not None or p.kind.value in ("blog", "caption"):
            p.payload["image_paths"] = all_paths
        if p.kind.value == "blog":
            if ana:
                p.payload["gen_source"] = ((p.payload.get("gen_source") or "") + "\n" + ana)[:8000]
            # 슬롯 재배치 — 글 텍스트 불변, [사진N] 마커만 재배치(권장 상단 내)
            p.payload["body"] = _ensure_photo_markers(p.payload.get("body") or "", min(len(all_paths), SLOT_RECOMMENDED))
            p.payload["photo_markers"] = [{"marker": f"[사진{i+1}]", "image_index": i, "image_path": pp}
                                          for i, pp in enumerate(all_paths[:SLOT_RECOMMENDED])]
        db.save_piece(p)
    # 영상 재생성(온디맨드 존중): 이전에 영상을 만든 세트만 — 만들었던 플랫폼 그대로 재생성.
    # 영상 미요청 세트는 SHORT도 없고 재생성도 없음(사용자가 원할 때 홈/키트에서 요청).
    from app.domain.models import ContentKind as _CK4
    _had_short = any(p.kind == _CK4.SHORT for p in pieces)
    _vmsg = ""
    if _had_short:
        _cs_prev = blog.payload.get("channel_status") or {}
        _want_prev = {ch for ch in ("shorts", "reels", "naver")
                      if (_cs_prev.get(ch) or {}).get("status") in ("done", "generating", "registered", "failed")} \
            or {"shorts", "reels", "naver"}
        for p in pieces:
            if p.kind == _CK4.SHORT:
                db.delete_piece(p.id, p.tenant_id)
        try:
            from app.services.ingest import _set_video_job, _spawn_video_bundle
            _set_video_job(asset_id, "registered", retried=False)
            asset = db.get_asset(asset_id)
            if asset:
                _spawn_video_bundle(tenant, asset, all_paths, blog.payload.get("brief") or {},
                                    want=frozenset(_want_prev))
            _vmsg = "·영상 재생성 중"
        except Exception:
            import logging
            logging.exception("[add-photos] 영상 재생성 예약 실패 asset=%s", asset_id)
    return RedirectResponse(f"/kit/{asset_id}/naver?ok=사진 {len(new_paths)}장 추가 — 슬롯 재배치{_vmsg}", status_code=303)


@app.post("/me/set/{asset_id}/delete")
def my_set_delete(request: Request, asset_id: str):
    """콘텐츠 세트 삭제(이력 관리) — 본인 것만."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    db.delete_set(asset_id, t.id)
    from urllib.parse import quote as _q
    return RedirectResponse("/me?tab=content&ok=" + _q("콘텐츠를 삭제했어요"), status_code=303)


_BOT_UA_RE = __import__("re").compile(
    r"(bot|crawl|spider|slurp|bingpreview|facebookexternalhit|facebot|"
    r"naverbot|yeti|googlebot|daumoa|kakaotalk-scrap|telegrambot|twitterbot|"
    r"whatsapp|discordbot|pinterest|semrush|ahrefs|mj12bot|dotbot|python-requests|"
    r"curl/|wget|headless|preview|monitor)", __import__("re").I)


def _is_bot_ua(ua: str) -> bool:
    """봇/크롤러/링크 미리보기 UA 판별 — 사람 클릭 집계에서 분리."""
    return bool(_BOT_UA_RE.search(ua or "")) or not (ua or "").strip()


@app.get("/r/{code}")
def link_redirect(code: str, request: Request, utm_source: str = "", src: str = "",
                  content: str = "", set: str = ""):
    """제휴/추적 단축링크 — 클릭 집계(행: 채널·콘텐츠·세트·시각·리퍼러·UA·봇여부) 후 원본으로 이동.
    src={channel}&content={piece 앞8자}&set={asset 앞8자} — 콘텐츠·세트별 유입 실측. utm_source 하위호환.
    봇/크롤러 UA는 별도 플래그로 기록하되 사람 클릭 집계엔 미포함(사장 언어 지표 정확도)."""
    link = db.get_link(code)
    if not link or not link.get("target"):
        return RedirectResponse("/", status_code=302)
    # 익명 방문자 특성(방문자 B1) — 신원 파악 금지: 익명 쿠키(방문 구분)·기기·국가(CF 헤더)까지만
    ua = request.headers.get("user-agent", "")
    _bot = _is_bot_ua(ua)
    device = "mobile" if ("Mobi" in ua or "Android" in ua) else "pc"
    region = (request.headers.get("cf-ipcountry") or "").strip()[:8]   # 국가 단위(도시 아님 — 정직)
    vid = (request.cookies.get("ovid") or "").strip()[:32]
    new_cookie = not vid and not _bot                  # 봇엔 쿠키 미발급
    if not vid:
        vid = uuid.uuid4().hex[:16]
    db.incr_link_click(code, referrer=request.headers.get("referer", ""),
                       ua=ua, utm_source=utm_source,
                       content_id=content, channel=(src or utm_source),
                       visitor_id=(vid if not _bot else ""), device=device, region=region,
                       is_bot=_bot, set_id=set)
    target = link["target"]
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    resp = RedirectResponse(target, status_code=302)
    if new_cookie:      # 익명 방문 구분용 — 쿠키를 지우면 추적되지 않음(리포트에 명시)
        resp.set_cookie("ovid", vid, max_age=31536000, samesite="lax")
    return resp


@app.get("/me/connect/{channel}/start")
def my_connect_start(request: Request, channel: str):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    try:
        ch = Channel(channel)
    except ValueError:
        return RedirectResponse("/me?err=지원하지 않는 채널", status_code=303)
    if not oauth.configured(ch):
        return RedirectResponse("/me?err=아직 준비 중(앱 심사) 채널입니다", status_code=303)
    return RedirectResponse(oauth.authorize_url(ch, t.id))


def _owned_pieces(user, asset_id):
    """세트가 로그인 유저 소유인지 확인 후 pieces 반환(아니면 None)."""
    pieces = db.get_set_pieces(asset_id)
    if not pieces:
        return None
    ut = (user or {}).get("tenant_id")
    return pieces if (ut and ut == pieces[0].tenant_id) else None


def _kit_card(title, inner):
    return (f"<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-3'>"
            f"<div class='font-bold mb-2'>{title}</div>{inner}</div>")


def _score_why(audit: dict) -> str:
    """점수 사유(읽기 전용) — 감점 경고 상위 2개를 사장님 말로(내부 용어 금지)."""
    _MAP = [("날조", "글 속 숫자가 입력 정보에 없어요 — 확인해 주세요"),
            ("도배", "같은 키워드가 너무 자주 반복돼요"),
            ("불일치", "제목이 약속한 내용이 본문에 부족해요"),
            ("빈약", "글 분량·정보가 조금 부족해요"),
            ("이미지", "사진이 더 있으면 좋아요"),
            ("경쟁", "검색 경쟁이 센 키워드예요"),
            ("전국", "검색 경쟁이 센 키워드예요"),
            ("과장", "표현이 규정상 과할 수 있어요"),
            ("링크", "구매·방문 링크가 없어요")]
    out = []
    for w in (audit.get("warnings") or []):
        for k, msg in _MAP:
            if k in str(w) and msg not in out:
                out.append(msg)
                break
        if len(out) >= 2:
            break
    return " · ".join(out)


def _result_naver_video(pieces, asset_id: str) -> str:
    """(결과 화면 1-1·1-2) 네이버 블로그 카드에 네이버용 영상 인라인 미리보기 — 키트 플레이어와 동일.
    생성 중이면 상태 문구(기존 pieces 폴링이 완성 시 화면을 갱신 → 자동 교체), 실패·부재면 생략."""
    try:
        from app.domain.models import ContentKind as _CKr
        short = next((p for p in pieces if p.kind == _CKr.SHORT and (p.payload or {}).get("naver_video")), None)
        nv = (short.payload.get("naver_video") or {}) if short else {}
        if nv:                                            # PHASE 1: 영상 메타 canonical(제목·해시태그 오염 제거)
            _bl = next((p for p in pieces if p.kind == _CKr.BLOG), None)
            _tn = db.get_tenant(pieces[0].tenant_id) if pieces else None
            if _bl and _tn:
                nv = _nv_canonical(_tn, _bl, nv)
        src_p = nv.get("path") or ""
        if src_p:
            _dl = f"/dl/{asset_id}/{os.path.basename(src_p)}"
            _slug_v = _set_slug(pieces)                   # 단일 소스 슬러그(별도 filename 경로 삭제)
            _fn = esc((_slug_v + "_네이버영상.mp4") if _slug_v else (nv.get("filename") or "naver-video.mp4"))
            _dur = int(nv.get("duration_sec") or 0)
            # 파일 재생 가능 여부(로컬 또는 R2) — 소실 시 죽은 버튼 대신 네이버 페이지(다시 만들기)로 유도
            _ok = os.path.exists(src_p)
            if not _ok:
                try:
                    from app import storage as _st
                    _ok = bool(_st.r2_media_url(short.tenant_id, os.path.basename(src_p)))
                except Exception:
                    _ok = False
            _action = (
                f"<a href='{_dl}' download='{_fn}' class='mt-2 flex items-center justify-center gap-1 px-4 py-2.5 "
                f"bg-emerald-500 hover:bg-emerald-600 active:scale-[.98] text-white text-sm font-bold rounded-xl transition'>"
                f"⬇ 영상 받기 (본문·클립 겸용 9:16)</a>"
                + (f"<div class='text-[11px] text-slate-400 text-center mt-1'>파일명: {_fn} · 약 {_dur}초</div>" if _dur else "")
            ) if _ok else (
                f"<a href='/kit/{asset_id}/naver' class='mt-2 flex items-center justify-center gap-1 px-4 py-2.5 "
                f"bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-bold rounded-xl transition'>🎬 네이버 영상 받기·다시 만들기</a>")
            _player = (f"<div class='mx-auto bg-black rounded-xl overflow-hidden' style='max-width:280px;aspect-ratio:9/16'>"
                       f"<video src='{_dl}' controls preload='none' class='w-full h-full' style='object-fit:contain'></video></div>"
                       if _ok else "")
            # 📱 클립 버튼은 '본편이 있을 때' 나와야 한다(2026-08-01 사장님 지적).
            #   클립은 본편에서 잘라내는 파생물인데, 기존엔 본편이 없을 때만 버튼을 그려
            #   영상이 만들어지는 순간 버튼이 사라졌다(정반대).
            _clip = nv.get("clip") or {}
            _cp = _clip.get("path") or ""
            if _cp and (os.path.exists(_cp) or True):
                _cdl = f"/dl/{asset_id}/{os.path.basename(_cp)}"
                _cfn = esc((_slug_v + "_클립.mp4") if _slug_v else "naver-clip.mp4")
                _clip_block = (
                    "<div class='mt-3 pt-3 border-t border-slate-100'>"
                    "<div class='text-xs font-bold text-slate-400 mb-1'>네이버 클립용 (짧은 훅형)</div>"
                    f"<a href='{_cdl}' download='{_cfn}' class='flex items-center justify-center gap-1 "
                    "px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold rounded-xl transition'>"
                    f"⬇ 클립 받기 (약 {int(_clip.get('duration_sec') or 0)}초)</a></div>")
            else:
                _clip_block = (
                    "<div class='mt-3 pt-3 border-t border-slate-100'>"
                    "<div class='text-xs font-bold text-slate-400 mb-1'>네이버 클립용 (짧은 훅형)</div>"
                    "<button type='button' class='w-full px-4 py-2.5 rounded-xl bg-indigo-600 "
                    "hover:bg-indigo-700 text-white text-sm font-bold transition' "
                    "onclick=\"vmPick(this,'" + esc(asset_id) + "','clip')\">"
                    "📱 네이버 클립 만들기</button>"
                    "<div class='text-[11px] text-slate-400 text-center mt-1'>"
                    "클립 지면에 업로드 · 이 영상에서 15~22초로 잘라내요</div></div>")
            return (f"<div class='mt-3'><div class='text-xs font-bold text-slate-400 mb-1'>네이버용 영상 (본문 첨부 · 9:16)</div>"
                    + _player + _action + _clip_block + _VMPICK_JS + "</div>")
        blog = next((p for p in pieces if p.kind == _CKr.BLOG), None)
        vj = (blog.payload.get("video_job") or {}) if blog else {}
        if vj.get("status") in ("registered", "running", "retrying"):   # 진행 중 — 단계 문구 + 폴링(완성 시 자동 갱신)
            _stg = esc(vj.get("stage") or "영상 만드는 중이에요 (몇 분 걸려요)")
            return ("<div class='mt-3'><div class='flex items-center gap-2 text-sm text-slate-600'>"
                    "<span class='inline-block w-4 h-4 border-2 border-slate-300 border-t-indigo-500 rounded-full animate-spin flex-shrink-0'></span>"
                    f"🎬 <b>네이버 영상 만드는 중</b> — <span id='nvVjStage'>{_stg}</span></div>"
                    "<div class='text-xs text-slate-400 mt-1'>완성되면 이 자리에 자동으로 나타나요 (화면 안 닫아도 돼요)</div>"
                    f"<script>(function(){{var n=0;var iv=setInterval(async function(){{n++;if(n>240){{clearInterval(iv);return;}}"
                    f"try{{var d=await (await fetch('/me/video/status?asset_id={esc(asset_id)}')).json();"
                    "var j=(d&&d.job)||{};var el=document.getElementById('nvVjStage');"
                    "if(el&&j.stage)el.textContent=j.stage;"
                    "if(j.status==='done'||j.status==='failed'){clearInterval(iv);location.reload();}"
                    "}catch(_){}},5000);})();</script></div>")
        _cs_nv = (((blog.payload.get("channel_status") or {}).get("naver") or {}).get("status") or "") if blog else ""
        if vj.get("status") == "failed" or _cs_nv == "failed":          # 실패 — 조용히 사라지지 않기(버그 실측): 사유+재시도
            _errraw = (vj.get("error") or "")
            _credit = ("credit" in _errraw.lower() and "too low" in _errraw.lower())
            return ("<div class='mt-3'><div class='text-sm text-slate-600 mb-1'>"
                    + ("💳 AI 사용량(크레딧)이 소진돼 지금은 만들 수 없어요" if _credit
                       else "😢 영상을 만들지 못했어요 — 다시 시도할 수 있어요") + "</div>"
                    + (("<div class='text-[11px] text-amber-700 bg-amber-50 rounded-lg px-2 py-1.5 mb-2'>"
                        "운영자에게 충전을 요청해 주세요. 충전되면 바로 다시 만들 수 있어요.</div>") if _credit
                       else (f"<div class='text-[11px] text-slate-400 mb-2'>{esc(_errraw[:80])}</div>" if _errraw else ""))
                    + "<button type='button' class='w-full px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-900 "
                    "text-white text-sm font-bold transition' "
                    "onclick=\"vmPick(this,'" + esc(asset_id) + "','naver')\">"
                    "🎬 네이버 영상 다시 만들기</button>" + _VMPICK_JS + "</div>")
        # 영상 온디맨드 — 네이버 영상은 블로그 카드 안이 자리(별도 채널 카드 없음) → 여기서 바로 생성 버튼
        #   ★ 판정 기준을 '플래그'가 아니라 '사실'로 바꾼다(2026-08-01 실사고: 어떤 세트는 버튼이
        #     나오고 어떤 세트는 안 나옴). 위에서 ①영상 있음 ②만드는 중 ③실패를 모두 걸러냈으니,
        #     여기 온 세트는 '영상이 없고 아무것도 안 돌고 있는' 상태다 — 언제나 만들 수 있어야 한다.
        #     기존 조건(=="not_requested")은 channel_status가 비었거나 naver 키가 없는 세트
        #     (품질 루프에 막혀 상태 기록이 안 됐거나, 이 기능 이전에 만들어진 옛 세트)를
        #     통째로 떨어뜨려 버튼이 사라졌다. 전 업종·전 플랜 공통.
        # 🎬 네이버는 올릴 자리가 두 곳이고 영상 성격도 다르다(2026-08-01 사장님 지시) →
        #   버튼도 요청도 따로. 블로그 첨부용(정보형 30~40초) / 클립 지면용(훅형 15~22초).
        _nvc = (((blog.payload.get("naver_video") or {}) if blog else {}).get("clip") or {})
        _btn = ("w-full px-4 py-2.5 rounded-xl text-white text-sm font-bold transition")
        return ("<div class='mt-3'><div class='text-xs font-bold text-slate-400 mb-1'>네이버용 영상</div>"
                "<div class='text-xs text-slate-500 mb-2'>필요한 것만 만들어요 — 글은 이미 완성!</div>"
                "<button type='button' class='" + _btn + " bg-slate-800 hover:bg-slate-900' "
                "onclick=\"vmPick(this,'" + esc(asset_id) + "','naver')\">"
                "🎬 블로그에 넣을 영상 만들기</button>"
                "<div class='text-[11px] text-slate-400 text-center mt-1'>글 본문에 첨부 · 30~40초 정보형</div>"
                + ("<div class='mt-2 text-[11px] text-emerald-600 text-center'>✅ 클립 만들어짐</div>"
                   if _nvc.get("path") else
                   ("<button type='button' class='" + _btn + " bg-indigo-600 hover:bg-indigo-700 mt-2' "
                    "onclick=\"vmPick(this,'" + esc(asset_id) + "','clip')\">"
                    "📱 네이버 클립 만들기</button>"
                    "<div class='text-[11px] text-slate-400 text-center mt-1'>클립 지면에 업로드 · 15~22초 훅형</div>"))
                + _VMPICK_JS + "</div>")
    except Exception:
        return ""


def _rewrite_running(pl: dict) -> bool:
    """다시쓰기 진행 판정 + 죽은 잡 자동 해제(2026-07-31 실사고: 배포 재시작이 스레드를 죽여
    'running'이 영영 남음 → 배너 고착·재시도 불가). 10분 넘은 running은 죽은 것으로 본다."""
    rj = (pl or {}).get("rewrite_job") or {}
    if rj.get("status") != "running":
        return False
    try:
        from datetime import datetime as _d
        return (_d.utcnow() - _d.fromisoformat(rj.get("ts", ""))).total_seconds() < 600
    except Exception:
        return False                                   # ts 불명 = 구식 기록 → 죽은 것으로


# 🎬 영상 만들기 직전 '대표 사진 고르기' 모달(2026-07-31, 사장님 지시) — 구세트 포함 모든 진입점 공용.
#   사진 로드 실패·0장이면 기존 동작(바로 생성)으로 조용히 폴백. 선택 안 하면 hero='auto'(AI 자동).
_VMPICK_JS = ("<script>if(!window.vmMake){"
              "window.vmMake=async function(b,a,p,h,ph){if(b)b.disabled=true;var fd=new FormData();"
              "fd.append('asset_id',a);fd.append('platforms',p);if(h)fd.append('hero',h);"
              "if(ph)fd.append('photos',ph);"
              "try{var d=await (await fetch('/me/video/make',{method:'POST',body:fd})).json();"
              "if(d.ok){location.reload();}else{alert(d.error||'요청에 실패했어요');if(b)b.disabled=false;}}"
              "catch(e){alert('요청에 실패했어요');if(b)b.disabled=false;}};"
              "window.vmPick=async function(b,a,p){var d=null;"
              "try{d=await (await fetch('/me/video/photos?asset_id='+encodeURIComponent(a))).json();}catch(e){}"
              "if(!d||!d.ok||!(d.photos||[]).length){window.vmMake(b,a,p,'','');return;}"
              "var hero=d.hero||'';"
              "var CAP=d.max_photos||9;"                                       # 영상 사진 상한(서버 단일 소스)
              "var inc={};d.photos.forEach(function(ph,i){inc[ph.name]=(i<CAP);});"  # 기본: 상한까지만
              "if((d.video_photos||[]).length){d.photos.forEach(function(ph){inc[ph.name]=false;});"
              "d.video_photos.slice(0,CAP).forEach(function(n){inc[n]=true;});}"     # 이전 선택 복원(상한 내)
              "var ov=document.createElement('div');"
              "ov.style.cssText='position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';"
              "var box=document.createElement('div');"
              "box.style.cssText='background:#fff;border-radius:20px;max-width:420px;width:100%;max-height:80vh;overflow:auto;padding:20px';"
              "function n_sel(){return d.photos.filter(function(ph){return inc[ph.name];}).length;}"
              "function render(){var g='';d.photos.forEach(function(ph){var IN=!!inc[ph.name];var H=(hero===ph.name)&&IN;"
              "g+='<div data-n=\"'+ph.name+'\" style=\"position:relative;cursor:pointer;border-radius:12px;overflow:hidden;border:3px solid '+(H?'#f59e0b':(IN?'#4f46e5':'transparent'))+';opacity:'+(IN?'1':'.35')+'\">'"
              "+'<img src=\"'+ph.url+'\" style=\"width:100%;aspect-ratio:1;object-fit:cover;display:block\">'"
              "+(IN?'':'<div style=\"position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(15,23,42,.35);color:#fff;font-size:12px;font-weight:700\">제외됨</div>')"
              "+(H?'<div style=\"position:absolute;top:6px;left:6px;background:#f59e0b;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px\">\\u2605 대표</div>':'')"
              "+(IN?'<button data-star=\"'+ph.name+'\" style=\"position:absolute;bottom:6px;left:6px;width:26px;height:26px;border-radius:999px;border:0;cursor:pointer;background:'+(H?'#f59e0b':'rgba(15,23,42,.45)')+';color:#fff;font-size:13px\">\\u2605</button>':'')"
              "+'</div>';});"
              "box.innerHTML='<div style=\"font-weight:800;font-size:16px;color:#0f172a;margin-bottom:4px\">영상에 쓸 사진 고르기</div>'"
              "+'<div style=\"font-size:12px;color:#94a3b8;margin-bottom:12px\">사진을 누르면 <b>넣고/빼고</b>, \\u2605를 누르면 <b>첫 장면 대표</b>가 돼요. </div>'"
              "+'<div style=\"font-size:12px;color:#4f46e5;background:#eef2ff;border-radius:8px;padding:7px 10px;margin-bottom:12px\">영상에는 <b>최대 '+CAP+'장</b>까지 써요 — 더 넣으면 제작이 오래 걸려요.'+(d.photos.length>CAP?(' 올린 '+d.photos.length+'장 중 '+CAP+'장을 골라주세요.'):'')+'</div>'"
              "+'<div style=\"display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px\">'+g+'</div>'"
              "+'<button id=\"vmGo\" style=\"width:100%;padding:13px;border-radius:12px;background:#4f46e5;color:#fff;font-weight:800;border:0;font-size:14px;cursor:pointer\">'+n_sel()+'장으로 영상 만들기'+(hero&&inc[hero]?' (\\u2605 대표 지정됨)':'')+'</button>'"
              "+'<button id=\"vmX\" style=\"width:100%;padding:10px;background:none;border:0;color:#94a3b8;font-size:12px;margin-top:4px;cursor:pointer\">취소</button>';"
              "box.querySelectorAll('[data-n]').forEach(function(el){el.onclick=function(ev){"
              "if(ev.target&&ev.target.getAttribute&&ev.target.getAttribute('data-star'))return;"
              "var n=el.getAttribute('data-n');"
              "if(inc[n]&&n_sel()<=1){alert('사진 1장은 있어야 영상을 만들 수 있어요');return;}"
              "if(!inc[n]&&n_sel()>=CAP){alert('영상에는 최대 '+CAP+'장까지 쓸 수 있어요. 다른 사진을 먼저 빼주세요.');return;}"
              "inc[n]=!inc[n];if(!inc[n]&&hero===n)hero='';render();};});"
              "box.querySelectorAll('[data-star]').forEach(function(el){el.onclick=function(ev){"
              "ev.stopPropagation();var n=el.getAttribute('data-star');hero=(hero===n)?'':n;render();};});"
              "box.querySelector('#vmGo').onclick=function(){var names=d.photos.filter(function(ph){return inc[ph.name];}).map(function(ph){return ph.name;});"
              "var all=(names.length===d.photos.length&&names.length<=CAP);ov.remove();"
              "window.vmMake(b,a,p,hero||'auto',all?'all':names.join(','));};"
              "box.querySelector('#vmX').onclick=function(){ov.remove();};}"
              "render();ov.appendChild(box);"
              "ov.onclick=function(e){if(e.target===ov)ov.remove();};document.body.appendChild(ov);};}"
              "</script>")


def _result_html(u, asset_id: str, back_href: str = "/me", back_label: str = "← 내 작업실"):
    """발행 소재 결과 HTML — 대시보드 인라인/독립 페이지 공용. 소유 아니면 None."""
    import re as _re
    pieces = _owned_pieces(u, asset_id)
    if pieces is None:
        return None

    def dl(path):
        return f"/dl/{asset_id}/{os.path.basename(path)}" if path else ""      # /dl이 R2로 리다이렉트

    def copy_block(cid, text, h="28"):
        return (f"<textarea id='{cid}' readonly class='w-full h-{h} border border-slate-200 rounded-xl p-2 text-sm bg-slate-50'>{esc(text)}</textarea>"
                f"<button type=button onclick=\"cp('{cid}',this)\" class='mt-1 px-3 py-1.5 bg-indigo-600 text-white text-xs font-bold rounded-lg'>복사</button>")

    imgs = (next((p.payload.get("image_paths") for p in pieces
                 if p.kind.value == "blog" and p.payload.get("image_paths")), None)
            or next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or [])
    # ★ 블로그 피스 우선(실측 버그): 다듬기 병렬화로 저장 순서가 뒤섞여 X 피스(발행용 4장 제한)가
    #   먼저 잡히면 그리드·ZIP·재정렬이 전부 4장으로 좁아짐 — 16장 중 4장만 다운로드된 원인.
    # ★ 결과 화면도 '글 흐름순' 정렬 — 네이버 페이지·ZIP과 단일 기준. 실측 버그: 여기만 원순서라
    #   결과 화면에서 본문 복사 + ZIP 다운로드 시 [사진N] 번호와 파일 번호가 어긋났음.
    try:
        _blog0 = next((p for p in pieces if p.kind.value == "blog"), None)
        if _blog0 and imgs:
            _tn0 = db.get_tenant(_blog0.tenant_id)
            _nb0, _od0, _ = _content_photo_layout(_tn0, _blog0)
            if _od0 and len(_od0) == len(imgs) and _od0 != list(range(len(imgs))):
                imgs = [imgs[i] for i in _od0]
                _blog0.payload["body"] = _nb0             # 재번호 마커 — 메모리 한정(미저장)
    except Exception:
        pass

    def pack_btn(pid, has_video):
        what = "글+사진+영상" if has_video else "글+사진"
        return (f"<a href='/kit/{asset_id}/pack/{pid}' class='flex-1 flex items-center justify-center gap-1 px-4 py-2.5 "
                f"bg-emerald-500 hover:bg-emerald-600 active:scale-[.98] text-white text-sm font-bold rounded-xl transition'>⬇ 이 채널 통째로 받기 ({what})</a>")

    def eb(pl):
        ex = pl.get("experts") or []
        return (f"<div class='text-[11px] text-indigo-400 font-semibold mb-2'>{' → '.join(ex)}</div>" if ex else "")

    tenant = db.get_tenant(pieces[0].tenant_id)
    sname = (tenant.name if tenant else "내 가게")
    handle = (_re.sub(r"[^a-zA-Z0-9]", "", sname) or "mystore").lower()[:15]
    first_img = next((f"/dl/{asset_id}/{os.path.basename(im)}" for im in imgs if im), "")
    wrap = "bg-white rounded-2xl border border-slate-200 shadow-sm hover:shadow-lg transition-shadow"

    def _av():
        return ("<div class='w-9 h-9 rounded-full bg-indigo-600 flex items-center justify-center text-white text-sm font-bold flex-shrink-0'>"
                f"{esc(sname[:1])}</div>")

    def _cp(cid, text, label):
        return (f"<textarea id='{cid}' class='hidden'>{esc(text)}</textarea>"
                f"<button type=button onclick=\"cp('{cid}',this)\" class='px-3.5 py-2.5 border border-slate-200 text-slate-600 hover:bg-slate-50 hover:border-slate-300 active:scale-[.98] text-xs font-bold rounded-xl transition'>📋 {label}</button>")

    def _blog_body(body):
        out = []
        for seg in _re.split(r"(\[사진\d+\])", body or ""):
            m = _re.fullmatch(r"\[사진(\d+)\]", seg or "")
            if m:
                i = int(m.group(1)) - 1
                if 0 <= i < len(imgs) and imgs[i]:
                    out.append(f"<img src='/dl/{asset_id}/{os.path.basename(imgs[i])}' class='my-3 rounded-xl w-full border border-slate-100'>")
            else:
                for ln in (seg or "").split("\n"):
                    s = ln.strip()
                    if s.startswith("#"):
                        out.append(f"<h3 class='font-bold text-base mt-4 mb-1 text-slate-900'>{esc(s.lstrip('# '))}</h3>")
                    elif s:
                        out.append(f"<p class='mb-2 leading-relaxed text-slate-700 text-sm'>{esc(s)}</p>")
        return "".join(out)

    def _blog_rich(title, body):
        """네이버에 '한 번에 붙여넣기'용 리치 HTML — 사진은 순서대로 base64 내장(외부링크 X)."""
        parts = [f"<h2 style='font-size:20px;font-weight:800;margin:0 0 14px'>{esc(title)}</h2>"]
        for seg in _re.split(r"(\[사진\d+\])", body or ""):
            m = _re.fullmatch(r"\[사진(\d+)\]", seg or "")
            if m:
                i = int(m.group(1)) - 1
                if 0 <= i < len(imgs) and imgs[i]:
                    uri = _img_thumb_data_uri(imgs[i], 900)      # 로컬 없으면 R2에서 가져옴
                    if uri:
                        parts.append(f"<img src='{uri}' style='max-width:100%;border-radius:8px;margin:14px 0'>")
            else:
                for ln in (seg or "").split("\n"):
                    s = ln.strip()
                    if s.startswith("#"):
                        parts.append(f"<h3 style='font-size:16px;font-weight:700;margin:18px 0 6px'>{esc(s.lstrip('# '))}</h3>")
                    elif s:
                        parts.append(f"<p style='margin:0 0 11px;line-height:1.75'>{esc(s)}</p>")
        return "".join(parts)

    def _hd(label, pl=None):
        badge = ""
        au = (pl or {}).get("ranking_audit") or {}
        sc = au.get("score")
        if sc:
            cls = ("bg-emerald-100 text-emerald-700" if sc >= 85 else
                   "bg-amber-100 text-amber-700" if sc >= 70 else "bg-slate-100 text-slate-600")
            badge = f"<span class='ml-2 text-[11px] font-bold px-2 py-0.5 rounded-full {cls}'>상위노출 {sc}점</span>"
            # ⚡ 품질 보정은 백그라운드(2026-08-01) — 글은 먼저 열리고 점수는 뒤에서 올라간다.
            #   끝나면 자동 새로고침해서 최종 점수를 보여준다(사장님이 새로고침할 필요 없음).
            if ((pl or {}).get("polish_job") or {}).get("status") == "running":
                badge += ("<span class='ml-1 text-[11px] font-bold text-indigo-500 bg-indigo-50 "
                          "px-2 py-0.5 rounded-full'>다듬는 중… 점수 더 올라가요</span>"
                          "<script>(function(){var n=0;var iv=setInterval(async function(){n++;"
                          "if(n>120){clearInterval(iv);return;}try{var d=await (await "
                          "fetch('/me/polish-status?asset_id=" + esc(asset_id) + "')).json();"
                          "if(d&&d.status==='done'){clearInterval(iv);location.reload();}}"
                          "catch(_){}},5000);})();</script>")
            why = _score_why(au)
            if why and sc < 100:
                badge += (f"<details class='inline-block ml-1 align-middle'><summary class='text-[11px] text-slate-400 cursor-pointer list-none'>왜?</summary>"
                          f"<span class='text-[11px] text-slate-500'>{esc(why)}</span></details>")
        ga = (pl or {}).get("geo_audit") or {}
        gs = ga.get("score")
        if gs is not None and gs > 0:                       # AI검색 준비 점수(GEO B2) — 병기
            gcls = ("bg-violet-100 text-violet-700" if gs >= 75 else "bg-slate-100 text-slate-600")
            badge += (f"<span class='ml-1.5 text-[11px] font-bold px-2 py-0.5 rounded-full {gcls}' "
                      f"title='AI 검색(ChatGPT 등)이 인용하기 유리한 구조 점수 — 인용을 보장하진 않아요'>AI검색 준비 {gs}점</span>")
        return f"<div class='text-xs font-bold text-slate-400 mb-2 flex items-center flex-wrap'>{label}{badge}</div>"
    naver_btn = (f"<a href='/kit/{asset_id}/naver' target='_blank' class='block text-center py-3 rounded-xl text-white text-sm font-extrabold "
                 "shadow-md hover:brightness-110 active:scale-[.99] transition' style='background:#03c75a'>네이버 블로그에 올리기 →</a>")
    cards = ""
    rendered_ch = set()          # 5채널 완전성 — 실제 카드가 그려진 채널(정합 감시·placeholder 판단)
    for p in pieces:
        k, pl = p.kind.value, p.payload
        has_video = bool(pl.get("video_path"))
        vurl = f"/dl/{asset_id}/{os.path.basename(pl.get('video_path',''))}" if has_video else ""  # /dl이 R2로 서빙
        block = ""
        if k == "caption":
            cap = pl.get("text", "")
            media = (f"<img src='{first_img}' class='w-full aspect-square object-cover'>" if first_img
                     else "<div class='w-full aspect-square bg-slate-100 flex items-center justify-center text-5xl text-slate-300'>📷</div>")
            block = (_hd("📷 인스타그램", pl) + f"<div class='{wrap} overflow-hidden'>"
                     "<div class='flex items-center gap-2 px-3.5 py-3'>" + _av()
                     + f"<div class='font-semibold text-sm'>{esc(sname)}</div><div class='ml-auto text-slate-400'>⋯</div></div>" + media
                     + "<div class='px-3.5 pt-3 flex items-center gap-4 text-2xl'><span>♡</span><span>💬</span><span>➤</span><span class='ml-auto'>🔖</span></div>"
                     + f"<div class='px-3.5 py-2 text-sm whitespace-pre-wrap leading-relaxed max-h-44 overflow-y-auto'><b>{esc(sname)}</b> {esc(cap)}</div>"
                     + f"<div class='px-3.5 pb-3.5 flex gap-2'>{pack_btn(p.id, has_video)}{_cp('c_cap', cap, '캡션')}</div></div>")
        elif k == "blog":
            if "?fromRss=" in (pl.get("body") or ""):     # 구세트 추적 파라미터 — 모든 렌더 경로 공통 정리(메모리 한정)
                pl["body"] = pl["body"].replace("?fromRss=true&trackingCode=rss", "")
            title = pl.get("selected_title") or pl.get("title", "")   # PHASE B: 선택 제목 우선
            sid = p.id[:5]
            body_part = _re.sub(r"\[사진(\d+)\]", r"⬇⬇ 여기에 사진\1 올리기 ⬇⬇", pl.get("body", "")).strip()
            body_part = body_part.replace("?fromRss=true&trackingCode=rss", "")   # 구세트 추적 파라미터 정리
            blog_copy = title + "\n\n" + body_part
            topts = [t for t in (pl.get("title_options") or []) if t]
            opts_html = ""
            if len(topts) >= 2:
                chips = "".join(f"<button type=button onclick=\"pickTitle('{sid}','{asset_id}',this)\" data-t=\"{esc(t)}\" "
                                "class='text-[11px] bg-slate-100 hover:bg-indigo-50 text-slate-600 px-2 py-1 rounded-lg mr-1 mb-1 text-left'>"
                                f"{esc(t[:26])}</button>" for t in topts)
                opts_html = (f"<div class='mb-2'><span class='text-[11px] text-slate-400'>제목 바꾸기 (검색 노출용 3안):</span>"
                             f"<div class='mt-1 flex flex-wrap'>{chips}</div>"
                             f"<div id='tsel{sid}' class='text-[11px] text-emerald-600 mt-0.5'>{'✅ 선택한 제목 (파일명·폴더 반영됨)' if pl.get('selected_title') else ''}</div></div>")
            # 사장님 이야기 하이라이트(A2) — '내 말이 글이 됐네' 실감. 본문에 원문이 보이면 그 사실까지 표기(정직)
            story = (pl.get("owner_story") or "").strip()
            story_html = ""
            if story:
                _in_body = story[:30] in (pl.get("body") or "")
                story_html = ("<div class='bg-violet-50 border border-violet-100 rounded-xl px-3.5 py-2.5 mb-3'>"
                              "<div class='text-[11px] font-bold text-violet-500 mb-0.5'>사장님 이야기가 글이 됐어요</div>"
                              f"<div class='text-sm text-violet-800'>“{esc(story)}”</div>"
                              + ("<div class='text-[11px] text-violet-400 mt-0.5'>이 문장이 본문에 그대로 들어갔어요</div>"
                                 if _in_body else
                                 "<div class='text-[11px] text-violet-400 mt-0.5'>이 이야기를 본문 표현에 녹였어요</div>")
                              + "</div>")
            block = (_hd("네이버 블로그", pl) + f"<div class='{wrap} p-5'>"
                     f"<div id='bt{sid}' class='text-lg font-extrabold text-slate-900 leading-snug mb-1.5'>{esc(title)}</div>"
                     + opts_html
                     + "<div class='flex items-center gap-2 text-xs text-slate-400 border-b border-slate-100 pb-2 mb-3'>" + _av()
                     + f"<span>{esc(sname)} 블로그 · 방금 전</span></div>"
                     + story_html
                     + f"<div class='max-h-72 overflow-y-auto'>{_blog_body(pl.get('body',''))}</div>"
                     + f"<textarea id='cb{sid}' data-body=\"{esc(body_part)}\" class='hidden'>{esc(blog_copy)}</textarea>"
                     + _result_naver_video(pieces, asset_id)
                     + "<div class='mt-4 space-y-2'>"
                     # 📮 발행 게이트: 80점 미달 글은 발행 버튼 봉인 — 재작성 버튼만(주방 철학: 미달 글 비노출)
                     # 다시쓰기는 백그라운드(2026-07-31 upstream error 실사고) — running이면 폴링 배너
                     + (("<div class='flex items-center gap-2 text-sm text-amber-700 bg-amber-50 rounded-xl p-3'>"
                         "<span class='inline-block w-4 h-4 border-2 border-amber-300 border-t-amber-600 rounded-full animate-spin flex-shrink-0'></span>"
                         "<b>AI가 글을 다시 쓰는 중이에요</b> — 1~2분 뒤 자동으로 새 글이 나타나요 (화면 안 닫아도 돼요)</div>"
                         f"<script>(function(){{var n=0;var iv=setInterval(async function(){{n++;if(n>60){{clearInterval(iv);return;}}"
                         f"try{{var d=await (await fetch('/me/rewrite-status?asset_id={esc(asset_id)}')).json();"
                         "if(d&&(d.status==='done'||d.status==='failed')){clearInterval(iv);location.reload();}"
                         "}catch(_){}},5000);})();</script>")
                        if _rewrite_running(pl) else
                        (f"<form method='post' action='/kit/{asset_id}/regen-blog' "
                         "onsubmit=\"var b=this.querySelector('button');b.disabled=true;"
                         "b.innerHTML='⏳ 접수 중…';b.classList.add('opacity-70','animate-pulse');\">"
                         "<button class='block w-full text-center py-3 rounded-xl text-white text-sm font-extrabold "
                         "bg-amber-500 hover:brightness-110 active:scale-[.99] transition shadow-md'>"
                         "🔧 품질 기준 미달 — AI가 다시 쓰기</button>"
                         "<div class='text-[11px] text-slate-400 text-center mt-1.5'>상위노출 기준(80점)에 못 미쳐 "
                         "발행을 잠시 막아뒀어요. 버튼을 누르면 AI가 다시 씁니다 (1~2분)</div></form>")
                        if (pl or {}).get("publish_blocked_score") else
                        # ⏳ 품질 보정이 아직 도는 중이면 발행 판정이 안 끝났다(2026-08-01 검토 지적).
                        #   글은 먼저 열어주되, 미달 글이 봉인 전에 발행되는 창은 막는다.
                        ("<div class='block w-full text-center py-3 rounded-xl text-sm font-extrabold "
                         "bg-slate-100 text-slate-400'>⏳ 상위노출 기준 확인 중 — 곧 발행할 수 있어요</div>"
                         "<div class='text-[11px] text-slate-400 text-center mt-1.5'>"
                         "AI가 점수를 마저 올리는 중입니다 (보통 1~3분)</div>")
                        if ((pl or {}).get("polish_job") or {}).get("status") == "running" else naver_btn)
                     + ("<div class='mt-3 pt-3 border-t border-slate-100 text-center' id='fb%s'>"
                        "<span class='text-xs text-slate-400 mr-2'>이 글 어땠나요?</span>"
                        "<button type=button onclick=\"fbv('%s','up')\" class='px-3 py-1.5 rounded-lg "
                        "border border-slate-200 hover:bg-slate-50 text-sm'>👍</button> "
                        "<button type=button onclick=\"fbv('%s','down')\" class='px-3 py-1.5 rounded-lg "
                        "border border-slate-200 hover:bg-slate-50 text-sm'>👎</button>"
                        "<div id='fbi%s' class='hidden mt-2'><input id='fbt%s' placeholder='한 줄만 남겨주세요 "
                        "(예: 문장이 너무 길어요)' class='w-full border border-slate-200 rounded-lg px-3 py-2 "
                        "text-sm'><button type=button onclick=\"fbs('%s')\" class='mt-1.5 w-full py-2 "
                        "bg-slate-800 text-white rounded-lg text-xs font-bold'>보내기</button></div></div>")
                       % (asset_id, asset_id, asset_id, asset_id, asset_id, asset_id)
                     + f"<div class='flex gap-2'>{pack_btn(p.id, bool(_set_naver_video(pieces)))}<button type=button onclick=\"cp('cb{sid}',this)\" class='px-3.5 py-2.5 border border-slate-200 text-slate-600 hover:bg-slate-50 text-xs font-bold rounded-xl transition'>글 복사</button></div></div></div>")
        elif k == "x_post":
            xt = pl.get("text", "")
            # X는 9:16 세로 업로드 공식 지원(1080×1920, Immersive Media Viewer 풀스크린 재생)
            # → 세로 소스를 세로 프레임으로 표시(좌우 레터박스 제거). 영상 없으면 사진 폴백(비율 유지).
            if vurl:
                xvid = (f"<div class='relative mx-auto mt-2 bg-black rounded-xl overflow-hidden' style='max-width:300px;aspect-ratio:9/16'>"
                        f"<video src='{vurl}' controls autoplay muted loop playsinline preload='metadata' poster='{first_img}' "
                        "class='w-full h-full' style='object-fit:cover'></video>"
                        "<button type=button onclick='omUnmute(this)' class='om-unmute absolute top-3 left-1/2 -translate-x-1/2 z-10 bg-black/80 text-white text-xs font-extrabold px-3.5 py-2 rounded-full shadow-lg'>🔇 탭하여 소리 켜기</button></div>")
            elif first_img:
                xvid = f"<img src='{first_img}' class='w-full rounded-xl mt-2 border border-slate-100' style='max-height:360px;object-fit:cover'>"
            else:
                xvid = ""
            block = (_hd("𝕏 X", pl) + f"<div class='{wrap} p-4'>"
                     "<div class='flex items-center gap-2 mb-2'>" + _av()
                     + f"<div><div class='font-bold text-sm leading-tight'>{esc(sname)}</div><div class='text-slate-400 text-xs'>@{handle} · now</div></div><div class='ml-auto text-lg font-bold'>𝕏</div></div>"
                     + f"<div class='text-sm whitespace-pre-wrap leading-relaxed text-slate-800'>{esc(xt)}</div>"
                     + xvid
                     + "<div class='flex items-center gap-10 text-slate-400 mt-3 text-sm'><span>💬</span><span>🔁</span><span>♡</span><span>📊</span></div>"
                     + f"<div class='mt-3 flex gap-2'>{(pack_btn(p.id, has_video)) if has_video else ''}{_cp('c_x', xt, '복사')}</div></div>")
        elif k == "short" and p.channel.value in ("youtube", "instagram"):
            # 영상 온디맨드(사용자 선택 존중): 네이버 영상의 기반 렌더로 저장된 쇼츠 피스는
            # 사용자가 그 채널을 요청(status=done 등)하기 전엔 카드로 안 보여준다(구건=상태 없음은 표시).
            _st_ch = "shorts" if p.channel.value == "youtube" else "reels"
            _cs_me = next((pp.payload.get("channel_status") for pp in pieces
                           if pp.kind.value == "blog" and pp.payload.get("channel_status")), {}) or {}
            if ((_cs_me.get(_st_ch) or {}).get("status") or "") == "not_requested":
                continue
            title = pl.get("title", "") or (pl.get("text", "")[:30])
            desc = pl.get("narration", "") or pl.get("text", "")
            lab = "유튜브 쇼츠" if p.channel.value == "youtube" else "인스타 릴스"
            dur = int(pl.get("duration_sec") or 0)
            durb = (f"<div class='absolute top-2 right-2 bg-black/70 text-white text-[11px] font-bold px-1.5 py-0.5 rounded'>{dur // 60}:{dur % 60:02d}</div>" if dur else "")
            durb += ("<div class='absolute top-2 left-2 bg-black/70 text-white text-[11px] font-bold px-1.5 py-0.5 rounded'>"
                     + ("쇼츠" if p.channel.value == "youtube" else "릴스") + "</div>")
            if vurl:
                player = (f"<div class='relative mx-auto bg-black rounded-xl overflow-hidden' style='max-width:340px;aspect-ratio:9/16'>"
                          f"<video src='{vurl}' controls autoplay muted loop playsinline preload='metadata' poster='{first_img}' "
                          f"class='w-full h-full' style='object-fit:cover'></video>{durb}"
                          "<button type=button onclick='omUnmute(this)' class='om-unmute absolute top-3 left-1/2 -translate-x-1/2 z-10 bg-black/80 text-white text-xs font-extrabold px-3.5 py-2 rounded-full shadow-lg'>🔇 탭하여 소리 켜기</button></div>")
            elif first_img:
                player = ("<div class='relative bg-black'>"
                          f"<img src='{first_img}' class='w-full max-h-[440px] object-cover opacity-85'>"
                          "<div class='absolute inset-0 flex flex-col items-center justify-center'>"
                          "<div class='w-14 h-14 rounded-full bg-white/90 flex items-center justify-center text-indigo-600 text-2xl shadow-lg'>▶</div>"
                          f"<span class='text-white text-xs mt-2'>영상은 ‘통째로 받기’에 포함</span></div>{durb}</div>")
            else:
                player = "<div class='w-full aspect-video bg-black flex items-center justify-center text-white text-3xl'>▶️</div>"
            sound_tip = pl.get("trending_sound_tip") or "발행 시 인스타/유튜브 앱에서 ‘트렌딩 사운드’를 입히면 도달이 크게 늘어요(1탭)."
            block = (_hd(lab, pl) + f"<div class='{wrap} overflow-hidden'>{player}"
                     f"<div class='p-4'><div class='font-bold text-sm mb-1'>{esc(title)}</div>"
                     f"<div class='text-xs text-slate-500 whitespace-pre-wrap max-h-24 overflow-y-auto'>{esc(desc)}</div>"
                     f"<div class='mt-2 bg-amber-50 border border-amber-100 text-amber-800 text-[11px] rounded-lg px-2.5 py-1.5'>🎵 {esc(sound_tip)}</div>"
                     f"<div class='mt-3 flex gap-2'>{pack_btn(p.id, has_video)}{_cp('c_v' + p.id[:5], title, '제목')}</div></div></div>")
        elif k == "marketplace":
            mk = pl.get("market", "마켓")
            names = pl.get("product_names") or []
            detail = pl.get("detail_body", "")
            tags = pl.get("tags") or []
            names_html = "".join(
                f"<div class='flex items-start gap-2 mb-1.5'><span class='text-slate-300 text-xs mt-1'>{i+1}</span>"
                f"<div class='flex-1 text-sm text-slate-800'>{esc(n)}</div>{_cp('c_mn' + str(i) + p.id[:4], n, '복사')}</div>"
                for i, n in enumerate(names[:3]))
            tags_html = "".join(f"<span class='inline-block bg-slate-100 text-slate-600 text-xs px-2 py-1 rounded-full mr-1 mb-1'>{esc(tg)}</span>" for tg in tags)
            summary = pl.get("detail_summary") or []
            spec = pl.get("spec_table", "")
            rkit = pl.get("review_kit") or []
            summary_html = ""
            if summary:
                summary_html = ("<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>요약본 (핵심 소구점 5줄)</div>"
                                + "".join(f"<div class='text-sm text-slate-700 mb-1'>· {esc(s)}</div>" for s in summary)
                                + _cp("c_ms" + p.id[:5], "\n".join(summary), "요약 복사"))
            spec_html = (("<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>스펙표 (입력값만)</div>"
                          f"<div class='text-xs text-slate-600 whitespace-pre-wrap border border-slate-100 rounded-lg p-2'>{esc(spec)}</div>")
                         if spec else "")
            rkit_html = ""
            if rkit:
                rkit_html = ("<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>리뷰 요청 문구 키트</div>"
                             + "".join(f"<div class='flex items-start gap-2 mb-1.5'><div class='flex-1 text-xs text-slate-600'>{esc(s)}</div>"
                                       f"{_cp('c_mr' + str(i) + p.id[:4], s, '복사')}</div>" for i, s in enumerate(rkit))
                             + "<div class='text-[11px] text-slate-400'>※ 리뷰 대가(포인트·사은품 조건) 제시는 플랫폼 규정 위반이에요 — 정당한 요청 문구만 담았어요.</div>")
            block = (_hd(f"🛒 {esc(mk)} 판매 콘텐츠", pl) + f"<div class='{wrap} p-4'>"
                     "<div class='text-xs font-bold text-slate-400 mb-1.5'>상품명 (검색 최적화 · 3안)</div>" + names_html
                     + "<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>상세페이지</div>"
                     + f"<div class='text-xs text-slate-600 whitespace-pre-wrap max-h-40 overflow-y-auto border border-slate-100 rounded-lg p-2'>{esc(detail)}</div>"
                     + summary_html + spec_html
                     + (f"<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>검색 태그</div><div>{tags_html}</div>" if tags_html else "")
                     + rkit_html
                     + f"<div class='mt-3 flex gap-2'>{_cp('c_md' + p.id[:5], detail, '상세 복사')}{pack_btn(p.id, False)}</div></div>")
        if block:
            grp = ("video" if k == "short" else "sell" if k == "marketplace" else "text")
            cards += f"<div class='break-inside-avoid mb-6 om-card' data-ch='{grp}'>" + block + "</div>"
            rendered_ch.add({"caption": "insta", "blog": "naver", "x_post": "x"}.get(
                k, ("shorts" if p.channel.value == "youtube" else "reels") if k == "short" else k))
    # ── 5채널 완전성: 누락 채널 상태 카드 + 정합 감시(8/10 사진 버그와 같은 패턴) ──
    import logging
    _cs_all = next((p.payload.get("channel_status") for p in pieces
                    if p.kind.value == "blog" and p.payload.get("channel_status")), {}) or {}
    _CH_LABEL = {"naver": "네이버 블로그", "shorts": "유튜브 쇼츠", "reels": "인스타 릴스",
                 "insta": "인스타그램", "x": "𝕏 X"}
    for _ch, _lab in _CH_LABEL.items():
        if _ch in rendered_ch:
            continue
        _st = (_cs_all.get(_ch) or {})
        _s = _st.get("status")
        if _s == "done":                                   # 상태는 done인데 카드 없음 = 정합 붕괴
            logging.getLogger("shopcast.kit").warning(
                "[정합] channel_status=done인데 렌더 블록 없음 asset=%s ch=%s", asset_id, _ch)
            continue
        if not _s:
            # ★ 상태 기록이 없어도 '만들기'는 열어둔다(2026-08-01 실사고: 버튼이 세트마다 나왔다
            #   안 나왔다 함). 상태가 비는 원인은 두 가지 — 이 기능 이전의 옛 세트, 그리고 생성이
            #   품질 루프에서 막혀 상태를 기록하지 못한 세트다. 어느 쪽이든 영상은 만들 수 있다.
            _s = "not_requested"
        if _s in ("generating", "registered"):
            _inner = ("<div class='flex items-center gap-2 text-sm text-slate-500'>"
                      "<span class='inline-block w-4 h-4 border-2 border-slate-300 border-t-indigo-500 rounded-full animate-spin'></span>"
                      "만드는 중이에요 (몇 분 걸려요) — 완성되면 자동으로 나타나요</div>")
        elif _s == "not_requested":                        # 영상 온디맨드 — 여기서 바로 요청 가능
            _inner = ("<div class='text-sm text-slate-500 mb-2'>영상은 필요할 때만 만들어요 — 글은 이미 완성!</div>"
                      "<button type='button' class='px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-900 "
                      "text-white text-xs font-bold transition' "
                      "onclick=\"vmPick(this,'" + esc(asset_id) + "','" + _ch + "')\">"
                      "🎬 이 영상 만들기</button>")
        elif int(_st.get("retries") or 0) >= 2:
            _inner = ("<div class='text-sm text-slate-500'>만들지 못했어요 — 아래 사유를 확인해 주세요</div>"
                      f"<div class='text-xs text-slate-400 mt-1'>{esc((_st.get('error') or '')[:120])}</div>")
        else:
            _inner = ("<div class='flex items-center gap-2 text-sm text-amber-700'>"
                      "<span class='inline-block w-4 h-4 border-2 border-amber-300 border-t-amber-600 rounded-full animate-spin'></span>"
                      "만들다 문제가 생겼어요 — 다시 만드는 중이에요</div>")
        cards += (f"<div class='break-inside-avoid mb-6 om-card' data-ch='text'>{_hd(_lab)}"
                  f"<div class='{wrap} p-5'>{_inner}</div></div>")
    for _ch, _st in _cs_all.items():                       # 역방향 정합: 렌더됐는데 상태가 failed
        if _ch in rendered_ch and (_st or {}).get("status") == "failed" and _ch != "naver":
            logging.getLogger("shopcast.kit").warning(
                "[정합] 렌더 블록은 있는데 channel_status=failed asset=%s ch=%s", asset_id, _ch)
    js = (_VMPICK_JS +          # ⭐ 대표 사진 고르기 모달(영상 만들기 버튼 공용)
          "<script>"
          "function omCopy(text){if(navigator.clipboard&&navigator.clipboard.writeText){return navigator.clipboard.writeText(text);}"
          "return new Promise(function(res,rej){var ta=document.createElement('textarea');ta.value=text;ta.setAttribute('readonly','');ta.style.position='fixed';ta.style.top='0';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();ta.setSelectionRange(0,text.length);var ok=false;try{ok=document.execCommand('copy');}catch(e){}document.body.removeChild(ta);ok?res():rej();});}"
          "function fbv(a,v){var i=document.getElementById('fbi'+a);"
          "if(v==='down'){i.classList.remove('hidden');}"
          "fetch('/me/feedback',{method:'POST',body:new URLSearchParams({asset_id:a,vote:v})})"
          ".then(function(){if(v==='up'){document.getElementById('fb'+a).innerHTML="
          "'<span class=\'text-xs text-emerald-600 font-bold\'>감사합니다! 다음 글에 반영할게요 🙌</span>';}});}"
          "function fbs(a){var t=document.getElementById('fbt'+a).value;"
          "fetch('/me/feedback',{method:'POST',body:new URLSearchParams({asset_id:a,vote:'down',text:t})})"
          ".then(function(r){return r.json();}).then(function(d){"
          "document.getElementById('fb'+a).innerHTML='<span class=\'text-xs text-indigo-600 font-bold\'>'"
          "+(d.applied?'접수했어요 — 다음 글부터 바로 반영됩니다 ✅':'접수했어요. 확인 후 개선할게요 🙏')"
          "+'</span>';});}"
          "function cp(id,btn){var t=document.getElementById(id);var o=btn.textContent;"
          "omCopy(t.value).then(function(){btn.textContent='✅ 복사됨';}).catch(function(){btn.textContent='길게 눌러 복사';});setTimeout(function(){btn.textContent=o;},1500);}"
          "async function copyRich(id,btn){var el=document.getElementById(id);var o=btn.textContent;"
          "try{await navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([el.innerHTML],{type:'text/html'}),'text/plain':new Blob([el.innerText],{type:'text/plain'})})]);btn.textContent='✅ 복사됨! 네이버 글쓰기에 붙여넣기';}"
          "catch(e){try{await omCopy(el.innerText);btn.textContent='✅ 글 복사됨(사진은 아래로 따로)';}catch(e2){btn.textContent='길게 눌러 복사';}}"
          "setTimeout(function(){btn.textContent=o;},2600);}"
          "function omFilter(g,btn){document.querySelectorAll('.om-card').forEach(function(c){c.style.display=(g==='all'||c.getAttribute('data-ch')===g)?'':'none';});"
          "document.querySelectorAll('#chFilter .om-fbtn').forEach(function(b){b.classList.remove('bg-indigo-600','text-white');b.classList.add('bg-slate-100','text-slate-600');});"
          "btn.classList.remove('bg-slate-100','text-slate-600');btn.classList.add('bg-indigo-600','text-white');}"
          "(function(){var vs=document.querySelectorAll('video[autoplay]');if(!vs.length)return;"
          "vs.forEach(function(v){v.muted=true;v.setAttribute('muted','');v.playsInline=true;});"       # 무음이어야 자동재생 허용
          "function tryplay(v){if(window.omSound){v.muted=false;}var p=v.play();if(p&&p.catch)p.catch(function(){});}"   # ⚠️ load() 호출 금지 — 리로드 루프(깜빡임) 원인
          "if('IntersectionObserver' in window){var io=new IntersectionObserver(function(es){es.forEach(function(e){"
          "if(e.isIntersecting){tryplay(e.target);}else{try{e.target.pause();}catch(_){}}});},{threshold:0.35});"
          "vs.forEach(function(v){io.observe(v);});}else{vs.forEach(tryplay);}"            # 화면에 보이는 영상 자동재생(릴스식)
          "var f=vs[0];if(f){var h=function(){tryplay(f);f.removeEventListener('canplay',h);};f.addEventListener('canplay',h);tryplay(f);}"  # 첫 영상: canplay 때 1회만(루프X)
          "document.addEventListener('touchstart',function(){tryplay(vs[0]);},{once:true});})();"  # 모바일 첫 터치 시 재생 보증
          "function omUnmute(btn){window.omSound=true;var v=btn.parentElement.querySelector('video');if(v){v.muted=false;v.volume=1;var p=v.play();if(p&&p.catch)p.catch(function(){});}document.querySelectorAll('.om-unmute').forEach(function(b){b.style.display='none';});}"
          "function pickTitle(sid,aid,btn){var t=btn.getAttribute('data-t');var el=document.getElementById('bt'+sid);if(el)el.textContent=t;var ta=document.getElementById('cb'+sid);if(ta)ta.value=t+'\\n\\n'+(ta.getAttribute('data-body')||'');"
          "fetch('/me/set/'+aid+'/title',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'title='+encodeURIComponent(t)}).then(function(){var m=document.getElementById('tsel'+sid);if(m)m.textContent='✅ 이 제목으로 저장됐어요 (파일명·폴더도 함께 반영)';});}"
          "</script>")
    brief = next((p.payload.get("brief") for p in pieces if p.payload.get("brief")), None)
    pipeline = ("<div class='bg-indigo-50 border border-indigo-100 rounded-2xl p-4 mb-4'>"
                "<div class='text-sm font-bold text-indigo-700 mb-1'>AI 전문가 팀이 제작했어요</div>"
                "<div class='text-xs text-indigo-500'>마케팅 전략가 → 카피라이터 → SEO 편집장 → 영상 감독</div>"
                + (f"<div class='text-xs text-slate-500 mt-2'>핵심 전략 키워드: <b>{esc(brief.get('core_keyword',''))}</b> · 앵글: {esc(brief.get('angle',''))}</div>" if brief else "")
                + "</div>")
    all_btn = (f"<a href='/kit/{asset_id}/pack-all' class='block text-center {_BTN} py-4 rounded-2xl mb-5 font-extrabold'>"
               "5채널 전체 한 번에 받기 "
               "<span class='opacity-80 font-medium text-sm'>· 글+사진+영상 (채널별 폴더)</span></a>")
    thumbs = "".join(f"<img src='/dl/{asset_id}/{os.path.basename(im)}' class='h-24 w-24 object-cover rounded-lg border border-slate-100'>"
                     for im in imgs if im)
    photos_strip = (("<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-4'>"
                     "<div class='font-bold text-sm mb-2'>📷 내가 올린 사진</div>"
                     f"<div class='flex gap-2 flex-wrap'>{thumbs}</div></div>") if thumbs else "")
    store_hd = (f"<div class='text-sm text-indigo-500 font-bold'>{esc(sname)}</div>"
                if sname and sname not in ("내 가게", "카카오회원", "구글회원") else "")
    # 영상(유튜브·릴스)은 백그라운드 → 아직 없으면 '생성 중' 배너 + 폴링(완성되면 자동 새로고침)
    # ★ 단, '최근 5분 이내 생성'일 때만 — 오래된 콘텐츠에 '생성 중'이 무한 표시되던 버그 방지
    _recent = False
    try:
        from datetime import datetime as _dt
        with db._conn() as _c:
            _row = _c.execute("SELECT MAX(created_at) AS m FROM content_pieces WHERE asset_id=?", (asset_id,)).fetchone()
        if _row and _row["m"]:
            _age = (_dt.utcnow() - _dt.fromisoformat(str(_row["m"]).replace("Z", ""))).total_seconds()
            _recent = 0 <= _age < 300
    except Exception:
        _recent = False
    _vid_poll = ""
    _cs_poll = next((p.payload.get("channel_status") for p in pieces
                     if p.kind.value == "blog" and p.payload.get("channel_status")), {}) or {}
    _pending_ch = any((v or {}).get("status") in ("generating", "registered", "failed") and int((v or {}).get("retries") or 0) < 2
                      for v in _cs_poll.values())
    # ★ 영상 배너는 '실제 요청된 영상'만(온디맨드) — '영상 부재=생성 중'으로 찍던 가짜 배너(구 자동생성
    #   잔재, 실측 캡처) 폐지. 진행 단계(stage)도 함께 표시, 완성(sig 변화) 시 자동 새로고침.
    _CHLAB_V = {"shorts": "유튜브 쇼츠", "reels": "인스타 릴스", "naver": "네이버 영상"}
    _gen_chs = [c for c in ("shorts", "reels", "naver")
                if (_cs_poll.get(c) or {}).get("status") in ("generating", "registered")]
    _banner = ""
    if _gen_chs:
        _banner = ("<div class='bg-amber-50 border border-amber-100 text-amber-700 rounded-2xl p-3.5 mb-5 text-sm flex items-center gap-2'>"
                   "<div class='w-4 h-4 border-2 border-amber-300 border-t-amber-600 rounded-full animate-spin flex-shrink-0'></div>"
                   f"<div>🎬 {'·'.join(_CHLAB_V[c] for c in _gen_chs)} <b>영상 만드는 중…</b>"
                   "<span id='vbStage' class='text-amber-600'></span> — 완성되면 자동으로 나타나요</div></div>")
    if _banner or (_recent and _pending_ch):
        _vid_poll = (_banner
                     + f"<script>(function(){{var base=null,n=0,aid='{asset_id}';"
                     "var iv=setInterval(async function(){n++;if(n>240){clearInterval(iv);return;}"
                     "try{var d=await (await fetch('/me/asset/'+aid+'/pieces')).json();"
                     "if(base===null){base=d.sig;}else if(d.sig!==base){clearInterval(iv);location.reload();return;}"
                     "var v=await (await fetch('/me/video/status?asset_id='+aid)).json();"
                     "var s=document.getElementById('vbStage');if(s&&v.job&&v.job.stage)s.textContent=' — '+v.job.stage;"
                     "}catch(_){}},5000);})();</script>")
    # 🎯 성과 추적 링크/QR — 콘텐츠에 넣으면 유입 집계(리포트와 연결)
    track_box = ""
    try:
        _tl = _ensure_track_link(tenant) if tenant else None
        if _tl:
            _base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
            _short = f"{_base}/r/{_tl['code']}"
            track_box = (
                "<div class='bg-white rounded-2xl border border-slate-100 p-4 mb-4 flex items-center gap-3'>"
                f"<img src='/me/qr/{_tl['code']}.png' class='w-16 h-16 rounded-lg border border-slate-100 flex-shrink-0 bg-white' alt='추적 QR'>"
                "<div class='flex-1 min-w-0'><div class='text-xs font-bold text-slate-700'>성과 추적 링크·QR</div>"
                "<div class='text-[11px] text-slate-400 mb-1'>콘텐츠·프로필에 넣으면 여기로 온 손님이 리포트에 집계돼요</div>"
                f"<input readonly value='{_short}' id='rtrk' class='w-full text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1 text-slate-600'></div>"
                "<button type=button onclick=\"omCopy(document.getElementById('rtrk').value);this.textContent='✅'\" class='flex-shrink-0 bg-indigo-600 text-white text-xs font-bold px-3 py-2 rounded-lg'>복사</button></div>")
    except Exception:
        track_box = ""
    # 채널 필터(탭) — 카드가 많을 때 글/영상/판매로 걸러보기
    _fbtns = [("all", "전체"), ("text", "글")]
    if any(p.kind.value == "short" for p in pieces):
        _fbtns.append(("video", "영상"))
    if any(p.kind.value == "marketplace" for p in pieces):
        _fbtns.append(("sell", "🛒 판매"))
    filter_bar = (("<div class='flex gap-2 mb-4 overflow-x-auto' id='chFilter'>"
                   + "".join("<button type=button onclick=\"omFilter('" + v + "',this)\" "
                             "class='om-fbtn flex-shrink-0 px-3.5 py-1.5 rounded-full text-xs font-bold whitespace-nowrap "
                             + ("bg-indigo-600 text-white" if v == "all" else "bg-slate-100 text-slate-600") + "'>" + lab + "</button>"
                             for v, lab in _fbtns)
                   + "</div>") if len(pieces) >= 3 else "")
    body = (f"<a href='{back_href}' class='inline-block text-sm text-slate-500 font-bold mb-2'>{back_label}</a>"
            + store_hd
            + "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>발행 소재</h2>"
            "<p class='text-slate-400 text-sm mb-5'>각 앱에 올리면 <b class='text-slate-600'>이렇게</b> 보여요. 글은 복사, 사진·영상은 다운로드하세요.</p>"
            + _vid_poll + pipeline + all_btn + track_box + filter_bar
            + "<div class='sm:columns-2 gap-6'>" + cards + "</div>" + js)
    return body


@app.get("/kit/{asset_id}", response_class=HTMLResponse)
def kit(request: Request, asset_id: str):
    """발행 소재 독립 페이지(공유·직접링크)."""
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    body = _result_html(u, asset_id)
    if body is None:
        return HTMLResponse(_subscriber_page("접근 불가",
            "<div class='bg-rose-50 text-rose-600 p-4 rounded-2xl'>내 콘텐츠가 아니거나 없는 세트예요.</div>"))
    return HTMLResponse(_subscriber_page("발행 소재", body))


def _workflow_guide(sec: str) -> str:
    """임시저장/이어쓰기 워크플로우 안내(블로그템플릿 PHASE 4) — 네이버는 PC↔모바일앱
    임시저장이 동기화되므로 'PC에서 뼈대 → 모바일에서 사진·지도 → 발행' 흐름이 가장 편하다.
    사용자 상황(PC만/모바일만/둘다)별 추천 흐름을 탭으로 제시."""
    flows = {
        "both": ("PC와 모바일 둘 다 (추천)",
                 ["PC 네이버 블로그 글쓰기에 <b>제목·본문 붙여넣기</b> (긴 글은 PC가 편해요)",
                  "우측 상단 <b>임시저장</b> — 모바일앱과 자동 동기화돼요",
                  "네이버 블로그 <b>앱 → 글쓰기 → 임시저장 글 이어쓰기</b>",
                  "폰에 저장한 사진을 [사진N] 자리에 업로드 + <b>장소 컴포넌트</b> 삽입",
                  "발행 → 아래 '발행함 ✓'으로 확인"]),
        "pc": ("PC만 쓸 때",
               ["사진을 먼저 PC로 저장(위 '전체 ZIP 받기'가 편해요)",
                "글쓰기에 제목·본문 붙여넣기 → [사진N] 자리에 사진 업로드",
                "<b>장소</b> 버튼으로 지도 컴포넌트 삽입([여기 네이버 지도 넣기] 자리)",
                "발행 → 아래 '발행함 ✓'으로 확인"]),
        "mobile": ("모바일만 쓸 때",
                   ["이 화면에서 제목·본문을 각각 <b>복사</b>",
                    "네이버 블로그 앱 → 글쓰기 → 붙여넣기",
                    "사진은 <b>⬇ 저장</b> 버튼으로 폰에 받은 뒤 업로드(붙여넣기는 불안정해요)",
                    "<b>장소</b> 버튼 → 위 초록 버튼으로 복사한 상호 붙여넣기 → 지도 삽입",
                    "발행 → 아래 '발행함 ✓'으로 확인"]),
    }
    tabs = ""
    panes = ""
    for i, (key, (label, steps)) in enumerate(flows.items()):
        on = "bg-slate-900 text-white" if i == 0 else "bg-slate-100 text-slate-600"
        tabs += (f"<button type=button onclick=\"wfTab('{key}',this)\" data-wftab=1 "
                 f"class='{on} px-3.5 py-2 rounded-xl text-xs font-bold transition whitespace-nowrap'>{label}</button>")
        lis = "".join(
            f"<li class='flex gap-2.5 items-start mb-2'><span class='flex-shrink-0 w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 text-[11px] font-bold flex items-center justify-center mt-0.5'>{n+1}</span>"
            f"<span class='text-sm text-slate-600'>{s}</span></li>" for n, s in enumerate(steps))
        panes += f"<ul id='wf_{key}' class='mt-3 {'hidden' if i else ''}'>{lis}</ul>"
    return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>어디서 쓰실 건가요? "
            "<span class='text-emerald-600'>(네이버는 PC↔모바일 임시저장이 동기화돼요)</span></div>"
            f"<div class='flex gap-1.5 overflow-x-auto'>{tabs}</div>{panes}"
            "<script>function wfTab(k,btn){['both','pc','mobile'].forEach(function(x){"
            "var p=document.getElementById('wf_'+x);if(p)p.classList.toggle('hidden',x!==k);});"
            "document.querySelectorAll('[data-wftab]').forEach(function(b){b.className=b.className.replace('bg-slate-900 text-white','bg-slate-100 text-slate-600');});"
            "btn.className=btn.className.replace('bg-slate-100 text-slate-600','bg-slate-900 text-white');}</script></div>")


def _naver_component_guide(tenant, blog, sec: str) -> str:
    """네이버 지도·장소 컴포넌트 삽입 가이드(블로그템플릿 PHASE 3) — 모바일 우선.
    지도는 텍스트가 아니라 네이버 '장소' 컴포넌트로 넣어야 플레이스 연결·지역SEO에 유리.
    본문 [여기 네이버 지도 넣기] 마커 위치에서 쓰는 3스텝 + 요소별 개별 복사."""
    from app.services import blogtpl
    body = blog.payload.get("body") or ""
    is_local = (getattr(tenant, "biz_type", "local") or "local") in ("local", "hybrid")
    if not is_local and blogtpl.MAP_MARKER not in body:
        return ""
    name = (getattr(tenant, "name", "") or "").strip()
    region = (getattr(tenant, "region", "") or "").strip()
    place_q = f"{name} {region}".strip() or name          # 장소 검색용 상호+지역
    big = ("w-full flex items-center justify-between gap-2 rounded-2xl px-4 py-3.5 text-sm font-bold "
           "transition active:scale-[.99]")               # 모바일 큰 터치 버튼

    def _copy_row(idx, emoji, label, value):
        if not (value or "").strip():
            return ""
        return (f"<div class='flex items-center gap-2 mb-2'>"
                f"<div class='flex-1 min-w-0 bg-slate-50 rounded-xl px-3.5 py-3 text-sm'>"
                f"<span class='text-[11px] font-bold text-slate-400 block'>{emoji} {label}</span>"
                f"<span class='text-slate-700 break-all'>{esc(value)}</span></div>"
                f"<textarea id='cg{idx}' class='hidden'>{esc(value)}</textarea>"
                f"<button onclick=\"omCopy(document.getElementById('cg{idx}').value);this.textContent='✅';"
                "var b=this;setTimeout(function(){b.textContent='복사';},1500)\" "
                "class='flex-shrink-0 w-16 py-3 bg-slate-900 hover:bg-slate-800 text-white text-sm font-bold rounded-xl transition'>복사</button></div>")

    # 스텝 진행 표시(①→②→③) + 큰 복사 버튼
    steps = ("<div class='flex items-center gap-1.5 mb-3'>"
             + "".join(f"<div class='flex-1 text-center'><div class='w-7 h-7 mx-auto rounded-full bg-emerald-600 text-white text-sm font-bold flex items-center justify-center'>{n}</div>"
                       f"<div class='text-[10px] text-slate-500 mt-1 leading-tight'>{s}</div></div>"
                       + ("<div class='w-4 h-px bg-slate-300 -mt-4'></div>" if n < 3 else "")
                       for n, s in [(1, "네이버 글쓰기에서<br><b>장소</b> 버튼"), (2, "아래 상호<br><b>붙여넣기</b>"), (3, "내 가게<br><b>선택</b>")])
             + "</div>")
    place_link = ""
    if (getattr(tenant, "map_url", "") or "").strip():
        place_link = (f"<a href='{esc(tenant.map_url)}' target=_blank rel=noopener "
                      f"class='{big} bg-emerald-50 text-emerald-700 border border-emerald-200 mb-2'>"
                      "<span>내 플레이스 열어 확인</span><span>↗</span></a>")
    rows = (_copy_row(1, "", "장소 검색용 (장소 버튼에 붙여넣기)", place_q)
            + _copy_row(2, "", "전화번호 (네이버가 자동으로 전화 연결 링크 처리 — 텍스트면 충분)",
                        getattr(tenant, "phone", ""))
            + _copy_row(3, "", "영업시간", getattr(tenant, "hours", ""))
            + _copy_row(4, "", "주차 안내", getattr(tenant, "parking", "")))
    return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-1'>지도는 <span class='text-emerald-600'>네이버 장소 컴포넌트</span>로!</div>"
            "<p class='text-xs text-slate-500 mb-3'>본문의 <b>[여기 네이버 지도 넣기]</b> 자리에 장소 컴포넌트를 넣으면 "
            "글이 내 플레이스와 연결돼 지역 검색에 유리해요. 링크 텍스트보다 훨씬 좋아요.</p>"
            + steps
            + f"<textarea id='cgq' class='hidden'>{esc(place_q)}</textarea>"
            f"<button onclick=\"omCopy(document.getElementById('cgq').value);this.querySelector('span').textContent='✅ 복사됨 — 이제 네이버 장소 버튼에 붙여넣기'\" "
            f"class='{big} bg-emerald-600 hover:bg-emerald-700 text-white mb-3'><span>'{esc(place_q)}' 복사</span><span>→</span></button>"
            + place_link
            + "<details class='mt-1'><summary class='text-xs font-bold text-slate-500 cursor-pointer select-none'>연락처·영업시간·주차 개별 복사 ▾</summary>"
            f"<div class='mt-2'>{rows}</div></details></div>")


def _internal_link_box(blog, sec: str) -> str:
    """내부링크 안내 — 같은 주제 축의 '발행 확인된' 내 글을 본문 끝에 링크로 넣도록 제안."""
    rel = blog.payload.get("related_posts") or []
    if not rel:
        try:
            from app.services import blogsync
            rel = blogsync.related_published(blog.tenant_id, blog.payload.get("target_keywords") or [])
        except Exception:
            rel = []
    if not rel:
        return ""
    rel = [dict(r, url=(r.get("url") or "").split("?")[0]) for r in rel]   # 구세트 저장분 추적 파라미터 정리
    links_text = "\n".join(f"▶ 함께 보면 좋은 글: {r.get('title') or r['url']}\n{r['url']}" for r in rel[:3])
    rows = "".join(
        f"<div class='flex items-center justify-between bg-slate-50 rounded-lg px-3 py-2 mb-1.5'>"
        f"<span class='text-sm text-slate-600 truncate'>{esc(r.get('title') or r['url'])}</span>"
        f"<a href='{esc(r['url'])}' target=_blank rel=noopener class='text-xs text-indigo-500 font-bold whitespace-nowrap ml-2'>보기 ↗</a></div>"
        for r in rel[:3])
    return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>내부링크 — 같은 주제 내 글과 연결 "
            "<span class='text-emerald-600'>(같은 주제 글끼리 서로 도움)</span></div>"
            "<p class='text-xs text-slate-500 mb-2'>발행할 때 본문 끝에 아래 글 링크를 넣어보세요. 같은 주제 글끼리 연결되면 "
            "블로그의 주제 전문성이 쌓여요.</p>" + rows +
            f"<textarea id='nvRel' class='hidden'>{esc(links_text)}</textarea>"
            "<button onclick=\"nvcp('nvRel',this)\" class='mt-1 px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-600 text-xs font-bold rounded-xl transition'>링크 문구 복사</button></div>")


def _angle_variant_box(blog, sec: str, cbtn: str) -> str:
    """앵글 변형 생성 버튼 — 후기/방법/가격 서로 다른 스마트블록 다중진입."""
    cur = blog.payload.get("angle") or ""
    btns = ""
    for a, lab, desc in (("review", "후기형", "'후기' 블록"), ("howto", "방법·과정형", "'방법' 블록·스니펫"),
                         ("price", "가격·비용형", "'가격/비용' 블록")):
        if a == cur:
            btns += (f"<div class='px-3.5 py-2 rounded-xl bg-indigo-50 text-indigo-600 text-xs font-bold'>"
                     f"✓ {lab} (이 글)</div>")
        else:
            btns += (f"<button type=button onclick=\"angVar('{a}',this)\" "
                     f"class='px-3.5 py-2 rounded-xl bg-slate-100 hover:bg-indigo-100 text-slate-600 text-xs font-bold transition'>"
                     f"＋ {lab} <span class='text-slate-400 font-normal'>{desc}</span></button>")
    return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>스마트블록 다중진입 — 같은 소재, 다른 앵글"
            "<span class='text-emerald-600'> (한 키워드로 여러 블록 노리기)</span></div>"
            "<p class='text-xs text-slate-500 mb-3'>후기형·방법형·가격형은 각각 다른 검색결과 블록에 걸려요. "
            "같은 사진으로 다른 앵글 글을 만들어 진입 기회를 늘려요.</p>"
            f"<div class='flex flex-wrap gap-2'>{btns}</div>"
            "<div id='angMsg' class='text-xs text-slate-400 mt-2'></div>"
            f"<script>async function angVar(a,btn){{var m=document.getElementById('angMsg');m.textContent='생성 요청 중…';btn.disabled=true;"
            "try{var fd=new FormData();fd.append('piece_id','" + blog.id + "');fd.append('angle',a);"
            "var r=await fetch('/api/blog/angle-variant',{method:'POST',body:fd});var d=await r.json();"
            "if(d.error){m.textContent=d.error;btn.disabled=false;return;}"
            "m.innerHTML='✅ '+d.msg+' <a href=\"/me?tab=content\" class=\"text-indigo-500 font-bold underline\">내 콘텐츠 →</a>';"
            "}catch(e){m.textContent='요청 실패';btn.disabled=false;}}</script></div>")


def _naver_publish_confirm_box(tenant, blog, sec: str, cbtn: str, ok: str = "", err: str = "") -> str:
    """발행 확인 카드 — 이미 확인됨(✅) / 자동 확인 버튼(RSS) + 수동 URL 붙여넣기 폼."""
    banner = ""
    if ok:
        banner = f"<div class='bg-emerald-50 text-emerald-700 p-3 rounded-xl mb-3 text-sm'>✅ {esc(ok)}</div>"
    if err:
        banner = f"<div class='bg-rose-50 text-rose-600 p-3 rounded-xl mb-3 text-sm'>⚠️ {esc(err)}</div>"
    pub = db.get_blog_publish(blog.id)
    if pub:
        how = "RSS 자동 확인" if pub.get("matched_by") == "rss" else "직접 확인"
        return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>✅ 발행 확인됨 <span class='text-emerald-600'>({how})</span></div>"
                + banner
                + f"<a href='{esc(pub.get('published_url') or '')}' target=_blank rel=noopener class='text-sm font-bold text-emerald-600 break-all'>"
                f"{esc(pub.get('published_url') or '')} ↗</a>"
                f"<p class='text-xs text-slate-400 mt-2'>발행 시각: {esc(db.fmt_kst(pub.get('published_at')))} · 이 글의 순위를 추적 중이에요.</p></div>")
    inp = "flex-1 border border-slate-200 rounded-xl px-3 py-2.5 text-sm"
    # (자동화 2-3a) URL 붙여넣기 기본 제거 — RSS 자동 감지(2시간 크론)가 기본, 버튼은 즉시 1회 조회.
    # 매칭 실패 시에만 URL 입력 폴백(nvFb)을 노출한다.
    if getattr(tenant, "blog_id", ""):
        auto = ("<div class='flex items-center gap-2 mb-2'>"
                f"<button type=button onclick='nvChk(this)' class='{cbtn} bg-emerald-600 hover:bg-emerald-700'>발행 확인</button>"
                "<span id='nvChkMsg' class='text-xs text-slate-400'></span></div>"
                "<p class='text-xs text-slate-400 mb-1'>안 눌러도 2시간 내 자동 감지돼요 — 발행 후 순위 추적이 자동 시작됩니다.</p>"
                "<script>async function nvChk(btn){var m=document.getElementById('nvChkMsg');m.textContent='확인 중…';btn.disabled=true;"
                "try{var r=await fetch('/api/blog/check-published',{method:'POST'});var d=await r.json();"
                "if(d.error){m.textContent=d.error;btn.disabled=false;return;}"
                "if(d.synced){m.textContent='✅ 새 글 '+d.synced+'건 추적 시작!';setTimeout(function(){location.reload();},900);}"
                "else{m.textContent='아직 RSS에 안 잡혔어요 — 2시간 내 자동 감지돼요.';btn.disabled=false;"
                "var fb=document.getElementById('nvFb');if(fb)fb.classList.remove('hidden');}"
                "}catch(e){m.textContent='확인 실패';btn.disabled=false;var fb=document.getElementById('nvFb');if(fb)fb.classList.remove('hidden');}}</script>")
        fallback = (f"<form method=post action='/me/blog/published' id='nvFb' class='hidden flex gap-2 mt-2'>"
                    f"<input type=hidden name=piece_id value='{blog.id}'>"
                    f"<input name=url placeholder='자동 매칭이 안 되면 발행한 글 주소를 붙여넣어 주세요' class='{inp}'>"
                    f"<button class='{cbtn} bg-indigo-600 hover:bg-indigo-700 whitespace-nowrap'>등록</button></form>")
        return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>발행 완료하셨나요? <span class='text-emerald-600'>(순위 추적 자동 시작)</span></div>"
                + banner + auto + fallback + "</div>")
    return (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>발행 완료하셨나요? <span class='text-emerald-600'>(순위 추적 시작)</span></div>"
            + banner
            + "<p class='text-xs text-amber-600 mb-3'><a href='/me#blog' class='font-bold underline'>내 블로그를 연결</a>하면 "
            "발행 여부를 자동으로 확인해 드려요. 연결 전에는 아래에 발행 주소를 남겨주세요.</p>"
            + f"<form method=post action='/me/blog/published' class='flex gap-2'>"
            f"<input type=hidden name=piece_id value='{blog.id}'>"
            f"<input name=url placeholder='발행한 글 주소 붙여넣기 (https://blog.naver.com/...)' class='{inp}'>"
            f"<button class='{cbtn} bg-indigo-600 hover:bg-indigo-700 whitespace-nowrap'>발행함 ✓</button></form></div>")


def _md_inline(s: str) -> str:
    """인라인 마크다운(**강조**) → <b>. HTML 이스케이프 후 변환(주입 방지). 업종 중립."""
    import re as _r
    s = esc(s or "")
    s = _r.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = _r.sub(r"__(.+?)__", r"<b>\1</b>", s)
    return s


def _md_to_naver_html(md: str) -> str:
    """마크다운 본문 → 네이버 스마트에디터 붙여넣기용 HTML(PHASE D).
    본문 15px(font-family 미지정=에디터 기본체) / 소제목(##) 19px bold(h태그 대신 인라인 스타일) /
    강조 bold / 표는 실제 <table>(2열 규격) / [사진N]·구분선·이모지 유지. 업종 중립."""
    import re as _r
    lines = (md or "").split("\n")
    out, i, n = [], 0, len(lines)
    _P = "<p style=\"font-size:15px;line-height:1.8;margin:0 0 12px\">{0}</p>"
    while i < n:
        ln = lines[i].rstrip()
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s in ("---", "***", "___"):
            out.append("<hr style=\"border:none;border-top:1px solid #ddd;margin:16px 0\">")
            i += 1
            continue
        if s.startswith("## "):                      # 소제목 → 19px bold(인라인)
            out.append("<p style=\"font-size:19px;font-weight:bold;margin:18px 0 8px\">"
                       + _md_inline(s[3:].lstrip("# ").strip()) + "</p>")
            i += 1
            continue
        if s.startswith("### "):
            out.append("<p style=\"font-size:17px;font-weight:bold;margin:14px 0 6px\">"
                       + _md_inline(s[4:].strip()) + "</p>")
            i += 1
            continue
        if s.startswith("|") and i + 1 < n and _r.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            # 표 블록: 헤더행 + 구분행 + 데이터행 → 실제 <table>(스마트에디터 지원)
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body_rows = [r for j, r in enumerate(rows) if not (j == 1 and all(_r.match(r"^[:\-\s]*$", c) for c in r))]
            th = "<table style=\"border-collapse:collapse;width:100%;font-size:15px;margin:0 0 12px\">"
            for j, r in enumerate(body_rows):
                cells = "".join(
                    f"<{'th' if j == 0 else 'td'} style=\"border:1px solid #ccc;padding:6px 8px;"
                    f"{'background:#f5f5f5;font-weight:bold;' if j == 0 else ''}text-align:left\">{_md_inline(c)}</{'th' if j == 0 else 'td'}>"
                    for c in r)
                th += f"<tr>{cells}</tr>"
            out.append(th + "</table>")
            continue
        if _r.match(r"^(\-|\*|•)\s+", s):             # 불릿 — •로(에디터 리스트 처리 편차 회피)
            out.append(_P.format("• " + _md_inline(_r.sub(r"^(\-|\*|•)\s+", "", s))))
            i += 1
            continue
        if _r.match(r"^\d+\.\s+", s):                 # 번호 리스트 — 번호 유지 평문형
            out.append(_P.format(_md_inline(s)))
            i += 1
            continue
        out.append(_P.format(_md_inline(s)))          # 일반 문단
        i += 1
    return "".join(out)


def _md_to_plain(md: str) -> str:
    """마크다운 본문 → 순수 평문(text/plain 폴백). ## 삭제·** 삭제·표는 '항목: 내용' 줄바꿈 서술. 업종 중립."""
    import re as _r
    lines = (md or "").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("|") and i + 1 < n and _r.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            data = [r for j, r in enumerate(rows) if not (j == 1 and all(_r.match(r"^[:\-\s]*$", c) for c in r))]
            hdr = data[0] if data else []
            for r in data[1:] if len(data) > 1 else data:
                if len(r) == 2:
                    out.append(f"{r[0]}: {r[1]}")     # 2열 → 항목: 내용
                else:                                  # 3열+ → 첫 열을 라벨, 나머지는 헤더: 값
                    _kv = ", ".join(f"{hdr[k]}: {c}" for k, c in enumerate(r) if k >= 1 and c and k < len(hdr))
                    out.append(f"{r[0]} — {_kv}" if _kv else " · ".join(c for c in r if c))
            continue
        s = _r.sub(r"^#{1,4}\s*", "", s)              # ## 삭제
        s = _r.sub(r"\*\*(.+?)\*\*", r"\1", s)        # ** 삭제
        s = _r.sub(r"__(.+?)__", r"\1", s)
        out.append(s)
        i += 1
    return "\n".join(out).strip()


@app.get("/kit/{asset_id}/naver", response_class=HTMLResponse)
def kit_naver(request: Request, asset_id: str, ok: str = "", err: str = ""):
    """네이버 블로그 붙여넣기 전용 화면 — 제목/본문(사진 위치 표시)/사진 순서대로 다운."""
    import re as _re
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    pieces = _owned_pieces(u, asset_id)
    if not pieces:
        return HTMLResponse(_subscriber_page("접근 불가", "<p>내 콘텐츠가 아니에요.</p>"))
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    if not blog:
        return HTMLResponse(_subscriber_page("네이버 블로그", "<p>블로그 글이 없어요.</p>"))
    # (정합 2-1) 사진 정본 = 피스 중 '가장 많은' image_paths(부분 기록 피스에 밀리지 않게)
    _lists = [p.payload.get("image_paths") or [] for p in pieces]
    imgs = max(_lists, key=len) if _lists else []
    if len({len(x) for x in _lists if x}) > 1:
        import logging as _lg2
        _lg2.getLogger("shopcast.kit").warning("[kit] 사진 수 불일치 asset=%s lists=%s → 최대 목록 채택",
                                               asset_id, sorted({len(x) for x in _lists if x}))
    tenant = db.get_tenant(pieces[0].tenant_id)
    # ★ 화면 표시 = 다운로드와 동일하게 '글 흐름순' 재정렬(아무 순서로 올려도 글 순서대로 그리드·캡션·마커).
    #    글 텍스트 불변(상위노출 로직 유지) — 사진 순서·마커 번호만. 설명 태부족이면 원순서 유지.
    _cap_ordered = None
    try:
        _newbody, _order, _caps0 = _content_photo_layout(tenant, blog)
        if _order and len(_order) == len(imgs) and _order != list(range(len(imgs))):
            imgs = [imgs[i] for i in _order]
            blog.payload["body"] = _newbody                # 재번호 마커(사진 순서와 정합) — 메모리 한정
            if _caps0 and len(_caps0) == len(_order):
                _cap_ordered = [_caps0[i] for i in _order]
    except Exception:
        pass
    sname = tenant.name if tenant else "내 가게"
    title = blog.payload.get("selected_title") or blog.payload.get("title", "")   # PHASE B: 선택 제목
    body_marked = _re.sub(r"\[사진(\d+)\]", r"\n\n[📷 사진\1 위치]\n\n", blog.payload.get("body", "")).strip()
    body_marked = body_marked.replace("?fromRss=true&trackingCode=rss", "")   # 구세트 내부링크 추적 파라미터 정리
    _slot_refs = {int(n) for n in _re.findall(r"\[사진(\d+)\]", blog.payload.get("body", ""))}
    if _slot_refs and max(_slot_refs) > len(imgs):     # (정합 2-1) 슬롯 참조 > 사진 수 감지(상시)
        import logging as _lg3
        _lg3.getLogger("shopcast.kit").warning("[kit] 슬롯 참조(%d) > 사진 수(%d) asset=%s",
                                               max(_slot_refs), len(imgs), asset_id)
    photos = [im for im in imgs if im]                          # /dl이 R2로 서빙
    vid = next((p for p in pieces if p.kind.value == "short" and p.payload.get("video_path")), None)
    vurl = f"/dl/{asset_id}/{os.path.basename(vid.payload['video_path'])}" if vid else ""  # 블로그 본문 삽입용
    _nv = _nv_canonical(tenant, blog, _set_naver_video(pieces))   # PHASE 1: 영상 메타 canonical(제목·해시태그 오염 제거)
    def _media_exists(p_):
        """로컬 또는 R2 미러 존재 — 컨테이너 교체로 로컬만 사라진 경우 오탐 방지(근본수정 [결함2])."""
        if p_ and os.path.exists(p_):
            return True
        try:
            from app import storage as _st
            return bool(p_ and _st.r2_media_url(pieces[0].tenant_id, os.path.basename(p_)))
        except Exception:
            return False
    # 메타가 있으면 섹션은 항상 노출 — 파일이 로컬·R2 모두 없을 때만(구버전·정리됨) '다시 만들기'로 전환.
    _nv_playable = bool(_nv.get("path") and _media_exists(_nv["path"]))
    _fn_base = _seo_photo_name(tenant, blog)               # 이미지 SEO(5-1): 지역-업종-피사체
    photo_cells = "".join(
        f"<div class='relative'><img src='/dl/{asset_id}/{os.path.basename(im)}' class='w-full aspect-square object-cover rounded-xl border border-slate-200'>"
        f"<div class='absolute top-2 left-2 w-7 h-7 rounded-full bg-black/75 text-white text-sm font-bold flex items-center justify-center'>{i+1}</div>"
        f"<a href='/dl/{asset_id}/{os.path.basename(im)}' download='{_fn_base}-{i+1:02d}.jpg' class='absolute bottom-2 right-2 bg-white/95 text-slate-700 text-xs font-bold px-2 py-1 rounded-lg shadow hover:bg-white'>⬇ 저장</a></div>"
        for i, im in enumerate(photos))
    sec = "bg-white rounded-2xl border border-slate-200 shadow-sm p-5 mb-5"
    cbtn = "px-4 py-2.5 rounded-xl text-white text-sm font-bold transition active:scale-[.98]"
    # 네이버 영상 미디어/액션 — 재생 가능하면 미리보기+받기, 파일 소실이면 '다시 만들기'(재발 없이 항상 받을 길 제공)
    if _nv_playable:
        _nvsrc = f"/dl/{asset_id}/{os.path.basename(_nv.get('path', ''))}"
        _vfn = (_canonical_slug(tenant, blog) or "네이버영상") + "_네이버영상.mp4"   # 단일 슬러그(별도 filename 삭제)
        _nv_media_html = (
            f"<div class='mx-auto bg-black rounded-xl overflow-hidden mb-3' style='max-width:320px;aspect-ratio:9/16'>"
            f"<video src='{_nvsrc}' controls preload='none' class='w-full h-full' style='object-fit:contain'></video></div>"
            f"<div class='text-sm font-bold text-slate-800 mb-1'>{esc(_nv.get('title', ''))}</div>"
            f"<textarea id='nvVT' class='hidden'>{esc(_nv.get('title', ''))}</textarea>"
            f"<div class='text-xs text-slate-500 whitespace-pre-wrap mb-2'>{esc(_nv.get('desc', ''))}</div>"
            f"<textarea id='nvVD' class='hidden'>{esc(_nv.get('desc', ''))}</textarea>"
            "<div class='flex flex-wrap gap-2'>"
            f"<a href='{_nvsrc}' download='{esc(_vfn)}' class='{cbtn} bg-emerald-600 hover:bg-emerald-700 inline-block'>⬇ 영상 받기 (본문·클립 겸용 9:16)</a>"
            f"<button onclick=\"nvcp('nvVT',this)\" class='{cbtn} bg-indigo-600 hover:bg-indigo-700'>제목 복사</button>"
            f"<button onclick=\"nvcp('nvVD',this)\" class='{cbtn} bg-indigo-600 hover:bg-indigo-700'>설명 복사</button></div>"
            f"<div class='text-[11px] text-slate-400 mt-2'>파일명: {esc(_vfn)} · 길이 약 {int(_nv.get('duration_sec') or 0)}초</div>"
            # ★ 클립 업로드 강조(2026-08-01 실측): 통합검색 첫 화면은 플레이스·숏텐츠·'네이버 클립'이
            #   차지하고 블로그 글은 안 보이는 판이 많다(부산 썬팅·썬팅업체 등 3개 키워드 실측 0건).
            #   같은 영상을 클립에 올리는 것이 통합검색 진입의 실질 카드라 안내를 강하게 둔다.
            + ((lambda _c: (
                f"<div class='mt-3 bg-amber-50 border border-amber-200 rounded-xl p-3'>"
                f"<div class='text-sm font-bold text-amber-800 mb-1'>🎬 클립 전용 버전({int(_c.get('duration_sec') or 0)}초)이 따로 있어요</div>"
                "<div class='text-xs text-amber-700 mb-2'>클립은 짧고 첫 화면이 강해야 잘 퍼집니다 — 본문용(긴 버전)과 "
                "따로 만들어 뒀어요. <b>아래 클립 버전을 올리세요.</b></div>"
                f"<a href='/dl/{asset_id}/{os.path.basename(_c.get('path',''))}' "
                f"download='{esc(_c.get('filename') or 'clip.mp4')}' "
                f"class='{cbtn} bg-amber-600 hover:bg-amber-700 inline-block'>⬇ 클립용 영상 받기</a></div>")
               )(_nv.get("clip")) if (_nv.get("clip") or {}).get("path") else "")
            + "<div class='mt-3 bg-amber-50 border border-amber-200 rounded-xl p-3'>"
            "<div class='text-sm font-bold text-amber-800 mb-1'>📌 이 영상, 네이버 클립에도 꼭 올리세요</div>"
            "<div class='text-xs text-amber-700 leading-relaxed'>검색 첫 화면(통합검색)에는 <b>네이버 클립 칸</b>이 "
            "따로 있습니다. 블로그 글만으로는 그 자리에 들어가기 어렵지만, 클립은 올리기만 하면 노출 기회가 생겨요. "
            "<b>이미 만든 영상 그대로</b> 올리면 되니 추가 비용도 없습니다.<br>"
            "올리는 법: 네이버 앱 → 하단 <b>+</b> → <b>클립</b> → 위 영상 선택 → 제목·설명 붙여넣기 → 업로드</div></div>")
    else:
        _nv_media_html = (
            "<p class='text-xs text-amber-600 mb-3'>영상 파일이 정리돼 지금은 받을 수 없어요 — 아래 버튼으로 다시 만들면 바로 받을 수 있어요 (1~2분).</p>"
            f"<form method=post action='/kit/{asset_id}/regen-naver'>"
            f"<button class='{cbtn} bg-emerald-600 hover:bg-emerald-700'>🎬 네이버 영상 다시 만들기</button></form>")
    # 두-트랙 보정 진행 배너 — 병렬 보정이 아직이면 알림(사진은 같은 주소로 완성본 교체됨)
    _edit_banner = ""
    try:
        from app.services.ingest import photo_edit_pending as _pep
        if _pep(asset_id, pieces[0].tenant_id):
            _edit_banner = ("<div class='flex items-center gap-2 bg-amber-50 border border-amber-200 "
                            "rounded-xl px-3 py-2.5 mb-3 text-sm text-amber-700'>"
                            "<span class='inline-block w-4 h-4 border-2 border-amber-300 border-t-amber-600 "
                            "rounded-full animate-spin flex-shrink-0'></span>"
                            "사진 보정(워터마크 제거·개인정보 가림)을 마무리하는 중이에요 — "
                            "잠시 후 새로고침하면 최종 사진으로 바뀝니다. 다운로드는 완료 후 진행돼요.</div>")
    except Exception:
        pass
    # 🎬 발행 직전 영상 넛지(2026 D.I.A. 영상 삽입 가점) — 온디맨드 원칙 유지: 강요 없는 안내 1줄
    _vid_nudge = ""
    try:
        _has_nv0 = any(((p.payload or {}).get("naver_video") or {}).get("path")
                       for p in pieces if p.kind.value == "short")
        _vj_now = next((p.payload.get("video_job") for p in pieces if p.kind.value == "blog"), None) or {}
        if not _has_nv0 and _vj_now.get("status") not in ("registered", "running", "retrying"):
            _vid_nudge = ("<div class='flex items-center gap-2 bg-violet-50 border border-violet-100 rounded-xl "
                          "px-3.5 py-2.5 mb-3 text-sm text-violet-700'>💡 이 글에 <b>영상(9:16)</b>을 넣으면 "
                          "네이버 노출 가점이 있어요 — 아래 '네이버 영상 만들기'로 몇 분이면 됩니다. "
                          "<span class='text-violet-400'>그냥 발행해도 괜찮아요.</span></div>")
    except Exception:
        pass
    body = (
        "<a href='javascript:history.back()' class='inline-block text-sm text-slate-500 font-bold mb-2'>← 결과로</a>"
        + _edit_banner + _vid_nudge
        + f"<div class='text-sm text-indigo-500 font-bold'>{esc(sname)}</div>"
        "<h1 class='text-2xl font-extrabold text-slate-900 mb-1'>네이버 블로그에 올리기</h1>"
        "<p class='text-slate-400 text-sm mb-5'>① 제목·본문 복사해서 붙여넣기 → ② 사진을 순서대로 저장 → ③ 본문 <b>[📷 사진N 위치]</b>에 네이버 사진버튼으로 올리기</p>"
        # 근거 카드(trust PHASE 3) — 본문 위쪽, 접힘 기본(복붙 흐름 무간섭·읽기 전용)
        + (f"<div class='{sec} pt-3 pb-3'>{_tc}</div>" if (_tc := _trust_card_html(blog)) else "")
        # 워크플로우 안내(블로그템플릿 PHASE 4) — PC/모바일/둘다 상황별 흐름
        + _workflow_guide(sec)
        # 제목
        + f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>1. 제목</div>"
        f"<div class='text-lg font-extrabold text-slate-900 mb-3'>{esc(title)}</div>"
        f"<textarea id='nvT' class='hidden'>{esc(title)}</textarea>"
        f"<button onclick=\"nvcp('nvT',this)\" class='{cbtn} bg-indigo-600 hover:bg-indigo-700'>제목 복사</button></div>"
        # 본문 — 리치텍스트 복사(PHASE D): text/html(서식 유지) + text/plain(기호 제거) dual-format
        f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>2. 본문 <span class='text-emerald-600'>(붙여넣으면 소제목·굵기·표 서식 유지)</span></div>"
        f"<div class='bg-slate-50 rounded-xl p-4 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto mb-3'>{esc(body_marked)}</div>"
        f"<div id='nvHtml' style='position:absolute;left:-9999px' aria-hidden='true'>{_md_to_naver_html(body_marked)}</div>"
        f"<textarea id='nvPlain' class='hidden'>{esc(_md_to_plain(body_marked))}</textarea>"
        f"<button onclick=\"copyRich2('nvHtml','nvPlain',this)\" style='min-height:48px' class='{cbtn} bg-emerald-500 hover:bg-emerald-600 w-full'>전체 본문 복사 (서식 유지)</button></div>"
        # 블로그 태그(쉼표 구분, 원클릭 복사) — 클립 해시태그(# 5개)와 형식·용도 구분
        + ((lambda _tags: (lambda _ht: (
            f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>태그 "
            "<span class='text-emerald-600'>(복사해서 태그란에 붙여넣기)</span></div>"
            f"<div class='bg-slate-50 rounded-xl p-3 text-sm text-slate-700 leading-relaxed mb-2'>{esc(_ht)}</div>"
            f"<textarea id='nvTags' class='hidden'>{esc(_ht)}</textarea>"
            f"<button onclick=\"nvcp('nvTags',this)\" class='{cbtn} bg-emerald-500 hover:bg-emerald-600 w-full'>태그 복사</button>"
            "<p class='text-[11px] text-slate-400 mt-1.5'>발행 화면 맨 아래 태그란에 붙여넣으세요 — 태그로도 손님이 들어와요.</p></div>"
        ))(" ".join("#" + t.replace(" ", "") for t in _tags)) if _tags else "")(_blog_tags(tenant, blog)))
        # 사진
        + (f"<div class='{sec}'><div class='flex items-center justify-between mb-3'>"
           "<div class='text-xs font-bold text-slate-400'>3. 사진 <span class='text-slate-500'>(순서대로)</span></div>"
           f"<a href='/kit/{asset_id}/pack/{blog.id}' class='text-xs font-bold text-indigo-600'>⬇ 전체 ZIP 받기</a></div>"
           f"<div class='grid grid-cols-3 sm:grid-cols-4 gap-3'>{photo_cells}</div>"
           "<p class='text-xs text-slate-400 mt-2'>사진은 이 파일명 그대로, 캡션은 사진 아래 붙여넣으면 검색에 더 잘 잡혀요.</p>"
           + ("<p class='text-[11px] text-amber-600 mt-1'>📷 일부 사진에 판매 플랫폼 로고·워터마크가 보여요 — 직접 찍은 사진이 검색에 더 유리해요.</p>"
              if "[워터마크]" in ((blog.payload or {}).get("gen_source") or "") else "")
           + f"<form method=post action='/me/set/{asset_id}/photos' enctype='multipart/form-data' class='flex items-center gap-2 mt-2'>"
           "<input type=file name=photos accept='image/*' multiple required class='text-xs flex-1'>"
           f"<button class='{cbtn} bg-slate-700 hover:bg-slate-800 whitespace-nowrap'>사진 추가</button></form>"
           "<p class='text-[11px] text-slate-400 mt-1'>과정 사진(물세척·재단 등)을 더 올리면 AI가 슬롯·캡션·영상을 다시 맞춰드려요 — 글 내용은 그대로예요.</p>"
           + _caption_box(tenant, blog, len(photos), caps=_cap_ordered) + "</div>" if photos else "")
        # 네이버용 영상(통합 블록 2-4) — 구 '동영상도 본문에' 블록 흡수, 쇼츠·릴스는 채널 카드 전용
        + ((f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>4. 네이버용 영상 <span class='text-emerald-600'>(블로그 첨부 · 클립 겸용)</span></div>"
            "<p class='text-xs text-slate-500 mb-3'>이 영상을 <b>본문 첫 소제목 아래</b>에 넣으세요 — 15초+ 영상은 검색 가점(D.I.A.+). "
            "같은 영상을 네이버 클립에도 올리면 지면이 하나 더 생겨요.</p>"
            + _nv_media_html + "</div>") if _nv else
           # C-light: 영상 미생성이어도 섹션·사유·버튼 노출(조용한 부재 금지) — 기존 regen-naver 배선 재사용.
           (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>4. 네이버용 영상</div>"
            "<div class='text-xs text-slate-500 mb-2'>이 세트는 아직 <b>영상이 만들어지지 않았어요</b> — "
            "본문에 소제목(##)이 없거나 생성이 중단된 경우예요. 아래 버튼으로 지금 만들 수 있어요.</div>"
            f"<form method=post action='/kit/{asset_id}/regen-naver'>"
            f"<button class='{cbtn} bg-emerald-600 hover:bg-emerald-700'>🎬 네이버 영상 만들기</button></form></div>"))
        # 5. 발행 후 마무리 — 사진 6장 권장(#3) + 서치어드바이저 색인(#3)
        + (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>{'5' if _nv else '4'}. 발행 후 — 상위노출 마무리</div>"
           "<ul class='text-xs text-slate-600 space-y-1.5 mb-3 list-none'>"
           + (f"<li>사진은 <b>6~15장 구간</b>이 무난해요 (지금 {len(photos)}장) — 더 올리셔도 AI가 알아서 골라 배치해요.</li>"
              if len(photos) < 6 else f"<li>사진 {len(photos)}장 ✓ — 본문 슬롯은 AI가 최적 배치했고, 나머지도 아래 그리드·ZIP에 전부 있어요.</li>")
           + "<li>직접 찍은 동영상까지 넣으면 D.I.A.+ 가점.</li>"
           # (색인 버튼 제거) 서치어드바이저는 blog.naver.com 소유확인 불가 → 수동 색인 요청 접수 불가.
           # 실구현된 fresh_index 크론(30분 집중 확인) 사실만 안내한다.
           + "<li>발행 직후 <b>24시간은 저희가 30분마다 색인을 자동 확인</b>해요 — 확인되면 리포트에 '네이버가 글을 받았어요'로 표시돼요.</li></ul></div>")
        # 🗺 네이버 장소 컴포넌트 가이드(블로그템플릿 PHASE 3) — 고정정보 블록 위치
        + _naver_component_guide(tenant, blog, sec)
        # (자동화 2-3) 내부링크는 생성 단계에서 본문에 자동 포함, 앵글 다중진입은 자동 큐가 수행 — 수동 섹션 제거
        # 6. 발행 확인(블로그등록 PHASE 2) — 자동(RSS 매칭) + 수동(URL 붙여넣기) 병행
        + _naver_publish_confirm_box(tenant, blog, sec, cbtn, ok, err)
        # 토스트
        + "<div id='nvToast' class='fixed bottom-6 left-1/2 -translate-x-1/2 bg-slate-900 text-white text-sm font-bold px-5 py-3 rounded-xl shadow-xl opacity-0 pointer-events-none transition-opacity'>✅ 복사됨</div>"
        + "<script>function nvcp(id,btn){var t=document.getElementById(id);omCopy(t.value);"
        "var o=btn.textContent;btn.textContent='✅ 복사됨';var tt=document.getElementById('nvToast');tt.style.opacity='1';"
        "setTimeout(function(){btn.textContent=o;tt.style.opacity='0';},1600);}"
        # PHASE D — dual-format 복사: text/html(서식) + text/plain(기호제거 폴백). 미지원 브라우저는 평문 폴백+안내.
        "async function copyRich2(hid,pid,btn){var h=document.getElementById(hid),p=document.getElementById(pid);"
        "var o=btn.textContent;var tt=document.getElementById('nvToast');"
        "function done(m){btn.textContent=m;tt.textContent=m;tt.style.opacity='1';setTimeout(function(){btn.textContent=o;tt.style.opacity='0';tt.textContent='✅ 복사됨';},2200);}"
        "try{if(navigator.clipboard&&window.ClipboardItem){await navigator.clipboard.write([new ClipboardItem("
        "{'text/html':new Blob([h.innerHTML],{type:'text/html'}),'text/plain':new Blob([p.value],{type:'text/plain'})})]);"
        "done('✅ 서식까지 복사됨! 네이버 글쓰기에 붙여넣기');return;}}catch(e){}"
        "try{await omCopy(p.value);done('✅ 글 복사됨(이 폰은 평문 — 붙여넣고 소제목만 굵게)');}catch(e2){done('길게 눌러 복사');}}"
        "</script>")
    return HTMLResponse(_subscriber_page("네이버 블로그", body))


@app.post("/kit/{asset_id}/regen-blog")
def kit_regen_blog(request: Request, asset_id: str):
    """(사용자용) 📮 발행 게이트 재작성 — 80점 미달로 봉인된 블로그를 소유자가 버튼 한 번으로 재생성.
    ★ 백그라운드 전환(2026-07-31 실사고): 동기 1~2분 재생성이 Railway 게이트웨이 타임아웃('upstream
    error')에 걸림 — 글은 만들어지는데 사용자에겐 오류로 보이고 재클릭하면 비용 중복. 즉시 303 반환,
    진행 상태는 blog.payload.rewrite_job + /me/rewrite-status 폴링으로 표시."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return HTMLResponse(status_code=404)
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    if not blog:
        return RedirectResponse(f"/me?view={asset_id}", status_code=303)
    if _rewrite_running(blog.payload):
        return RedirectResponse(f"/me?view={asset_id}", status_code=303)   # 중복 클릭 = 무시(비용 보호)
    from datetime import datetime as _dt
    db.update_piece_payload(blog.id, {"rewrite_job": {"status": "running",
                                                      "ts": _dt.utcnow().isoformat()}})

    import copy as _cp
    _old_pl = _cp.deepcopy(blog.payload or {})         # 📸 재작성 전 스냅샷 — 더 나빠지면 되돌림
    _old_score = ((_old_pl.get("ranking_audit") or {}).get("score"))

    def _bg_rewrite():
        st, note = "failed", ""
        try:
            admin_regen_blog(asset_id)                 # 재생성 — 사진·다른 채널 불변
            from app.services import qualitycheck as _qcg
            _blog0 = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
            _src0 = (_blog0.payload.get("gen_source") if _blog0 else "") or ""
            _qcg.score_gate(asset_id, source=_src0)    # 재생성본도 게이트 재판정(미달이면 다시 봉인)
            st = "done"
            # ★ 더 나은 판 유지(2026-07-31 실사고: 재작성이 75→71점 — 나쁜 판으로 교체됐음):
            #   새 점수 < 기존 점수면 스냅샷 복원(비용은 이미 썼지만 글은 안 나빠지게).
            #   채널·영상 상태는 복원에서 제외(재작성 중 변했을 수 있는 라이브 상태).
            _b1 = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
            _new_score = (((_b1.payload or {}).get("ranking_audit") or {}).get("score")) if _b1 else None
            if (_b1 and isinstance(_old_score, int) and isinstance(_new_score, int)
                    and _new_score < _old_score):
                _keep = {k: (_b1.payload or {}).get(k)
                         for k in ("channel_status", "video_job", "image_paths") if k in (_b1.payload or {})}
                _b1.payload = {**_old_pl, **_keep}
                db.save_piece(_b1)
                note = f"재작성 {_new_score}점 < 기존 {_old_score}점 — 기존 글을 유지했어요"
                logging.getLogger("shopcast.ingest").warning(
                    "[kit-regen-blog] 재작성 점수 하락(%s→%s) — 스냅샷 복원 asset=%s",
                    _old_score, _new_score, asset_id)
        except Exception:
            logging.getLogger("shopcast.ingest").exception("[kit-regen-blog] 실패 asset=%s", asset_id)
        finally:
            try:
                _b2 = next((p for p in db.get_set_pieces(asset_id) if p.kind.value == "blog"), None)
                if _b2:
                    db.update_piece_payload(_b2.id, {"rewrite_job": {"status": st, "note": note,
                                                                     "ts": _dt.utcnow().isoformat()}})
            except Exception:
                pass
    import threading as _th_rw
    _th_rw.Thread(target=_bg_rewrite, daemon=True).start()
    return RedirectResponse(f"/me?view={asset_id}", status_code=303)


@app.get("/me/rewrite-status")
def me_rewrite_status(request: Request, asset_id: str = ""):
    """다시쓰기 진행 폴링 — running/done/failed (소유 검증)."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return JSONResponse({"ok": False}, status_code=404)
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    if not blog:
        return JSONResponse({"ok": True, "status": ""})
    _st = (blog.payload.get("rewrite_job") or {}).get("status") or ""
    if _st == "running" and not _rewrite_running(blog.payload):
        _st = "failed"                                 # 죽은 잡 — 폴링 화면이 새로고침해 버튼 복구
    return JSONResponse({"ok": True, "status": _st})


@app.get("/me/polish-status")
def me_polish_status(request: Request, asset_id: str = ""):
    """품질 보정(백그라운드) 진행 폴링 — running/done (소유 검증).
    2026-08-01: 글은 텍스트 완성 즉시 열리고 보정은 뒤에서 돈다 → 끝나면 화면이 스스로 갱신."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return JSONResponse({"ok": False}, status_code=404)
    blog = next((p for p in pieces if p.kind.value == "blog"), None)
    pj = ((blog.payload if blog else {}) or {}).get("polish_job") or {}
    _st = pj.get("status") or "done"                   # 기록 없음 = 옛 세트 → 진행 중 아님
    if _st == "running":                               # 죽은 잡(배포 재시작 등) 자동 해제 — 15분
        try:
            from datetime import datetime as _dps
            if (_dps.utcnow() - _dps.fromisoformat(pj.get("ts", ""))).total_seconds() > 900:
                _st = "done"
        except Exception:
            _st = "done"
    return JSONResponse({"ok": True, "status": _st})


@app.post("/kit/{asset_id}/regen-naver")
def kit_regen_naver(request: Request, asset_id: str):
    """(사용자용) 네이버용 영상 다시 만들기 — 파일이 정리돼 받을 수 없을 때 소유자가 직접 복구.
    admin_regen_naver 로직 재사용(글·사진·쇼츠 불변). 완료 후 네이버 페이지로 복귀."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return HTMLResponse(status_code=404)
    # ★ 비동기 전환(2026-07-31 upstream error 실사고): 동기 렌더(1~2분+)가 게이트웨이 타임아웃에
    #   걸리던 것을 기존 온디맨드 영상 머신(request_video_bundle: 백그라운드+진행 표시+폴링)으로 교체.
    ok = False
    try:
        _t = db.get_tenant(pieces[0].tenant_id)
        from app.services.ingest import request_video_bundle
        ok, _err = request_video_bundle(_t, asset_id, {"naver"}) if _t else (False, "tenant 없음")
    except Exception:
        logging.getLogger("shopcast.ingest").exception("[kit-regen-naver] 실패 asset=%s", asset_id)
    msg = ("ok=네이버 영상을 만드는 중이에요 — 1~2분 뒤 이 페이지에 자동으로 나타나요"
           if ok else "err=영상 다시 만들기에 실패했어요 — 잠시 후 다시 시도해 주세요")
    return RedirectResponse(f"/kit/{asset_id}/naver?{msg}", status_code=303)


@app.get("/dl/{asset_id}/{fname}")
def dl_media(request: Request, asset_id: str, fname: str):
    import re
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces or not re.fullmatch(r"[A-Za-z0-9._-]+", fname):
        return HTMLResponse(status_code=404)
    path = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), pieces[0].tenant_id, fname)
    if not os.path.exists(path):
        from app import storage as _st
        r2 = _st.r2_media_url(pieces[0].tenant_id, fname)   # 로컬 정리됨 → R2에서 서빙
        return RedirectResponse(r2, status_code=302) if r2 else HTMLResponse(status_code=404)
    ext = fname.rsplit(".", 1)[-1].lower()
    mt = {"mp4": "video/mp4", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mt, filename=fname)


CHKO = {"blog": "네이버블로그", "caption": "인스타그램", "x_post": "X", "marketplace": "판매콘텐츠"}


def _ch_folder(piece) -> str:
    if piece.kind.value == "short":
        return "유튜브쇼츠" if piece.channel.value == "youtube" else "인스타릴스"
    return CHKO.get(piece.kind.value, piece.kind.value)


def _piece_pack_entries(piece, imgs, prefix="", slug="", nv=None):
    """채널 하나의 (zip경로, 소스) 목록 — 글.txt + 사진 + 영상 한 묶음.
    이미지 파일명 슬러그는 세트 확정 슬러그(slug) 단일 소스 사용 — 미전달 시에만 자기 target_keywords 폴백.
    nv={path,filename}: 네이버용 영상(본문 첨부·클립 겸용 9:16) — 블로그 채널 키트에 함께 담김."""
    import re as _re2
    k, pl = piece.kind.value, piece.payload
    # 이미지 SEO — 파일명에 세트 확정 키워드(네이버·구글 이미지검색이 파일명을 읽음). 폴더/태그와 동일 소스.
    _kwbase = (slug or "").strip() or (
        _re2.sub(r'[\\/:*?"<>|\s]+', "", ((pl.get("target_keywords") or [""])[0] or "")).strip("_")[:30] or "사진")
    ent = []

    def add(name, src):
        ent.append((f"{prefix}{name}", src))
    if k == "blog":
        txt = f"[제목]\n{pl.get('selected_title') or pl.get('title','')}\n\n[본문]\n{pl.get('body','')}\n"
        if pl.get("tags"):
            txt += "\n[태그]\n" + " ".join(pl["tags"]) + "\n"
        add("네이버블로그_글.txt", ("text", txt))
        for i, im in enumerate(imgs, 1):
            add(f"{_kwbase}_{i}{os.path.splitext(im)[1] or '.jpg'}", im)
        _nvp = (nv or {}).get("path") or ""            # 네이버용 영상(본문 첨부·클립 겸용 9:16) 포함
        if _nvp:
            add(f"{_kwbase}_네이버영상.mp4", _nvp)
    elif k == "caption":
        add("인스타_캡션.txt", ("text", pl.get("text", "")))
        for i, im in enumerate(imgs, 1):
            add(f"{_kwbase}_{i}{os.path.splitext(im)[1] or '.jpg'}", im)
    elif k == "short" and piece.channel.value == "youtube":
        add("유튜브_제목설명.txt", ("text", f"[제목]\n{pl.get('title','')}\n\n[설명]\n{pl.get('narration','')}\n"))
        if pl.get("video_path"):
            add("유튜브쇼츠_영상.mp4", pl["video_path"])
    elif k == "short" and piece.channel.value == "instagram":
        if pl.get("text"):
            add("릴스_캡션.txt", ("text", pl["text"]))
        if pl.get("video_path"):
            add("인스타릴스_영상.mp4", pl["video_path"])
    elif k == "x_post":
        add("X_글.txt", ("text", pl.get("text", "")))
    elif k == "marketplace":
        pn = pl.get("product_names") or []
        txt = ("[상품명 후보 3안]\n" + "\n".join(f"{i + 1}. {n}" for i, n in enumerate(pn))
               + "\n\n[상세페이지]\n" + pl.get("detail_body", "")
               + (("\n\n[검색 태그]\n" + ", ".join(pl.get("tags") or [])) if pl.get("tags") else "")
               + (("\n\n[내 스토어 링크]\n" + pl["buy_url"]) if pl.get("buy_url") else ""))
        add(f"{pl.get('market', '마켓')}_판매콘텐츠.txt", ("text", txt))
        for i, im in enumerate(imgs, 1):
            add(f"{_kwbase}_{i}{os.path.splitext(im)[1] or '.jpg'}", im)
    return ent


def _set_slug(pieces) -> str:
    """세트 전체가 공유하는 확정 슬러그 — 블로그 피스(권위) 기준. 태그·에셋 파일명·폴더·zip 단일 소스."""
    if not pieces:
        return ""
    blogp = next((p for p in pieces if getattr(p.kind, "value", "") == "blog"), pieces[0])
    try:
        t = db.get_tenant(blogp.tenant_id)
    except Exception:
        t = None
    return _canonical_slug(t, blogp) if t else ""


def _nv_canonical(tenant, blog, nv: dict) -> dict:
    """PHASE 1 — 네이버 영상 메타(제목·설명·해시태그)를 canonical 키워드에서 재유도(낡은 저장값 오염 무시).
    표시·키트·게이트가 전부 이 결과를 참조. 업종 중립."""
    import re as _r
    from app.services import indschema as _isc
    if not nv:
        return nv
    canon = _canonical_keyword(tenant, blog)
    ind = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
    region = (blog.payload or {}).get("canonical_region")   # ★ canonical_region만(기초지역 누수 차단)
    if region is None:
        try:
            _hk = _isc.get_schema(getattr(tenant, "industry", ""), getattr(tenant, "biz_type", "local") or "local").get("allow_region_hook")
            region = seo.canonical_region(getattr(tenant, "region", "") or "", getattr(tenant, "biz_type", "local") or "local",
                                          getattr(tenant, "industry", ""), allow_region_hook=_hk, verify_volume=False)
        except Exception:
            region = ""
    region = region or ""
    title = f"{canon} 핵심만 정리했어요".strip()
    desc = (f"{canon} 관련 내용을 영상으로 정리했어요.\n{getattr(tenant,'name','')} · {region}\n"
            "자세한 내용은 블로그 본문에 있어요.")
    seeds = [canon.replace(" ", ""), (region + ind).replace(" ", ""), ind,
             (region.split()[0] if region.split() else "") + ind]
    tags = []
    for s in seeds:
        s = _r.sub(r"[^가-힣A-Za-z0-9]", "", s)
        if s and len(s) >= 2 and f"#{s}" not in tags:
            tags.append(f"#{s}")
    return {**nv, "title": title, "desc": desc + "\n" + " ".join(tags[:5]), "hashtags": tags[:5]}


def _kit_contamination_gate(tenant, pieces) -> dict:
    """★ PHASE 3 — 발행 산출물 전 텍스트 표면 오염 게이트(최후 방어선, 업종 중립, 단어경계).
    스키마 attribute_axes 속성 토큰이 (세트 컨텍스트 ∪ 본문 실등장)에 없이 어떤 표면(본문·태그·캡션·
    영상 제목/설명/해시태그·파일명·폴더)에 등장하면 오염 → 차단. 특정 토큰('레이') 예외처리 0.
    반환 {passed, violations:[{surface, token, snippet}], ctx}."""
    import re as _r
    from app.services import indschema as _isc
    blog = next((p for p in pieces if getattr(p.kind, "value", "") == "blog"), None)
    if not blog:
        return {"passed": True, "violations": [], "ctx": []}
    sch = _isc.get_schema(getattr(tenant, "industry", ""), getattr(tenant, "biz_type", "local") or "local")
    attr_vocab = [a for a in _isc.attribute_tokens(sch) if a]

    def _wb(tok, text):
        return bool(_r.search(r"(?<![가-힣])" + _r.escape(tok), text or ""))

    pl = blog.payload or {}
    body = pl.get("body") or ""
    title = pl.get("selected_title") or pl.get("title") or ""
    canon = _canonical_keyword(tenant, blog)
    gensrc = pl.get("gen_source") or ""                  # vision 분석(실사진 묘사) — 실 set 내용
    body_core = _body_core(body)                          # 관련글·CTA 제외(참조성 등장 배제)
    # ★ 허용 컨텍스트 = '현재 세트'만 — canonical 매물(제목) ∪ 본문 핵심부·vision 실등장. tenant 인벤토리 전체 아님.
    #   타매물(모닝) 토큰은 이 세트 본문/사진에 실제 등장할 때만 허용. 낡은 키워드 경로로 새어든 토큰(레이)은 차단.
    ctx = set()
    for a in attr_vocab:
        if _wb(a, title) or _wb(a, canon) or _wb(a, body_core) or _wb(a, gensrc):
            ctx.add(a)
    ctx = {x for x in ctx if x}
    # 표면 수집 — 본문은 핵심부(관련글·CTA 링크의 타매물 참조는 오염 아님). canonical 유도 영상 메타 사용.
    surfaces = {"본문": body_core, "제목": title, "파일명": _set_slug(pieces)}
    try:                                                  # 태그 = 재생성분(UI) + 저장분(키트 txt에 실림) 둘 다 스캔
        surfaces["태그"] = " ".join((_blog_tags(tenant, blog) or []) + list(pl.get("tags") or []))
    except Exception:
        pass
    try:
        surfaces["캡션"] = " ".join(c for c in (_photo_captions(tenant, blog, len(pl.get("image_paths") or [])) or []) if c)
    except Exception:
        pass
    _rawnv = _set_naver_video(pieces) or {}
    nv = _nv_canonical(tenant, blog, _rawnv)
    if nv:
        surfaces["영상제목"] = nv.get("title", "")
        surfaces["영상설명"] = nv.get("desc", "")
        surfaces["해시태그"] = " ".join(nv.get("hashtags") or [])
        surfaces["영상자막"] = " ".join(_rawnv.get("scene_texts") or [])   # 자막 오염도 영상 표면으로 격리
    # 속성 축 — 스키마 속성 토큰이 세트 컨텍스트에 없이 표면 등장하면 위반(레이 등)
    violations = []
    for name, text in surfaces.items():
        for a in attr_vocab:
            if _wb(a, text) and a not in ctx:
                violations.append({"surface": name, "token": a, "axis": "attr",
                                   "snippet": _r.sub(r"\s+", " ", text)[:80]})
    # ── 지역 축(PHASE 2) — 속성 축과 동일한 단일 규칙 틀. 기초지역(구·군) 지명이 canonical_region ∪
    #    주소 표기 블록에 없이 표면에 등장하면 위반(제목·태그·해시태그·영상 등). 파일명·폴더는 지역 허용축.
    _cores = seo.basic_region_cores(getattr(tenant, "region", "") or "")   # 예: 부산 기장군 → [기장]
    _creg = pl.get("canonical_region")
    if _creg is None:
        try:
            _hk = _isc.get_schema(getattr(tenant, "industry", ""), getattr(tenant, "biz_type", "local") or "local").get("allow_region_hook")
            _creg = seo.canonical_region(getattr(tenant, "region", "") or "",
                                         getattr(tenant, "biz_type", "local") or "local",
                                         getattr(tenant, "industry", ""), allow_region_hook=_hk, verify_volume=False)
        except Exception:
            _creg = ""
    _addr = (pl.get("fixed_info_block") or "")            # 찾아오는 길 주소 원문 — 여기의 기초지역은 정보(허용)
    _region_ctx = (_creg or "") + " " + _addr
    for name, text in surfaces.items():
        if name in ("파일명",):                            # 파일명은 지역 결합 허용(부산중고차)
            continue
        for core in _cores:
            if _wb(core, text) and not _wb(core, _region_ctx):
                violations.append({"surface": name, "token": core, "axis": "region",
                                   "snippet": _r.sub(r"\s+", " ", text)[:80]})
    if violations:
        import logging as _lg
        _lg.getLogger("shopcast.kit").warning("[오염게이트] 차단 %d건: %s", len(violations),
                                              [(v["surface"], v.get("token")) for v in violations][:8])
    return {"passed": not violations, "violations": violations, "ctx": sorted(ctx), "canonical_region": _creg}


def _set_naver_video(pieces) -> dict:
    """세트의 네이버용 영상 메타({path,filename}) — short 피스에 저장됨. 없으면 {}."""
    for p in (pieces or []):
        nv = (getattr(p, "payload", None) or {}).get("naver_video") or {}
        if nv.get("path"):
            return nv
    return {}


def _fetch_local_or_r2(path: str):
    """파일 바이트 — 로컬 → R2 S3 API(인증·재시도) → 공개 URL 폴백. 실패 시 None.
    실측 결함 수정: 공개 URL(r2.dev)만 쓰면 ZIP 16장 버스트에서 레이트리밋으로 12장 조용히 탈락."""
    try:
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        from app import storage as _st
        if path and _st.r2_configured():
            data = _st.fetch_bytes(path)               # S3 API 인증 경로(레이트리밋 없음, 3회 재시도)
            if data:
                return data
            import urllib.request                      # 최후 폴백: 공개 URL(구키 호환)
            key = os.path.relpath(path, _st.STORAGE_DIR).replace(os.sep, "/")
            url = os.environ["R2_PUBLIC_URL"].rstrip("/") + "/" + key
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})   # r2.dev가 기본 UA 차단
            return urllib.request.urlopen(req, timeout=25).read()
    except Exception:
        return None
    return None


def _wait_photo_edit(asset_id: str, tenant_id: str = "", timeout: int = 60) -> bool:
    """(두-트랙 안전장치) 병렬 사진 보정 미완료면 최대 timeout초 대기 — 워터마크·번호판이
    남은 원본이 다운로드로 나가는 것을 방지. 완료(또는 잡 없음) True, 타임아웃 False."""
    import time as _tm
    from app.services.ingest import photo_edit_pending
    t0 = _tm.time()
    while photo_edit_pending(asset_id, tenant_id):
        if _tm.time() - t0 > timeout:
            logging.getLogger("shopcast.kit").warning("[pack] 보정 대기 타임아웃 asset=%s", asset_id)
            return False
        _tm.sleep(2)
    return True


def _zip_bytes(entries) -> bytes:
    """ZIP을 메모리에서 생성(디스크 미사용). 로컬 삭제된 사진·영상은 R2에서 받아 포함.
    조용한 누락 금지: 회수 실패 파일이 있으면 안내 텍스트를 ZIP에 동봉 + 에러 로그."""
    import zipfile
    import io
    buf = io.BytesIO()
    _missing = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, src in entries:
            if isinstance(src, tuple) and src[0] == "text":
                z.writestr(arc, src[1])
            elif src:
                data = _fetch_local_or_r2(src)      # 로컬 → S3 API(재시도) → 공개 URL
                if data:
                    z.writestr(arc, data)
                else:
                    _missing.append(arc)
        if _missing:
            logging.getLogger("shopcast.kit").error("[pack] 파일 회수 실패 %d건: %s",
                                                    len(_missing), _missing[:5])
            z.writestr("⚠️ 일부 파일 누락 — 다시 다운로드해 주세요.txt",
                       "일시 오류로 아래 파일이 이번 ZIP에서 빠졌습니다. 다시 다운로드하면 보통 해결됩니다:\n"
                       + "\n".join(_missing))
    return buf.getvalue()


def _zip_response(data: bytes, filename: str):
    from urllib.parse import quote
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)})


def _safe_title(pieces) -> str:
    """다운로드 파일명용 — 콘텐츠 제목(블로그 제목 우선)에서 파일명 금지문자 제거."""
    import re
    t = next((p.payload.get("selected_title") or p.payload.get("title")
              for p in pieces if (p.payload.get("selected_title") or p.payload.get("title"))), "") or "올린다콘텐츠"
    t = re.sub(r'[\\/:*?"<>|\n\r\t]', "", t).strip()[:40]
    return t or "올린다콘텐츠"


# D-3: 표면별 격리 — 핵심 표면(본문·제목) 위반만 전체 차단, 비핵심(영상·태그·캡션 등)은 해당 표면만 제외.
_CORE_SURFACES = ("본문", "제목")
_VIDEO_SURFACES = ("영상제목", "영상설명", "해시태그", "영상자막")


def _contam_status(pieces) -> dict:
    """오염 표면 분류 → {core_block, dirty:set, video_dirty, violations}. 게이트는 그대로(판정 재사용)."""
    try:
        t = db.get_tenant(pieces[0].tenant_id) if pieces else None
        if not t:
            return {"core_block": False, "dirty": set(), "video_dirty": False, "violations": []}
        g = _kit_contamination_gate(t, pieces)
        if g.get("passed"):
            return {"core_block": False, "dirty": set(), "video_dirty": False, "violations": []}
        dirty = {v["surface"] for v in g.get("violations", [])}
        return {"core_block": any(s in _CORE_SURFACES for s in dirty),
                "dirty": dirty, "video_dirty": any(s in _VIDEO_SURFACES for s in dirty),
                "violations": g.get("violations", [])}
    except Exception:
        return {"core_block": False, "dirty": set(), "video_dirty": False, "violations": []}


def _contamination_block(pieces):
    """PHASE 3 최후 방어선 — ★ 핵심 표면(본문·제목) 오염만 전체 다운로드 차단(409). 비핵심 표면 오염은
    차단하지 않고(None 반환) 호출부가 해당 표면만 zip에서 제외(D-3 표면별 격리). 통과면 None."""
    st = _contam_status(pieces)
    if st["core_block"]:
        _core = [v for v in st["violations"] if v["surface"] in _CORE_SURFACES]
        _vs = ", ".join(f"{v['surface']}:{v['token']}" for v in _core[:8])
        return HTMLResponse(
            "<div style='font-family:sans-serif;max-width:520px;margin:60px auto;padding:24px'>"
            "<h2 style='color:#e11d48'>⚠ 오염 감지 — 키트 생성 보류</h2>"
            f"<p>글의 핵심(본문·제목)에 현재 매물과 무관한 속성이 섞여 있어 다운로드를 막았습니다: <b>{esc(_vs)}</b></p>"
            "<p>이 글을 <b>다시 생성</b>하면 정정된 매물 정보로 깨끗하게 만들어져요. "
            "재생성 후 다시 받아주세요.</p></div>", status_code=409)
    return None


def _ordered_imgs_for_pack(pieces, imgs):
    """다운로드 사진을 '글 흐름순'으로 재정렬 — 사용자가 아무 순서로 올려도 ZIP은 글 순서대로 번호.
    블로그 본문 기준 콘텐츠 매칭(_content_photo_layout). 블로그 피스 body도 재번호(이 요청 한정·미저장)."""
    blogp = next((p for p in pieces if p.kind.value == "blog"), None)
    if not (blogp and imgs):
        return imgs
    try:
        tnt = db.get_tenant(blogp.tenant_id)
        new_body, order, _caps = _content_photo_layout(tnt, blogp)
        if order and len(order) == len(imgs) and order != list(range(len(imgs))):
            blogp.payload["body"] = new_body          # 재번호 마커(사진 순서와 정합) — 메모리 한정
            return [imgs[i] for i in order]
    except Exception:
        pass
    return imgs


@app.get("/kit/{asset_id}/pack/{pid}")
def kit_pack(request: Request, asset_id: str, pid: str):
    """채널 1개 통째 ZIP(글+사진+영상)."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return HTMLResponse(status_code=404)
    piece = next((p for p in pieces if p.id == pid), None)
    if not piece:
        return HTMLResponse(status_code=404)
    _blk = _contamination_block(pieces)
    if _blk:
        return _blk
    _st = _contam_status(pieces)                              # D-3: 영상 표면 오염이면 영상만 제외(글·사진은 유지)
    _wait_photo_edit(asset_id, pieces[0].tenant_id)           # 병렬 보정 미완료 사진 유출 방지
    imgs = (next((p.payload.get("image_paths") for p in pieces
                 if p.kind.value == "blog" and p.payload.get("image_paths")), None)
            or next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or [])
    # ★ 블로그 피스 우선(실측 버그): 다듬기 병렬화로 저장 순서가 뒤섞여 X 피스(발행용 4장 제한)가
    #   먼저 잡히면 그리드·ZIP·재정렬이 전부 4장으로 좁아짐 — 16장 중 4장만 다운로드된 원인.
    imgs = _ordered_imgs_for_pack(pieces, imgs)               # 글 흐름순 사진 정렬
    _nv = None if _st["video_dirty"] else _set_naver_video(pieces)
    data = _zip_bytes(_piece_pack_entries(piece, imgs, slug=_set_slug(pieces), nv=_nv))
    return _zip_response(data, f"{_safe_title(pieces)}_{_ch_folder(piece)}.zip")


@app.get("/kit/{asset_id}/pack-all")
def kit_pack_all(request: Request, asset_id: str):
    """5채널 전체 ZIP — 채널별 폴더로 정리."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return HTMLResponse(status_code=404)
    _blk = _contamination_block(pieces)
    if _blk:
        return _blk
    _st = _contam_status(pieces)                              # D-3: 영상 표면 오염이면 영상만 제외(글·사진·타채널 유지)
    _wait_photo_edit(asset_id, pieces[0].tenant_id)           # 병렬 보정 미완료 사진 유출 방지
    imgs = (next((p.payload.get("image_paths") for p in pieces
                 if p.kind.value == "blog" and p.payload.get("image_paths")), None)
            or next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or [])
    # ★ 블로그 피스 우선(실측 버그): 다듬기 병렬화로 저장 순서가 뒤섞여 X 피스(발행용 4장 제한)가
    #   먼저 잡히면 그리드·ZIP·재정렬이 전부 4장으로 좁아짐 — 16장 중 4장만 다운로드된 원인.
    imgs = _ordered_imgs_for_pack(pieces, imgs)               # 글 흐름순 사진 정렬(아무 순서로 올려도 글 순서대로)
    _slug = _set_slug(pieces)
    _nv = None if _st["video_dirty"] else _set_naver_video(pieces)
    entries = []
    for p in pieces:
        entries += _piece_pack_entries(p, imgs, prefix=f"{_ch_folder(p)}/", slug=_slug, nv=_nv)
    data = _zip_bytes(entries)
    return _zip_response(data, f"{_safe_title(pieces)}_5채널전체.zip")


@app.get("/demo/{name}")
def demo_asset(name: str):
    """랜딩 데모/테스트 결과용 샘플 파일(사진/영상/음성)."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):   # 경로 조작 차단
        return HTMLResponse(status_code=404)
    path = os.path.join(os.path.dirname(__file__), "static", "demo", name)
    if not os.path.exists(path):
        return HTMLResponse(status_code=404)
    ext = name.rsplit(".", 1)[-1].lower()
    media = {"mp4": "video/mp4", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "png": "image/png", "mp3": "audio/mpeg"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media)


# ── 운영자 대시보드 ──────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin():
    drafts = db.list_pieces(ContentStatus.DRAFT)
    failed = db.list_pieces(ContentStatus.FAILED)
    published = db.list_pieces(ContentStatus.PUBLISHED)
    auto_shops = sum(1 for t in db.list_tenants() if (t.autonomy or 0) >= 1)
    cards = ("<div class='grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6'>"
             + stat_card("확인 필요(예외)", len(drafts) + len(failed), "amber")
             + stat_card("자동 발행", len(published), "emerald")
             + stat_card("자동화 가게", f"{auto_shops}/{len(db.list_tenants())}", "indigo")
             + stat_card("실패", len(failed), "rose") + "</div>")
    # 예외(사람 확인 필요) = 검수대기/실패를 세트로 묶어 표시
    sets = db.list_sets(statuses=["draft", "failed"])
    if not sets:
        exc = ("<div class='bg-white rounded-2xl border border-slate-100 p-8 text-center text-slate-400'>"
               "🎉 확인할 예외가 없습니다 — 자동 발행이 잘 돌고 있어요.</div>")
    else:
        exc = ""
        for s in sets:
            ps = [p for p in db.get_set_pieces(s["asset_id"])
                  if p.status in (ContentStatus.DRAFT, ContentStatus.FAILED)]
            if not ps:
                continue
            rep = next((p for p in ps if p.payload.get("text") or p.payload.get("title")), ps[0])
            preview = esc((rep.payload.get("text") or rep.payload.get("title") or "")[:64])
            chips = "".join(
                f"<span class='text-[11px] px-2 py-1 rounded-lg bg-slate-50 border border-slate-100 mr-1 mb-1 inline-block'>"
                f"{CHMAP.get(p.channel.value, p.channel.value)} {badge(p.status.value)}</span>" for p in ps)
            why = "점수 미달·반자동·발행실패 → 사람 확인"
            exc += (
                "<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-3 flex gap-4 items-start'>"
                f"<img src='/asset/{ps[0].id}' class='w-14 h-14 object-cover rounded-xl bg-slate-100 shrink-0'>"
                "<div class='flex-1 min-w-0'>"
                f"<div class='flex items-center gap-2 flex-wrap'><b class='text-slate-800'>{esc(s['tenant'])}</b>"
                f"<span class='text-xs text-slate-400'>{esc(s['created'])} · {len(ps)}건 예외</span></div>"
                f"<div class='text-sm text-slate-500 truncate mt-0.5'>{preview}…</div>"
                f"<div class='mt-2'>{chips}</div><div class='text-[11px] text-amber-600 mt-1'>⚠️ {why}</div></div>"
                f"<a href='/admin/set/{s['asset_id']}' class='px-4 py-2 bg-indigo-600 text-white text-xs font-semibold rounded-xl hover:bg-indigo-700 shrink-0'>처리</a></div>")
    # 자동 발행 로그(최근)
    log = ""
    for p in published[:12]:
        t = db.get_tenant(p.tenant_id)
        log += (f"<div class='flex items-center gap-2 text-xs py-1.5 border-b border-slate-50'>"
                f"<span class='text-emerald-500'>✅</span><b class='text-slate-600'>{esc(t.name if t else '')}</b>"
                f"<span class='text-slate-400'>{CHMAP.get(p.channel.value, p.channel.value)}</span>"
                f"<span class='text-slate-500 truncate flex-1'>{esc((p.payload.get('text') or p.payload.get('title') or '')[:40])}</span></div>")
    log_box = (f"<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mt-6'>"
               f"<div class='font-bold text-slate-700 mb-2 text-sm'>🤖 최근 자동 발행</div>"
               f"{log or '<p class=text-slate-400 text-sm>아직 자동 발행 내역이 없습니다.</p>'}</div>")
    head = "<h2 class='font-bold text-slate-700 mb-3'>⚠️ 확인 필요 (예외만)</h2>"
    return shell("review", "운영 현황", cards + head + exc + log_box,
                 subtitle="자동 발행 중 — 예외만 확인하세요")


@app.get("/admin/board", response_class=HTMLResponse)
def board(tenant: str = "", channel: str = "", status: str = "", q: str = "",
          date_from: str = "", date_to: str = "", page: int = 1):
    jobs = db.list_jobs(tenant_id=tenant or None, channel=channel or None,
                        status=status or None, q=q, date_from=date_from, date_to=date_to)
    tenants = db.list_tenants()
    # 통계
    def cnt(s):
        return sum(1 for j in jobs if j["status"] == s)
    cards = ("<div class='grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6'>"
             + stat_card("검수 대기", cnt("draft"), "amber")
             + stat_card("승인됨", cnt("approved"), "indigo")
             + stat_card("발행 완료", cnt("published"), "emerald")
             + stat_card("실패", cnt("failed"), "rose") + "</div>")
    # 상태 탭
    def tab(label, sval):
        on = sval == status
        cls = "bg-indigo-600 text-white" if on else "bg-white text-slate-500 border border-slate-200 hover:bg-slate-50"
        qp = f"?status={sval}" + (f"&channel={channel}" if channel else "") + (f"&tenant={tenant}" if tenant else "")
        return f"<a href='/admin/board{qp}' class='px-4 py-2 rounded-xl text-sm font-medium {cls}'>{label}</a>"
    tabs = ("<div class='flex flex-wrap gap-2 mb-4'>" + tab("전체", "")
            + "".join(tab(STATUS_KO[s], s) for s in ["draft", "approved", "scheduled", "published", "failed"]) + "</div>")
    # 필터
    topt = "<option value=''>전체 가게</option>" + "".join(
        f"<option value='{t.id}'{' selected' if t.id == tenant else ''}>{esc(t.name)}</option>" for t in tenants)
    chmap = {"instagram": "인스타그램", "naver_blog": "네이버 블로그", "youtube": "유튜브", "x": "X"}
    copt = "<option value=''>전체 채널</option>" + "".join(
        f"<option value='{c}'{' selected' if c == channel else ''}>{l}</option>" for c, l in chmap.items())
    sopt = "<option value=''>전체 상태</option>" + "".join(
        f"<option value='{s}'{' selected' if s == status else ''}>{STATUS_KO[s]}</option>" for s in STATUS_KO)
    inp = "border border-slate-200 rounded-xl px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-400 outline-none"
    filt = (f"<form method=get action='/admin/board' class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-5 flex flex-wrap items-center gap-2'>"
            f"<input name=q value=\"{esc(q)}\" placeholder='🔍 제목 검색' class='{inp} flex-1 min-w-[140px]'>"
            f"<select name=tenant class='{inp}'>{topt}</select>"
            f"<select name=channel class='{inp}'>{copt}</select>"
            f"<select name=status class='{inp}'>{sopt}</select>"
            f"<input type=date name=date_from value='{esc(date_from)}' class='{inp}'>"
            f"<span class='text-slate-300'>~</span>"
            f"<input type=date name=date_to value='{esc(date_to)}' class='{inp}'>"
            f"<button class='px-5 py-2 bg-indigo-600 text-white text-sm font-semibold rounded-xl hover:bg-indigo-700'>검색</button>"
            f"<a href='/admin/board' class='px-4 py-2 bg-slate-100 text-slate-600 text-sm rounded-xl hover:bg-slate-200'>초기화</a></form>")
    bulk = (f"<form method=post action='/admin/board/bulk' class='mb-3'>"
            f"<input type=hidden name=tenant value=\"{esc(tenant)}\"><input type=hidden name=channel value=\"{esc(channel)}\">"
            f"<button class='px-4 py-2 bg-emerald-600 text-white rounded-xl text-sm font-semibold hover:bg-emerald-700'>"
            f"🚀 우수(85+) 검수대기 일괄 승인·발행</button></form>")
    # 페이지네이션
    per = 20
    total = len(jobs)
    pages = max(1, (total + per - 1) // per)
    page = max(1, min(page, pages))
    page_jobs = jobs[(page - 1) * per: page * per]
    # 테이블
    head = ("<tr class='text-left text-xs text-slate-400 border-b border-slate-100'>"
            "<th class='px-4 py-3 font-semibold'>가게</th><th class='px-4 py-3 font-semibold'>채널</th>"
            "<th class='px-4 py-3 font-semibold'>제목</th><th class='px-4 py-3 font-semibold'>상태</th>"
            "<th class='px-4 py-3 font-semibold'>점수</th><th class='px-4 py-3 font-semibold'>예상 노출</th>"
            "<th class='px-4 py-3 font-semibold'>생성</th>"
            "<th class='px-4 py-3 font-semibold'>발행</th><th class='px-4 py-3 font-semibold text-right'>액션</th></tr>")
    rows = ""
    for j in page_jobs:
        sc = j["score"]
        sc_html = ("<span class='px-2 py-0.5 rounded-full text-xs font-bold "
                   + ("bg-emerald-50 text-emerald-600" if (sc or 0) >= 85 else
                      "bg-amber-50 text-amber-600" if (sc or 0) >= 70 else "bg-rose-50 text-rose-600")
                   + f"'>{sc}</span>") if sc is not None else "<span class='text-slate-300'>-</span>"
        rows += ("<tr class='border-b border-slate-50 hover:bg-slate-50/70 transition'>"
                 f"<td class='px-4 py-3 text-sm font-medium text-slate-700'>{esc(j['tenant'])}</td>"
                 f"<td class='px-4 py-3 text-xs text-slate-500'>{esc(chmap.get(j['channel'], j['channel']))}<br><span class='text-slate-300'>{esc(j['kind'])}</span></td>"
                 f"<td class='px-4 py-3 text-sm text-slate-700 max-w-[220px] truncate'>{esc(j['title'][:38])}</td>"
                 f"<td class='px-4 py-3'>{badge(j['status'])}<div class='text-[11px] text-slate-400 mt-0.5'>{STATUS_KO.get(j['status'],'')}</div></td>"
                 f"<td class='px-4 py-3'>{sc_html}</td>"
                 f"<td class='px-4 py-3 text-xs text-emerald-600 font-medium'>{esc(j.get('reach') or '-')}</td>"
                 f"<td class='px-4 py-3 text-xs text-slate-400'>{esc(j['created_at'])}</td>"
                 f"<td class='px-4 py-3 text-xs text-slate-400'>{esc(j['published_at'] or '-')}</td>"
                 f"<td class='px-4 py-3 text-right'><a href='/admin/review/{j['id']}' class='px-3 py-1.5 bg-slate-100 text-slate-700 text-xs font-semibold rounded-lg hover:bg-indigo-600 hover:text-white transition'>검수</a></td></tr>")
    if not page_jobs:
        rows = "<tr><td colspan=9 class='px-4 py-12 text-center text-slate-400'>조건에 맞는 콘텐츠가 없습니다.</td></tr>"
    # 페이지 네비
    def pl(pg):
        qp = (f"?page={pg}" + (f"&status={status}" if status else "") + (f"&channel={channel}" if channel else "")
              + (f"&tenant={tenant}" if tenant else "") + (f"&q={q}" if q else ""))
        return f"/admin/board{qp}"
    nav_pg = ""
    if pages > 1:
        prev = f"<a href='{pl(page-1)}' class='px-3 py-1.5 rounded-lg bg-slate-100 text-sm'>← 이전</a>" if page > 1 else ""
        nxt = f"<a href='{pl(page+1)}' class='px-3 py-1.5 rounded-lg bg-slate-100 text-sm'>다음 →</a>" if page < pages else ""
        nav_pg = f"<div class='flex items-center justify-center gap-3 mt-1'>{prev}<span class='text-sm text-slate-500'>{page} / {pages}</span>{nxt}</div>"
    table = (f"<div class='bg-white rounded-2xl border border-slate-100 shadow-sm overflow-hidden'>"
             f"<div class='overflow-x-auto'><table class='w-full'>{head}{rows}</table></div>"
             f"<div class='px-4 py-3 text-xs text-slate-400 border-t border-slate-50'>총 {total}건 · {page}/{pages} 페이지</div></div>{nav_pg}")
    return shell("board", "포스팅 현황판", cards + tabs + filt + bulk + table,
                 subtitle=f"전체 발행 작업 현황 · {total}건")


@app.post("/admin/board/bulk")
def board_bulk(tenant: str = Form(""), channel: str = Form("")):
    """필터 범위 내 점수 85+ 검수대기 → 승인·발행(반자동 채널은 건너뜀)."""
    jobs = db.list_jobs(tenant_id=tenant or None, channel=channel or None, status="draft")
    for j in jobs:
        if (j["score"] or 0) >= 85:
            p = db.get_piece(j["id"])
            if not p:
                continue
            pub = get_publisher(p.channel)
            if not pub.supports_auto_publish:   # 네이버 등 반자동은 일괄에서 제외
                continue
            db.set_piece_status(p.id, ContentStatus.APPROVED)
            p.status = ContentStatus.APPROVED
            publish_and_record(p)
    return RedirectResponse(f"/admin/board?tenant={tenant}&channel={channel}", status_code=303)


@app.get("/admin/set/{asset_id}", response_class=HTMLResponse)
def set_detail(asset_id: str):
    ps = db.get_set_pieces(asset_id)
    if not ps:
        return HTMLResponse("<p>없는 세트입니다.</p>", status_code=404)
    t = db.get_tenant(ps[0].tenant_id)
    rlo = sum((p.payload.get("reach") or {}).get("low", 0) for p in ps)
    rhi = sum((p.payload.get("reach") or {}).get("high", 0) for p in ps)
    top = (f"<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-5 mb-5 flex flex-wrap items-center gap-3'>"
           f"<img src='/asset/{ps[0].id}' class='w-14 h-14 rounded-xl object-cover'>"
           f"<div class='flex-1'><b class='text-slate-800'>{esc(t.name if t else '')}</b>"
           f"<div class='text-sm text-emerald-600 font-semibold'>👁 세트 합산 예상 도달 {rlo:,}~{rhi:,}</div></div>"
           f"<form method=post action='/admin/set/{asset_id}/approve-all'><button class='px-4 py-2 bg-slate-100 text-slate-700 text-sm font-semibold rounded-xl hover:bg-slate-200'>전체 승인</button></form>"
           f"<form method=post action='/admin/set/{asset_id}/publish-all'><button class='px-4 py-2 bg-emerald-600 text-white text-sm font-semibold rounded-xl hover:bg-emerald-700'>🚀 전체 발행</button></form></div>")
    rows = ""
    for p in ps:
        r = p.payload.get("reach") or {}
        sc = (p.payload.get("ranking_audit") or {}).get("score")
        prev = esc((p.payload.get("text") or p.payload.get("title") or "")[:80])
        rows += ("<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-3 flex gap-4 items-center'>"
                 f"<img src='/asset/{p.id}' class='w-14 h-14 rounded-xl object-cover bg-slate-100 shrink-0'>"
                 "<div class='flex-1 min-w-0'>"
                 f"<div class='flex items-center gap-2 mb-0.5'><b class='text-sm'>{CHMAP.get(p.channel.value, p.channel.value)} {p.kind.value}</b>"
                 f"{badge(p.status.value)}"
                 + (f"<span class='text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-semibold'>{sc}점</span>" if sc is not None else "")
                 + (f"<span class='text-xs text-slate-400'>👁 {r.get('label','')}</span>" if r else "") + "</div>"
                 f"<div class='text-sm text-slate-500 truncate'>{prev}…</div></div>"
                 f"<a href='/admin/review/{p.id}' class='px-4 py-2 bg-indigo-600 text-white text-xs font-semibold rounded-xl hover:bg-indigo-700 shrink-0'>상세 검수</a></div>")
    body = f"<a href='/admin' class='text-sm text-slate-400'>← 검수 목록</a><div class='mt-2'>{top}{rows}</div>"
    return shell("review", "세트 검수", body, subtitle=f"{t.name if t else ''} · {len(ps)}개 채널")


@app.post("/admin/set/{asset_id}/approve-all")
def set_approve_all(asset_id: str):
    for p in db.get_set_pieces(asset_id):
        if p.status in (ContentStatus.DRAFT,):
            db.set_piece_status(p.id, ContentStatus.APPROVED)
    return RedirectResponse(f"/admin/set/{asset_id}", status_code=303)


@app.post("/admin/set/{asset_id}/publish-all", response_class=HTMLResponse)
def set_publish_all(asset_id: str):
    results = []
    for p in db.get_set_pieces(asset_id):
        if p.status == ContentStatus.REJECTED:
            continue
        if p.status != ContentStatus.PUBLISHED:
            db.set_piece_status(p.id, ContentStatus.APPROVED)
            p.status = ContentStatus.APPROVED
            res = publish_and_record(p)
            results.append((p.channel.value, res))
    return RedirectResponse("/admin", status_code=303)


AUTONOMY_LABEL = {0: "수동 검수", 1: "점수게이트 자동(85+)", 2: "완전 자동"}


@app.get("/admin/shops", response_class=HTMLResponse)
def shops(ok: str = "", err: str = ""):
    base = os.environ.get("SHOPCAST_BASE", "http://127.0.0.1:8000")
    inp = "border border-slate-200 rounded-lg px-2 py-1.5 text-sm w-full"
    banner = (f"<div class='bg-emerald-50 text-emerald-700 p-3 rounded-xl mb-3 text-sm'>✅ {esc(ok)}</div>" if ok else "")
    banner += (f"<div class='bg-rose-50 text-rose-600 p-3 rounded-xl mb-3 text-sm'>⚠️ {esc(err)}</div>" if err else "")
    aopt0 = "".join(f"<option value='{lv}'>{lab}</option>" for lv, lab in AUTONOMY_LABEL.items())
    addform = (
        "<details class='bg-white rounded-2xl border border-slate-100 shadow-sm p-5 mb-4'>"
        "<summary class='font-bold text-slate-700 cursor-pointer'>➕ 새 고객(가게) 추가</summary>"
        "<form method=post action='/admin/shops/new' class='grid sm:grid-cols-2 gap-2 mt-3'>"
        f"<input name=name placeholder='상호 *' required class='{inp}'>"
        f"<input name=industry placeholder='업종 * (자유 입력 — 예: 꽃집, 헬스장, 치과)' required class='{inp}'>"
        f"<input name=region placeholder='지역 (예: 수원 영통)' class='{inp}'>"
        f"<select name=autonomy class='{inp}'>{aopt0}</select>"
        # ── 사업형태(분류축) ──
        f"<select name=biz_type class='{inp} sm:col-span-2 font-semibold'>"
        "<option value=local>🏪 동네 매장(소상공인) — 방문·예약 유도</option>"
        "<option value=seller>📦 온라인 셀러(쿠팡·11번가·스토어) — 구매 유도</option>"
        "<option value=hybrid>🔁 매장+온라인 동시</option></select>"
        f"<input name=phone placeholder='전화 (매장)' class='{inp}'>"
        f"<input name=hours placeholder='🕐 영업시간 (매장)' class='{inp}'>"
        f"<input name=address placeholder='주소 (매장)' class='{inp}'>"
        f"<input name=map_url placeholder='🗺 네이버 지도 링크 (매장)' class='{inp}'>"
        # ── 셀러 부가정보 ──
        f"<select name=marketplace class='{inp}'>"
        "<option value=''>🛒 마켓 선택 (셀러)</option><option value=coupang>쿠팡</option>"
        "<option value=11st>11번가</option><option value=smartstore>스마트스토어</option>"
        "<option value=gmarket>지마켓</option><option value=self>자사몰</option></select>"
        f"<input name=brand_name placeholder='🏷 브랜드/스토어명 (셀러)' class='{inp}'>"
        f"<input name=buy_url placeholder='🔗 상세페이지/스토어 URL (셀러)' class='{inp}'>"
        f"<input name=search_kw placeholder='🔎 검색어 유도 — 쿠팡 등 직링크 불가시 (셀러)' class='{inp}'>"
        "<button class='px-4 py-2 bg-indigo-600 text-white text-sm font-bold rounded-xl sm:col-span-2'>"
        "가게 추가 (업종 프로필 자동 생성)</button></form>"
        "<p class='text-xs text-slate-400 mt-2'>※ 업종 프리셋에 없으면 AI가 맞춤 프로필을 자동 생성합니다. "
        "사업형태(매장/셀러)에 따라 글 마무리(지도 vs 구매링크)·CTA·키워드가 자동으로 달라집니다. "
        "쿠팡은 외부 직링크 제약이 있어 '검색어 유도'를 권장합니다.</p>"
        "</details>")
    biz_meta = {"local": ("🏪 동네매장", "bg-emerald-100 text-emerald-700"),
                "seller": ("📦 온라인셀러", "bg-amber-100 text-amber-700"),
                "hybrid": ("🔁 매장+온라인", "bg-indigo-100 text-indigo-700")}
    mk_names = {"coupang": "쿠팡", "11st": "11번가", "smartstore": "스마트스토어",
                "gmarket": "지마켓", "self": "자사몰", "": ""}
    cards = ""
    for t in db.list_tenants():
        tok = db.tenant_token(t.id)
        link = f"{base}/u/{tok}"
        aopt = "".join(f"<option value='{lv}'{' selected' if (t.autonomy or 0) == lv else ''}>{lab}</option>"
                       for lv, lab in AUTONOMY_LABEL.items())
        bt = (t.biz_type or "local")
        blabel, bcls = biz_meta.get(bt, biz_meta["local"])
        mk = mk_names.get(t.marketplace or "", t.marketplace or "")
        biz_badge = (f"<span class='text-[11px] font-bold px-2 py-0.5 rounded-full {bcls}'>{blabel}"
                     + (f" · {esc(mk)}" if (bt in ('seller', 'hybrid') and mk) else "") + "</span>")
        bopt = "".join(f"<option value='{k}'{' selected' if bt == k else ''}>{lab.split(' ',1)[1] if ' ' in lab else lab}</option>"
                       for k, (lab, _c) in biz_meta.items())
        mopt = "".join(f"<option value='{k}'{' selected' if (t.marketplace or '') == k else ''}>{v or '마켓 선택'}</option>"
                       for k, v in mk_names.items())
        bizform = (
            f"<form method=post action='/admin/shops/{t.id}/classify' class='grid sm:grid-cols-2 gap-2 mt-2'>"
            f"<select name=biz_type class='{inp} font-semibold'>{bopt}</select>"
            f"<select name=marketplace class='{inp}'>{mopt}</select>"
            f"<input name=brand_name value=\"{esc(t.brand_name)}\" placeholder='🏷 브랜드/스토어명' class='{inp}'>"
            f"<input name=search_kw value=\"{esc(t.search_kw)}\" placeholder='🔎 검색어 유도(쿠팡 등)' class='{inp}'>"
            f"<input name=buy_url value=\"{esc(t.buy_url)}\" placeholder='🔗 상세페이지/스토어 URL' class='{inp} sm:col-span-2'>"
            "<button class='px-3 py-1.5 bg-amber-500 text-white text-xs font-semibold rounded-lg sm:col-span-2'>"
            "사업형태·구매정보 저장 (글 마무리/CTA 자동 전환)</button></form>")
        cards += (
            "<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-5 mb-3'>"
            "<div class='flex flex-wrap items-center gap-3 mb-3'>"
            f"<b class='text-slate-800'>{esc(t.name)}</b>"
            f"{biz_badge}"
            f"<span class='text-xs text-slate-400'>{esc(t.industry)} · {esc(t.region)}</span>"
            f"<a href='/u/{tok}' class='text-indigo-600 text-xs break-all'>{esc(link)}</a>"
            "<div class='ml-auto flex gap-2'>"
            f"<a href='/admin/connect/{t.id}' class='px-3 py-1.5 bg-slate-100 text-slate-700 text-xs font-semibold rounded-lg hover:bg-slate-200'>🔗 계정 연결</a>"
            f"<form method=post action='/admin/shops/{t.id}/remix' class='inline'><button class='px-3 py-1.5 bg-fuchsia-100 text-fuchsia-700 text-xs font-semibold rounded-lg hover:bg-fuchsia-200' title='잘 된 콘텐츠 포맷으로 새 변형 생성'>🔥 위너 리믹스</button></form>"
            f"<a href='/u/{tok}' class='px-3 py-1.5 bg-indigo-600 text-white text-xs font-semibold rounded-lg'>업로드</a></div></div>"
            # 자동화 레벨
            f"<form method=post action='/admin/shops/{t.id}/autonomy' class='flex items-center gap-2 mb-3'>"
            "<span class='text-xs font-semibold text-slate-500'>🤖 자동화</span>"
            f"<select name=level class='{inp} max-w-xs'>{aopt}</select>"
            "<button class='px-3 py-1.5 bg-slate-800 text-white text-xs rounded-lg'>적용</button>"
            "<span class='text-[11px] text-slate-400'>수동→점수게이트→완전자동 (검수 부담↓)</span></form>"
            # 연락처/장소(블로그 자동 삽입)
            f"<form method=post action='/admin/shops/{t.id}/profile' class='grid sm:grid-cols-2 gap-2'>"
            f"<input name=phone value=\"{esc(t.phone)}\" placeholder='전화번호' class='{inp}'>"
            f"<input name=hours value=\"{esc(t.hours)}\" placeholder='🕐 영업시간' class='{inp}'>"
            f"<input name=address value=\"{esc(t.address)}\" placeholder='주소' class='{inp}'>"
            f"<input name=map_url value=\"{esc(t.map_url)}\" placeholder='🗺 네이버 지도 링크' class='{inp}'>"
            "<button class='px-3 py-1.5 bg-slate-100 text-slate-700 text-xs font-semibold rounded-lg sm:col-span-2'>연락처·장소 저장 (블로그에 자동 삽입)</button></form>"
            + bizform +
            "</div>")
    return shell("shops", "가게 관리", banner + addform + cards, subtitle=f"등록 가게 {len(db.list_tenants())}곳")


@app.post("/admin/shops/new")
def shop_new(name: str = Form(""), industry: str = Form(""), region: str = Form(""),
             autonomy: int = Form(0), phone: str = Form(""), hours: str = Form(""),
             address: str = Form(""), map_url: str = Form(""), biz_type: str = Form("local"),
             marketplace: str = Form(""), brand_name: str = Form(""),
             buy_url: str = Form(""), search_kw: str = Form("")):
    if not (name.strip() and industry.strip()):
        return RedirectResponse("/admin/shops", status_code=303)
    from app.industries import ensure_profile
    t = db.create_tenant(name.strip(), industry.strip(), region.strip(), biz_type.strip() or "local")
    db.set_autonomy(t.id, autonomy)
    db.update_tenant_profile(t.id, phone, address, hours, map_url)
    db.update_tenant_classification(t.id, biz_type, marketplace, buy_url, search_kw, brand_name)
    ensure_profile(industry.strip())   # 프리셋에 없으면 AI가 업종 프로필 자동 생성·저장
    return RedirectResponse("/admin/shops", status_code=303)


@app.post("/admin/shops/{tid}/classify")
def shop_classify(tid: str, biz_type: str = Form("local"), marketplace: str = Form(""),
                  brand_name: str = Form(""), buy_url: str = Form(""), search_kw: str = Form("")):
    db.update_tenant_classification(tid, biz_type, marketplace, buy_url, search_kw, brand_name)
    return RedirectResponse("/admin/shops", status_code=303)


@app.post("/admin/shops/{tid}/remix")
def shop_remix(tid: str):
    """위너 리믹스 — 이 가게에서 가장 점수 높았던 콘텐츠의 소재로 새 변형을 재생성(검증된 포맷 재활용)."""
    t = db.get_tenant(tid)
    if not t:
        return RedirectResponse("/admin/shops", status_code=303)
    jobs = [j for j in db.list_jobs(tenant_id=tid, limit=200) if j.get("score")]
    if not jobs:
        return RedirectResponse("/admin/shops?err=리믹스할 콘텐츠가 아직 없어요", status_code=303)
    best = max(jobs, key=lambda j: j["score"])
    piece = db.get_piece(best["id"])
    imgs = [p for p in ((piece.payload.get("image_paths") if piece else []) or []) if p and os.path.exists(p)]
    if not imgs:
        return RedirectResponse("/admin/shops?err=원본 사진이 없어 리믹스 불가", status_code=303)
    try:
        files = [(open(p, "rb").read(), os.path.basename(p)) for p in imgs[:4]]
    except Exception:
        return RedirectResponse("/admin/shops?err=사진 읽기 실패", status_code=303)
    base_note = (piece.payload.get("title") or piece.payload.get("narration") or t.name)[:60]
    remix_note = f"[리믹스 — 잘 된 콘텐츠({best['score']}점) 새 버전. 다른 훅·각도로 변형] {base_note}"
    ingest_upload(t, files, remix_note)
    return RedirectResponse(f"/admin/shops?ok=리믹스 생성 완료(원본 {best['score']}점)", status_code=303)


@app.get("/admin/ops", response_class=HTMLResponse)
def ops(ok: str = "", err: str = ""):
    """대행 운영 관제탑 — 오늘 할 일 큐 + 가게별 파이프라인 + 주간 스케줄."""
    tenants = db.list_tenants()
    inp = "border border-slate-200 rounded-lg px-2 py-1.5 text-sm"
    total_draft = week_pub = behind = 0
    cards = ""
    for t in tenants:
        st = db.tenant_ops_stats(t.id)
        target = t.publish_schedule or 0
        tok = db.tenant_token(t.id)
        week_pub += st["pub_week"]; total_draft += st["draft"]
        if st["total"] == 0:
            light, bcls, status = "⚪", "bg-slate-100 text-slate-500", "소재 없음 — 사진 요청"
        elif st["draft"] > 0:
            light, bcls, status = "🔴", "bg-rose-100 text-rose-700", f"검수 대기 {st['draft']}건"
        elif target and st["pub_week"] < target:
            light, bcls, status = "🟡", "bg-amber-100 text-amber-700", f"발행 부족 {st['pub_week']}/{target}"
            behind += 1
        else:
            light, bcls, status = "🟢", "bg-emerald-100 text-emerald-700", "정상"
        sopts = "".join(f"<option value='{n}'{' selected' if target == n else ''}>"
                        f"{'미설정' if n == 0 else f'주 {n}회'}</option>" for n in (0, 1, 2, 3, 5, 7))
        review_btn = (f"<a href='/admin/board?tenant={t.id}&status=draft' class='px-3 py-1.5 bg-indigo-600 text-white text-xs font-bold rounded-lg'>검수 {st['draft']}건 →</a>"
                      if st["draft"] else "")
        cards += (
            "<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4'>"
            f"<div class='flex items-center gap-2 mb-1'><span class='text-lg'>{light}</span>"
            f"<b class='text-slate-800'>{esc(t.name)}</b>"
            f"<span class='text-[11px] text-slate-400'>{esc(t.industry or '업종 미설정')}</span>"
            f"<span class='ml-auto text-[11px] font-semibold px-2 py-0.5 rounded-full {bcls}'>{esc(status)}</span></div>"
            f"<div class='text-xs text-slate-500 mb-3'>이번주 발행 {st['pub_week']} · 검수대기 {st['draft']} · 누적 {st['total']}</div>"
            "<div class='flex flex-wrap gap-2 items-center'>"
            + review_btn
            + f"<a href='/u/{tok}' class='px-3 py-1.5 bg-emerald-500 text-white text-xs font-semibold rounded-lg'>사진 올리기</a>"
            + f"<a href='/admin/adpack/{t.id}' class='px-3 py-1.5 bg-indigo-100 text-indigo-700 text-xs font-semibold rounded-lg'>🎯 광고 소재팩</a>"
            + f"<form method=post action='/admin/shops/{t.id}/remix' class='inline'><button class='px-3 py-1.5 bg-fuchsia-100 text-fuchsia-700 text-xs font-semibold rounded-lg'>🔥 리믹스</button></form>"
            + f"<form method=post action='/admin/shops/{t.id}/schedule' class='inline flex items-center gap-1 ml-auto'>"
            + f"<span class='text-[11px] text-slate-400'>주간목표</span><select name=weekly class='{inp}'>{sopts}</select>"
            + "<button class='px-2 py-1.5 bg-slate-800 text-white text-xs rounded-lg'>저장</button></form>"
            "</div></div>")
    # 오늘 할 일 큐(검수 대기 세트)
    drafts = db.list_sets(statuses=["draft"], limit=100)
    if drafts:
        todo = "".join(
            "<div class='flex items-center gap-3 bg-white rounded-xl border border-rose-100 p-3'>"
            "<span>🔴</span>"
            f"<div><b class='text-sm'>{esc(d['tenant'] or '(가게)')}</b> "
            f"<span class='text-xs text-slate-400'>{d['n']}개 · {esc(d['created'])}</span></div>"
            f"<a href='/admin/set/{d['asset_id']}' class='ml-auto px-3 py-1.5 bg-indigo-600 text-white text-xs font-bold rounded-lg'>검수하기 →</a></div>"
            for d in drafts)
        todo_html = f"<div class='space-y-2'>{todo}</div>"
    else:
        todo_html = "<div class='bg-emerald-50 text-emerald-700 rounded-xl p-4 text-sm'>✅ 검수할 대기 건이 없습니다. 깔끔!</div>"
    banner = (f"<div class='bg-emerald-50 text-emerald-700 p-3 rounded-xl mb-4 text-sm'>✅ {esc(ok)}</div>" if ok else "")
    banner += (f"<div class='bg-rose-50 text-rose-600 p-3 rounded-xl mb-4 text-sm'>⚠️ {esc(err)}</div>" if err else "")
    stats = ("<div class='grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6'>"
             + stat_card("검수 대기(할 일)", total_draft, "rose")
             + stat_card("이번주 발행", week_pub, "emerald")
             + stat_card("발행 부족 가게", behind, "amber")
             + stat_card("등록 가게", len(tenants), "indigo") + "</div>")
    body = (banner + stats
            + "<h2 class='font-bold text-slate-700 mb-2'>📋 오늘 할 일 (검수 대기)</h2>" + todo_html
            + "<h2 class='font-bold text-slate-700 mt-6 mb-2'>🏪 가게별 상태</h2>"
            + f"<div class='grid sm:grid-cols-2 gap-3'>{cards or '<p class=\"text-slate-400 text-sm\">등록된 가게가 없습니다.</p>'}</div>")
    return shell("ops", "운영 관제탑", body, subtitle=f"대행 {len(tenants)}곳 · 오늘 검수 {total_draft}건")


@app.post("/admin/shops/{tid}/schedule")
def shop_schedule(tid: str, weekly: int = Form(0)):
    db.set_publish_schedule(tid, weekly)
    return RedirectResponse("/admin/ops?ok=주간 발행 목표를 저장했어요", status_code=303)


def _best_video_piece(tid: str):
    """그 가게의 광고로 쓸 숏폼(점수 높은 것 우선, 영상 있는 것)."""
    jobs = [j for j in db.list_jobs(tenant_id=tid, limit=300) if j.get("kind") == "short"]
    jobs.sort(key=lambda j: (j.get("score") or 0), reverse=True)
    for j in jobs:
        p = db.get_piece(j["id"])
        if p and p.payload.get("video_path") and os.path.exists(p.payload["video_path"]):
            return p
    return None


def _med(tid: str, path: str) -> str:
    return f"/admin/media/{tid}/{os.path.basename(path)}" if (path and os.path.exists(path)) else ""


@app.get("/admin/media/{tid}/{fname}")
def admin_media(tid: str, fname: str):
    import re
    if not re.fullmatch(r"[A-Za-z0-9._-]+", fname):
        return HTMLResponse(status_code=404)
    path = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tid, fname)
    if not os.path.exists(path):
        return HTMLResponse(status_code=404)
    ext = fname.rsplit(".", 1)[-1].lower()
    mt = {"mp4": "video/mp4", "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
          "zip": "application/zip", "mp3": "audio/mpeg"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mt)


@app.get("/admin/adpack/{tid}", response_class=HTMLResponse)
def adpack(tid: str):
    """광고 소재팩 — 6/15초 광고컷 + 규격 + 광고카피 3세트 + zip."""
    from app.services import adpack as ap
    t = db.get_tenant(tid)
    if not t:
        return HTMLResponse("없는 가게입니다.", status_code=404)
    piece = _best_video_piece(tid)
    if not piece:
        body = ("<a href='/admin/ops' class='text-sm text-slate-400'>← 관제탑</a>"
                "<div class='bg-amber-50 text-amber-700 p-4 rounded-2xl mt-3'>아직 광고로 만들 영상이 없어요. "
                f"먼저 <a href='/u/{db.tenant_token(tid)}' class='underline font-semibold'>사진을 올려 숏폼</a>을 생성하세요.</div>")
        return shell("ops", f"{esc(t.name)} · 광고 소재팩", body, subtitle="영상 없음")
    out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tid)
    # 광고컷(캐시)
    cuts = piece.payload.get("ad_cuts") or {}
    if not cuts or not all(os.path.exists(v) for v in cuts.values()):
        cuts = ap.build_cuts(piece.payload["video_path"], out_dir)
        piece.payload["ad_cuts"] = cuts
        db.save_piece(piece)
    # 광고카피(캐시)
    copies = piece.payload.get("ad_copy")
    if not copies:
        copies = ap.build_copy(t, piece)
        piece.payload["ad_copy"] = copies
        db.save_piece(piece)
    variants = piece.payload.get("video_variants") or {}
    # 영상 미리보기 타일
    vids = []
    for label, path in [("세로 원본(9:16)", piece.payload.get("video_path")),
                        ("광고컷 15초", cuts.get("15s")), ("광고컷 6초", cuts.get("6s")),
                        ("정사각 1:1", variants.get("square")), ("피드 4:5", variants.get("feed45"))]:
        url = _med(tid, path or "")
        if url:
            vids.append(f"<div class='bg-white rounded-2xl border border-slate-100 p-2'>"
                        f"<video src='{url}' controls muted class='w-full rounded-xl' style='max-height:360px'></video>"
                        f"<div class='text-xs font-semibold text-slate-600 text-center py-1'>{label}</div></div>")
    copy_cards = "".join(
        "<div class='bg-white rounded-2xl border border-slate-100 p-4'>"
        f"<div class='text-[11px] font-bold text-fuchsia-600 mb-1'>버전 {i+1}</div>"
        f"<div class='font-bold text-slate-800 mb-1'>{esc(c['headline'])}</div>"
        f"<p class='text-sm text-slate-600 mb-2'>{esc(c['body'])}</p>"
        f"<span class='text-xs bg-slate-800 text-white px-2 py-1 rounded'>{esc(c['cta'])}</span></div>"
        for i, c in enumerate(copies))
    guide = ("<div class='bg-indigo-50 text-indigo-700 rounded-2xl p-4 text-sm mt-4'>"
             "📣 <b>광고 돌리는 법</b>: 6초=인지형 / 15초=전환형. 메타 광고관리자(또는 유튜브 캠페인)에 "
             "위 영상 + 광고카피를 넣고 예산·타겟만 설정하면 됩니다. 규격(1:1·4:5·9:16)은 노출 위치별로 자동 매칭돼요.</div>")
    body = (f"<a href='/admin/ops' class='text-sm text-slate-400'>← 관제탑</a>"
            f"<div class='flex items-center gap-3 mt-2 mb-4'><h1 class='text-xl font-extrabold'>{esc(t.name)} 광고 소재팩</h1>"
            f"<a href='/admin/adpack/{tid}/zip' class='ml-auto bg-indigo-600 text-white font-bold text-sm px-4 py-2 rounded-xl'>⬇ 전체 zip 다운로드</a></div>"
            "<h2 class='font-bold text-slate-700 mb-2'>🎬 영상 소재 (광고용)</h2>"
            f"<div class='grid sm:grid-cols-2 lg:grid-cols-3 gap-3'>{''.join(vids)}</div>"
            "<h2 class='font-bold text-slate-700 mt-6 mb-2'>✍️ 광고 카피 (A/B/C)</h2>"
            f"<div class='grid sm:grid-cols-3 gap-3'>{copy_cards}</div>" + guide)
    return shell("ops", f"{esc(t.name)} · 광고 소재팩", body, subtitle="유료광고 바로 투입 가능")


@app.get("/admin/adpack/{tid}/zip")
def adpack_zip(tid: str):
    from app.services import adpack as ap
    t = db.get_tenant(tid)
    piece = _best_video_piece(tid)
    if not (t and piece):
        return HTMLResponse("소재 없음", status_code=404)
    out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tid)
    cuts = piece.payload.get("ad_cuts") or ap.build_cuts(piece.payload["video_path"], out_dir)
    variants = piece.payload.get("video_variants") or {}
    copies = piece.payload.get("ad_copy") or ap.build_copy(t, piece)
    files = {}
    if piece.payload.get("video_path"):
        files["세로_원본_9x16.mp4"] = piece.payload["video_path"]
    if cuts.get("15s"):
        files["광고_15초.mp4"] = cuts["15s"]
    if cuts.get("6s"):
        files["광고_6초.mp4"] = cuts["6s"]
    if variants.get("square"):
        files["정사각_1x1.mp4"] = variants["square"]
    if variants.get("feed45"):
        files["피드_4x5.mp4"] = variants["feed45"]
    for i, p in enumerate((piece.payload.get("image_paths") or [])[:4]):
        files[f"사진{i+1}.jpg"] = p
    zpath = ap.build_zip(out_dir, files, ap.copy_text(t, copies))
    return FileResponse(zpath, filename="광고소재팩.zip", media_type="application/zip")


# ── 결제(토스페이먼츠 정기결제) ─────────────────────────────
@app.get("/billing", response_class=HTMLResponse)
def billing(request: Request, plan: str = "pro"):
    from app.services import pay, pay_paddle
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    plan = plan if plan in pay.PLANS else "pro"
    info = pay.PLANS[plan]
    base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
    # 패들(Paddle) 우선 — 설정돼 있으면 오버레이 체크아웃
    if pay_paddle.configured():
        token = pay_paddle.client_token()
        pid = pay_paddle.price_id(plan)
        envset = "Paddle.Environment.set('sandbox');" if pay_paddle.env() == "sandbox" else ""
        email = esc((u.get("email") or "").replace("'", ""))
        inner = (
            "<div class='bg-white rounded-2xl border p-6 max-w-md mx-auto text-center'>"
            f"<div class='text-lg font-bold mb-1'>{esc(info['name'])}</div>"
            f"<div class='text-3xl font-extrabold my-2'>월 {info['price']:,}원</div>"
            "<p class='text-slate-500 text-sm mb-5'>카드로 매월 자동 결제. 언제든 해지 가능. (세금계산서·영수증 자동)</p>"
            "<button onclick='subscribe()' class='w-full bg-indigo-600 text-white font-bold py-3 rounded-xl'>구독 시작하기</button></div>"
            "<script src='https://cdn.paddle.com/paddle/v2/paddle.js'></script>"
            f"<script>{envset}Paddle.Initialize({{token:'{token}'}});function subscribe(){{Paddle.Checkout.open({{"
            f"items:[{{priceId:'{pid}',quantity:1}}],customer:{{email:'{email}'}},"
            f"customData:{{user_id:'{u['id']}',plan:'{plan}'}},"
            f"settings:{{successUrl:'{base}/me?ok='+encodeURIComponent('결제 완료! 곧 플랜이 활성화돼요 🎉')}}}});}}</script>")
        return HTMLResponse(_subscriber_page(f"{info['name']} 구독", inner))
    if not pay.configured():
        return HTMLResponse(_subscriber_page("결제 준비 중",
            "<div class='bg-amber-50 text-amber-700 p-5 rounded-2xl text-sm'>결제(토스페이먼츠)가 아직 연결되지 않았어요. "
            "운영자에게 문의하시면 플랜을 바로 열어드립니다. (TOSS 키 등록 후 자동 결제 가능)</div>"))
    ck = pay.client_key()
    customer_key = "cust_" + u["id"].replace("-", "")[:24]
    inner = (
        "<div class='bg-white rounded-2xl border p-6 max-w-md mx-auto text-center'>"
        f"<div class='text-lg font-bold mb-1'>{esc(info['name'])}</div>"
        f"<div class='text-3xl font-extrabold my-2'>월 {info['price']:,}원</div>"
        "<p class='text-slate-500 text-sm mb-5'>카드 등록 후 매월 자동 결제. 언제든 해지 가능.</p>"
        "<button onclick='subscribe()' class='w-full bg-indigo-600 text-white font-bold py-3 rounded-xl'>카드 등록하고 구독 시작</button></div>"
        "<script src='https://js.tosspayments.com/v1/payment'></script>"
        f"<script>const tp=TossPayments('{ck}');function subscribe(){{tp.requestBillingAuth('카드',"
        f"{{customerKey:'{customer_key}',successUrl:'{base}/billing/success?plan={plan}',failUrl:'{base}/billing/fail'}});}}</script>")
    return HTMLResponse(_subscriber_page(f"{info['name']} 구독", inner))


@app.get("/billing/success")
def billing_success(request: Request, plan: str = "pro", customerKey: str = "", authKey: str = ""):
    from app.services import pay
    from datetime import datetime, timedelta
    import uuid as _uuid
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not (authKey and customerKey):
        return RedirectResponse("/billing/fail", status_code=303)
    if not db.claim_once("toss:" + authKey):     # 새로고침·프리페치 이중청구 방지(B10)
        return RedirectResponse("/me?ok=이미 처리된 결제예요 🎉", status_code=303)
    issued = pay.issue_billing_key(authKey, customerKey)
    if issued.get("error") or not issued.get("billingKey"):
        return HTMLResponse(_subscriber_page("결제 등록 실패",
            f"<div class='bg-rose-50 text-rose-600 p-5 rounded-2xl'>카드 등록 실패: {esc(issued.get('error',''))} "
            "<a href='/billing?plan=pro' class='underline'>다시 시도</a></div>"))
    plan = plan if plan in pay.PLANS else "pro"
    info = pay.PLANS[plan]
    paid = pay.charge(issued["billingKey"], customerKey, info["price"], "ord_" + _uuid.uuid4().hex[:20], info["name"])
    if paid.get("error"):
        return HTMLResponse(_subscriber_page("결제 실패",
            f"<div class='bg-rose-50 text-rose-600 p-5 rounded-2xl'>결제 실패: {esc(paid.get('error',''))} "
            "<a href='/billing?plan=pro' class='underline'>다시 시도</a></div>"))
    expires = (datetime.utcnow() + timedelta(days=30)).isoformat()
    db.upsert_subscription(u["id"], plan, "active", issued["billingKey"], customerKey, info["price"], expires)
    db.set_user_plan(u["id"], plan)
    return RedirectResponse("/me?ok=결제 완료! 플랜이 활성화됐어요 🎉", status_code=303)


@app.get("/billing/fail")
def billing_fail(message: str = ""):
    return HTMLResponse(_subscriber_page("결제 취소",
        f"<div class='bg-rose-50 text-rose-600 p-5 rounded-2xl'>결제가 완료되지 않았어요. {esc(message)} "
        "<a href='/billing?plan=pro' class='underline font-semibold'>다시 시도</a></div>"))


@app.post("/webhook/paddle")
async def paddle_webhook(request: Request):
    """패들 구독 이벤트 웹훅 — 서명 검증 후 플랜 활성/해지. custom_data.user_id로 사용자 매칭."""
    import json
    from app.services import pay_paddle
    raw = (await request.body()).decode("utf-8", "ignore")
    sig = request.headers.get("Paddle-Signature", "")
    if not pay_paddle.verify_webhook(sig, raw):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    try:
        ev = json.loads(raw)
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    etype = ev.get("event_type", "")
    data = ev.get("data", {}) or {}
    cd = data.get("custom_data") or {}
    uid = cd.get("user_id")
    if uid and db.get_user(uid):
        from datetime import datetime, timedelta
        if etype in ("subscription.activated", "subscription.created", "transaction.completed"):
            # 플랜은 custom_data.plan(클라 조작 가능)이 아니라 실제 결제된 price id로 서버 검증(B4)
            plan = pay_paddle.plan_from_event(data)
            if not plan:
                import logging
                logging.warning("paddle webhook: price id 매칭 실패 — 플랜 변경 보류 uid=%s", uid)
                return JSONResponse({"ok": True, "note": "unrecognized price id"}, status_code=200)
            db.set_user_plan(uid, plan)
            exp = (datetime.utcnow() + timedelta(days=32)).isoformat()
            try:
                db.upsert_subscription(uid, plan, "active", billing_key=str(data.get("id", "")),
                                       customer_key=str(data.get("customer_id", "")), expires_at=exp)
            except Exception:
                pass
        elif etype in ("subscription.canceled", "subscription.paused", "subscription.past_due"):
            db.set_user_plan(uid, "free")
    return JSONResponse({"ok": True})


@app.post("/admin/reports/send-due")
def reports_send_due():
    """7일 순위 리포트 발송(성장 PHASE 2) — 발송은 스텁, 크론/운영자가 호출."""
    from app.services import growth
    return JSONResponse(growth.send_due_reports())


@app.post("/admin/reports/weekly")
def reports_weekly_now():
    """주간 블로그 리포트 즉시 발송(수동 트리거) — 스케줄러와 동일 로직(블로그등록 PHASE 4)."""
    from app.services import weekly_report
    return JSONResponse(weekly_report.send_all())


@app.post("/admin/billing/charge-due")
def billing_charge_due():
    """정기결제 갱신 — 만료 임박 구독을 빌링키로 자동 청구(운영자/크론이 호출)."""
    from app.services import pay
    from datetime import datetime, timedelta
    import uuid as _uuid
    done = failed = 0
    for s in db.subs_due_for_charge(within_days=1):
        info = pay.PLANS.get(s["plan"])
        if not info:
            continue
        r = pay.charge(s["billing_key"], s["customer_key"], info["price"],
                       "ord_" + _uuid.uuid4().hex[:20], info["name"])
        if r.get("error"):
            failed += 1
            db.upsert_subscription(s["user_id"], s["plan"], "past_due", s["billing_key"],
                                   s["customer_key"], info["price"], s["expires_at"])
        else:
            exp = (datetime.utcnow() + timedelta(days=30)).isoformat()
            db.upsert_subscription(s["user_id"], s["plan"], "active", s["billing_key"],
                                   s["customer_key"], info["price"], exp)
            done += 1
    return {"charged": done, "failed": failed}


# ── 구독자 관리 (운영자) ─────────────────────────────────
@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(ok: str = "", err: str = ""):
    from app.services import pay
    users = db.list_users()
    inp = "border border-slate-200 rounded-lg px-2 py-1.5 text-sm"
    pmeta = {"free": ("무료", "bg-slate-100 text-slate-600"),
             "self": ("셀프", "bg-indigo-100 text-indigo-700"),
             "agency": ("대행", "bg-amber-100 text-amber-700")}
    paid = sum(1 for u in users if (u.get("plan") or "free") != "free")
    rows = ""
    for u in users:
        plan = u.get("plan") or "free"
        lbl, cls = pmeta.get(plan, pmeta["free"])
        sub = db.get_subscription(u["id"])
        exp = (sub or {}).get("expires_at", "")[:10]
        substat = (f"~{exp}" if exp else "-")
        used = (f"무료 {u.get('free_used') or 0}/2" if plan == "free"
                else (f"이번달 {db.month_usage(u['id'])}" + (f"/{pay.PLANS.get(plan,{}).get('monthly')}" if pay.PLANS.get(plan,{}).get('monthly') else "")))
        popt = "".join(f"<option value='{k}'{' selected' if plan==k else ''}>{v[0]}</option>" for k, v in pmeta.items())
        rows += (
            "<tr class='border-t'>"
            f"<td class='py-2 pr-2'>{esc(u.get('email') or u.get('name') or '(회원)')}</td>"
            f"<td class='pr-2'><span class='text-xs font-bold px-2 py-0.5 rounded-full {cls}'>{lbl}</span></td>"
            f"<td class='pr-2 text-slate-500'>{used}</td>"
            f"<td class='pr-2 text-slate-400 text-xs'>{substat}</td>"
            f"<td class='pr-2 text-slate-400 text-xs'>{(u.get('created_at') or '')[:10]}</td>"
            "<td class='pr-2'>"
            f"<form method=post action='/admin/users/{u['id']}/plan' class='flex gap-1'>"
            f"<select name=plan class='{inp}'>{popt}</select>"
            "<button class='px-2 py-1 bg-slate-800 text-white text-xs rounded-lg'>변경</button></form></td>"
            f"<td><form method=post action='/admin/users/{u['id']}/reset'>"
            "<button class='px-2 py-1 bg-slate-100 text-slate-600 text-xs rounded-lg'>사용량 리셋</button></form></td></tr>")
    banner = (f"<div class='bg-emerald-50 text-emerald-700 p-3 rounded-xl mb-3 text-sm'>✅ {esc(ok)}</div>" if ok else "")
    stats = ("<div class='grid grid-cols-3 gap-4 mb-6'>"
             + stat_card("전체 회원", len(users), "indigo")
             + stat_card("유료 회원", paid, "emerald")
             + stat_card("무료 회원", len(users) - paid, "slate") + "</div>")
    table = ("<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 overflow-x-auto'>"
             "<table class='w-full text-sm'><thead><tr class='text-slate-400 text-xs text-left'>"
             "<th class='pb-2'>회원</th><th>플랜</th><th>사용량</th><th>구독만료</th><th>가입</th><th>플랜변경</th><th></th>"
             f"</tr></thead><tbody>{rows or '<tr><td class=py-6 colspan=7>회원이 없습니다.</td></tr>'}</tbody></table></div>"
             "<p class='text-xs text-slate-400 mt-3'>※ 결제(토스) 없이도 여기서 플랜을 수동 지정하면 즉시 유료처럼 이용됩니다(수동 청구 시).</p>")
    return shell("users", "구독자 관리", banner + stats + table, subtitle=f"회원 {len(users)}명 · 유료 {paid}")


@app.post("/admin/users/{uid}/plan")
def admin_user_plan(uid: str, plan: str = Form("free")):
    db.set_user_plan(uid, plan)
    if plan in ("basic", "pro", "self", "agency"):   # 운영자 수동 활성화(결제 없이 30일)
        from datetime import datetime, timedelta
        db.upsert_subscription(uid, plan, "active", "", "", 0,
                               (datetime.utcnow() + timedelta(days=30)).isoformat())
    return RedirectResponse("/admin/users?ok=플랜을 변경했어요", status_code=303)


@app.post("/admin/users/{uid}/reset")
def admin_user_reset(uid: str):
    db.reset_usage(uid)
    return RedirectResponse("/admin/users?ok=사용량을 리셋했어요", status_code=303)


@app.api_route("/admin/demo/reset", methods=["GET", "POST"])
def admin_demo_reset(ip: str = ""):
    """무료 체험 IP 사용량 초기화(ip 지정 시 해당 IP만, 없으면 전체)."""
    db.reset_demo_usage(ip.strip())
    return {"ok": True, "scope": ip.strip() or "전체", "message": "무료 체험 사용량을 초기화했어요"}


def _prune_old_media(tenant_id: str, keep_recent: int = 4) -> int:
    """오래된 세트의 영상·캐러셀 파일 삭제(디스크 확보). 텍스트·사진·최근 세트는 유지."""
    freed = 0
    try:
        sets = db.list_sets(tenant_id=tenant_id, limit=500)   # 최신순
    except Exception:
        return 0
    for s in sets[keep_recent:]:                              # 최근 keep_recent개 이후(오래된 것)
        for p in db.get_set_pieces(s["asset_id"]):
            targets = [p.payload.get("video_path")] + list(p.payload.get("carousel_paths") or [])
            for fp in targets:
                if fp and os.path.exists(fp):
                    try:
                        freed += os.path.getsize(fp)
                        os.remove(fp)
                    except Exception:
                        pass
    return freed


@app.get("/admin/whois")
def admin_whois(email: str = ""):
    """진단 — 이메일의 사용자·가게 온보딩 상태(중복 계정/미온보딩 확인)."""
    email = (email or "").lower().strip()
    out = {"email": email, "users": []}
    with db._conn() as c:
        rows = c.execute("SELECT id,email,tenant_id,plan,created_at FROM users WHERE email=?", (email,)).fetchall()
    for r in rows:
        ru = dict(r)
        t = db.get_tenant(ru.get("tenant_id")) if ru.get("tenant_id") else None
        ru["tenant_name"] = getattr(t, "name", None)
        ru["tenant_industry"] = getattr(t, "industry", None)
        ru["onboarded"] = bool((getattr(t, "industry", "") or "").strip())
        try:
            ru["sets"] = len(db.list_sets(tenant_id=ru.get("tenant_id"))) if ru.get("tenant_id") else 0
            ru["stores"] = len(db.list_user_stores(ru["id"]))
        except Exception:
            ru["sets"] = ru["stores"] = "?"
        out["users"].append(ru)
    out["user_count"] = len(out["users"])
    return out


@app.get("/admin/recent-users")
def admin_recent_users(n: int = 15):
    """진단 — 최근 가입 사용자(게스트/미온보딩 새 계정 양산 여부 확인)."""
    out = []
    with db._conn() as c:
        rows = c.execute("SELECT id,email,tenant_id,created_at FROM users ORDER BY created_at DESC LIMIT ?",
                         (n,)).fetchall()
    for r in rows:
        ru = dict(r)
        t = db.get_tenant(ru.get("tenant_id")) if ru.get("tenant_id") else None
        ru["onboarded"] = bool((getattr(t, "industry", "") or "").strip())
        ru["guest"] = str(ru.get("email", "")).endswith("@ollinda.guest")
        out.append({"email": ru["email"], "onboarded": ru["onboarded"],
                    "guest": ru["guest"], "created_at": ru["created_at"]})
    return {"count": len(out), "users": out}


def _referenced_media() -> set:
    """DB의 모든 피스 payload + tenant 프로필이 참조하는 로컬 파일 경로 집합(실경로 정규화)."""
    import json
    refs = set()

    def _add(v):
        if isinstance(v, str) and v.strip().startswith(("/", "storage")):
            refs.add(os.path.realpath(v.strip()))

    with db._conn() as c:
        rows = c.execute("SELECT payload FROM content_pieces").fetchall()
    for r in rows:
        try:
            pl = json.loads(r["payload"] or "{}")
        except Exception:
            continue
        stack = [pl]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
            else:
                _add(cur)
    return refs


@app.get("/admin/set/{asset_id}/result", response_class=HTMLResponse)
def admin_set_result_preview(asset_id: str):
    """진단(읽기 전용) — 소유자 관점의 결과 화면 실렌더(5채널 카드 실측용). 수정 없음."""
    with db._conn() as c:
        row = c.execute("SELECT tenant_id FROM content_pieces WHERE asset_id=? LIMIT 1", (asset_id,)).fetchone()
    if not row:
        return HTMLResponse("<pre>세트 없음</pre>", status_code=404)
    with db._conn() as c:
        ur = c.execute("SELECT id FROM users WHERE tenant_id=?", (row["tenant_id"],)).fetchone()
    u = db.get_user(ur["id"]) if ur else None
    if not u:
        return HTMLResponse("<pre>소유 사용자 없음</pre>", status_code=404)
    html = _result_html(u, asset_id)
    return HTMLResponse(html if html else "<pre>렌더 실패(소유 불일치)</pre>")


_PHANTOM_TOKENS = ("캐스퍼", "레이", "테슬라")
_REGEN_PRESERVE = ("image_paths", "channel_status", "_publish_blocked")   # meta 보존(사진·발행상태). 나머지=재생성 반영


def _regen_piece_common(asset_id: str, kind_val: str, channel_val: str = "", dry: bool = False) -> dict:
    """★ 전 피스 타입 공통 재생성 경로(blog/short/caption/x_post/marketplace) — 표면마다 도구 따로 만드는 구조 폐기.
    공통 규칙: 앵커=세트 그라운드트루스(블로그 gen_source=사진분석 우선, 없으면 vision 재분석) → 그 위에서
    재생성 → 4형제+테슬라 오염 없으면 제자리 교체(같은 piece id REPLACE). 재생성물 오염이면 미저장(409).
    발행상태·사진(image_paths·channel_status) 보존, 나머지 콘텐츠는 새것 반영. 사진 소실이면 blocked 표시(보존)."""
    import re as _r
    import json as _j
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    from app.registry import get_generator as _gg
    pieces = db.get_set_pieces(asset_id)
    target = next((p for p in pieces if p.kind.value == kind_val
                   and (not channel_val or p.channel.value == channel_val)), None)
    if not target:
        return {"ok": False, "error": f"피스 없음(kind={kind_val} ch={channel_val})", "status": 404}
    t = db.get_tenant(target.tenant_id)
    _a = db.get_asset(asset_id)
    if not (t and _a):
        return {"ok": False, "error": "tenant/asset 없음", "status": 404}
    old = dict(target.payload or {})
    _imgs = (next((p.payload.get("image_paths") for p in pieces if (p.payload or {}).get("image_paths")), [])
             or old.get("image_paths") or [])
    paths = _restore_media(target.tenant_id, _imgs)   # 디스크+R2 전량 복원(일부 디스크존재 시 누락 방지)
    if not paths:                                        # 사진 소실 → blocked 표시만(보존, 삭제·재생성 금지)
        if not dry:
            old["_publish_blocked"] = "phantom_no_photo"
            target.payload = old
            db.save_piece(target)
        return {"ok": True, "action": "photo_lost_marked_blocked", "asset_id": asset_id}
    # 앵커 = 세트 그라운드트루스: 블로그 gen_source(사진분석)를 재활용(빈 vision 문제 회피), 없으면 vision 재분석
    _blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    note = ((((_blog.payload or {}).get("gen_source")) if _blog else "") or (getattr(_a, "note", "") or "")).strip() \
        or "[자동 글감] 매물 실사진 세트"
    if "[사진" not in note:
        try:
            from app import vision                   # 이 모듈은 vision을 전역으로 들이지 않는다
            _an = vision.analyze_all(paths, t.industry)
            if _an:
                note += f"\n[사진 분석] {_an[:2500]}"
        except Exception:
            pass
    _a.note = note
    _a.content_type = old.get("content_type") or "sell"
    try:
        new = _gg(target.kind).generate(t, _a, paths)
    except Exception:
        import traceback
        return {"ok": False, "error": traceback.format_exc()[-400:], "status": 500}
    npl = (new.payload or {}) if new else {}
    if not npl:
        return {"ok": False, "action": "gen_empty", "status": 500}
    _phantom = [tok for tok in _PHANTOM_TOKENS
                if _r.search(r"(?<![가-힣])" + tok, _j.dumps(npl, ensure_ascii=False))]
    _new_title = npl.get("selected_title") or npl.get("title") or ""
    if _phantom:                                         # 재생성물도 오염 → 미저장(안전)
        return {"ok": False, "action": "still_contaminated", "phantom": _phantom, "new_title": _new_title, "status": 409}
    if dry:
        return {"ok": True, "action": "dry_clean", "kind": kind_val, "new_title": _new_title}
    # 옛 렌더 파일 경로 수집(교체로 고아가 될 것) — 디스크 만차 유발 방지. 사진(image_paths)은 절대 제외.
    def _vid_paths(d):
        out = []
        for k in ("video_path", "body_path"):
            if isinstance(d.get(k), str) and d[k].endswith(".mp4"):
                out.append(d[k])
        _nv = d.get("naver_video") or {}
        for k in ("path", "body_path"):
            if isinstance(_nv.get(k), str) and _nv[k].endswith(".mp4"):
                out.append(_nv[k])
        return out
    _old_vids = set(_vid_paths(old))
    for k, v in npl.items():                             # 새 콘텐츠 반영, meta(사진·발행상태)만 보존
        if k not in _REGEN_PRESERVE:
            old[k] = v
    old.pop("_publish_blocked", None)
    target.payload = old
    db.save_piece(target)
    _new_vids = set(_vid_paths(old))
    for _ov in (_old_vids - _new_vids):                  # 교체된 옛 영상만 삭제(사진 원본 불포함 — .mp4만)
        try:
            if _ov and os.path.exists(_ov) and _ov.endswith(".mp4"):
                os.remove(_ov)
        except Exception:
            pass
    return {"ok": True, "action": "regenerated", "kind": kind_val, "new_title": _new_title}


@app.get("/admin/tenant-photos")
def admin_tenant_photos(tid: str = "", check_r2: str = "1"):
    """(진단·vision 불필요) tenant 전 세트의 사진 정합 — 세트별 [asset_id / 사진 수 / 디스크 존재 / R2 존재].
    그랜저·모닝 세트(실측 각 14장) 행방·R2 정합 확정용. check_r2=1이면 각 사진 R2 HEAD(캡 20/세트)."""
    from app.domain.models import ContentKind as _CK
    if not tid:
        return JSONResponse({"ok": False, "error": "tid 필요"}, status_code=400)
    import requests as _rq
    from app import storage as _st
    rows, _seen = [], set()
    for s in db.list_sets(tenant_id=tid, limit=300):
        aid = s.get("asset_id")
        if not aid or aid in _seen:
            continue
        _seen.add(aid)
        blog = next((p for p in db.get_set_pieces(aid) if p.kind == _CK.BLOG), None)
        if not blog:
            continue
        imgs = (blog.payload or {}).get("image_paths") or []
        on_disk = sum(1 for x in imgs if x and os.path.exists(x))
        in_r2 = None
        if check_r2 == "1" and imgs:
            in_r2 = 0
            for x in imgs[:20]:
                try:
                    url = _st.r2_media_url(blog.tenant_id, os.path.basename(x))
                    if url and _rq.head(url, timeout=8).status_code == 200:
                        in_r2 += 1
                except Exception:
                    pass
        _title = (blog.payload or {}).get("selected_title") or (blog.payload or {}).get("title") or ""
        rows.append({"asset_id": aid, "photos": len(imgs), "on_disk": on_disk, "in_r2": in_r2,
                     "title": _title[:40]})
    rows.sort(key=lambda r: -r["photos"])
    return JSONResponse({"ok": True, "tid": tid, "sets": rows})


@app.get("/admin/set/{asset_id}/catalog")
def admin_set_catalog(asset_id: str):
    """PHASE 2-A: 사진 카탈로그(디렉터의 눈) — 세트 사진 R2 복원 → vision.build_catalog. V3 검증용.
    반환 {n_photos, catalog:[{id,subject,part,text,shot,flags}], blocked?}. 카탈로그 부실이면 blocked."""
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    t = db.get_tenant(blog.tenant_id)
    raw = (next((p.payload.get("image_paths") for p in pieces if (p.payload or {}).get("image_paths")), [])
           or [])
    paths = _restore_media(blog.tenant_id, raw)   # 디스크+R2 합쳐 전량 복원(일부 디스크존재 시 나머지 R2 누락 버그 수정)
    if not paths:
        return JSONResponse({"ok": True, "blocked": "photo_lost", "n_photos": 0,
                             "note": "사진 소실 — 재업로드 후 재시도(대체 이미지 금지)."})
    from app import vision as _vzc0
    cat = _vzc0.build_catalog(paths, getattr(t, "industry", "") or "")
    if getattr(_vzc0, "_CATALOG_CREDIT_EXHAUSTED", False):    # 크레딧 고갈 → 사용자 안내(조용한 실패 금지)
        return JSONResponse({"ok": False, "blocked": "vision_credit",
                             "note": "사진 분석 API 크레딧이 소진된 것 같아요 — 크레딧을 확인/충전한 뒤 다시 시도해 주세요.",
                             "n_photos": len(paths), "catalog_n": len(cat)}, status_code=402)
    if not cat or len(cat) < max(1, len(paths) // 2):        # 카탈로그 부실 → 디렉터 콜 금지(앵커 게이트 원칙)
        return JSONResponse({"ok": True, "blocked": "catalog_poor", "n_photos": len(paths),
                             "catalog_n": len(cat), "note": "카탈로그 부실 — 영상 보류(재분석 필요).",
                             "_raw": getattr(_vzc0, "_CATALOG_LAST_RAW", "")})
    return JSONResponse({"ok": True, "n_photos": len(paths), "catalog_n": len(cat), "catalog": cat})


@app.get("/admin/set/{asset_id}/storyboard")
def admin_set_storyboard(asset_id: str, channel: str = "naver"):
    """PHASE 2-B: 디렉터 콘티 생성 — 세트 사진 카탈로그 + 본문 → director.build_storyboard(계약 render_v1).
    V4(콘티 원문)·V5(line→사진 부위 대조) 검증용. 카탈로그/본문 부실이면 blocked(억지 생성 금지)."""
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    from app.services import director as _dir
    from app.generators.video import ShortVideoGenerator as _SVG
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    t = db.get_tenant(blog.tenant_id)
    pl = blog.payload or {}
    body = pl.get("body") or ""
    raw = pl.get("image_paths") or []
    paths = _restore_media(blog.tenant_id, raw)   # 디스크+R2 전량
    if not paths:
        return JSONResponse({"ok": True, "blocked": "photo_lost", "note": "사진 소실 — 재업로드 후."})
    try:
        from app import vision as _vzc
        cat = _vzc.build_catalog(paths, getattr(t, "industry", "") or "")
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "error": "catalog: " + traceback.format_exc()[-400:]}, status_code=500)
    if getattr(_vzc, "_CATALOG_CREDIT_EXHAUSTED", False):
        return JSONResponse({"ok": False, "blocked": "vision_credit",
                             "note": "사진 분석 API 크레딧이 소진된 것 같아요 — 확인/충전 후 다시 시도해 주세요."},
                            status_code=402)
    if not cat or len(cat) < max(1, len(paths) // 2):
        return JSONResponse({"ok": True, "blocked": "catalog_poor", "catalog_n": len(cat),
                             "note": "카탈로그 부실 — 영상 보류."})
    canon = _canonical_keyword(t, blog)
    # 세트 실값(data_card 전용) — 기존 추출기 재사용
    try:
        from app.services import indschema as _isc
        _sch = _isc.get_schema(getattr(t, "industry", "") or "", getattr(t, "biz_type", "local") or "local")
        _dvals = _SVG._extract_data_points(body, pl.get("gen_source") or "", _sch,
                                           getattr(t, "biz_type", "local") or "local")
    except Exception:
        _dvals = []
    try:
        sb = _dir.build_storyboard(body, cat, canon, channel=channel, data_values=_dvals)
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "error": "director: " + traceback.format_exc()[-400:],
                             "catalog": cat}, status_code=500)
    if not sb:
        return JSONResponse({"ok": True, "blocked": "storyboard_failed",
                             "note": "콘티 생성 실패(재시도 후) — 현행 로직 폴백 대상.",
                             "_fail": getattr(_dir, "_SB_LAST_FAIL", ""), "catalog": cat})
    # V5 대조: line → 배정 사진 부위
    _by_id = {c["id"]: c for c in cat}
    mapping = []
    for s in sb.get("scenes", []):
        sh = s.get("shot") or {}
        if "photo_id" in sh:
            _c = _by_id.get(sh["photo_id"], {})
            mapping.append({"role": s.get("role"), "line": s.get("line", "")[:40],
                            "photo_id": sh["photo_id"], "part": _c.get("part", ""),
                            "crop": sh.get("crop"), "reason": sh.get("reason", "")[:50]})
        else:
            mapping.append({"role": s.get("role"), "line": s.get("line", "")[:40], "card": sh.get("card")})
    return JSONResponse({"ok": True, "n_photos": len(paths), "canonical": canon,
                         "catalog": cat, "storyboard": sb, "line_photo_mapping": mapping,
                         "escalation_trace": getattr(_dir, "_SB_TRACE", [])})


@app.get("/admin/set/{asset_id}/render-storyboard")
def admin_render_storyboard(asset_id: str, channel: str = "naver", price: str = "", mileage: str = "",
                            backend: str = "", gorender_url: str = ""):
    """2-C 콘티→렌더 어댑터 실행 — catalog→director→ShortVideoGenerator.render_storyboard.
    콘티 존재 시에만 어댑터, 없으면 blocked(호출부가 기존 경로 폴백). 렌더 큐(RENDER_SEM)·디스크 하한 게이트 경유.
    반환: 디렉터판 영상 URL + 씬별 [콘티 지정 vs 렌더 실행] 대조 로그."""
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    from app.services import director as _dir
    from app.generators import video as _vid
    from app.strategies import resolve_strategy
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    t = db.get_tenant(blog.tenant_id)
    pl = blog.payload or {}
    body = pl.get("body") or ""
    raw = pl.get("image_paths") or []
    paths = _restore_media(blog.tenant_id, raw)   # 디스크+R2 전량 복원
    if not paths:
        return JSONResponse({"ok": True, "blocked": "photo_lost", "note": "사진 소실 — 재업로드 후."})
    try:
        from app import vision as _vzc
        cat = _vzc.build_catalog(paths, getattr(t, "industry", "") or "")
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "error": "catalog: " + traceback.format_exc()[-400:]}, status_code=500)
    if getattr(_vzc, "_CATALOG_CREDIT_EXHAUSTED", False):
        return JSONResponse({"ok": False, "blocked": "vision_credit",
                             "note": "사진 분석 크레딧 소진 — 확인/충전 후 재시도."}, status_code=402)
    if not cat or len(cat) < max(1, len(paths) // 2):
        return JSONResponse({"ok": True, "blocked": "catalog_poor", "catalog_n": len(cat),
                             "note": "카탈로그 부실 — 어댑터 보류(기존 경로 폴백)."})
    canon = _canonical_keyword(t, blog)
    # ★ VG3: 판매가는 딜러 명시값만. price 파라미터(딜러 직접 입력) 최우선, 없으면 gen_source→body에서 해석.
    #   어디에도 없으면 ''=가격 카드 금지(서류 출고가 승격 차단).
    _gsrc = pl.get("gen_source") or ""
    _sale = (price or "").strip() or _vid._resolve_sale_price(_gsrc, body)
    _mile = (mileage or "").strip()   # 주행거리 canonical(딜러 명시) — 전 표면 단일화 기준
    try:
        from app.services import indschema as _isc
        _sch = _isc.get_schema(getattr(t, "industry", "") or "", getattr(t, "biz_type", "local") or "local")
        _dvals = _vid.ShortVideoGenerator._extract_data_points(
            body, _gsrc, _sch, getattr(t, "biz_type", "local") or "local", sale_price=_sale)
    except Exception:
        _dvals = []
    sb = _dir.build_storyboard(body, cat, canon, channel=channel, data_values=_dvals)
    if not sb:                                    # ★ 콘티 없으면 어댑터 미가동 — 기존 경로가 폴백(자동)
        return JSONResponse({"ok": True, "blocked": "no_storyboard", "note": "콘티 없음 — 기존 렌더 경로 폴백.",
                             "_fail": getattr(_dir, "_SB_LAST_FAIL", "")})
    # catalog id = paths의 1-기반 인덱스 → 사진 경로 매핑(_restore_media 경유 경로)
    img_by_id = {c["id"]: paths[c["id"] - 1] for c in cat if 1 <= c.get("id", 0) <= len(paths)}
    strat = resolve_strategy(t)
    kws = [canon] if canon else []
    # ★ 렌더 백엔드 어댑터(python|go|shadow) — 분기는 어댑터 안에만. RENDER_SEM은 어댑터가 관리.
    from app.services import render_backend as _rb
    vp, note, dur, cover, compare, _bmeta = _rb.render(
        sb, img_by_id, kws, t, strat, title=(pl.get("title") or canon),
        sale_price=_sale, mileage=_mile, mode_override=backend, url_override=gorender_url)
    if not vp:
        return JSONResponse({"ok": True, "blocked": "render_failed", "note": note, "compare": compare,
                             "backend": _bmeta})
    vurl = f"/admin/media/{t.id}/{os.path.basename(vp)}"
    curl = f"/admin/media/{t.id}/{os.path.basename(cover)}" if cover else ""
    return JSONResponse({"ok": True, "video_url": vurl, "cover_url": curl, "note": note,
                         "duration_sec": dur, "n_scenes_directed": len(sb.get("scenes", [])),
                         "n_scenes_rendered": len([c for c in compare if c.get("dur")]),
                         "canonical": canon, "sale_price": _sale or "(미명시 — 가격 카드 없음)",
                         "mileage": _mile or "(미명시 — 단일화 안 함)",
                         "compare": compare, "backend": _bmeta,
                         "escalation_trace": getattr(_dir, "_SB_TRACE", [])})


@app.get("/admin/render-shadow-log")
def admin_render_shadow_log(limit: int = 50):
    """gorender shadow 병행운전 비교 로그(V6) — 세트별 [py vs go] 해상도·길이·씬수·크기·렌더시간."""
    from app.services import render_backend as _rb
    rows = _rb.shadow_log(limit=limit)
    return JSONResponse({"ok": True, "backend": _rb.backend(), "gorender_url": _rb.GORENDER_URL,
                         "n": len(rows), "rows": rows})


@app.post("/admin/pii-test")
async def admin_pii_test(request: Request, photo: UploadFile = File(...)):
    """문서 PII 마스킹 검증 — 이미지 업로드 → detect_personal_info(고해상·식별번호) + mask_personal_info →
    검출 박스 + 마스킹본 URL 반환(전후 대조). 발행 전 '누락 0' 실증용."""
    data = await photo.read()
    try:
        import base64 as _b64
        import shutil as _sh
        import tempfile as _tf
        from app import vision as _vz
        from app.media import photo_boost as _pb
        with _tf.TemporaryDirectory(prefix="piitest_") as work:   # /tmp — 만차 /data 볼륨 회피
            orig = os.path.join(work, "orig.jpg")
            with open(orig, "wb") as f:
                f.write(data)
            # OCR 진단(문서 PII 검출 0 원인 규명)
            import subprocess as _sp2
            _tp = os.environ.get("TESSDATA_PREFIX", "")
            _dbg = {"tesseract": bool(_sh.which("tesseract")), "tessdata_prefix": _tp,
                    "kor_exists": os.path.exists(os.path.join(_tp, "kor.traineddata")) if _tp else None}
            try:
                _oc = os.path.join(work, "ocrdbg")
                _rr = _sp2.run(["tesseract", orig, _oc, "-l", "kor+eng", "tsv"], capture_output=True, timeout=90)
                _tsv = open(_oc + ".tsv", encoding="utf-8").read() if os.path.exists(_oc + ".tsv") else ""
                _dbg["ocr_words"] = sum(1 for ln in _tsv.splitlines()[1:]
                                        if len(ln.split("\t")) >= 12 and ln.split("\t")[11].strip())
                _dbg["stderr"] = _rr.stderr.decode("utf-8", "ignore")[-200:]
                # 등록번호 누락 원인 규명: '370'/'4358' 포함 토큰과 같은 줄 이웃 토큰 원시 샘플
                _samp = []
                for ln in _tsv.splitlines()[1:]:
                    c = ln.split("\t")
                    if len(c) >= 12 and c[11].strip() and ("370" in c[11] or "4358" in c[11] or "다" in c[11]):
                        _samp.append({"t": c[11].strip(), "line": (c[2], c[3], c[4]),
                                      "x": c[6], "w": c[8], "conf": c[10]})
                _dbg["tok_sample"] = _samp[:12]
            except Exception as _e:
                _dbg["ocr_error"] = repr(_e)[:150]
            _dbg["vision_configured"] = _vz.configured()
            _dbg["has_anthropic_key"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
            try:      # blurworker(YOLO) 배선 진단 — 번호판·얼굴 마스킹이 워커 경유인지 확정
                from app.services import blur_client as _bc
                _dbg["blur_url"] = bool(os.environ.get("BLUR_WORKER_URL"))
                _dbg["blur_configured"] = _bc.configured()
                _wb = _bc.detect(orig) if _bc.configured() else None
                _dbg["blur_detect"] = ("none(폴백)" if _wb is None else f"{len(_wb)}건")
                if _wb:
                    _dbg["blur_boxes"] = [{"type": x["type"], "box": [round(x[k], 3) for k in ("x0", "y0", "x1", "y1")]} for x in _wb[:6]]
            except Exception as _be:
                _dbg["blur_error"] = repr(_be)[:150]
            try:
                _pi = list(_vz.detect_personal_info(orig))
            except Exception as _ve:
                _pi = []; _dbg["personal_info_error"] = repr(_ve)[:200]
            _dbg["personal_info_n"] = len(_pi)
            boxes = _pi + list(_vz.detect_document_pii(orig))
            try:
                _pl = list(_vz.detect_plates_vision(orig))
                _dbg["plate_tile_n"] = len(_pl)
                boxes += _pl
            except Exception as _pe:
                _dbg["plate_tile_error"] = repr(_pe)[:200]
            _dbg["vision_raw"] = getattr(_vz, "_LAST_VISION_RAW", "")[-900:]
            masked = os.path.join(work, "masked.jpg")
            _sh.copy(orig, masked)
            _pb._MASK_LAST_LOG = []
            n = _pb.mask_personal_info(masked)
            with open(masked, "rb") as f:
                mbytes = f.read()
            # 오버레이(엔카 뷰어 UI·로고) 탐지·제거 진단
            try:
                _ov_det = _vz.detect_overlay(orig)
                _dbg["overlay_detect"] = {"present": _ov_det.get("present"), "type": _ov_det.get("type"),
                                          "coverage": _ov_det.get("coverage"),
                                          "overlays": [{"kind": o.get("kind"), "conf": o.get("conf"),
                                                        "cov": o.get("coverage"),
                                                        "box": [round(float(o.get(k, 0)), 3) for k in ("x0", "y0", "x1", "y1")]}
                                                       for o in (_ov_det.get("overlays") or [])[:8]]}
                _clean = os.path.join(work, "clean.jpg")
                _sh.copy(orig, _clean)
                _pb._MASK_LAST_LOG = []
                _ov_rep = _pb.remove_overlay(_clean)
                _dbg["overlay_remove"] = {k: _ov_rep.get(k) for k in ("action", "removed", "type", "coverage", "kinds")}
                _dbg["overlay_log"] = _pb._MASK_LAST_LOG[-10:]
                with open(_clean, "rb") as f:
                    _clean_b64 = _b64.b64encode(f.read()).decode()
            except Exception as _oe:
                _dbg["overlay_error"] = repr(_oe)[:200]
                _clean_b64 = ""
        rows = [{"type": b.get("type"), "value": b.get("value", ""),
                 "conf": round(float(b.get("conf", 0.5)), 2),
                 "box": [round(float(b.get(k, 0)), 3) for k in ("x0", "y0", "x1", "y1")],
                 "masked": float(b.get("conf", 0.5)) >= _pb.PII_CONF_MIN} for b in boxes]
        return JSONResponse({"ok": True, "detected": len(boxes), "masked_count": n,
                             "gate_conf_min": _pb.PII_CONF_MIN, "boxes": rows, "ocr_debug": _dbg,
                             "mask_log": _pb._MASK_LAST_LOG[-20:],
                             "masked_b64": _b64.b64encode(mbytes).decode(),   # 마스킹본(전후 대조용)
                             "clean_b64": locals().get("_clean_b64", "")})     # 오버레이 제거본
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "error": "pii-test: " + traceback.format_exc()[-500:]}, status_code=500)


@app.get("/admin/set/{asset_id}/render-job")
def admin_render_job(asset_id: str, channel: str = "naver", price: str = "", mileage: str = ""):
    """gorender 이관 — render_job_v1 + 자산(카드·TTS·ASS·BGM·사진) 사전생성 → zip 다운로드.
    Go 워커 파리티 검증(V2)용. render_storyboard와 '동일 해석'을 build_render_job이 재현(로직 수정 0)."""
    import io as _io
    import shutil as _sh
    import tempfile as _tf
    import zipfile as _zf
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    from app.services import director as _dir
    from app.generators import video as _vid
    from app.services import render_job as _rj
    from app.strategies import resolve_strategy
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    t = db.get_tenant(blog.tenant_id)
    pl = blog.payload or {}
    body = pl.get("body") or ""
    paths = _restore_media(blog.tenant_id, pl.get("image_paths") or [])
    if not paths:
        return JSONResponse({"ok": True, "blocked": "photo_lost"})
    from app import vision as _vzc
    cat = _vzc.build_catalog(paths, getattr(t, "industry", "") or "")
    if getattr(_vzc, "_CATALOG_CREDIT_EXHAUSTED", False):
        return JSONResponse({"ok": False, "blocked": "vision_credit"}, status_code=402)
    if not cat or len(cat) < max(1, len(paths) // 2):
        return JSONResponse({"ok": True, "blocked": "catalog_poor", "catalog_n": len(cat)})
    canon = _canonical_keyword(t, blog)
    _gsrc = pl.get("gen_source") or ""
    _sale = (price or "").strip() or _vid._resolve_sale_price(_gsrc, body)
    _mile = (mileage or "").strip()
    try:
        from app.services import indschema as _isc
        _sch = _isc.get_schema(getattr(t, "industry", "") or "", getattr(t, "biz_type", "local") or "local")
        _dvals = _vid.ShortVideoGenerator._extract_data_points(
            body, _gsrc, _sch, getattr(t, "biz_type", "local") or "local", sale_price=_sale)
    except Exception:
        _dvals = []
    sb = _dir.build_storyboard(body, cat, canon, channel=channel, data_values=_dvals)
    if not sb:
        return JSONResponse({"ok": True, "blocked": "no_storyboard", "_fail": getattr(_dir, "_SB_LAST_FAIL", "")})
    img_by_id = {c["id"]: paths[c["id"] - 1] for c in cat if 1 <= c.get("id", 0) <= len(paths)}
    strat = resolve_strategy(t)
    kws = [canon] if canon else []
    work = _tf.mkdtemp(prefix="renderjob_")
    try:
        job = _rj.build_render_job(sb, img_by_id, kws, t, strat, work, sale_price=_sale, mileage=_mile)
        if not job:
            return JSONResponse({"ok": True, "blocked": "empty_job"})
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w", _zf.ZIP_STORED) as z:   # 이미 압축된 자산 — STORED(무압축, 빠름)
            for fn in sorted(os.listdir(work)):
                z.write(os.path.join(work, fn), fn)
        buf.seek(0)
        from fastapi.responses import Response as _Resp
        return _Resp(content=buf.read(), media_type="application/zip",
                     headers={"Content-Disposition": f'attachment; filename="renderjob_{asset_id[:8]}_{channel}.zip"',
                              "X-Job-Scenes": str(len(job.get("scenes", [])))})   # 한글 헤더 금지(latin-1) — 값은 job.json 내
    except Exception:
        import traceback
        return JSONResponse({"ok": False, "error": "render-job: " + traceback.format_exc()[-700:]},
                            status_code=500)
    finally:
        _sh.rmtree(work, ignore_errors=True)


@app.get("/admin/set/{asset_id}/mask-trace")
def admin_mask_trace(asset_id: str):
    """워터마크·개인정보 오폭 원인 확정 — 세트 사진마다 detect_personal_info/detect_overlay를 dry-run,
    [사진/박스/유형/신뢰도/처리여부] 실측. would_process=현 신뢰도 게이트(PII/OVERLAY_CONF_MIN) 통과 여부.
    ※ 현 저장본은 이미 마스킹 반영본(ingest 제자리) — 이 추적은 검출기 거동·신뢰도 재현용이다."""
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    from app import vision as _vzc
    from app.media import photo_boost as _pb
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    raw = (blog.payload or {}).get("image_paths") or []
    paths = _restore_media(blog.tenant_id, raw)
    if not paths:
        return JSONResponse({"ok": True, "blocked": "photo_lost"})
    out = []
    for i, p in enumerate(paths, 1):
        try:
            pii = _vzc.detect_personal_info(p)
        except Exception:
            pii = []
        try:
            ov = _vzc.detect_overlay(p)
        except Exception:
            ov = {"present": False}
        pii_rows = [{"type": b.get("type"), "conf": round(float(b.get("conf", 0.5)), 2),
                     "box": [round(float(b.get(k, 0)), 3) for k in ("x0", "y0", "x1", "y1")],
                     "would_process": float(b.get("conf", 0.5)) >= _pb.PII_CONF_MIN} for b in pii]
        ov_rows = [{"kind": o.get("kind"), "conf": round(float(o.get("conf", 0.5)), 2),
                    "coverage": o.get("coverage"),
                    "box": [round(float(o.get(k, 0)), 3) for k in ("x0", "y0", "x1", "y1")],
                    "would_process": (float(o.get("conf", 0.5)) >= _pb.OVERLAY_CONF_MIN
                                      and (o.get("coverage") or 1.0) <= _pb._REMOVE_MAX_COV)}
                   for o in (ov.get("overlays") or [])]
        out.append({"id": i, "url": f"/admin/media/{blog.tenant_id}/{os.path.basename(p)}",
                    "pii": pii_rows, "overlay_type": ov.get("type"),
                    "attached_c": ov.get("type") == "c", "overlays": ov_rows})
    # ★ Encar 가림막 등 부착물(type-c) — 제거 불가. '지워지지 않는다' 경고(원본 교체 판단용).
    attached = [{"id": r["id"], "url": r["url"]} for r in out if r["attached_c"]]
    would = sum(1 for r in out for x in (r["pii"] + r["overlays"]) if x.get("would_process"))
    return JSONResponse({"ok": True, "n_photos": len(paths),
                         "gate": {"pii_conf_min": _pb.PII_CONF_MIN, "overlay_conf_min": _pb.OVERLAY_CONF_MIN,
                                  "overlay_max_cov": _pb._REMOVE_MAX_COV},
                         "would_process_total": would,
                         "attached_warning": {"note": "이 사진들의 부착물(가림막·스티커)은 자동 제거되지 않습니다 — 원본 교체를 검토하세요.",
                                              "photos": attached} if attached else None,
                         "photos": out})


@app.post("/admin/set/{asset_id}/regen-piece")
def admin_regen_piece(asset_id: str, kind: str = "blog", channel: str = "", dry: str = ""):
    """전 피스 타입 공통 재생성 — kind=blog|short|caption|x_post|marketplace, channel(선택 youtube/instagram)."""
    r = _regen_piece_common(asset_id, kind.strip().lower(), channel.strip().lower(), dry == "1")
    return JSONResponse(r, status_code=r.pop("status", 200))


@app.post("/admin/set/{asset_id}/regen-blog")
def admin_regen_blog(asset_id: str, dry: str = ""):
    """(래퍼) 블로그 재생성 → 공통 경로 호출(하위호환)."""
    r = _regen_piece_common(asset_id, "blog", "", dry == "1")
    return JSONResponse(r, status_code=r.pop("status", 200))


@app.get("/admin/restore-token")
def admin_restore_token(tid: str = "", token: str = "", dry: str = "1"):
    """(복구) 과오 세척 되돌림 — tid 가게의 전 피스 target_keywords에서, token(예 '썬팅지')이 스키마 속성
    토큰이면 '이 가게가 정당히 취급하는 것'으로 보고 누락된 곳에 재추가. 루마 '썬팅지' 오제거 복원용.
    안전: token이 해당 업종 스키마 attribute_axes에 실제 있을 때만 재추가(임의 주입 금지). dry=1 판정만."""
    from app.services import indschema as _isc
    if not (tid and token):
        return JSONResponse({"ok": False, "error": "tid·token 필요"}, status_code=400)
    t = db.get_tenant(tid)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    _biz = getattr(t, "biz_type", "local") or "local"
    _toks = _isc.attribute_tokens(_isc.get_schema(getattr(t, "industry", ""), _biz))
    if token not in _toks:
        return JSONResponse({"ok": False, "error": f"'{token}'은 이 업종 스키마 속성 토큰 아님 — 재추가 거부(임의 주입 금지)"}, status_code=400)
    restored, seen = [], set()
    for s in db.list_sets(tenant_id=tid, limit=300):
        aid = s.get("asset_id")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        for p in db.get_set_pieces(aid):
            pl = p.payload or {}
            tk = pl.get("target_keywords")
            # token이 이 업종 스키마 속성 토큰(위에서 검증)이면 이 가게가 정당히 취급하는 것 → 누락분에 재추가.
            #   (오제거 복원: 서비스업 axis0 토큰은 전부 정당하므로 무조건 재추가가 안전. 임의 토큰은 위 400에서 차단.)
            if isinstance(tk, list) and token not in tk:
                restored.append({"asset": aid[:8], "piece": p.id[:8]})
                if dry != "1":
                    pl["target_keywords"] = tk + [token]
                    p.payload = pl
                    db.save_piece(p)
    return JSONResponse({"ok": True, "tid": tid, "token": token, "dry": dry == "1",
                         "restored_count": len(restored), "restored": restored[:40]})


@app.get("/admin/phantom-sweep")
def admin_phantom_sweep(tid: str = "", dry: str = "1"):
    """4형제 등 '유령 속성 토큰' 전수 세척 — (1) 제목/태그 표면 오염 세트 asset_id 목록(regen-blog 대상),
    (2) target_keywords 필드에서 유령 토큰만 제거(제자리·나머지 무변경 — 이 필드는 영상 카피 등 생성 입력으로
    소비되는 씨앗). 컨텍스트=asset.note(사진분석·사장입력)+재고만(오염 가능한 제목/키워드는 컨텍스트서 제외).
    dry=1 판정만. tid 필수(안전). 발행불가(_publish_blocked) 세트는 필드세척만·목록서 표시."""
    from app.domain.models import ContentKind as _CK
    if not tid:
        return JSONResponse({"ok": False, "error": "tid 필요"}, status_code=400)
    t = db.get_tenant(tid)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    _biz = getattr(t, "biz_type", "local") or "local"
    _ind = getattr(t, "industry", "") or ""
    _inv = [c.get("model") for c in db.recent_inventory_context(tid, limit=12) if c.get("model")]
    phantom_title, swept = [], []
    _seen_assets = set()
    for s in db.list_sets(tenant_id=tid, limit=300):
        aid = s.get("asset_id")
        if not aid or aid in _seen_assets:
            continue
        _seen_assets.add(aid)
        _a = db.get_asset(aid)
        _note = (getattr(_a, "note", "") or "")            # ★ 컨텍스트 = 그라운드트루스만(note+재고), 제목/키워드 제외
        for p in db.get_set_pieces(aid):
            pl = p.payload or {}
            title = pl.get("selected_title") or pl.get("title") or ""
            # ★ 원리 통일(#2): allowed = 현재 세트 컨텍스트 ∪ 본문 실등장 — note + 본문 + gen_source(사진분석).
            #   재고는 부가일 뿐(재고 개념 없는 서비스업도 본문에 실등장한 정당어=썬팅지는 통과 → 오제거 방지).
            _ctx = _note + "\n" + (pl.get("body") or "") + "\n" + (pl.get("gen_source") or "")
            # (1) 제목 표면 오염 판정(컨텍스트에 제목 자신 미포함)
            if title:
                _kt, _dt = seo.drop_phantom_attr_kws([title], _ind, _biz, context_text=_ctx, inventory_models=_inv)
                if _dt:
                    phantom_title.append({"asset_id": aid, "piece": p.id[:8], "kind": p.kind.value,
                                          "title": title, "phantom": [d[1] for d in _dt if isinstance(d, tuple)],
                                          "blocked": bool(pl.get("_publish_blocked"))})
            # (2) target_keywords 필드 세척(씨앗 오염 제거) — 컨텍스트=note+본문+gen_source(본문 실등장 반영)
            tk = pl.get("target_keywords") or []
            if tk:
                _kk, _dk = seo.drop_phantom_attr_kws(list(tk), _ind, _biz, context_text=_ctx, inventory_models=_inv)
                if _dk:
                    swept.append({"asset_id": aid, "piece": p.id[:8], "kind": p.kind.value,
                                  "removed": [d[0] for d in _dk if isinstance(d, tuple)],
                                  "before": tk, "after": _kk})
                    if dry != "1":
                        pl["target_keywords"] = _kk
                        p.payload = pl
                        db.save_piece(p)
    return JSONResponse({"ok": True, "tid": tid, "dry": dry == "1", "inventory": _inv,
                         "phantom_title_count": len(phantom_title), "phantom_title_sets": phantom_title,
                         "field_swept_count": len(swept), "field_swept": swept})


@app.post("/admin/set/{asset_id}/regen-captions")
def admin_regen_captions(asset_id: str):
    """사진 캡션 소스만 재생성 — gen_source의 [사진N] 묘사를 vision 재분석으로 교체.
    글 본문·사진·영상·타 채널 불변. 오염 소스(라벨 유출) 세척용."""
    import re as _r
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _restore_media
    from app import vision as _vz
    blog = next((p for p in db.get_set_pieces(asset_id) if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    tenant = db.get_tenant(blog.tenant_id)
    paths = _restore_media(blog.tenant_id, blog.payload.get("image_paths") or [])
    if not (tenant and paths):
        return JSONResponse({"ok": False, "error": f"사전조건: paths={len(paths)}"}, status_code=409)
    fresh = (_vz.analyze_all(paths, tenant.industry) or "").strip()
    if not _r.search(r"\[사진1\]", fresh):
        return JSONResponse({"ok": False, "error": "재분석 형식 불량(기존 소스 유지)", "head": fresh[:200]}, status_code=500)
    src = blog.payload.get("gen_source") or ""
    src = _r.sub(r"\[사진\d+\][^\n]*\n?", "", src).strip()      # 기존 [사진N] 라인 제거
    blog.payload["gen_source"] = (fresh + "\n" + src)[:8000]
    db.save_piece(blog)
    caps = _photo_captions(tenant, blog, len(paths))
    return JSONResponse({"ok": True, "n": len(paths), "captions": caps})


@app.post("/admin/set/{asset_id}/regen-naver")
def admin_regen_naver(asset_id: str):
    """네이버용 영상만 재생성(렌더 결함 교정) — 쇼츠·릴스·글·사진 등 타 산출물 불변.
    씬·자막 소스는 글 본문 발췌(빌더 서두 자막 게이트 경유). 이전 naver 파일은 폐기."""
    from app.domain.models import ContentKind as _CK
    from app.strategies import resolve_strategy
    from app.services.ingest import _restore_media
    from app.generators.video import ShortVideoGenerator
    pieces = db.get_set_pieces(asset_id)
    short = next((p for p in pieces if p.kind == _CK.SHORT and p.channel.value == "youtube"), None)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not (short and blog):
        return JSONResponse({"ok": False, "error": "쇼츠/블로그 피스 없음"}, status_code=404)
    tenant = db.get_tenant(short.tenant_id)
    asset = db.get_asset(asset_id)
    paths = _restore_media(short.tenant_id, short.payload.get("image_paths") or blog.payload.get("image_paths") or [])
    if not (tenant and asset and paths):
        return JSONResponse({"ok": False, "error": f"사전조건: tenant={bool(tenant)} asset={bool(asset)} paths={len(paths)}"}, status_code=409)
    gen = ShortVideoGenerator()
    out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
    os.makedirs(out_dir, exist_ok=True)
    kws = short.payload.get("target_keywords") or []
    old_nv = dict(short.payload.get("naver_video") or {})
    vid_imgs = gen._downscale_for_video(paths)
    import logging as _lg2                                       # V4: 씬-자막 매칭 로그 캡처(판정표 표면화)
    _mcap = []
    class _MatchGrab(_lg2.Handler):
        def emit(self, r):
            try:
                m = r.getMessage()
                if "씬-자막 매칭" in m or "지시어 불일치" in m or "사진 재배정" in m:
                    _mcap.append(m)
            except Exception:
                pass
    _vlog = _lg2.getLogger("shopcast.video")
    _grab = _MatchGrab(); _grab.setLevel(_lg2.WARNING); _vlog.addHandler(_grab)
    try:
        npath, nmeta = gen._naver_video(tenant, asset, vid_imgs, kws, resolve_strategy(tenant), out_dir)
    except Exception as e:
        import traceback
        _vlog.removeHandler(_grab)
        return JSONResponse({"ok": False, "error": traceback.format_exc()[-800:], "match_log": _mcap}, status_code=500)
    finally:
        _vlog.removeHandler(_grab)
        for _vp in vid_imgs:
            if _vp not in paths and _vp.endswith("_vid.jpg") and os.path.exists(_vp):
                try:
                    os.remove(_vp)
                except Exception:
                    pass
    if not npath:
        return JSONResponse({"ok": False, "error": "naver 재생성 실패",
                             "build_note": (nmeta or {}).get("_build_note", "(사유 미기록)"),
                             "old_kept": True}, status_code=500)
    for fp in (old_nv.get("path"), old_nv.get("body_path")):   # 이전 산출물 폐기(블러 패딩 파일 포함)
        if fp and fp != npath and os.path.exists(fp):
            try:
                os.remove(fp)
            except Exception:
                pass
    for p in pieces:                                           # 같은 세트의 SHORT(쇼츠·릴스) payload 동기화
        if p.kind == _CK.SHORT and (p.payload or {}).get("naver_video"):
            p.payload["naver_video"] = nmeta
            db.save_piece(p)
    if not any(p.kind == _CK.SHORT and (p.payload or {}).get("naver_video") for p in pieces):
        short.payload["naver_video"] = nmeta
        db.save_piece(short)
    return JSONResponse({"ok": True, "match_log": _mcap, "naver_video": {k: nmeta.get(k) for k in
                        ("path", "filename", "title", "duration_sec", "quality", "hashtags")}})


@app.post("/admin/set/{asset_id}/backfill-status")
def admin_backfill_status(asset_id: str):
    """구세트(상태 기록 이전) channel_status 백필 — 실재 피스·파일 검사 결과만 기록(추정 금지)."""
    from app.domain.models import ContentKind as _CK
    from app.services.ingest import _set_channel_status
    pieces = db.get_set_pieces(asset_id)
    if not any(p.kind == _CK.BLOG for p in pieces):
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    def _has(kind, ch=None):
        return any(p.kind == kind and (ch is None or p.channel.value == ch) for p in pieces)
    short = next((p for p in pieces if p.kind == _CK.SHORT and (p.payload or {}).get("naver_video")), None)
    nv_ok = bool(short and ((short.payload.get("naver_video") or {}).get("path")))
    cs = {"insta": {"status": "done" if _has(_CK.CAPTION) else "failed", "error": "" if _has(_CK.CAPTION) else "산출물 없음"},
          "x": {"status": "done" if _has(_CK.X_POST) else "failed", "error": "" if _has(_CK.X_POST) else "산출물 없음"},
          "shorts": {"status": "done" if _has(_CK.SHORT, "youtube") else "failed"},
          "reels": {"status": "done" if _has(_CK.SHORT, "instagram") else "failed"},
          "naver": {"status": "done" if nv_ok else "failed"}}
    _set_channel_status(asset_id, cs)
    return JSONResponse({"ok": True, "channel_status": cs})


@app.post("/admin/set/{asset_id}/regen-channel")
def admin_regen_channel(asset_id: str, kind: str = "", force: str = ""):
    """누락·실패 텍스트 채널(caption/x_post/blog) 단건 재생성 — 표준 경로 재사용(전 게이트 경유).
    글·기존 정상 피스 불변. 이미 피스가 있으면 거부(임의 재생성 금지) — force=1은 사용자 지시 재생성 전용(기존 피스 교체)."""
    from app.domain.models import ContentKind as _CK
    _map = {"caption": _CK.CAPTION, "x_post": _CK.X_POST, "blog": _CK.BLOG}
    ck = _map.get(kind)
    if not ck:
        return JSONResponse({"ok": False, "error": "kind는 caption|x_post|blog"}, status_code=400)
    pieces = db.get_set_pieces(asset_id)
    if not pieces:
        return JSONResponse({"ok": False, "error": "세트 없음"}, status_code=404)
    _old_piece = next((p for p in pieces if p.kind == ck), None)
    if _old_piece and force != "1":
        return JSONResponse({"ok": False, "error": "이미 존재 — 임의 재생성 금지(force=1은 사용자 지시 전용)"}, status_code=409)
    _keep = {}                                          # blog 교체 시 상태 필드 보존(channel_status·video_job은 blog에 저장)
    if _old_piece:
        if ck == _CK.BLOG:
            _keep = {k: _old_piece.payload.get(k) for k in ("channel_status", "video_job") if _old_piece.payload.get(k)}
        db.delete_piece(_old_piece.id, _old_piece.tenant_id)
        pieces = db.get_set_pieces(asset_id)
    ref = next((p for p in pieces if p.kind == _CK.BLOG), pieces[0] if pieces else _old_piece)   # 참조: blog 우선
    from app.services.ingest import _regen_text_piece, _set_channel_status, KIND_TO_CHANNEL
    tenant = db.get_tenant(ref.tenant_id)
    asset = db.get_asset(asset_id)
    if not (tenant and asset):
        return JSONResponse({"ok": False, "error": "tenant/asset 소실"}, status_code=404)
    ok = _regen_text_piece(tenant, asset, ck, ref)
    if ok and _keep:                                    # 보존한 상태 필드를 새 blog에 이식
        newb = next((p for p in db.get_set_pieces(asset_id) if p.kind == _CK.BLOG), None)
        if newb:
            newb.payload.update(_keep)
            db.save_piece(newb)
    ch = KIND_TO_CHANNEL.get(ck, "naver")
    _set_channel_status(asset_id, {ch: {"status": "done" if ok else "failed"}})
    made = next((p for p in db.get_set_pieces(asset_id) if p.kind == ck), None)
    return JSONResponse({"ok": ok, "channel": ch,
                         "text": (made.payload.get("text") or (made.payload.get("title", "") + "\n" +
                                  (made.payload.get("body") or "")[:400]) if made else None)})


@app.post("/admin/inventory-save")
def admin_inventory_save(tid: str = "", model: str = "", year: str = "", car_class: str = "",
                         purge: str = "", purge_model: str = ""):
    """진단/백필 — 매물 컨텍스트 수동 저장. purge=1이면 저장 전 tenant 전체 정정(손상 레코드 무효화),
    purge_model=X면 그 model만 삭제. PHASE 0 오염 소스 처리용."""
    if not tid.strip():
        return JSONResponse({"ok": False, "error": "tid 필요"}, status_code=400)
    _deleted = 0
    if purge == "1":
        _deleted = db.purge_inventory_context(tid.strip())          # 전체 정정(클린 슬레이트)
    elif purge_model.strip():
        _deleted = db.purge_inventory_context(tid.strip(), purge_model.strip())
    if model.strip():
        db.save_inventory_context(tid.strip(), model.strip(), year.strip(), car_class.strip())
    return JSONResponse({"ok": True, "deleted": _deleted,
                         "context": db.recent_inventory_context(tid.strip(), limit=10)})


@app.post("/admin/relink-publish")
def admin_relink_publish(old_piece: str = "", new_piece: str = ""):
    """발행 기록 재연결 — 재생성으로 piece_id가 바뀐 발행 글의 추적 복구(라이브 글 불변)."""
    op, np = old_piece.strip(), new_piece.strip()
    if not (op and np):
        return JSONResponse({"ok": False, "error": "old_piece·new_piece 필요"}, status_code=400)
    try:
        with db._conn() as c:
            cur = c.execute("UPDATE blog_publishes SET piece_id=? WHERE piece_id=?", (np, op))
        return JSONResponse({"ok": True, "relinked": cur.rowcount})
    except Exception as e:
        return JSONResponse({"ok": False, "error": repr(e)[:120]}, status_code=500)


@app.get("/admin/indschema")
def admin_indschema(industry: str = "", biz_type: str = "seller", desc: str = "", nocache: str = ""):
    """진단 — 업종 스키마 추론 실값(V3). nocache=1이면 캐시·시드 무시하고 추론 강제."""
    from app.services import indschema as _is
    if nocache == "1":
        inf = _is._infer(industry, biz_type, desc)
        return JSONResponse({"industry": industry, "forced_infer": True, "schema": inf})
    return JSONResponse({"industry": industry, "schema": _is.get_schema(industry, biz_type, desc)})


@app.get("/admin/smartblock")
def admin_smartblock(seed: str = "중고차", region: str = ""):
    """진단 — 스마트블록 세부주제 근사(연관어+검색량+의도유형→앵글). V2 검증."""
    from app.services import smartblock as _sb
    seeds = [s for s in (seed, f"{region} {seed}".strip()) if s.strip()]
    subs = _sb.subtopics(seeds, min_volume=100, limit=12)
    return JSONResponse({"seeds": seeds, "count": len(subs),
                         "subtopics": [{**s, "angle": _sb.angle_for(s["keyword"])} for s in subs]})


@app.get("/admin/inventory")
def admin_inventory(tid: str = ""):
    """진단 — tenant 매물 컨텍스트 + 셀러 롱테일 후보(검색량 포함)."""
    from app.services import autoqueue as _aq, searchad as _sa
    t = db.get_tenant(tid.strip())
    if not t:
        return JSONResponse({"error": "tenant 없음"}, status_code=404)
    cands = _aq._seller_longtail_candidates(t)
    vols = {}
    if _sa.configured() and cands:
        for vv in _sa.keyword_volumes(cands[:8], limit=80):
            vols[(vv.get("keyword") or "").replace(" ", "")] = vv.get("total", 0)
    return JSONResponse({"context": db.recent_inventory_context(tid.strip(), limit=6),
                         "candidates": [{"kw": c, "volume": vols.get(c.replace(" ", ""))} for c in cands]})


@app.post("/admin/queue-clean-region")
def admin_queue_clean_region(tid: str = ""):
    """구재고 정리 — 셀러·병행 tenant의 pending 큐 중 기초지역(구·군) 타깃을 삭제(미소비만).
    이미 발행된 글·generating/done 항목은 불변. 삭제 후 refill이 보정 키워드로 재적재."""
    tid = tid.strip()
    t = db.get_tenant(tid)
    if not t:
        return JSONResponse({"ok": False, "error": "tenant 없음"}, status_code=404)
    from app.services import autoqueue as _aq
    import re as _rq
    # 타 지역 오염 검출 — 키워드에 시·군·구 이름이 있는데 tenant 광역/기초 어느 것과도 안 맞으면 엉뚱 지역
    _reg_all = set(_rq.findall(r"[가-힣]{2,}", (t.region or "")))
    _reg_cores = set(_aq._basic_region_tokens(t.region or "")) | {
        _rq.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|도)$", "", x) for x in _reg_all}
    _CITY = _rq.compile(r"([가-힣]{2,3})(시|군|구|매매단지)")
    def _foreign_region(kw):
        for m2 in _CITY.finditer(kw or ""):
            core = m2.group(1)
            if core and not any(core in rc or rc in core for rc in _reg_cores if rc):
                return True
        return False
    removed = []
    for r in db.writing_queue_rows(tid, status="pending", limit=200):
        kw = r.get("target_keyword") or ""
        _ind0 = ((t.industry or "").replace("/", ",").split(",")[0] or "").strip().replace(" ", "")
        _bare_generic = ((getattr(t, "biz_type", "local") or "local") in ("seller", "hybrid")
                         and kw.replace(" ", "") == _ind0)   # '중고차' 단독(지역·차종 없음) = 전국 대형, 글 타깃 부적합
        if _aq._seller_kw_blocked(t, kw) or _foreign_region(kw) or _bare_generic:
            removed.append({"id": r["id"], "kw": kw})
    if removed:
        with db._conn() as c:
            c.execute("DELETE FROM writing_queue WHERE id IN (%s)" % ",".join("?" * len(removed)),
                      tuple(x["id"] for x in removed))
    # 보정 규칙으로 재적재
    added = {}
    try:
        added = _aq.refill(t)
    except Exception as e:
        added = {"refill_error": repr(e)[:120]}
    return JSONResponse({"ok": True, "removed": removed, "refilled": added})


@app.get("/admin/queue-audit")
def admin_queue_audit(tid: str = ""):
    """진단 — tenant writing_queue 전수(생성시각·상태·타깃) + 발행 글 타깃. D1 이분법 대조용."""
    tid = tid.strip()
    if not tid:
        return JSONResponse({"error": "tid 필요"}, status_code=400)
    rows = db.writing_queue_rows(tid, limit=100)
    out = [{"id": r.get("id"), "source": r.get("source_type"), "keyword": r.get("target_keyword"),
            "status": r.get("status"), "created_at": r.get("created_at"), "attempts": r.get("attempts"),
            # ★ 80자에서 자르면 실패 사유가 안 보인다(실측: 'NameError("nam'에서 끊겼다).
            #   진단 엔드포인트가 진단을 못 하게 만드는 절단 — 사유는 온전히 보여준다.
            "reason": (r.get("reason") or "")[:600]}
           for r in rows]
    # 발행 확인된 글의 타깃 키워드
    pubs = []
    try:
        with db._conn() as c:
            prs = c.execute("SELECT piece_id, post_title, published_at FROM blog_publishes WHERE tenant_id=? "
                            "ORDER BY published_at DESC LIMIT 10", (tid,)).fetchall()
        for pr in prs:
            pc = db.get_piece(pr["piece_id"])
            kw = ((pc.payload.get("target_keywords") or [""])[0] if pc else "") or ""
            pubs.append({"piece": pr["piece_id"], "title": (pr["post_title"] or "")[:40],
                         "target_kw": kw, "published_at": pr["published_at"]})
    except Exception:
        pass
    return JSONResponse({"tenant": tid, "queue_count": len(out), "queue": out, "published": pubs})


@app.get("/admin/kwpattern")
def admin_kwpattern(kw: str = "", nocache: str = ""):
    """진단 — 셀러 상위 글 패턴 분석 결과 + 주입 블록 미리보기(검증용)."""
    from app.services import kwpattern as _kwp
    pat = _kwp.analyze(kw.strip(), use_cache=(nocache != "1"))
    if not pat:
        return JSONResponse({"ok": False, "error": "분석 불가(무키/결과 0/키워드 없음)"}, status_code=404)
    return JSONResponse({"ok": True, "pattern": pat, "block": _kwp.directive_block(pat)})


@app.get("/admin/set/{asset_id}/pieces.json")
def admin_set_pieces_json(asset_id: str):
    """진단(읽기 전용) — 세트 피스들의 영상 관련 payload 요약(naver 2종·해시태그·자막·경로 존재 여부)."""
    out = []
    for p in db.get_set_pieces(asset_id):
        pl = p.payload or {}
        nv = pl.get("naver_video") or {}
        out.append({
            "kind": str(p.kind), "id": p.id[:8], "full_id": p.id,
            "video_path": pl.get("video_path"),
            "video_exists": bool(pl.get("video_path")) and os.path.exists(pl.get("video_path") or ""),
            "duration_sec": pl.get("duration_sec"),
            "subtitles_n": len(pl.get("subtitles") or []),
            "note": (pl.get("assemble_note") or "")[:200],
            "naver_video": {k: nv.get(k) for k in
                            ("path", "body_path", "title", "filename", "filename_body", "filename_clip",
                             "hashtags", "duration_sec", "quality", "scene_texts")} if nv else None,
            "naver_exists": {"clip": bool(nv.get("path")) and os.path.exists(nv.get("path") or ""),
                             "body": bool(nv.get("body_path")) and os.path.exists(nv.get("body_path") or "")} if nv else None,
            "video_job": (pl.get("video_job") or None) if p.kind and "BLOG" in str(p.kind) else None,
            "channel_status": (pl.get("channel_status") or None) if p.kind and "BLOG" in str(p.kind) else None,
            "blog_tags": (_blog_tags(db.get_tenant(p.tenant_id), p) if p.kind and "BLOG" in str(p.kind) else None),
        })
    _a = db.get_asset(asset_id)
    _tid0 = next((p.tenant_id for p in db.get_set_pieces(asset_id)), "")
    # 진단 신뢰: 피스 0건인데 asset은 존재 → 이 asset_id가 스테일(재생성으로 세트 앵커가 새 asset_id로 갈림).
    _hint = None
    if not out and _a:
        _atid = getattr(_a, "tenant_id", "") or ""
        _recent = []
        try:                                            # 같은 tenant의 최신 세트 asset_id 안내(올바른 조회 대상)
            for s in db.list_sets(tenant_id=_atid, limit=5):
                _recent.append({"asset_id": s.get("asset_id"), "title": (s.get("title") or "")[:40]})
        except Exception:
            pass
        _hint = {"issue": "이 asset_id에 content_pieces 0건 — asset은 존재. 재생성으로 세트 앵커가 "
                          "새 asset_id로 갈렸을 가능성(스테일 참조). 검증기 버그 아님.",
                 "tenant_id": _atid, "recent_sets": _recent}
    return {"asset_id": asset_id, "tenant_id": _tid0 or getattr(_a, "tenant_id", ""),
            "asset_note": (getattr(_a, "note", "") or "")[:2000], "pieces": out, "stale_hint": _hint}


@app.api_route("/admin/disk-sos", methods=["GET", "POST"])
def admin_disk_sos(hard: str = "", days: int = 3):
    """🚑 긴급 디스크 확보 — DB 미접근(디스크 풀로 SQLite 'disk I/O error' 상태 복구용).
    안전 재생성 파일만 삭제: *.tmp/.part/.ass/.wav/.zip + *_vid.jpg(영상용 다운스케일 임시).
    hard=1이면 days일 지난 mp4/png(영상·커버 — R2 미러 존재분)도 삭제. 원본 사진(jpg 등)·DB 파일 불변."""
    import time as _t
    import shutil as _sh
    from app.storage import STORAGE_DIR
    now = _t.time()
    freed, n = 0, 0
    SAFE = (".tmp", ".part", ".ass", ".wav", ".zip")
    for root, _d, fs in os.walk(STORAGE_DIR):
        for fn in fs:
            fp = os.path.join(root, fn)
            low = fn.lower()
            if low.endswith(".db") or low.endswith(".sqlite") or "sqlite" in low or low.endswith((".db-wal", ".db-shm")):
                continue                                    # DB 파일 절대 불변
            try:
                rm = low.endswith(SAFE) or low.endswith("_vid.jpg")
                if not rm and hard == "1" and (low.endswith(".mp4") or low.endswith(".png")):
                    rm = (now - os.path.getmtime(fp)) > days * 86400
                if rm:
                    sz = os.path.getsize(fp)
                    os.remove(fp)
                    freed += sz
                    n += 1
            except Exception:
                pass
    du = _sh.disk_usage(STORAGE_DIR)
    return {"removed": n, "freed_mb": round(freed / 1e6, 1),
            "disk_mb": {"free": round(du.free / 1e6), "used": round(du.used / 1e6)}}


@app.api_route("/admin/disk", methods=["GET", "POST"])
def admin_disk(prune: str = ""):
    """디스크 진단 — 확장자별 사용량 + DB 미참조(고아) 미디어 집계.
    prune=1이면 고아 영상·커버·임시파일만 삭제(사진·DB·참조 파일 불변 — R2 무관하게 안전)."""
    import shutil as _sh
    import time as _tp
    from collections import defaultdict
    from app.storage import STORAGE_DIR
    # ★ PHASE 1(1-3): 만차 시 _referenced_media(DB 조회)가 SQLite I/O로 죽어 prune 전체가 500 → 복구 불가였다.
    #   DB 조회 실패면 '파일 기준 폴백'(나이 든 mp4/png/wav/ass = 고아 취급, R2 미러가 서빙하므로 안전). 사진(jpg) 절대 제외.
    try:
        refs = _referenced_media()
        _refs_ok = True
    except Exception as _re:
        refs, _refs_ok = set(), False
        logging.warning("[disk] 참조 조회 실패(만차 추정) — 파일 기준 폴백 복구: %s", repr(_re)[:100])
    _now = _tp.time()
    by_ext = defaultdict(lambda: [0, 0])
    orphans, orphan_bytes = [], 0
    for root, _d, fs in os.walk(STORAGE_DIR):
        for fn in fs:
            fp = os.path.join(root, fn)
            try:
                sz = os.path.getsize(fp)
            except Exception:
                continue
            ext = fn.rsplit(".", 1)[-1].lower()[:6] if "." in fn else "?"
            by_ext[ext][0] += 1
            by_ext[ext][1] += sz
            # 고아 후보: 생성 산출물류만(mp4/png/wav/ass) — 원본 사진(jpg 등)은 절대 건드리지 않음
            if ext in ("mp4", "png", "wav", "ass"):
                if _refs_ok:
                    _orphan = os.path.realpath(fp) not in refs
                else:                                    # DB 미조회 폴백: 나이 든(>1일) 산출물만(R2 서빙)
                    try:
                        _orphan = (_now - os.path.getmtime(fp)) > 86400
                    except Exception:
                        _orphan = False
                if _orphan:
                    orphans.append(fp)
                    orphan_bytes += sz
    freed = 0
    if prune == "1":
        for fp in orphans:
            try:
                sz = os.path.getsize(fp)
                os.remove(fp)
                freed += sz
            except Exception:
                pass
    du = _sh.disk_usage(STORAGE_DIR)
    return {"disk_mb": {"free": round(du.free / 1e6), "used": round(du.used / 1e6)},
            "by_ext_mb": {k: [v[0], round(v[1] / 1e6, 1)] for k, v in
                          sorted(by_ext.items(), key=lambda kv: -kv[1][1])},
            "referenced": len(refs), "orphans": len(orphans),
            "orphan_mb": round(orphan_bytes / 1e6, 1), "freed_mb": round(freed / 1e6, 1)}


@app.api_route("/admin/cleanup", methods=["GET", "POST"])
def admin_cleanup():
    """디스크 확보 — 사장님(OWNER) 소유 tenant만 남기고 데모·테스트 저장폴더+DB 전부 삭제 + 사장님 오래된 영상 정리."""
    import shutil
    import subprocess
    from app.storage import STORAGE_DIR
    keep = set()
    with db._conn() as c:
        for r in c.execute("SELECT tenant_id, email FROM users").fetchall():
            if (r["email"] or "").lower() in OWNER_EMAILS and r["tenant_id"]:
                keep.add(r["tenant_id"])
    freed, removed = 0, 0
    if os.path.isdir(STORAGE_DIR):
        for name in list(os.listdir(STORAGE_DIR)):
            p = os.path.join(STORAGE_DIR, name)
            if os.path.isdir(p) and name not in keep:
                for root, _d, fs in os.walk(p):
                    for fn in fs:
                        try:
                            freed += os.path.getsize(os.path.join(root, fn))
                        except Exception:
                            pass
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
    try:
        with db._conn() as c:
            if keep:
                ph = ",".join("?" * len(keep))
                c.execute(f"DELETE FROM content_pieces WHERE tenant_id NOT IN ({ph})", tuple(keep))
                c.execute(f"DELETE FROM tenants WHERE id NOT IN ({ph})", tuple(keep))
    except Exception:
        pass
    # 사장님(보존) tenant의 오래된 영상도 정리 (keep_recent=2로 강하게)
    for tid in keep:
        freed += _prune_old_media(tid, keep_recent=2)
    # ★ 저장소 전체 — 모든 확장자(사진·영상·캐러셀·ffmpeg 임시) 오래된 파일 삭제, 최근 40개만 유지
    from collections import defaultdict
    allf, by_ext = [], defaultdict(lambda: [0, 0])
    for root, _d, fs in os.walk(STORAGE_DIR):
        for fn in fs:
            fp = os.path.join(root, fn)
            try:
                sz = os.path.getsize(fp)
                allf.append((os.path.getmtime(fp), sz, fp))
                e = fp.rsplit(".", 1)[-1].lower()[:6]
                by_ext[e][0] += 1
                by_ext[e][1] += sz
            except Exception:
                pass
    allf.sort(reverse=True)                    # 최신 먼저
    for _mt, sz, fp in allf[40:]:              # 최근 40개만 남기고 전부 삭제(R2에 사본 있음)
        try:
            os.remove(fp)
            freed += sz
        except Exception:
            pass
    breakdown = {e: {"n": v[0], "mb": round(v[1] / 1e6, 1)}
                 for e, v in sorted(by_ext.items(), key=lambda x: -x[1][1])[:8]}
    try:
        df = subprocess.run(["df", "-h", STORAGE_DIR], capture_output=True, text=True, timeout=8).stdout
    except Exception:
        df = ""
    return {"kept_tenants": len(keep), "removed_folders": removed, "freed_mb": round(freed / 1e6, 1),
            "file_types": breakdown, "df": df}


@app.api_route("/admin/testgen", methods=["GET", "POST"])
def admin_testgen(biz: str = "local", note: str = "", photos: list[UploadFile] = File(None)):
    """진단/샘플 — ingest_upload 동기 실행. photos 여러 장 업로드 지원. note로 메모 지정. biz=seller면 셀러 샘플 가게를 사장님 계정에 연결."""
    import traceback
    import io
    from PIL import Image
    from app.services.ingest import ingest_upload
    if biz == "seller":
        t = next((x for x in db.list_tenants() if x.name == "올린다 셀러샘플"), None)
        if not t:
            t = db.create_tenant("올린다 셀러샘플", "차량용 전자기기", "", "seller")
        db.update_tenant_classification(t.id, "seller", "coupang",
                                        "https://smartstore.naver.com/sample", "차량용 후방카메라 내비게이션", "올린다")
        try:  # 사장님 계정에 연결 → 내 콘텐츠에서 가게 전환해 확인 가능
            ph = ",".join("?" * len(OWNER_EMAILS))
            with db._conn() as c:
                for r in c.execute(f"SELECT id FROM users WHERE email IN ({ph})", tuple(OWNER_EMAILS)).fetchall():
                    db.link_store(r["id"], t.id)
        except Exception:
            pass
        note = note or "차량용 후방카메라·내비게이션 세트. 부산 동구 매장 설치 화면. 3D 내비, 후방 가이드라인"
    else:
        t = next((x for x in db.list_tenants()
                  if (x.industry or "").strip() and not getattr(x, "is_demo", 0)
                  and (x.biz_type or "local") != "seller"), None)
        note = note or "[샘플] 부산 동구 매장에서 직접 설치한 차량 내비게이션·후방카메라 화면"
    if not t:
        return {"err": "no tenant"}
    files = []
    for ph_f in (photos or []):
        if ph_f is not None and getattr(ph_f, "filename", ""):
            files.append((ph_f.file.read(), ph_f.filename))
    if not files:                                                # 사진 없으면 더미 1장
        b = io.BytesIO()
        Image.new("RGB", (600, 400), (120, 140, 90)).save(b, "JPEG")
        files = [(b.getvalue(), "test.jpg")]
    # 여러 장은 동기 생성이 HTTP 타임아웃을 넘김 → 백그라운드 스레드로 실행, 즉시 반환
    import threading

    def _bg():
        try:
            ingest_upload(t, files, note)
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "started": True, "tenant": t.name, "biz": biz, "photos": len(files)}


@app.get("/admin/scenegen")
def admin_scenegen():
    """진단 — 정상 영상 경로(_build_scene_video)가 프로덕션에서 왜 실패하는지 note/error 반환."""
    import os
    import traceback
    from PIL import Image
    from app.generators.video import ShortVideoGenerator
    from app.strategies import resolve_strategy
    t = next((x for x in db.list_tenants() if (x.industry or "").strip() and not getattr(x, "is_demo", 0)), None)
    if not t:
        return {"err": "no tenant"}
    d = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), t.id)
    os.makedirs(d, exist_ok=True)
    # 실제와 동일: 큰 사진(5712×4284) 3장 + 6문장으로 씬 경로 직접 테스트
    imgs = []
    for i in range(3):
        p = os.path.join(d, f"big{i}.jpg")
        Image.new("RGB", (5712, 4284), (70 + i * 25, 90, 120)).save(p, quality=90)
        imgs.append(p)
    from app.domain.models import AssetType
    a = db.create_asset(t.id, AssetType.IMAGE, imgs[0],
                        "흰색 포터2 냉동탑차 앞유리·측면 열차단 썬팅 시공. 여름 더위·눈부심 개선. 부산 초량.")
    import time as _t
    t0 = _t.time()
    try:
        piece = ShortVideoGenerator().generate(t, a, imgs)     # 전체 흐름(LLM 스크립트 포함) · 3장
        vp = piece.payload.get("video_path", "")
        return {"full_ok": bool(vp), "dur_sec": piece.payload.get("duration_sec"),
                "fname": os.path.basename(vp) if vp else None,
                "narration_len": len(piece.payload.get("narration", "") or ""),
                "n_scenes": (piece.payload.get("narration", "") or "").count("\n") + 1,
                "elapsed_sec": round(_t.time() - t0)}
    except Exception as e:
        return {"err": repr(e), "tb": traceback.format_exc()[-1200:], "elapsed_sec": round(_t.time() - t0)}


@app.api_route("/admin/testaccount", methods=["GET", "POST"])
def admin_testaccount(email: str = "", pw: str = "", uses: int = 8):
    """지인 테스트 계정 생성/갱신 — 아이디(이메일)+비번 로그인 + 지정 횟수 부여."""
    if not (email and pw):
        return {"err": "email·pw 필요"}
    existing = db.get_user_by_email(email)
    h, salt = auth.hash_pw(pw)
    free_used = FREE_LIMIT - int(uses)     # 예: 2 - 8 = -6 → 8회 사용 가능
    if existing:
        uid = existing["id"]
        with db._conn() as c:
            c.execute("UPDATE users SET pw_hash=?, salt=?, free_used=?, plan='free' WHERE id=?",
                      (h, salt, free_used, uid))
    else:
        u = db.create_user(email=email, pw_hash=h, salt=salt)
        uid = u["id"]
        with db._conn() as c:
            c.execute("UPDATE users SET free_used=? WHERE id=?", (free_used, uid))
    return {"ok": True, "login_url": "https://ollinda.kr/login",
            "아이디": email, "비밀번호": pw, "부여횟수": int(uses), "신규": not existing}


@app.get("/admin/audiocheck")
def admin_audiocheck():
    """진단 — 프로덕션 오디오 체인(TTS 생성 + BGM 찾기 + mux) 어디서 무음이 되는지."""
    import subprocess
    import os
    import tempfile
    import re
    from app.media import bgm as _bgm, tts as _tts
    out = {}
    d = tempfile.mkdtemp()
    b = _bgm.pick()
    out["bgm_pick"] = b
    out["bgm_exists"] = bool(b and os.path.exists(b))
    out["tts_configured"] = _tts.configured()
    wav = None
    try:
        wav = _tts.synthesize("안녕하세요, 소리 테스트입니다. 잘 들리나요.", d)
        out["tts_ok"] = bool(wav and os.path.exists(wav) and os.path.getsize(wav) > 500)
        out["tts_size"] = os.path.getsize(wav) if wav and os.path.exists(wav) else 0
        out["tts_last_err"] = getattr(_tts, "LAST_ERR", "")
    except Exception as e:
        out["tts_err"] = repr(e)[:120]
    vid = os.path.join(d, "v.mp4")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=5", "-t", "5", vid], capture_output=True)
    wav_in = wav if (wav and os.path.exists(wav)) else os.path.join(d, "s.wav")
    if wav_in.endswith("s.wav"):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5", wav_in], capture_output=True)
    outv = os.path.join(d, "out.mp4")
    if b and os.path.exists(b):
        fc = ("[1:a]volume=1.0[v];[2:a]volume=0.22[bg];[v][bg]amix=inputs=2:duration=first:normalize=0[m];"
              "[m]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        cmd = ["ffmpeg", "-y", "-i", vid, "-i", wav_in, "-stream_loop", "-1", "-i", b,
               "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-shortest", outv]
    else:
        cmd = ["ffmpeg", "-y", "-i", vid, "-i", wav_in, "-filter_complex", "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]",
               "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-shortest", outv]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out["mux_ok"] = (r.returncode == 0 and os.path.exists(outv))
    if not out["mux_ok"]:
        out["mux_stderr"] = r.stderr[-500:]
    else:
        vol = subprocess.run(["ffmpeg", "-i", outv, "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
        m = re.search(r"mean_volume: ([\-0-9.]+)", vol)
        out["output_mean_db"] = m.group(1) if m else "?"
    return out


@app.get("/admin/ffmpegcheck")
def admin_ffmpegcheck():
    """진단 — 프로덕션 ffmpeg가 ASS 자막(libass)을 실제로 렌더하는지."""
    import subprocess
    import os
    import tempfile
    out = {}
    try:
        v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10).stdout
        out["version"] = v.split("\n")[0][:60]
        out["build_has_libass"] = "--enable-libass" in v
    except Exception as e:
        out["version_err"] = str(e)[:80]
    try:
        f = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10).stdout
        out["subtitles_filter"] = (" subtitles " in f)
    except Exception as e:
        out["filters_err"] = str(e)[:80]
    try:                                # 실제 자막 렌더 테스트
        from app.generators import video as _v
        d = tempfile.mkdtemp()
        ass = os.path.join(d, "t.ass")
        with open(ass, "w") as fp:
            fp.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 200\nPlayResY: 200\n\n"
                     "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, Alignment\nStyle: D,Pretendard,40,2\n\n"
                     "[Events]\nFormat: Layer, Start, End, Style, Text\n"
                     "Dialogue: 0,0:00:00.00,0:00:02.00,D,자막테스트\n")
        outv = os.path.join(d, "o.mp4")
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=200x200:d=2",
               "-vf", f"subtitles=filename='{ass}':fontsdir='{_v._FONT_DIR}'", "-t", "2", outv]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out["subtitle_render_ok"] = (r.returncode == 0 and os.path.exists(outv) and os.path.getsize(outv) > 500)
        if not out["subtitle_render_ok"]:
            out["subtitle_stderr"] = r.stderr[-400:]
        out["font_dir_exists"] = os.path.isdir(_v._FONT_DIR)
    except Exception as e:
        out["render_err"] = repr(e)[:150]
    return out


@app.get("/admin/videocheck")
def admin_videocheck():
    """진단 — 내 콘텐츠 영상 재생 체인(로컬/R2/URL/접근) 어디서 막히는지."""
    import os
    from app import storage as _st
    out = {"r2_configured": _st.r2_configured(),
           "R2_PUBLIC_URL_set": bool(os.environ.get("R2_PUBLIC_URL"))}
    shorts = []
    for t in db.list_tenants():
        for j in db.list_jobs(tenant_id=t.id, limit=60):
            p = db.get_piece(j["id"])
            if p and p.kind.value == "short" and p.channel.value == "youtube" and p.payload.get("video_path"):
                shorts.append(p)
    if not shorts:
        return {**out, "err": "no youtube short with video_path"}
    shorts.sort(key=lambda p: str(p.created_at or ""), reverse=True)
    out["total_youtube_shorts"] = len(shorts)
    out["recent"] = [{"dur": p.payload.get("duration_sec"),
                      "scene_note": (p.payload.get("_scene_note") or "(비어있음)")[:150],
                      "fname": os.path.basename(p.payload["video_path"])[:24]}
                     for p in shorts[:5]]
    piece = shorts[0]     # 가장 최신
    fname = os.path.basename(piece.payload["video_path"])
    local = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), piece.tenant_id, fname)
    out.update({"tenant": piece.tenant_id[:8], "fname": fname, "local_exists": os.path.exists(local),
                "newest_dur": piece.payload.get("duration_sec"),
                "newest_scene_note": (piece.payload.get("_scene_note") or "")[:160],
                "newest_assemble_note": (piece.payload.get("assemble_note") or "")[:120]})
    try:
        r2url = _st.r2_media_url(piece.tenant_id, fname)
        out["r2_url_built"] = bool(r2url)
        if r2url:
            import requests
            r = requests.get(r2url, headers={"Range": "bytes=0-1024", "User-Agent": "Mozilla/5.0"}, timeout=15)
            out["r2_fetch_status"] = r.status_code
            out["serves_ok"] = r.status_code in (200, 206)
    except Exception as e:
        out["r2_err"] = repr(e)[:120]
    return out


@app.get("/admin/geminicheck")
def admin_geminicheck():
    """진단 — 프로덕션 GEMINI_API_KEY로 텍스트·TTS·이미지 호출해 실제 작동 확인."""
    import os
    import requests
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"gemini": "no key on server"}
    base = "https://generativelanguage.googleapis.com/v1beta/models/"
    out = {"key_prefix": key[:9]}
    try:
        r = requests.post(base + "gemini-2.5-flash:generateContent", params={"key": key},
                          json={"contents": [{"parts": [{"text": "ok"}]}]}, timeout=20)
        out["text_ok"] = (r.status_code == 200)
    except Exception as e:
        out["text_err"] = str(e)[:80]
    try:
        r = requests.post(base + "gemini-2.5-flash-preview-tts:generateContent", params={"key": key},
                          json={"contents": [{"parts": [{"text": "안녕하세요"}]}],
                                "generationConfig": {"responseModalities": ["AUDIO"],
                                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}}},
                          timeout=45)
        out["tts_voice_ok"] = (r.status_code == 200)
        if r.status_code != 200:
            out["tts_msg"] = (r.json().get("error", {}).get("message", "") or "")[:80]
    except Exception as e:
        out["tts_err"] = str(e)[:80]
    # 네이버 검색광고(실검색량) 키 작동 확인
    try:
        from app.services import searchad
        out["searchad_configured"] = searchad.configured()
        if searchad.configured():
            v = searchad.keyword_volumes(["자동차썬팅"])
            out["searchad_ok"] = bool(v)
            out["searchad_sample"] = (f"{v[0]['keyword']}={v[0]['total']}/월" if v else "빈 결과")
    except Exception as e:
        out["searchad_err"] = str(e)[:80]
    return out


@app.post("/admin/shops/{tid}/autonomy")
def shop_autonomy(tid: str, level: int = Form(0)):
    db.set_autonomy(tid, level)
    return RedirectResponse("/admin/shops", status_code=303)


@app.post("/admin/shops/{tid}/profile")
def shop_profile(tid: str, phone: str = Form(""), address: str = Form(""),
                 hours: str = Form(""), map_url: str = Form("")):
    db.update_tenant_profile(tid, phone, address, hours, map_url)
    return RedirectResponse("/admin/shops", status_code=303)


@app.get("/admin/industries", response_class=HTMLResponse)
def industries_page():
    from app.industries import PROFILES
    inp = "border border-slate-200 rounded-lg px-2 py-1.5 text-sm w-full"
    # 프리셋(읽기 전용)
    pres = "".join(
        f"<div class='bg-white rounded-xl border border-slate-100 p-3 text-sm'>"
        f"<b>{esc(p.name)}</b> <span class='text-[11px] text-emerald-600'>프리셋</span>"
        f"<div class='text-xs text-slate-500 mt-1'>{esc(p.persona[:60])}…</div></div>"
        for p in PROFILES.values())
    # AI/수정 프로필(편집 가능)
    customs = db.list_industry_profiles()
    forms = ""
    for c in customs:
        forms += (
            f"<form method=post action='/admin/industries/{esc(c['key'])}' class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-3'>"
            f"<div class='flex items-center gap-2 mb-2'><b>{esc(c['name'])}</b>"
            f"<span class='text-[11px] px-2 py-0.5 rounded bg-violet-50 text-violet-600'>{esc(c.get('source','ai'))}</span></div>"
            f"<input type=hidden name=name value=\"{esc(c['name'])}\">"
            f"<label class='text-xs text-slate-500'>페르소나(말투)</label><textarea name=persona rows=2 class='{inp} mb-2'>{esc(c.get('persona',''))}</textarea>"
            f"<label class='text-xs text-slate-500'>톤</label><input name=tone value=\"{esc(c.get('tone',''))}\" class='{inp} mb-2'>"
            f"<label class='text-xs text-slate-500'>해시태그(쉼표)</label><input name=hashtags value=\"{esc(', '.join(c.get('hashtag_seeds',[])))}\" class='{inp} mb-2'>"
            f"<label class='text-xs text-slate-500'>콘텐츠 앵글(줄바꿈)</label><textarea name=angles rows=2 class='{inp} mb-2'>{esc(chr(10).join(c.get('content_angles',[])))}</textarea>"
            f"<label class='text-xs text-slate-500'>촬영 가이드(줄바꿈)</label><textarea name=photo rows=2 class='{inp} mb-2'>{esc(chr(10).join(c.get('photo_guide',[])))}</textarea>"
            f"<label class='text-xs text-slate-500'>CTA</label><input name=cta value=\"{esc(c.get('cta',''))}\" class='{inp} mb-2'>"
            f"<label class='text-xs text-slate-500'>주의(줄바꿈)</label><input name=cautions value=\"{esc(', '.join(c.get('cautions',[])))}\" class='{inp} mb-3'>"
            "<div class='flex gap-2'><button class='px-4 py-2 bg-indigo-600 text-white text-sm font-semibold rounded-xl'>저장</button>"
            f"<button formaction='/admin/industries/{esc(c['key'])}/regen' class='px-4 py-2 bg-slate-100 text-slate-700 text-sm font-semibold rounded-xl'>🤖 AI 재생성</button></div></form>")
    if not customs:
        forms = "<div class='bg-white rounded-2xl border border-slate-100 p-6 text-center text-slate-400'>AI 생성 업종이 아직 없습니다. 가게 추가 시 프리셋에 없는 업종이면 자동 생성됩니다.</div>"
    body = ("<h2 class='font-bold text-slate-700 mb-2'>🤖 AI 생성·수정 업종</h2>" + forms
            + "<h2 class='font-bold text-slate-700 mt-6 mb-2'>📌 프리셋 업종(코드 내장)</h2>"
            + f"<div class='grid sm:grid-cols-3 gap-2'>{pres}</div>")
    return shell("industries", "업종 프로필", body, subtitle="업종별 톤·해시태그·가이드 관리")


@app.post("/admin/industries/{key}")
def industries_save(key: str, name: str = Form(""), persona: str = Form(""), tone: str = Form(""),
                    hashtags: str = Form(""), angles: str = Form(""), photo: str = Form(""),
                    cta: str = Form(""), cautions: str = Form("")):
    from app.industries import _to_list
    data = {"key": key, "name": name, "aliases": [name], "persona": persona, "tone": tone,
            "hashtag_seeds": [("#" + t.lstrip("#")) for t in _to_list(hashtags)],
            "content_angles": _to_list(angles), "photo_guide": _to_list(photo),
            "cta": cta, "cautions": _to_list(cautions)}
    db.save_industry_profile(key, name, data, source="manual")
    return RedirectResponse("/admin/industries", status_code=303)


@app.post("/admin/industries/{key}/regen")
def industries_regen(key: str):
    from app.industries import _generate_ai
    cur = db.get_industry_profile(key)
    name = (cur or {}).get("name", key)
    data = _generate_ai(name, key)
    if data:
        db.save_industry_profile(key, name, data, source="ai")
    return RedirectResponse("/admin/industries", status_code=303)


# ── 계정 연결 (OAuth) ────────────────────────────────────
@app.get("/admin/connect/{tenant_id}", response_class=HTMLResponse)
def connect_page(tenant_id: str, ok: str = "", err: str = ""):
    t = db.get_tenant(tenant_id)
    if not t:
        return HTMLResponse("<p>없는 가게입니다.</p>", status_code=404)
    connected = {a.channel: a for a in db.list_channel_accounts(tenant_id)}
    rows = []
    for ch in CONNECTABLE:
        acc = connected.get(ch)
        if acc and acc.access_token_enc:
            meta = f" <span class='text-xs text-slate-400'>{esc(str(acc.meta))}</span>"
            state = f"<span class='text-green-600 text-sm font-semibold'>✅ 연결됨</span>{meta}"
            btn = (f"<a href='/admin/connect/{tenant_id}/{ch.value}/start' "
                   f"class='px-3 py-1.5 bg-slate-200 rounded-lg text-xs'>다시 연결</a>")
        elif oauth.configured(ch):
            state = "<span class='text-slate-400 text-sm'>미연결</span>"
            btn = (f"<a href='/admin/connect/{tenant_id}/{ch.value}/start' "
                   f"class='px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs'>연결하기</a>")
        else:
            state = "<span class='text-amber-600 text-sm'>⚙️ 앱 키 미설정</span>"
            btn = "<span class='text-xs text-slate-400'>env 설정 필요</span>"
        rows.append(f"<div class='bg-white rounded-xl shadow-sm p-4 mb-2 flex items-center justify-between'>"
                    f"<div><b>{CHANNEL_LABEL[ch]}</b><br>{state}</div>{btn}</div>")
    banner = ""
    if ok:
        banner = f"<div class='bg-green-50 text-green-700 p-3 rounded-lg mb-3 text-sm'>✅ {esc(ok)} 연결 완료</div>"
    if err:
        banner = f"<div class='bg-rose-50 text-rose-600 p-3 rounded-lg mb-3 text-sm'>⚠️ {esc(err)}</div>"
    note = ("<p class='text-xs text-slate-400 mt-4'>※ 네이버 블로그는 공식 발행 API가 없어 자동연결 불가(초안 제공→사장님 직접 발행). "
            "인스타는 비즈/크리에이터 계정 + Meta 앱 심사가 필요합니다.</p>")
    body = (nav("shops") + f"<a href='/admin/shops' class='text-sm text-slate-400'>← 가게</a>"
            f"<h1 class='text-xl font-bold mt-2 mb-4'>{esc(t.name)} · 계정 연결</h1>{banner}"
            + "".join(rows) + note)
    return page("계정 연결", body)


@app.get("/admin/connect/{tenant_id}/{channel}/start")
def connect_start(tenant_id: str, channel: str):
    try:
        ch = Channel(channel)
    except ValueError:
        return HTMLResponse("<p>지원하지 않는 채널.</p>", status_code=400)
    if not oauth.configured(ch):
        return RedirectResponse(f"/admin/connect/{tenant_id}?err=앱 키 미설정({channel})", status_code=303)
    return RedirectResponse(oauth.authorize_url(ch, tenant_id))


@app.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    tenant_id, ch = oauth.parse_state(state)
    if not tenant_id or not ch:
        return HTMLResponse("<p>잘못된 state(변조 의심).</p>", status_code=400)
    # 구독자 본인 가게면 /me로, 운영자면 /admin/connect로 복귀
    owner = db.get_user_by_tenant(tenant_id)
    base = "/me" if owner else f"/admin/connect/{tenant_id}"
    if error or not code:
        return RedirectResponse(f"{base}?err=취소되었거나 코드 없음", status_code=303)
    try:
        tok = oauth.exchange_code(ch, code, state)
        db.save_channel_account(tenant_id, ch, tok["access_token"], tok.get("refresh_token", ""), tok.get("meta"))
    except Exception as e:
        return RedirectResponse(f"{base}?err={esc(str(e)[:80])}", status_code=303)
    return RedirectResponse(f"{base}?ok={CHANNEL_LABEL.get(ch, ch.value)} 연결 완료", status_code=303)


# ── 사장님 업로드 ────────────────────────────────────────
def _upload_form_html(tenant, token: str, target_kw: str = "", angle: str = "",
                      src: str = "") -> str:
    """모던·간결 생성 카드 — 가게이름/링크 자동인식 + 사진 + 형태 + 목적 → 5채널 생성.
    target_kw/angle: 진단→생성 연결(상위노출 PHASE 1) — 이 키워드/앵글을 겨냥한 글 생성.
    src='briefing': 아침 브리핑 원클릭 진입(브리핑 PHASE 3) — 파트너 톤 배너."""
    bt = (tenant.biz_type or "local")
    _angle_lab = {"review": "후기형", "howto": "방법·과정형", "price": "가격·비용형"}.get(angle, "")
    target_banner = ""
    if target_kw and src == "briefing":
        # 브리핑 원클릭: "사진만 보내면 나머지는 제가" — 짐을 나눠 지는 경험
        target_banner = ("<div class='flex items-center gap-2.5 bg-[#EEF2FF] border border-indigo-200 rounded-2xl p-3.5'>"
                         f"{_ic('wand', 'w-5 h-5 text-indigo-600 flex-shrink-0')}<div class='text-sm text-slate-700'>"
                         "오늘 브리핑의 글감은 제가 잡아뒀어요"
                         + (f" · <b>{_angle_lab}</b>" if _angle_lab else "")
                         + " — <b>사진 3장만</b> 올려주세요. 글·영상·발행 준비는 제가 할게요.</div>"
                         "<button type=button onclick=\"fetch('/api/briefing/pass',{method:'POST'}).then(r=>r.json())"
                         ".then(d=>{alert(d.message||'내일 다시 브리핑드릴게요');location.href='/me';})\" "
                         "class='ml-auto text-xs text-slate-400 hover:text-slate-600 whitespace-nowrap'>오늘은 패스</button></div>")
    elif target_kw:
        target_banner = ("<div class='flex items-center gap-2.5 bg-amber-50 border border-amber-200 rounded-2xl p-3.5'>"
                         f"{_ic('target', 'w-5 h-5 text-amber-600 flex-shrink-0')}<div class='text-sm text-slate-700'>"
                         "이번 글의 글감은 AI가 정해뒀어요"
                         + (f" · <b>{_angle_lab}</b> 앵글" if _angle_lab else "")
                         + " — 제목·본문에 자연스럽게 반영돼요.</div>"
                         "<a href='/me' class='ml-auto text-xs text-slate-400 hover:text-slate-600 whitespace-nowrap'>해제 ×</a></div>")
    inp = ("w-full border border-slate-200 rounded-xl px-4 py-3 text-sm "
           "focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition")
    chips = "".join(
        "<label class='cursor-pointer'>"
        f"<input type=radio name=purpose value='{p}' class='peer sr-only'>"
        "<span class='inline-block px-3.5 py-2 rounded-full text-sm font-medium border border-slate-200 text-slate-600 "
        f"peer-checked:bg-indigo-600 peer-checked:text-white peer-checked:border-indigo-600 transition'>{p}</span></label>"
        for p in ["방문 유도", "판매 전환", "신상품 홍보", "이벤트·할인", "후기·신뢰"])

    def _bz(val, emoji, label):
        return ("<label class='cursor-pointer'>"
                f"<input type=radio name=biztype value='{val}'{' checked' if bt == val else ''} "
                f"onclick=\"document.getElementById('s_biz').value='{val}';bizFields('{val}')\" class='peer sr-only'>"
                "<div class='rounded-2xl border-2 border-slate-200 p-3.5 text-center transition "
                f"peer-checked:border-indigo-600 peer-checked:bg-indigo-50 peer-checked:text-indigo-700'>"
                f"<div class='text-2xl'>{emoji}</div><div class='font-bold text-sm mt-0.5'>{label}</div></div></label>")
    biz_toggle = ("<div class='grid grid-cols-2 gap-2.5'>" + _bz("local", _ic("store", "w-6 h-6 mx-auto text-indigo-600"), "동네 매장")
                  + _bz("seller", _ic("package", "w-6 h-6 mx-auto text-indigo-600"), "온라인 셀러") + "</div>")
    lb = "block text-sm font-bold text-slate-800 mb-2"
    # 매물 링크(선택) — 셀러·병행만. 입력 시 구매 CTA가 실링크로(발행 시 추적 URL 치환), 미입력 시 현행 문구.
    listing_field = ("" if bt == "local" else
                     f"<div><label class='{lb}'>매물·상품 링크 <span class='text-slate-400 font-normal text-xs'>(선택 — 넣으면 글의 구매 안내가 이 링크로 연결돼요)</span></label>"
                     f"<input name=listing_url placeholder='https:// 매물 상세·상품 페이지 주소' class='{inp}'></div>")
    # 저장된 가게정보로 미리 채움(한번 인식되면 계속) — 기본명은 비움
    _nm = esc(tenant.name) if getattr(tenant, "name", "") and tenant.name not in ("내 가게", "새 가게", "카카오회원", "구글회원") else ""
    _ind0 = esc(getattr(tenant, "industry", "") or "")
    _rg = esc(getattr(tenant, "region", "") or "")
    _tel0 = esc(getattr(tenant, "phone", "") or "")
    _addr = esc(getattr(tenant, "address", "") or "")
    _map0 = esc(getattr(tenant, "map_url", "") or "")
    _hint = (f"<span class='text-emerald-600 font-semibold'>✓ {_nm} · {_ind0} 저장됨 (수정 가능)</span>" if _nm else "입력하면 업종·주소가 자동으로 채워져요 (없어도 OK)")
    # 이미 저장된 가게(이름+업종)면 입력필드를 접어서 대시보드처럼 깔끔하게(펼치면 수정)
    _store_open = "" if (_nm and _ind0) else "open"
    _store_summary = (f"<b>{_nm}</b> · {_ind0} <span class='ml-1 text-indigo-500 font-bold'>✏️ 정보 수정 ▾</span>"
                      if _nm else "2. 내 가게 / 상품 정보")
    form = f"""<form method=post action='/u/{token}/upload' enctype='multipart/form-data' onsubmit='return showGen(event)' class='space-y-6'>
      <input type=hidden name=s_name id=s_name value="{_nm}"><input type=hidden name=s_industry id=s_industry value="{_ind0}"><input type=hidden name=s_biz id=s_biz value='{bt}'>
      <input type=hidden name=target_kw value="{esc(target_kw)}"><input type=hidden name=angle value="{esc(angle)}">
      {target_banner}
      <div><label class='{lb}'>1. 어떤 장사인가요?</label>{biz_toggle}</div>
      <details {_store_open} class='rounded-2xl border border-slate-100 bg-slate-50/50 p-4'><summary id=storeSummary class='{lb} mb-0 cursor-pointer select-none'>{_store_summary}</summary>
        <div id=lk_hint2 class='text-xs text-indigo-500 font-semibold mt-3 mb-1.5'></div>
        <div class='flex gap-2'>
          <input id=lk_q value="{_nm}" placeholder='가게 이름 (자동 인식)' class='{inp} flex-1'>
          <button type=button onclick='lookupStore()' class='px-5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold text-sm whitespace-nowrap transition'>자동 인식</button></div>
        <div id=lk_result class='text-xs mt-2 mb-2 text-slate-400'>{_hint}</div>
        <div id=sf_local class='grid grid-cols-2 gap-2'>
          <input name=s_region id=s_region value="{_rg}" placeholder='지역 (예: 부산 동구)' class='{inp}'>
          <input name=s_tel id=s_tel value="{_tel0}" placeholder='전화번호' class='{inp}'>
          <input name=s_address id=s_address value="{_addr}" placeholder='주소' class='{inp} col-span-2'>
          <input name=s_map id=s_map value="{_map0}" placeholder='네이버 플레이스 URL (선택)' class='{inp} col-span-2'></div>
        <div id=sf_seller class='grid grid-cols-2 gap-2 hidden'>
          <input name=s_buy id=s_buy value="{esc(getattr(tenant,'buy_url','') or '')}" placeholder='내 스토어/상품 링크 (손님이 갈 곳) *필수' class='{inp} col-span-2'>
          <input name=s_market id=s_market value="{esc(getattr(tenant,'marketplace','') or '')}" placeholder='마켓 (쿠팡·스마트스토어·11번가)' class='{inp}'>
          <input name=s_brand id=s_brand value="{esc(getattr(tenant,'brand_name','') or '')}" placeholder='브랜드명' class='{inp}'>
          <input name=s_search id=s_search value="{esc(getattr(tenant,'search_kw','') or '')}" placeholder='검색어 유도 (예: 폴딩박스)' class='{inp} col-span-2'></div></details>
      <div><label class='{lb}'>3. 사진 <span class='text-slate-400 font-normal text-xs'>(끌어서 순서 변경 · × 삭제)</span>
        <span class='inline-block ml-1 bg-indigo-50 text-indigo-600 text-[11px] font-bold px-2 py-0.5 rounded-full'>자동 전문가 보정</span></label>
        <div id=up_preview class='grid grid-cols-3 sm:grid-cols-4 gap-2'></div>
        <input type=file name=photos id=up_photos accept='image/*' multiple required class='hidden'>
        <p class='text-xs text-slate-400 mt-1.5'><b class='text-slate-500'>＋</b> 로 여러 장 추가(최대 30장) · <b class='text-slate-500'>사진은 내용에 맞는 위치에 AI가 자동 배치해요</b> — 순서 신경 안 쓰셔도 돼요</p>
        <p class='text-xs text-slate-400 mt-1'><b class='text-amber-500'>★</b> 를 누르면 그 사진이 <b class='text-slate-500'>영상 대표 사진</b>이 돼요 (안 고르면 AI가 선택)</p>
        <label class='flex items-center gap-2 text-sm text-slate-600 mt-2 cursor-pointer'>
          <input type=checkbox id=pg_overlay_cb checked class='w-4 h-4 rounded accent-indigo-600'>
          사진 속 문구·워터마크 지우기 <span class='text-[11px] text-slate-400'>(번호판·개인정보 가림은 항상 자동)</span></label>
        <p class='text-[11px] text-slate-400 mt-1'>※ <b class='text-slate-500'>본인이 촬영했거나 사용 권리를 가진 사진</b>만 올려주세요.</p></div>
      <div><label class='{lb}'>4. 목적 <span class='text-slate-400 font-normal text-xs'>(선택)</span></label>
        <div class='flex flex-wrap gap-2'>{chips}</div></div>
      {listing_field}
      <div><label class='{lb}'>5. 사진 확인·정보 <span class='text-slate-400 font-normal text-xs'>(선택 · 넣을수록 글이 구체적으로 좋아져요)</span></label>
        <input type=hidden name=confirmed id=pg_confirmed><input type=hidden name=intent id=pg_intent><input type=hidden name=vision_analysis id=pg_vision>
        <input type=hidden name=answers id=pg_answers><input type=hidden name=experience id=pg_experience>
        <input type=hidden name=stash_keys id=pg_stashkeys>
        <input type=hidden name=hero_idx id=pg_hero><input type=hidden name=clean_overlay id=pg_overlay value=1>
        <div id=pg_guess class='mb-2'></div>
        <div id=pg_questions class='mb-2'></div>
        <input name=note maxlength=50 oninput="var c=document.getElementById('reqc');if(c)c.textContent=this.value.length+'/50';" placeholder='꼭 반영할 요청 (예: 급매 강조 / 차분한 톤)' class='{inp}'>
        <div class='text-right text-xs text-slate-400 mt-1'><span id=reqc>0/50</span></div></div>
      <button id=pd_submit class='w-full py-4 rounded-2xl bg-indigo-600 hover:bg-indigo-700 text-white font-extrabold text-lg transition disabled:opacity-40 disabled:cursor-not-allowed'>5채널 콘텐츠 생성하기</button>
      <div id=pd_submit_hint class='hidden text-center text-xs text-slate-400'></div>
      <p class='text-center text-xs text-slate-400'>인스타·네이버·유튜브·X + 영상을 AI가 자동 생성 (20~40초)</p></form>"""
    js = ("<script>"
          "function bizFields(v){var l=document.getElementById('sf_local'),s=document.getElementById('sf_seller');if(l&&s){if(v==='seller'){l.classList.add('hidden');s.classList.remove('hidden');}else{s.classList.add('hidden');l.classList.remove('hidden');}}"
          "var q=document.getElementById('lk_q'),h=document.getElementById('lk_hint2');"
          "if(v==='seller'){if(q)q.placeholder='내 상품/스토어 링크 붙여넣기 (또는 상품명)';if(h)h.innerHTML='내 상품 링크를 붙이면 그게 손님이 갈 <b>판매 링크</b>가 돼요. 링크 없으면 상품명으로 검색(정보만) 후 <b>내 링크는 직접 입력</b>.';}"
          "else{if(q)q.placeholder='가게 이름 (자동 인식)';if(h)h.innerHTML='';}}"
          "var PM={f:[],drag:-1,hero:-1};"
          "function pmHero(i){PM.hero=(PM.hero===i)?-1:i;pmRender();}"
          + f"var UPTOK='{token}';"
          # 사진 보정 선행(개선 ③): 사진이 목록에 들어오는 즉시 원본을 서버에 선업로드 → 서버가
          # 백그라운드로 워터마크 제거·개인정보 가림을 미리 실행. 제출 시 키를 돌려줘 보정본 재사용.
          "PM.sk=new WeakMap();"
          "async function pmStash(f){try{var fd=new FormData();fd.append('token',UPTOK);fd.append('photo',f);"
          "var d=await (await fetch('/api/intake/stash',{method:'POST',body:fd})).json();"
          "PM.sk.set(f,(d&&d.ok&&d.key)?d.key:'');}catch(e){PM.sk.set(f,'');}}"
          "function pmStashAll(){PM.f.forEach(function(x){if(!PM.sk.has(x)){PM.sk.set(x,'pending');pmStash(x);}});}"
          "function pmSync(){var dt=new DataTransfer();PM.f.forEach(function(x){dt.items.add(x);});document.getElementById('up_photos').files=dt.files;pmStashAll();}"
          "function pmDel(i){PM.f.splice(i,1);if(PM.hero===i)PM.hero=-1;else if(PM.hero>i)PM.hero--;pmRender();if(typeof pdOffer==='function')pdOffer();}"
          "function pmAdd(){document.getElementById('up_photos').click();}"
          "function pmDrop(target){if(PM.drag<0)return;var hf=(PM.hero>=0)?PM.f[PM.hero]:null;var it=PM.f.splice(PM.drag,1)[0];if(target>PM.f.length)target=PM.f.length;if(target<0)target=0;PM.f.splice(target,0,it);PM.drag=-1;if(hf)PM.hero=PM.f.indexOf(hf);pmRender();}"
          "function pmRender(){var pv=document.getElementById('up_preview');pv.innerHTML='';"
          "PM.f.forEach(function(x,i){var d=document.createElement('div');d.className='relative aspect-square cursor-move';d.draggable=true;"
          "d.ondragstart=function(e){PM.drag=i;e.dataTransfer.effectAllowed='move';};"
          "d.ondragover=function(e){e.preventDefault();d.classList.add('ring-2','ring-indigo-400');};"
          "d.ondragleave=function(){d.classList.remove('ring-2','ring-indigo-400');};"
          "d.ondrop=function(e){e.preventDefault();d.classList.remove('ring-2','ring-indigo-400');pmDrop(i);};"
          "var im=document.createElement('img');im.src=URL.createObjectURL(x);im.className='w-full h-full object-cover rounded-xl border border-slate-100 pointer-events-none';d.appendChild(im);"
          "d.insertAdjacentHTML('beforeend',"
          "\"<div class='absolute top-1 left-1 w-5 h-5 rounded-full bg-black/60 text-white text-[10px] font-bold flex items-center justify-center pointer-events-none'>\"+(i+1)+\"</div>\"+"
          "\"<button type=button onclick='pmDel(\"+i+\")' class='absolute top-1 right-1 w-5 h-5 rounded-full bg-rose-500 text-white text-xs leading-none flex items-center justify-center'>&times;</button>\"+"
          "\"<button type=button onclick='pmHero(\"+i+\")' title='영상 대표 사진' class='absolute bottom-1 left-1 w-6 h-6 rounded-full \"+(PM.hero===i?'bg-amber-400 text-white':'bg-black/40 text-white/80')+\" text-xs leading-none flex items-center justify-center'>\\u2605</button>\"+"
          "(PM.hero===i?\"<div class='absolute bottom-1 right-1 bg-amber-400 text-white text-[9px] font-bold px-1.5 py-0.5 rounded pointer-events-none'>영상 대표</div>\":''));"
          "pv.appendChild(d);});"
          "var add=document.createElement('button');add.type='button';add.onclick=pmAdd;"
          "add.className='aspect-square rounded-xl border-2 border-dashed border-slate-300 text-slate-400 hover:border-indigo-400 hover:text-indigo-500 flex flex-col items-center justify-center transition';"
          "add.ondragover=function(e){e.preventDefault();};add.ondrop=function(e){e.preventDefault();pmDrop(PM.f.length);};"
          "add.innerHTML=\"<span class='text-2xl leading-none'>＋</span><span class='text-[10px] mt-0.5'>사진 추가</span>\";pv.appendChild(add);pmSync();}"
          # 유료 폼 스마트 입력(콘텐츠생성 PHASE 7) — AI 선추측 확인 + 업종별 질문(공용 헬퍼 재사용)
          "function pdReady(ok,msg){var b=document.getElementById('pd_submit'),h=document.getElementById('pd_submit_hint');"
          "if(b)b.disabled=!ok;if(h){h.textContent=msg||'';h.classList.toggle('hidden',!msg);}}"
          # 동의 단계(무료 UX 이식): 사진 정리 끝나면 '분석 시작' — 자동 분석으로 비용 낭비 방지
          "var _pgseq=0;"
          "function pdOffer(){var box=document.getElementById('pg_guess');if(!box)return;_pgseq++;"
          "var c=document.getElementById('pg_confirmed'),v=document.getElementById('pg_vision');if(c)c.value='';if(v)v.value='';pdReady(true,'');"
          "if(!PM.f.length){box.innerHTML='';return;}"
          "box.innerHTML='<div class=\"bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm\">'"
          "+'<div class=\"text-slate-700\">사진 <b>'+PM.f.length+'장</b> 준비됐어요. <b>3초 뒤 자동으로 AI 확인</b>을 시작해요 — 사진을 정리하면 다시 미뤄져요.</div>'"
          "+'<div class=\"flex items-center gap-2 mt-2\"><button type=\"button\" id=\"pg_start\" class=\"px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold\">지금 바로 시작</button>'"
          "+'<span class=\"text-[11px] text-slate-400\">안 해도 바로 만들 수 있어요</span></div></div>';"
          "document.getElementById('pg_start').onclick=function(){paidGuess();};"
          # 분석 자동 시작(테트리스 원칙 1) — 3초 디바운스: 목록이 바뀌면 _pgseq가 올라 예약이 무효됨
          "var _as=_pgseq;setTimeout(function(){if(_as===_pgseq&&PM.f.length)paidGuess();},3000);}"
          "async function paidGuess(){var box=document.getElementById('pg_guess');if(!box||!PM.f.length)return;"
          "var seq=++_pgseq,fin=false;pdReady(false,'사진을 확인하는 중이에요 — 잠시만요');"
          "box.innerHTML='<div class=\"bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5\">'"
          "+'<div id=\"pg_pl\" class=\"text-xs font-bold text-slate-600 mb-1.5\">사진 분석 중…</div>'"
          "+'<div class=\"w-full h-1.5 bg-slate-200 rounded-full overflow-hidden\"><div id=\"pg_pb\" class=\"h-full bg-indigo-500 rounded-full\" style=\"width:15%;transition:width .5s\"></div></div></div>';"
          "var stg=['사진 분석 중…','무엇이 담겼는지 파악 중…','거의 다 됐어요…'],si=0,w=15;"
          "var st=setInterval(function(){var l=document.getElementById('pg_pl'),b=document.getElementById('pg_pb');"
          "if(!l||!b){clearInterval(st);return;}si=Math.min(si+1,2);w=Math.min(w+22,90);l.textContent=stg[si];b.style.width=w+'%';},2200);"
          "var n=Math.min(PM.f.length,8),tmo=Math.min(45000,25000+4000*n);"
          "var to=setTimeout(function(){if(fin||seq!==_pgseq)return;fin=true;clearInterval(st);"
          "box.innerHTML='';pdReady(true,'사진 확인이 오래 걸려 건너뛰었어요 — 바로 만들 수 있어요');},tmo);"
          "var fd=new FormData();fd.append('industry',(document.getElementById('s_industry')||{}).value||'');"
          "fd.append('purpose',(document.querySelector('input[name=purpose]:checked')||{}).value||'');"
          # 전 장수 전송(사진분석 단일화) — 1280px 축소 후 업로드(landing 무료폼과 동일 패턴). 생성 시 재사용 → vision 중복 0.
          "async function _shr(f){try{if(!/^image\\//.test(f.type||''))return f;"
          "var bmp=await createImageBitmap(f);var mx=Math.max(bmp.width,bmp.height);"
          "if(mx<=1280&&f.size<1500000)return f;"
          "var s=Math.min(1,1280/mx),cv=document.createElement('canvas');"
          "cv.width=Math.round(bmp.width*s);cv.height=Math.round(bmp.height*s);"
          "cv.getContext('2d').drawImage(bmp,0,0,cv.width,cv.height);"
          "var b=await new Promise(function(r){cv.toBlob(r,'image/jpeg',0.85);});"
          "return b?new File([b],(f.name||'p').replace(/\\.[^.]+$/,'')+'.jpg',{type:'image/jpeg'}):f;}catch(e){return f;}}"
          "var _sm=await Promise.all(PM.f.slice(0,30).map(_shr));if(fin||seq!==_pgseq)return;"
          "_sm.forEach(function(f){fd.append('photos',f);});"
          "try{var r=await fetch('/api/intake/guess',{method:'POST',body:fd});var d=await r.json();"
          "if(fin||seq!==_pgseq)return;fin=true;clearTimeout(to);clearInterval(st);"
          "if(d.guess&&window.intakeConfirmUI){intakeConfirmUI(box,d.guess,d.analysis||'','pg_confirmed','pg_vision',function(){pdReady(true,'');},"
          "{interp:d.interpretation||'',conf:d.confidence||'',choices:d.choices||[],learned:d.learned_intent||'',iid:'pg_intent'});"
          "var s=document.createElement('button');s.type='button';s.className='block mx-auto mt-1.5 text-[11px] text-slate-400 underline';"
          "s.textContent='확인 건너뛰고 진행';s.onclick=function(){box.innerHTML='';pdReady(true,'');};box.appendChild(s);"
          "pdReady(false,'위 사진 확인(맞아요/수정) 후 만들 수 있어요');}"
          "else{box.innerHTML='';pdReady(true,'');}"
          "}catch(e){if(fin||seq!==_pgseq)return;fin=true;clearTimeout(to);clearInterval(st);box.innerHTML='';pdReady(true,'');}}"
          "function paidQuestions(){var i=(document.getElementById('s_industry')||{}).value||'';"
          "var p=(document.querySelector('input[name=purpose]:checked')||{}).value||'';"
          "if(window.intakeQuestionsUI)intakeQuestionsUI(document.getElementById('pg_questions'),i,(document.getElementById('s_biz')||{}).value||'local',p,'pg_exp');}"
          "(function(){var inp=document.getElementById('up_photos');if(inp){inp.addEventListener('change',function(){Array.from(inp.files||[]).forEach(function(x){PM.f.push(x);});pmRender();pdOffer();});pmRender();}bizFields((document.getElementById('s_biz')||{}).value||'local');"
          "setTimeout(paidQuestions,300);"     # 저장된 업종으로 최초 질문 로드(프리필: 매장정보는 고정블록이라 안 물음)
          "document.querySelectorAll('input[name=purpose]').forEach(function(r){r.addEventListener('change',paidQuestions);});"
          "var f=document.querySelector('form[action$=\"/upload\"]');"
          "if(f)f.addEventListener('submit',function(e){var b=document.getElementById('pd_submit');if(b&&b.disabled){e.preventDefault();return;}var a=document.getElementById('pg_answers');if(a)a.value=JSON.stringify(window.__intakeAnswers||{});"
          "var sk=document.getElementById('pg_stashkeys');"
          "if(sk)sk.value=JSON.stringify(PM.f.map(function(x){var k=PM.sk.get(x);return (k&&k!=='pending')?k:'';}));"
          "var hh=document.getElementById('pg_hero');if(hh)hh.value=(PM.hero>=0)?String(PM.hero):'';"
          "var ov=document.getElementById('pg_overlay'),cb=document.getElementById('pg_overlay_cb');if(ov&&cb)ov.value=cb.checked?'1':'0';"
          "var e1=document.getElementById('pg_exp'),e2=document.getElementById('pg_experience');if(e1&&e2)e2.value=e1.value||'';});})();"
          "function fillStore(d){document.getElementById('s_name').value=d.name||'';document.getElementById('s_industry').value=d.industry||'';"
          "var bz=(d.type==='seller')?'seller':'local';document.getElementById('s_biz').value=bz;bizFields(bz);"
          "document.getElementById('s_region').value=d.region||'';document.getElementById('s_tel').value=d.tel||'';if(d.buy_url){document.getElementById('s_buy').value=d.buy_url;}"
          "document.getElementById('s_address').value=d.address||'';"
          "var mp=document.getElementById('s_map');if(mp)mp.value=d.map_url||'';document.getElementById('lk_q').value=d.name||document.getElementById('lk_q').value;"
          "var mk=document.getElementById('s_market');if(mk&&d.market)mk.value=d.market;var br=document.getElementById('s_brand');if(br&&d.brand)br.value=d.brand;var sk=document.getElementById('s_search');if(sk&&d.search_kw)sk.value=d.search_kw;"
          "var rb=document.querySelector('input[name=biztype][value=\"'+bz+'\"]');if(rb)rb.checked=true;"
          "var kind=(bz==='seller')?'온라인 셀러':'동네 매장';"
          "document.getElementById('lk_result').innerHTML='<span class=\"text-emerald-600 font-semibold\">✓ '+(d.name||'')+' · '+(d.industry||'')+(d.region?(' · '+d.region):'')+' 선택됨 (저장)</span>';"
          "if(typeof paidQuestions==='function')paidQuestions();"
          "try{if(d.name){var fd2=new FormData();fd2.append('name',d.name||'');fd2.append('industry',d.industry||'');fd2.append('region',d.region||'');fd2.append('biz_type',bz);fd2.append('phone',d.tel||'');fd2.append('address',d.address||'');fd2.append('map_url',d.map_url||'');if(d.buy_url)fd2.append('buy_url',d.buy_url);if(d.lat)fd2.append('lat',d.lat);if(d.lon)fd2.append('lon',d.lon);if(d.market)fd2.append('marketplace',d.market);if(d.brand)fd2.append('brand_name',d.brand);if(d.search_kw)fd2.append('search_kw',d.search_kw);fetch('/me/store',{method:'POST',body:fd2});}}catch(_){}}"
          "function pickCand(i){var c=(window.__cands||[])[i];if(c){c.type='local';fillStore(c);}}"
          "async function lookupStore(){var q=document.getElementById('lk_q').value.trim();if(!q)return;"
          "var b=document.getElementById('lk_result');b.innerHTML='<span class=\"text-slate-400\">인식 중…</span>';"
          "var _bz=((document.querySelector('input[name=biztype]:checked')||{}).value)||(document.getElementById('s_biz')||{}).value||'';"
          "try{var r=await fetch('/api/lookup?q='+encodeURIComponent(q)+(_bz?('&biz='+_bz):''));var d=await r.json();"
          "if(d.type==='none'){b.innerHTML='<span class=\"text-slate-400\">못 찾았어요 — 그냥 사진 올리고 만들어도 돼요</span>';return;}"
          "if(d.candidates&&d.candidates.length>1){window.__cands=d.candidates;"
          "var _isS=(d.candidates[0].mall!==undefined||d.candidates[0].price);"
          "b.innerHTML='<div class=\"text-amber-600 font-semibold mb-1\">⚠️ 여러 개가 있어요. 내 '+(_isS?'상품':'가게')+'을(를) 선택하세요:</div>'+d.candidates.map(function(c,i){var meta=(c.mall||c.industry||'');var sub=(c.price?(Number(c.price).toLocaleString()+'원'):(c.address||''));return '<button type=button onclick=\"pickCand('+i+')\" class=\"block w-full text-left bg-white border border-slate-200 rounded-lg p-2 mb-1 text-xs hover:bg-indigo-50\"><b>'+c.name+'</b> <span class=\"text-slate-400\">'+meta+'</span><br><span class=\"text-slate-400\">'+sub+'</span></button>';}).join('');return;}"
          "fillStore(d);"
          "}catch(e){b.innerHTML='<span class=\"text-rose-400\">인식 실패</span>';}}"
          "async function showGen(e){if(e&&e.preventDefault)e.preventDefault();var f=(e&&e.target)?e.target:document.querySelector('form[action*=\"/upload\"]');"
          "var o=document.getElementById('genOverlay');o.classList.remove('hidden');o.classList.add('flex');"
          "try{if(window.Notification&&Notification.permission==='default')Notification.requestPermission();}catch(_){}"
          # ★ 실제 진행률(/me/gen-progress)의 단계별 라벨·퍼센트를 그대로 표시(가짜 타이머 폐기).
          #   사용자가 지금 뭘 하는지 정확히 봄: '사진 3/16장 분석'→'블로그 글 쓰는 중'→'인스타 캡션'→'다듬는 중'→'영상'.
          "function setBar(v){var b=document.getElementById('gBar');if(b)b.style.width=Math.max(4,Math.min(100,v))+'%';var g=document.getElementById('gPct');if(g)g.textContent=Math.round(v)+'%';}"
          "function setLabel(t){var gl=document.getElementById('gLabel');if(gl&&t)gl.textContent=t;}"
          "function setDetail(t){var d=document.getElementById('gDetail');if(d)d.textContent=t||'';}"
          "function setSlow(t){var s=document.getElementById('gSlow');if(s)s.textContent=t||'';}"
          "var base=0;try{base=(await (await fetch('/me/sets/count')).json()).n;}catch(_){}"
          "var fd=new FormData(f);try{if(window.PM&&PM.f&&PM.f.length){fd.delete('photos');PM.f.forEach(function(x){fd.append('photos',x);});}}catch(_){}"
          "try{await fetch(f.action,{method:'POST',body:fd});}catch(_){}"
          "var aid='';var n=0;"
          "function done(url){clearInterval(iv);location.href=url;}"
          "var iv=setInterval(async function(){n++;if(n>240){done(aid?('/me?view='+aid):'/me');return;}"
          "try{"
          "var pr=await (await fetch('/me/gen-progress')).json();"
          "if(pr&&pr.status&&pr.status!=='idle'){if(pr.label)setLabel(pr.label);if(pr.pct!=null)setBar(pr.pct*100);setDetail(pr.detail||'');setSlow(pr.slow||'');"
          "if(pr.status==='failed'){clearInterval(iv);setLabel('생성이 중단됐어요 — 다시 시도해 주세요');setSlow('사진 수를 줄이거나 잠시 후 다시 시도해 주세요');return;}"
          # ★ 완성의 순간(테트리스 원칙 4): done 신호 → 보러가기 버튼 + 3초 자동 이동 + (딴 탭이면) 브라우저 알림.
          #   구조건(피스 5개)은 영상 온디맨드 이후 영원히 안 채워져 사용자가 100%에서 방치됐음(캡처 실측).
          "if(pr.status==='done'){clearInterval(iv);"
          "if(!aid){try{var d0=await (await fetch('/me/sets/count')).json();if(d0.n>base)aid=d0.latest;}catch(_){}}"
          "var url=aid?('/me?view='+aid):'/me';"
          "setBar(100);setLabel('✅ 콘텐츠 완성!');setDetail('영상은 목록에서 원하는 플랫폼을 골라 만들 수 있어요');setSlow('');"
          "var tm=document.getElementById('gTeam');if(tm)tm.textContent='3초 뒤 자동으로 이동해요';"
          "var go=document.getElementById('gGo');if(go){go.href=url;go.classList.remove('hidden');}"
          "try{if(document.hidden&&window.Notification&&Notification.permission==='granted')"
          "new Notification('올린다 — 콘텐츠 완성!',{body:'글과 사진이 준비됐어요. 눌러서 확인하세요.'});}catch(_){}"
          "setTimeout(function(){location.href=url;},3000);return;}}"
          "if(!aid){var d=await (await fetch('/me/sets/count')).json();if(d.n>base){aid=d.latest;}}"
          "}catch(_){}"
          "},2000);return false;}"
          "</script>")
    gen_overlay = ("<div id='genOverlay' class='fixed inset-0 z-50 hidden items-center justify-center' style='background:rgba(15,23,42,.45);backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)'>"
                   "<div class='bg-white rounded-2xl p-6 w-80 max-w-[88vw] text-center shadow-2xl'>"
                   "<div id='gLabel' class='font-bold text-sm mb-1'>준비 중…</div>"
                   "<div id='gDetail' class='text-xs text-indigo-500 mb-2 min-h-4'></div>"
                   "<div class='w-full h-2 bg-slate-100 rounded-full overflow-hidden'><div id='gBar' class='h-full bg-indigo-500' style='width:4%;transition:width .4s'></div></div>"
                   "<div id='gPct' class='text-slate-400 text-xs mt-1.5'>0%</div>"
                   "<div id='gSlow' class='text-xs text-amber-600 mt-2'></div>"
                   "<a id='gGo' class='hidden block mt-3 w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 "
                   "text-white font-extrabold text-sm transition'>지금 보러 가기 →</a>"
                   "<p id='gTeam' class='text-xs text-slate-400 mt-3'>AI 전문가팀이 만드는 중…</p></div></div>")
    return form + js + gen_overlay


@app.get("/u/{token}", response_class=HTMLResponse)
def upload_form(token: str):
    tenant, _ = db.get_tenant_by_token(token)
    if not tenant:
        return HTMLResponse("<p>잘못된 링크입니다.</p>", status_code=404)
    body = (f"<h1 class='text-xl font-bold mb-1'>{esc(tenant.name)}</h1>"
            f"<p class='text-slate-500 text-sm mb-5'>사진과 한 줄 설명만 보내주세요. 나머지는 저희가 합니다 🙂</p>"
            + _upload_form_html(tenant, token))
    return page(f"{tenant.name} · 업로드", body)


@app.post("/u/{token}/upload", response_class=HTMLResponse)
async def upload(token: str, req: Request, photos: list[UploadFile] = File(...), note: str = Form(""),
                 purpose: str = Form(""), target: str = Form(""), extra: str = Form(""),
                 request: str = Form(""), s_name: str = Form(""), s_industry: str = Form(""),
                 s_biz: str = Form(""), s_region: str = Form(""), s_tel: str = Form(""),
                 s_buy: str = Form(""), s_address: str = Form(""), photo_desc: str = Form(""),
                 listing_url: str = Form(""),
                 s_map: str = Form(""), s_market: str = Form(""), s_brand: str = Form(""),
                 s_search: str = Form(""), target_kw: str = Form(""), angle: str = Form(""),
                 confirmed: str = Form(""), vision_analysis: str = Form(""),
                 answers: str = Form(""), experience: str = Form(""), intent: str = Form(""),
                 stash_keys: str = Form(""), hero_idx: str = Form(""),
                 clean_overlay: str = Form("1")):
    tenant, _ = db.get_tenant_by_token(token)
    if not tenant:
        return HTMLResponse("<p>잘못된 링크입니다.</p>", status_code=404)
    # 가게명/업종 자동인식 + 동적 가게정보(매장:지역·전화·주소·플레이스 / 셀러:마켓·브랜드·검색어·링크) 저장
    if s_name.strip() or s_industry.strip():
        db.rename_tenant(tenant.id, s_name.strip() or tenant.name,
                         s_industry.strip() or tenant.industry, s_region.strip() or tenant.region)
    if any(x.strip() for x in (s_tel, s_address, s_map, s_region)):
        db.update_tenant_profile(tenant.id, s_tel.strip() or tenant.phone,
                                 s_address.strip() or tenant.address, tenant.hours, s_map.strip() or tenant.map_url)
    _bz = s_biz.strip() if s_biz.strip() in ("local", "seller", "hybrid") else (tenant.biz_type or "local")
    if _bz != (tenant.biz_type or "local") or any(x.strip() for x in (s_market, s_buy, s_search, s_brand)):
        db.update_tenant_classification(tenant.id, _bz, s_market.strip() or tenant.marketplace,
                                        s_buy.strip() or tenant.buy_url, s_search.strip() or tenant.search_kw,
                                        s_brand.strip() or tenant.brand_name)
    tenant, _ = db.get_tenant_by_token(token)   # 갱신본 재로드 (업종 프로필 생성은 백그라운드에서)
    # 플랜별 쿼터(셀프서비스 가게만; 운영자/대행 tenant는 owner 없음 → 무제한)
    owner = db.get_user_by_tenant(tenant.id)
    block = _quota_block(owner)
    if block:
        return page("이용 안내", block)
    files = await _read_image_uploads(photos)
    if not files:
        return HTMLResponse("<p>이미지 파일을 한 장 이상 올려주세요. (jpg·png·webp·heic, 최대 25MB)</p>", status_code=400)
    # 선행 보정(개선 ③) 회수: stash가 이미 정리(워터마크·개인정보)한 장은 보정본 바이트로 대체하고
    # 인덱스를 표시 — ingest가 그 장의 느린 정리를 스킵. 미완료·실패·불일치는 원본 그대로(안전 폴백).
    # 워터마크 제거를 끈 경우 stash 보정본을 쓰지 않음(이미 오버레이가 제거돼 있어 되돌릴 수 없음) —
    # 원본으로 진행하고 ingest가 PII 마스킹만 실행(개인정보 가림은 사용자 선택과 무관하게 항상).
    _overlay_on = clean_overlay.strip() != "0"
    _pre_idx: set = set()
    if stash_keys.strip() and _overlay_on:
        try:
            import json as _sj
            _keys = _sj.loads(stash_keys)
            for _i, _k in enumerate(_keys if isinstance(_keys, list) else []):
                if _i >= len(files) or not _k:
                    continue
                _e = _INTAKE_STASH.get(str(_k))
                if (_e and _e.get("tid") == tenant.id and _e.get("done") and _e.get("cleaned")
                        and os.path.exists(_e.get("path", ""))):
                    with open(_e["path"], "rb") as _sf:
                        files[_i] = (_sf.read(), os.path.basename(_e["path"]))
                    _pre_idx.add(_i)
        except Exception:
            _pre_idx = set()
    # 사진 설명·목적·요청(최대 50자)을 메모에 합쳐 AI 생성 품질↑
    parts = []
    if photo_desc.strip():
        parts.append(f"[사진 설명] {photo_desc.strip()[:120]}")   # AI가 사진 내용을 정확히 이해
    if purpose:
        parts.append(f"[콘텐츠 목적] {purpose}")
    if target:
        parts.append(f"[타겟 고객] {target}")
    if extra:
        parts.append(f"[추가 정보] {extra}")
    full_note = "\n".join(parts)
    user_req = (note or request or "").strip()[:50]   # 사용자 요청 = 최대 50자, 최우선 반영 (req=Request 파라미터와 충돌 금지)
    if user_req:
        full_note = f"[반드시 반영할 요청] {user_req}\n" + full_note
    _lurl = (listing_url or "").strip()[:300]
    if _lurl.startswith(("http://", "https://")):     # 매물 링크(선택) — 구매 CTA를 실링크로(발행 시 추적 URL 치환)
        full_note += f"\n[매물 링크(실제 — 구매/상세 CTA에 이 링크를 그대로 사용, X 제외)] {_lurl}"
    # 생성은 시간이 오래 걸려(전략가→3채널→SEO편집) 요청을 붙잡으면 서버 타임아웃(500).
    # → 백그라운드 스레드에서 생성하고 요청은 즉시 반환. 완료되면 대시보드에 자동 표시.
    _ind = s_industry.strip()
    _record_usage(owner)                           # 쿼터 선예약 — 동시 업로드로 한도 우회 방지(B7)

    def _bg_generate():
        nonlocal target_kw, angle
        # 💾 죽지 않는 잡: 입력을 스풀에 보존 + 잡 기록 — 배포·재시작으로 스레드가 죽어도
        #   부팅 시 이어하기(사고 4회의 근본책, 2026-07-29 사장님 승인)
        _job_id = str(__import__("uuid").uuid4())
        try:
            _spool = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"),
                                  tenant.id, "_jobspool", _job_id)
            os.makedirs(_spool, exist_ok=True)
            for _ji, (_jb, _jn) in enumerate(files):
                with open(os.path.join(_spool, f"{_ji:02d}_{os.path.basename(_jn)[:40]}"), "wb") as _jf:
                    _jf.write(_jb)
            db.save_gen_job(_job_id, tenant.id, _spool,
                            {"note": full_note, "target_kw": (target_kw or "").strip()[:40],
                             "angle": (angle or "").strip(),
                             "pre_idx": sorted(_pre_idx or [])})
        except Exception:
            logging.getLogger("shopcast.jobs").exception("[gen-job] 스풀 실패(생성은 계속)")
        try:
            _prune_old_media(tenant.id, keep_recent=5)   # 생성 전 오래된 영상 정리(디스크 확보)
            # 자동 글감(auto): 타겟 미지정 업로드면 큐가 다음 글감(키워드·앵글)을 결정한다
            _q_claim = None
            if not (target_kw or "").strip():
                try:
                    from app.services import autoqueue as _aq
                    from app import db as _db2
                    if not _db2.writing_queue_rows(tenant.id, status="pending", limit=1):
                        _aq.refill(tenant)
                    _q_claim = _db2.claim_writing(tenant.id)
                    if _q_claim:
                        target_kw = _q_claim["target_keyword"]
                        if _q_claim.get("angle") in ("review", "howto", "price"):
                            angle = _q_claim["angle"]
                        import logging as _lg
                        _lg.getLogger("shopcast.autoqueue").info(
                            "[autoqueue] 업로드 소비 %s t=%s kw=%r reason=%s",
                            _q_claim["source_type"], tenant.id, target_kw, _q_claim["reason"])
                except Exception:
                    _q_claim = None
            if _ind:
                from app.industries import ensure_profile
                ensure_profile(_ind)
            from app.services import smart_intake as _si
            _intake = {"confirmed": confirmed.strip()[:120],
                       "analysis": (vision_analysis or "").strip()[:12000],
                       "answers": _si.parse_answers(answers),
                       "experience": experience.strip()[:200],
                       "intent": intent.strip()[:40],
                       "clean_overlay": _overlay_on}
            if intent.strip():                          # (vision-intent 3-2) 선택 이력 학습
                try:
                    from app import db as _dbi
                    _dbi.record_intent(tenant.id, intent.strip())
                except Exception:
                    pass
            _note2 = full_note
            if _q_claim and "제목 매력" in (_q_claim.get("reason") or ""):
                _note2 += ("\n[제목 재도전 — 저CTR] 이전 글과 완전히 다른 스타일의 제목 후보를 뽑아라"
                           "(질문형/구체 숫자형/경험 고백형 등). 본문이 답할 수 있는 약속만 제목에 담아라.")
            if _q_claim and "근소격차" in (_q_claim.get("reason") or ""):
                _note2 += ("\n[경쟁 격차 공략] 바로 위 경쟁 글보다 더 구체적인 실측·경험·사진 설명을 담아라. "
                           "같은 의도를 더 정확히 충족하는 글이 이긴다(비방 금지).")
            made = ingest_upload(tenant, files, _note2,
                                 target_kw=target_kw.strip()[:40],
                                 angle=(angle.strip() if angle.strip() in ("review", "howto", "price") else ""),
                                 intake=_intake, pre_cleaned_idx=_pre_idx)
            # ⭐ 영상 대표 사진(사용자 선택) — 블로그 payload에 basename 영속(온디맨드 영상이 재정렬에 사용)
            try:
                _hi = int(hero_idx)
            except (TypeError, ValueError):
                _hi = -1
            if made and _hi >= 0:
                _hbp = next((p for p in made if p.kind.value == "blog"), None)
                _hips = (_hbp.payload.get("image_paths") if _hbp else None) or []
                if _hbp and _hi < len(_hips):
                    db.update_piece_payload(_hbp.id, {"hero_photo": os.path.basename(_hips[_hi])})
            if made and _q_claim:
                _bp = next((p for p in made if p.kind.value == "blog"), None)
                from app import db as _db3
                _db3.mark_writing(_q_claim["id"], "done", piece_id=(_bp.id if _bp else ""))
            elif _q_claim:
                from app import db as _db3
                _db3.rollback_writing(_q_claim["id"])
            if not made:
                _refund_usage(owner)               # 생성 결과 없음 → 예약 원복
            db.finish_gen_job(_job_id, "done" if made else "failed",
                              asset_id=(made[0].asset_id if made else ""))
            try:
                import shutil as _shj
                _shj.rmtree(_spool, ignore_errors=True)   # 완료된 잡 스풀 정리(디스크)
            except Exception:
                pass
        except Exception:
            _refund_usage(owner)                   # 실패 → 예약 원복
            db.finish_gen_job(_job_id, "failed")
            import logging, traceback
            logging.exception("[upload-bg] 생성 실패 tenant=%s", tenant.id)
            try:      # 조용한 실패 금지 — 사유를 진행률에 기록(사용자 안내 + 진단)
                db.set_gen_progress(tenant.id, "failed", "생성이 중단됐어요",
                                    "잠시 후 다시 시도해 주세요", None, status="failed",
                                    error=traceback.format_exc()[-500:])
            except Exception:
                pass
    from app import llm as _llmu          # 💳 크레딧 소진이면 생성 자체를 시작하지 않는다(사장님 지시)
    if _llmu.credit_out():
        try:
            db.set_gen_progress(tenant.id, "failed", "AI 사용량 소진",
                                _llmu.CREDIT_MSG, None, status="failed", error="credit_out")
        except Exception:
            pass
        if auth.current_user(req):
            from urllib.parse import quote as _qc
            return RedirectResponse("/me?err=" + _qc(_llmu.CREDIT_MSG), status_code=303)
        return page("잠시 중지", "<div class='bg-white rounded-xl shadow-sm p-6 text-center'>"
                    "<div class='text-4xl mb-2'>💳</div>"
                    f"<p class='text-slate-600 text-sm'>{esc(_llmu.CREDIT_MSG)}</p></div>")
    try:      # ★ 새 생성 시작 즉시 진행률 리셋 — 직전 생성의 낡은 값(84% '영상 대본' 등) 잔상 방지.
        db.set_gen_progress(tenant.id, "start", "준비 중", "사진 정리 중", 0.02, new=True)
    except Exception:
        pass
    import threading
    threading.Thread(target=_bg_generate, daemon=True).start()
    if auth.current_user(req):                     # 로그인 회원 → 대시보드(생성 중 표시)
        return RedirectResponse("/me?gen=1", status_code=303)
    body = ("<div class='bg-white rounded-xl shadow-sm p-6 text-center'>"
            "<div class='text-4xl mb-2'>✨</div>"
            "<h1 class='text-xl font-bold mb-1'>만드는 중이에요!</h1>"
            "<p class='text-slate-500 text-sm'>20~60초 뒤 내 작업실에 자동으로 나타나요.</p>"
            f"<a href='/me' class='inline-block mt-4 text-indigo-600 text-sm font-semibold'>내 작업실로 가기 →</a></div>")
    return page("생성 중", body)


# ── 검수 (채널/종류별) ───────────────────────────────────
def _audit_box(audit: dict | None) -> str:
    """상위노출 점검 결과(점수+경고) 표시."""
    if not audit:
        return ""
    score = audit.get("score", 0)
    grade = audit.get("grade", "")
    color = "emerald" if score >= 85 else ("amber" if score >= 70 else "rose")
    warns = audit.get("warnings", [])
    items = "".join(f"<li>⚠️ {esc(w)}</li>" for w in warns) or "<li>✅ 주요 이슈 없음</li>"
    return (f"<div class='text-xs bg-{color}-50 text-{color}-700 rounded-lg p-2 mb-3'>"
            f"<b>📊 상위노출 점검: {score}/100 ({esc(grade)})</b>"
            f"<ul class='mt-1 space-y-0.5'>{items}</ul></div>")


def _info(label: str, val: str) -> str:
    if not val:
        return ""
    return (f"<div class='mb-2'><span class='text-xs font-semibold text-slate-500'>{esc(label)}</span>"
            f"<div class='text-sm bg-slate-50 rounded-lg p-2'>{esc(val)}</div></div>")


def _scenes_table(scenes: list) -> str:
    if not scenes:
        return ""
    rows = ""
    for i, s in enumerate(scenes, 1):
        rows += ("<tr class='border-t'>"
                 f"<td class='p-1 align-top text-slate-400'>{i}</td>"
                 f"<td class='p-1 align-top whitespace-nowrap'>{esc(s.get('time_range',''))}</td>"
                 f"<td class='p-1 align-top'>{esc(s.get('visual_description',''))}</td>"
                 f"<td class='p-1 align-top'>{esc(s.get('camera_movement',''))}</td>"
                 f"<td class='p-1 align-top font-semibold'>{esc(s.get('on_screen_text',''))}</td>"
                 f"<td class='p-1 align-top text-slate-600'>{esc(s.get('narration_segment',''))}</td></tr>")
    return ("<p class='text-xs font-semibold text-slate-500 mt-3 mb-1'>🎬 장면 구성</p>"
            "<div class='overflow-x-auto'><table class='text-xs w-full'>"
            "<tr class='text-slate-400'><td>#</td><td>시간</td><td>비주얼</td><td>카메라</td><td>자막</td><td>내레이션</td></tr>"
            f"{rows}</table></div>")


def _editor(pid: str, p) -> str:
    """종류별 편집 UI + 풍부한 메타 표시."""
    from app.domain.models import ContentKind
    if p.kind == ContentKind.BLOG:                      # 네이버 블로그 SEO 초안
        n = len(p.payload.get("image_paths") or [])
        numbered = "".join(
            f"<div class='inline-block text-center mr-2'>"
            f"<img src='/asset/{pid}/{i}' class='h-20 w-20 object-cover rounded-lg border'>"
            f"<div class='text-xs font-semibold text-blue-600'>[사진{i+1}]</div></div>"
            for i in range(n))
        legend = (f"<p class='text-xs font-semibold text-slate-500 mb-1'>📸 본문 [사진N] 위치에 넣을 사진(순서대로)</p>"
                  f"<div class='flex overflow-x-auto mb-3'>{numbered}</div>") if n else ""
        info = (legend
                + _info("메타설명", p.payload.get("meta_description", ""))
                + _info("이미지 배치 제안", p.payload.get("recommended_image_placement", ""))
                + _info("SEO 키워드", ", ".join(p.payload.get("seo_keywords", []))))
        return (info + f"<form method=post action='/admin/review/{pid}/save' class='space-y-2'>"
                f"<input name=title value=\"{esc(p.payload.get('title',''))}\" class='w-full border rounded-lg p-2 text-sm font-bold'>"
                f"<textarea name=body rows=14 class='w-full border rounded-lg p-3 text-sm'>{esc(p.payload.get('body',''))}</textarea>"
                f"<input name=tags value=\"{esc(', '.join(p.payload.get('tags', [])))}\" class='w-full border rounded-lg p-2 text-xs' placeholder='태그'>"
                f"<button class='px-4 py-2 bg-slate-200 rounded-lg text-sm'>💾 저장</button></form>")
    if p.kind == ContentKind.SHORT:                     # 유튜브 숏 기획
        meta = (_info("길이 · 플랫폼", f"{p.payload.get('duration','')} · {p.payload.get('target_platform','')}")
                + _info("0~3초 훅", p.payload.get("hook_strategy", ""))
                + _info("🎙 내레이션(TTS 대본)", p.payload.get("narration", ""))
                + _scenes_table(p.payload.get("scenes", []))
                + f"<p class='text-xs text-amber-600 mt-2'>※ {esc(p.payload.get('tts_note',''))} · {esc(p.payload.get('bgm_note',''))}</p>")
        return (meta + f"<form method=post action='/admin/review/{pid}/save' class='space-y-2 mt-3'>"
                f"<input name=title value=\"{esc(p.payload.get('title',''))}\" class='w-full border rounded-lg p-2 text-sm font-bold' placeholder='제목'>"
                f"<input name=subtitle value=\"{esc(p.payload.get('subtitle',''))}\" class='w-full border rounded-lg p-2 text-sm' placeholder='영상 자막(번인)'>"
                f"<button class='px-4 py-2 bg-slate-200 rounded-lg text-sm'>💾 저장</button></form>")
    return (f"<form method=post action='/admin/review/{pid}/save'>"   # 인스타 캡션
            f"<textarea name=text rows=10 class='w-full border rounded-lg p-3 text-sm mb-2'>{esc(p.payload.get('text',''))}</textarea>"
            f"<button class='px-4 py-2 bg-slate-200 rounded-lg text-sm'>💾 저장</button></form>")


def _gallery(pid: str, p) -> str:
    """업로드된 사진 전부를 썸네일로 표시(여러 장)."""
    n = len(p.payload.get("image_paths") or [p.payload.get("image_path")])
    thumbs = "".join(
        f"<img src='/asset/{pid}/{i}' class='h-24 w-24 object-cover rounded-lg bg-white border'>"
        for i in range(n))
    cap = f"<p class='text-xs text-slate-400 mb-1'>사진 {n}장</p>" if n > 1 else ""
    return cap + f"<div class='flex gap-2 overflow-x-auto mb-4'>{thumbs}</div>"


def _blog_preview(pid: str, p) -> str:
    """네이버 글쓰기 화면처럼 — 문단 사이사이 사진 인라인 + 장소 + 연락처."""
    import re
    t = db.get_tenant(p.tenant_id)
    title = esc(p.payload.get("title", ""))
    body = p.payload.get("body", "") or ""
    n_imgs = len(p.payload.get("image_paths") or [])
    # [사진N] 마커로 분할 → 문단 + 이미지 교차 배치
    parts = re.split(r"\[사진(\d+)\]", body)
    html_blocks = ""
    for i, seg in enumerate(parts):
        if i % 2 == 0:  # 텍스트 문단
            txt = esc(seg.strip())
            if txt:
                html_blocks += f"<p class='text-sm text-slate-700 leading-relaxed whitespace-pre-line my-2'>{txt}</p>"
        else:  # 사진 번호
            idx = int(seg) - 1
            if 0 <= idx < n_imgs:
                html_blocks += f"<img src='/asset/{pid}/{idx}' class='w-full max-h-72 object-cover rounded-xl my-2'>"
    # 장소 + 연락처 블록(가게 프로필)
    place = ""
    if t and (t.address or t.map_url):
        maplink = f"<a href='{esc(t.map_url)}' class='text-indigo-600 underline'>네이버 지도</a>" if t.map_url else ""
        place = (f"<div class='mt-3 p-3 bg-slate-50 rounded-xl text-sm'>📍 <b>찾아오시는 길</b><br>"
                 f"{esc(t.address)} {maplink}</div>")
    contact = ""
    if t and (t.phone or t.hours):
        contact = (f"<div class='mt-2 p-3 bg-slate-50 rounded-xl text-sm'>📞 <b>연락처</b><br>"
                   f"{esc(t.phone)}" + (f" · 영업 {esc(t.hours)}" if t.hours else "") + "</div>")
    tags = " ".join("#" + esc(x) for x in p.payload.get("tags", []))
    miss = ("<p class='text-xs text-amber-600 mt-2'>※ 장소·연락처가 비어있어요 — 가게 관리에서 입력하면 자동으로 들어갑니다.</p>"
            if not (place or contact) else "")
    return (f"<div class='bg-white border border-slate-200 rounded-2xl p-4 mb-3'>"
            f"<div class='text-xs text-slate-400 mb-2'>📝 네이버 발행 미리보기</div>"
            f"<h3 class='text-base font-bold text-slate-800 mb-2'>{title}</h3>"
            f"{html_blocks}{place}{contact}"
            f"<p class='text-xs text-indigo-500 mt-2'>{tags}</p>{miss}</div>")


def _media(pid: str, p) -> str:
    from app.domain.models import ContentKind
    if p.kind == ContentKind.BLOG:
        return _blog_preview(pid, p)
    if p.kind == ContentKind.SHORT:
        _vp = p.payload.get("video_path") or ""
        _vok = _vp and (os.path.exists(_vp) or __import__("app.storage", fromlist=["x"]).r2_media_url(
            p.tenant_id, os.path.basename(_vp)))
        if _vok:
            return (f"<video src='/video/{pid}' controls class='w-full max-h-96 rounded-xl bg-black mb-2'></video>"
                    + _gallery(pid, p))
        return (_gallery(pid, p)
                + f"<p class='text-xs text-amber-600 mb-3'>⚠️ 영상 미생성: {esc(p.payload.get('assemble_note',''))}</p>")
    return _gallery(pid, p)


@app.get("/admin/review/{pid}", response_class=HTMLResponse)
def review(pid: str):
    p = db.get_piece(pid)
    if not p:
        return HTMLResponse("<p>없는 콘텐츠입니다.</p>", status_code=404)
    t = db.get_tenant(p.tenant_id)
    pub = get_publisher(p.channel)
    actions = ("<div class='flex gap-2 mt-3'>"
               f"<form method=post action='/admin/review/{pid}/approve'><button class='px-4 py-2 bg-blue-600 text-white rounded-lg text-sm'>✅ 승인</button></form>"
               f"<form method=post action='/admin/review/{pid}/reject'><button class='px-4 py-2 bg-rose-100 text-rose-600 rounded-lg text-sm'>✕ 반려</button></form>")
    if p.status == ContentStatus.APPROVED:
        label = "📋 초안 내보내기" if not pub.supports_auto_publish else "🚀 발행"
        actions += f"<form method=post action='/admin/publish/{pid}'><button class='px-4 py-2 bg-green-600 text-white rounded-lg text-sm'>{label}</button></form>"
    actions += "</div>"
    # AI 수정 지시 + 자동 보완
    autofix = ""
    if (p.payload.get("ranking_audit") or {}).get("warnings"):
        autofix = (f"<form method=post action='/admin/review/{pid}/autofix' class='mt-2'>"
                   f"<button class='px-3 py-2 bg-violet-100 text-violet-700 rounded-lg text-sm font-semibold'>"
                   f"✨ AI 자동 보완 (점검 경고 반영)</button></form>")
    revise = (f"<div class='mt-4 pt-3 border-t'>"
              f"<p class='text-xs font-semibold text-slate-500 mb-1'>✏️ AI에게 수정 지시</p>"
              f"<form method=post action='/admin/review/{pid}/revise' class='flex gap-2'>"
              f"<input name=instruction placeholder='예: 가격 정보 추가 / 더 친근하게 / 제목 더 강하게' "
              f"class='flex-1 border rounded-lg p-2 text-sm'>"
              f"<button class='px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm'>수정</button></form>"
              f"{autofix}</div>")
    actions += revise
    body = (nav() +
            f"<a href='/admin' class='text-sm text-slate-400'>← 대시보드</a>"
            f"<h1 class='text-xl font-bold mt-2 mb-1'>{esc(t.name if t else '')} {badge(p.status.value)}</h1>"
            f"<p class='text-xs text-slate-400 mb-2'>{p.channel.value} · {p.kind.value}"
            + ("" if pub.supports_auto_publish else " · <span class='text-amber-600'>반자동(사람 발행)</span>") + "</p>"
            + (f"<div class='text-xs bg-emerald-50 text-emerald-700 rounded-lg p-2 mb-2'>🎯 SEO 타겟 키워드: "
               f"{esc(', '.join(p.payload.get('target_keywords', [])))}</div>"
               if p.payload.get("target_keywords") else "")
            + _audit_box(p.payload.get("ranking_audit"))
            + (lambda r: (f"<div class='text-xs bg-violet-50 text-violet-700 rounded-lg p-2 mb-3'>"
                          f"<b>👁 예상 노출: {esc(r.get('label',''))} ({esc(r.get('unit',''))})</b> "
                          f"<span class='text-violet-400'>· {esc(r.get('basis',''))} · {esc(r.get('note',''))}</span></div>")
               if r else "")(p.payload.get("reach"))
            + _media(pid, p) + _editor(pid, p) + actions)
    return page("검수", body)


@app.post("/admin/review/{pid}/save")
def review_save(pid: str, text: str = Form(None), title: str = Form(None),
                body: str = Form(None), subtitle: str = Form(None), tags: str = Form(None)):
    fields = {}
    if text is not None:
        fields["text"] = text
    if title is not None:
        fields["title"] = title
    if body is not None:
        fields["body"] = body
    if subtitle is not None:
        fields["subtitle"] = subtitle
    if tags is not None:
        fields["tags"] = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
    db.update_piece_payload(pid, fields)
    return RedirectResponse(f"/admin/review/{pid}", status_code=303)


@app.post("/admin/review/{pid}/approve")
def review_approve(pid: str):
    db.set_piece_status(pid, ContentStatus.APPROVED)
    return RedirectResponse(f"/admin/review/{pid}", status_code=303)


@app.post("/admin/set/{asset_id}/regen-video")
def admin_regen_video(asset_id: str, sync: str = ""):
    """영상 폐기 후 재생성 — SHORT 피스 삭제 + video_job 리셋(retried 해제) → 워치독이 재생성.
    sync=1이면 요청 안에서 동기 실행해 실패 사유를 그대로 반환(보이지 않는 스레드 진단용).
    글·사진·기존 키트 산출물 불변(영상 피스만)."""
    from app.domain.models import ContentKind as _CK
    n = 0
    blog = None
    for p in db.get_set_pieces(asset_id):
        if p.kind == _CK.SHORT:
            pl = p.payload or {}
            nv = pl.get("naver_video") or {}
            for fp in ([pl.get("video_path"), pl.get("cover_path"), nv.get("path"), nv.get("body_path")]
                       + list(pl.get("carousel_paths") or [])):   # 이전 산출물 정리 — 고아 mp4 누적으로 디스크 풀(실측) 재발 방지
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
            db.delete_piece(p.id, p.tenant_id)
            n += 1
        if p.kind == _CK.BLOG:
            blog = p
    from app.services.ingest import _set_video_job
    _set_video_job(asset_id, "registered", retried=False)
    if sync == "1" and blog:
        import os as _os
        import traceback
        try:
            from app.services.ingest import _make_video_bundle
            tenant = db.get_tenant(blog.tenant_id)
            asset = db.get_asset(asset_id)
            from app.services.ingest import _restore_media
            paths = _restore_media(blog.tenant_id, blog.payload.get("image_paths") or [])
            if not (tenant and asset and paths):
                return HTMLResponse(f"<pre>사전 조건 실패: tenant={bool(tenant)} asset={bool(asset)} paths={len(paths)}</pre>")
            _set_video_job(asset_id, "running", retried=True)
            _make_video_bundle(tenant, asset, paths, blog.payload.get("brief") or {})
            _set_video_job(asset_id, "done")
            import json as _json
            _short = next((p for p in db.get_set_pieces(asset_id)
                           if p.kind == _CK.SHORT and (p.payload or {}).get("video_path")), None)
            _out = {"done": True}
            if _short:
                pl = _short.payload or {}
                nv = pl.get("naver_video") or {}
                _out = {"done": True, "video_path": pl.get("video_path"),
                        "subtitles": pl.get("subtitles"), "llm_route": pl.get("llm_route"),
                        "naver_video": {k: nv.get(k) for k in ("path", "title", "filename", "duration_sec")},
                        "naver_scene_texts": nv.get("scene_texts")}
            import shutil as _sh
            _du = _sh.disk_usage(_os.environ.get("SHOPCAST_STORAGE", "storage"))
            _out["disk_mb"] = {"free": round(_du.free / 1e6), "used": round(_du.used / 1e6), "total": round(_du.total / 1e6)}
            return HTMLResponse(f"<pre>{esc(_json.dumps(_out, ensure_ascii=False, indent=1))}</pre>")
        except Exception:
            return HTMLResponse(f"<pre>동기 재생성 실패:\n{esc(traceback.format_exc()[-1800:])}</pre>")
    return RedirectResponse(f"/admin/set/{asset_id}?ok=영상 {n}건 폐기·재생성 예약", status_code=303)


@app.post("/admin/review/{pid}/reject")
def review_reject(pid: str):
    db.set_piece_status(pid, ContentStatus.REJECTED)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/review/{pid}/revise")
def review_revise(pid: str, instruction: str = Form("")):
    p = db.get_piece(pid)
    if p and instruction.strip():
        revise_piece(p, instruction.strip())
    return RedirectResponse(f"/admin/review/{pid}", status_code=303)


@app.post("/admin/review/{pid}/autofix")
def review_autofix(pid: str):
    p = db.get_piece(pid)
    if p:
        audit = p.payload.get("ranking_audit") or seo.quality_audit(p.channel.value, p.kind.value, p.payload)
        revise_piece(p, autofix_instruction(audit, p.kind.value))
    return RedirectResponse(f"/admin/review/{pid}", status_code=303)


@app.post("/admin/publish/{pid}", response_class=HTMLResponse)
def publish(pid: str):
    p = db.get_piece(pid)
    if not p:
        return HTMLResponse("<p>없는 콘텐츠입니다.</p>", status_code=404)
    result = publish_and_record(p)
    # 반자동(네이버): 발행 대신 '초안 복사 + 사람이 발행' 안내
    if result.detail.get("manual"):
        d = result.detail.get("draft", {})
        full = (esc(d.get("title", "")) + "\n\n" + esc(d.get("body", ""))
                + "\n\n" + esc(" ".join("#" + x for x in d.get("tags", []))))
        n = len(p.payload.get("image_paths") or [])
        numbered = "".join(
            f"<div class='inline-block text-center mr-2'>"
            f"<img src='/asset/{pid}/{i}' class='h-24 w-24 object-cover rounded-lg border'>"
            f"<div class='text-xs font-semibold text-blue-600'>[사진{i+1}]</div></div>"
            for i in range(n))
        legend = (f"<p class='text-xs font-semibold text-slate-500 mt-2 mb-1'>📸 [사진N] 위치에 넣을 사진(순서대로)</p>"
                  f"<div class='flex overflow-x-auto mb-3'>{numbered}</div>") if n else ""
        body = (nav() + f"<a href='/admin' class='text-sm text-slate-400'>← 대시보드</a>"
                "<h1 class='text-xl font-bold mt-2 mb-2'>📋 네이버 블로그 초안</h1>"
                f"<p class='text-xs text-slate-500 mb-3'>{esc(d.get('guide',''))}</p>"
                f"<textarea readonly rows=16 class='w-full border rounded-lg p-3 text-sm mb-3'>{full}</textarea>"
                f"{legend}"
                f"<form method=post action='/admin/review/{pid}/done'>"
                f"<button class='px-4 py-2 bg-green-600 text-white rounded-lg text-sm'>✅ 직접 발행 완료로 표시</button></form>")
        return page("초안 내보내기", body)
    msg = (f"🚀 발행 성공 (id={esc(result.external_id)})" if result.ok else f"⚠️ 발행 실패: {esc(result.error)}")
    sim = " <span class='text-xs text-amber-600'>(시뮬레이션)</span>" if result.detail.get("simulated") else ""
    body = (nav() + f"<div class='bg-white rounded-xl shadow-sm p-6'><p class='font-semibold'>{msg}{sim}</p>"
            f"<a href='/admin' class='inline-block mt-4 text-blue-600 text-sm'>← 대시보드</a></div>")
    return page("발행 결과", body)


@app.post("/admin/review/{pid}/done")
def review_done(pid: str):
    """반자동(네이버) — 사장님/운영자가 직접 발행 후 완료 표시."""
    db.set_piece_status(pid, ContentStatus.PUBLISHED)
    return RedirectResponse("/admin", status_code=303)


# ── 미디어 서빙 ──────────────────────────────────────────
def _serve_media(path: str, url_key: str = "", payload: dict | None = None):
    """로컬 파일 우선, 없으면 R2 공개 URL로 302 리다이렉트(로컬 삭제 후에도 서빙·발행 유지, B5)."""
    if path and os.path.exists(path):
        return FileResponse(path)
    url = storage.public_url_for(path) or ((payload or {}).get(url_key) if url_key else None)
    if url:
        return RedirectResponse(url, status_code=302)
    return HTMLResponse(status_code=404)


@app.get("/asset/{pid}")
def asset_image(pid: str):
    p = db.get_piece(pid)
    if not p:
        return HTMLResponse(status_code=404)
    return _serve_media(p.payload.get("image_path"), "image_url", p.payload)


@app.get("/asset/{pid}/{idx}")
def asset_image_idx(pid: str, idx: int):
    p = db.get_piece(pid)
    if not p:
        return HTMLResponse(status_code=404)
    paths = p.payload.get("image_paths") or [p.payload.get("image_path")]
    if idx < 0 or idx >= len(paths) or not paths[idx]:
        return HTMLResponse(status_code=404)
    return _serve_media(paths[idx])


@app.get("/video/{pid}")
def asset_video(pid: str):
    p = db.get_piece(pid)
    if not p:
        return HTMLResponse(status_code=404)
    path = p.payload.get("video_path")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="video/mp4")
    return _serve_media(path, "video_url", p.payload)   # 로컬 삭제 시 R2 리다이렉트(B5)
