"""
shopcast 웹 MVP — 서버렌더(FastAPI).
흐름: 사장님 업로드(/u/{token}) → AI 캡션 생성 → 운영자 검수(/admin) → 인스타 발행(토큰 없으면 시뮬).
실행: uvicorn app.main:app --reload
"""
from __future__ import annotations

import os

import base64
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
    db.init_db()
    if not db.list_tenants():           # 시작 업종 6종 데모 가게 시드
        for key in ACTIVE_INDUSTRIES:
            p = PROFILES[key]
            db.create_tenant(name=f"데모 {p.name}", industry=p.name, region="수원")
    try:                                # 경쟁사 일일 자동 스캔(apscheduler 미설치 시 graceful)
        from app import scheduler
        scheduler.start()
    except Exception:
        import logging
        logging.exception("[startup] 스케줄러 기동 실패 — 자동 스캔 없이 계속")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "shopcast", "version": app.version}


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
              "analysis": (vision_analysis or "").strip()[:4000],
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
                _demo_jobs[job] = {"status": "done", "html": html, "ts": _time.time()}
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
    return JSONResponse({"ready": True, "teaser": True, "teaser_html": j.get("html", "")})


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
    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []
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
    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []
    entries = []
    for p in pieces:
        entries += _piece_pack_entries(p, imgs, prefix=f"{_ch_folder(p)}/")
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
        for i, (data, fname) in enumerate(files[:6]):
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
    return RedirectResponse("/me?tab=report#place", status_code=303)


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
        last = (sets[0].get("created") or "")[:10]
        d0 = datetime.date.fromisoformat(last)
        days = (datetime.date.today() - d0).days
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
                "cta": "성과 보기", "href": "/me?tab=report"}
    return {"emoji": "wand", "text": "오늘 콘텐츠 하나로 노출을 늘려보세요. 매주 2~3개가 상위노출의 정석이에요.",
            "cta": "만들기", "href": "/me"}


@app.get("/me", response_class=HTMLResponse)
def my_dashboard(request: Request, ok: str = "", err: str = "", gen: str = ""):
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    tok = db.tenant_token(t.id)
    inp = "w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm"
    banner = ""
    if ok:
        banner = f"<div class='bg-emerald-50 text-emerald-700 p-3 rounded-xl mb-4 text-sm'>✅ {esc(ok)}</div>"
    if err:
        banner = f"<div class='bg-rose-50 text-rose-600 p-3 rounded-xl mb-4 text-sm'>⚠️ {esc(err)}</div>"
    if gen:   # 생성 중 — 스피너 + 완료되면 자동 새로고침(폴링)
        _base_n = len(db.list_sets(tenant_id=t.id))
        banner = ("<div class='bg-indigo-50 border border-indigo-100 text-indigo-700 p-4 rounded-2xl mb-4 flex items-center gap-3'>"
                  "<div class='w-6 h-6 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin flex-shrink-0'></div>"
                  "<div><div class='font-bold text-sm'>AI 전문가팀이 콘텐츠를 만들고 있어요</div>"
                  "<div class='text-xs text-indigo-500'>20~60초 걸려요 · 완료되면 자동으로 나타나요 (이 화면 유지)</div></div></div>"
                  f"<script>(function(){{var base={_base_n},n=0;var iv=setInterval(async function(){{n++;if(n>40){{clearInterval(iv);location.reload();return;}}"
                  "try{var r=await fetch('/me/sets/count');var d=await r.json();if(d.n>base){clearInterval(iv);location.href='/me?ok='+encodeURIComponent('콘텐츠가 완성됐어요! 아래에서 확인하세요');}}catch(e){}"
                  "}},3000);})();</script>")
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
                  "<div class='grid grid-cols-2 gap-2'>"
                  + _bopt("local", _ic("store", "w-6 h-6 mx-auto text-indigo-600"), "동네 매장", "방문·예약 유도 · 지도/연락처")
                  + _bopt("seller", _ic("package", "w-6 h-6 mx-auto text-indigo-600"), "온라인 셀러", "구매링크·상품 키워드")
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
            return (f"<a href='/me?tab=report#blog' title='실측 기준 · 위치·기기별 차이' "
                    f"class='inline-block text-[11px] font-bold px-2 py-0.5 rounded-full {cls}'>"
                    f"{esc(kw)}{vtxt} · {body}</a>")
        for s in sets:
            ps = db.get_set_pieces(s["asset_id"])
            _nclk = sum(_ccounts.get(p.id[:8], 0) for p in ps)
            _ebadge = _expose_badge(ps)
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
                + (f"<div class='mt-1'>{_ebadge}</div>" if _ebadge else "") + "</div>"
                + f"<a href='/me?view={s['asset_id']}' class='px-3.5 py-2 bg-indigo-600 hover:bg-indigo-700 active:scale-[.98] text-white text-xs font-bold rounded-xl transition'>보기</a>"
                + f"<form method=post action='/me/set/{s['asset_id']}/delete' onsubmit=\"return confirm('이 콘텐츠를 삭제할까요?')\">"
                + "<button class='px-1.5 py-2 text-slate-300 hover:text-rose-500 text-base transition' title='삭제'>" + _ic("xcircle", "w-4 h-4") + "</button></form></div>")
        hist = "<div class='grid sm:grid-cols-2 gap-3'>" + "".join(_cards) + "</div>"
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
    # 성과 데이터(통계 카드 + 최근 키워드)
    _sets2, _scores, _kws2, _np = db.list_sets(tenant_id=t.id, limit=200), [], [], 0
    for s in _sets2:
        for p in db.get_set_pieces(s["asset_id"]):
            _np += 1
            sc = (p.payload.get("ranking_audit") or {}).get("score")
            if isinstance(sc, (int, float)):
                _scores.append(sc)
            for k in (p.payload.get("target_keywords") or []):
                if k and k not in _kws2:
                    _kws2.append(k)
    _avg = round(sum(_scores) / len(_scores)) if _scores else 0

    def _statc(icon, chip, num, label):
        return (f"<div class='bg-white rounded-3xl border border-slate-100 shadow-sm p-6 flex items-center gap-5'>"
                f"<div class='w-16 h-16 rounded-2xl flex items-center justify-center text-3xl {chip} flex-shrink-0'>{icon}</div>"
                f"<div class='min-w-0'><div class='text-4xl sm:text-5xl font-extrabold text-slate-900 leading-none tracking-tight'>{num}</div>"
                f"<div class='text-sm text-slate-400 font-semibold mt-1.5'>{label}</div></div></div>")
    stats_row = (("<div class='grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6'>"
                  + _statc(_ic("package", "w-7 h-7"), "bg-[#EEF2FF] text-indigo-600", len(_sets2), "만든 세트")
                  + _statc(_ic("grid", "w-7 h-7"), "bg-[#EEF2FF] text-indigo-600", _np, "채널 발행물")
                  + _statc(_ic("target", "w-7 h-7"), "bg-[#EEF2FF] text-indigo-600", _avg, "평균 노출점수") + "</div>") if _sets2 else "")
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
    elif tab == "report":                                 # 성과 리포트 · 최근 키워드 + 순위(자동) (전체 폭)
        active = "report"
        _kwbox = ""    # (auto) '노리는 키워드' 태그 제거 — AI 내부 재료
        # 키워드 순위 — 페이지 열면 자동 조회(네이버 지역검색)
        _rankbox = ""    # (auto) 키워드 순위 UI 제거 — /me/rank는 엔진 내부용으로 유지
        # 🔁 상위노출 루프 진행상황(상위노출 PHASE 6) — 진단→타겟생성→발행일관성→순위변화 한눈에
        _loopbox = ""
        try:
            from app.services import pubcal as _pc
            _wp2 = _pc.week_plan(t, _plan)
            _n_pub = len(db.list_blog_publishes(t.id, limit=30))
            _n_imp = len(db.improving_keywords(t.id))
            _has_diag = bool((t.industry or "").strip())

            def _step(num, emoji, label, state, sub):
                on = "border-emerald-300 bg-emerald-50" if state else "border-slate-200 bg-white"
                return (f"<div class='flex-1 min-w-[130px] rounded-2xl border {on} p-3'>"
                        f"<div class='text-[10px] font-bold text-slate-400'>STEP {num}</div>"
                        f"<div class='text-sm font-extrabold text-slate-800'>{emoji} {label}</div>"
                        f"<div class='text-[11px] text-slate-500 mt-0.5'>{sub}</div></div>")
            _loopbox = (f"<div class='{_fw} mb-5'>"
                        "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>상위노출 실행 루프</h2>"
                        "<p class='text-sm text-slate-400 mb-4'>진단 → 타겟 글 → 꾸준한 발행 → 순위 추적·학습. 올린다가 이 루프를 돌려요.</p>"
                        "<div class='flex gap-2.5 flex-wrap'>"
                        + _step(1, _ic("search", "w-4 h-4 mx-auto"), "진단", _has_diag, "AI가 기회를 자동 탐색" if _has_diag else "가게 정보를 설정하세요")
                        + _step(2, _ic("pen", "w-4 h-4 mx-auto"), "타겟 생성", len(db.list_sets(tenant_id=t.id, limit=1)) > 0,
                                "미노출 키워드 겨냥 글")
                        + _step(3, _ic("calendar", "w-4 h-4 mx-auto"), "발행 일관성", _wp2["done"] >= 1,
                                f"이번 주 {_wp2['done']}/{_wp2['target']}회" + (" · 발행확인 " + str(_n_pub) + "건" if _n_pub else ""))
                        + _step(4, _ic("refresh", "w-4 h-4 mx-auto"), "추적·학습", _n_imp > 0 or _n_pub > 0,
                                (f"추적 글 {min(_n_pub, 10)}개 · " if _n_pub else "")
                                + (f"오른 키워드 {_n_imp}개 → 다음 글에 강화" if _n_imp else "순위 자동추적 중"))
                        + "</div></div>")
        except Exception:
            _loopbox = ""
        # (auto) '놓치는 키워드' 섹션 제거 — 미노출 키워드는 자동 글감 큐(P2)가 처리한다.
        _missbox = ""
        # 🎯 성과 실측 — 추적 링크/QR로 '이 콘텐츠 보고 온 손님' 집계
        _tl = _ensure_track_link(t)
        _clicks = sum(int(l.get("clicks") or 0) for l in db.list_links(t.id))
        _trackbox = ""
        if _tl:
            _base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
            _short = f"{_base}/r/{_tl['code']}"
            _trackbox = (
                f"<div class='{_fw} mt-5' id='qr'>"
                "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>성과 실측 · 내 손님 추적</h2>"
                "<p class='text-sm text-slate-400 mb-4'>이 QR을 <b>명함·매장 앞</b>에 붙이면, 찍고 온 손님이 여기 집계돼요. "
                "사용법: <b class='text-slate-600'>① QR 이미지 저장 ② 인쇄해서 붙이기</b> — 링크는 인스타 프로필에 넣어도 돼요.</p>"
                "<div class='flex items-center gap-5 flex-wrap'>"
                f"<img src='/me/qr/{_tl['code']}.png' class='w-28 h-28 rounded-xl border border-slate-100 p-1 bg-white' alt='추적 QR'>"
                "<div class='flex-1 min-w-[220px]'>"
                f"<div class='text-4xl font-extrabold text-indigo-600'>{_clicks}<span class='text-base text-slate-400 font-bold ml-1'>회 유입</span></div>"
                "<div class='mt-2 flex items-center gap-2'>"
                f"<input readonly value='{_short}' id='trkurl' class='flex-1 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-600'>"
                "<button type=button onclick=\"omCopy(document.getElementById('trkurl').value);this.textContent='✅'\" class='flex-shrink-0 bg-indigo-600 text-white text-sm font-bold px-3 py-2 rounded-lg'>복사</button></div>"
                f"<div class='text-xs text-slate-400 mt-1.5'>→ {esc(_tl.get('label',''))}(으)로 연결돼요</div>"
                f"<a href='/me/qr/{_tl['code']}.png' download='ollinda-qr.png' class='inline-block mt-2 text-xs font-bold text-indigo-500'>⬇ QR 이미지 저장</a>"
                "</div></div></div>")
        # 🏆 콘텐츠 성과(추적 P2) — 전부 추적링크 클릭 실측. '조회수' 아님을 명시(정직).
        _perfbox = ""
        try:
            _rank3 = db.content_click_ranking(t.id, days=30, limit=3)
            _split = db.channel_click_split(t.id, days=30)
            _series = db.daily_click_series(t.id, 7)
            _ch_lab = {"naver_blog": "네이버 블로그", "instagram": "인스타그램", "marketplace": "판매 콘텐츠",
                       "x": "X", "qr": "매장 QR(오프라인)", "direct": "직접·기타"}
            _rows = ""
            for i, rk in enumerate(_rank3, 1):
                _b = db.find_piece_brief(t.id, rk["content_id"]) or {}
                _tt = esc((_b.get("title") or "(삭제된 콘텐츠)")[:34])
                _cl = _ch_lab.get(rk.get("channel") or _b.get("channel") or "", rk.get("channel") or "")
                _rows += (f"<div class='flex items-center gap-3 py-2 border-b border-slate-100'>"
                          f"<span class='text-lg font-extrabold text-violet-500 w-6'>{i}</span>"
                          f"<div class='flex-1 min-w-0'><div class='text-sm font-bold text-slate-700 truncate'>{_tt}</div>"
                          f"<div class='text-[11px] text-slate-400'>{_cl}</div></div>"
                          f"<span class='text-xl font-extrabold text-violet-600'>{rk['n']}<span class='text-xs text-slate-400 font-bold ml-0.5'>명</span></span></div>")
            _coach = ""    # (auto) 코칭 배너 제거 — 잘 되는 방향은 글감 큐(P3)가 자동 굳히기
            _top3 = ((f"<div class='mb-4'><div class='text-sm font-bold text-slate-600 mb-1'>가장 손님 많이 데려온 콘텐츠 TOP {len(_rank3)}</div>{_rows}{_coach}</div>")
                     if _rank3 else
                     ("<div class='mb-4 bg-slate-50 border border-slate-100 rounded-xl px-4 py-3 text-sm text-slate-500'>"
                      "아직 유입이 없어요 — 발행물 속 추적링크·매장 QR로 온 손님이 여기 콘텐츠별로 집계돼요. "
                      "글을 발행하고 링크가 눌리면 TOP 3가 채워집니다."
                      "<div class='mt-1.5'><a href='/me' class='text-xs font-semibold text-indigo-600'>첫 글을 발행하면 여기가 채워져요 — 홈에서 사진을 올려주세요 →</a></div></div>"))
            _sp_rows = "".join(
                f"<div class='flex items-center justify-between py-1.5'><span class='text-sm text-slate-600'>{_ch_lab.get(k, esc(k))}</span>"
                f"<span class='text-base font-extrabold text-violet-600'>{v}<span class='text-xs text-slate-400 font-bold ml-0.5'>클릭</span></span></div>"
                for k, v in _split.items())
            _mx = max((d["n"] for d in _series), default=0) or 1
            _bars = "".join(
                f"<div class='flex-1 flex flex-col items-center gap-1'>"
                f"<div class='w-full max-w-[26px] rounded-t bg-violet-400' style='height:{max(3, int(44 * d['n'] / _mx))}px'></div>"
                f"<span class='text-[10px] text-slate-400'>{d['date']}</span></div>" for d in _series)
            _perfbox = (
                f"<div class='{_fw} mt-5'>"
                "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>콘텐츠 성과 · 링크 클릭 실측</h2>"
                "<p class='text-sm text-slate-500 mb-1'>내가 만든 글에 넣은 링크를 손님이 몇 번 눌렀는지 — "
                "<b class='text-slate-700'>어느 글이 손님을 데려오는지</b> 보여요.</p>"
                "<p class='text-sm text-slate-400 mb-4'>집계는 올린다 추적링크 클릭 기준이에요 — "
                "<b class='text-slate-600'>조회수·체류는 네이버/인스타 앱에서 확인하세요.</b> "
                "온라인 글(블로그·인스타) 유입과 매장 QR(오프라인) 유입은 구분 집계돼요.</p>"
                + _top3
                + "<div class='grid sm:grid-cols-2 gap-6'>"
                + f"<div><div class='text-sm font-bold text-slate-600 mb-1'>채널별 유입 (30일)</div>{_sp_rows or '<span class=\"text-sm text-slate-400\">아직 유입이 없어요 — 블로그·인스타·매장 QR별로 나눠 보여드려요<div class=\'mt-1.5\'><a href=\'/me\' class=\'text-xs font-semibold text-indigo-600\'>첫 글을 발행하면 여기가 채워져요 — 홈에서 사진을 올려주세요 →</a></div></span>'}</div>"
                + f"<div><div class='text-sm font-bold text-slate-600 mb-1'>최근 7일 추이</div><div class='flex items-end gap-1.5 h-16'>{_bars}</div></div>"
                + "</div>" + _visitor_box(t) + "</div>")
        except Exception:
            _perfbox = ""
        # (auto) 순위 성장(키워드 나열)·키워드 순위·최근 키워드 섹션 제거 — AI 내부 재료.
        # 사장님에게는 '글의 결과'(내 네이버 블로그 글별 순위)와 손님 실측만 보여준다.
        main_inner = (_sbadge + _ai_summary(t) + stats_row + _loopbox + _missbox
                      + _blog_connect_card(t, _fw) + _place_card(t, _fw)
                      + _trackbox + _perfbox)
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
                           "<a href='/me?tab=report#blog' class='flex-shrink-0 bg-emerald-600 text-white text-sm font-bold px-4 py-2 rounded-xl hover:bg-emerald-700 transition'>연결하기</a></div>")
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
            if not _due_html:
                try:
                    from app.services import autoqueue as _aq2
                    _st = _aq2.state(t)
                    if _st.get("need_photos"):
                        _due_html = ("<div class='flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-5'>"
                                     f"<span class='text-amber-500'>{_ic('camera', 'w-5 h-5 flex-shrink-0')}</span>"
                                     "<div class='flex-1 text-sm text-amber-800'><b>다음 글을 만들려면 사진이 필요해요</b> — "
                                     "가게 사진 3장만 올려주시면 글은 AI가 알아서 써둘게요.</div>"
                                     "<a href='/me#makebox' class='flex-shrink-0 bg-amber-500 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>사진 올리기</a></div>")
                except Exception:
                    pass
            # 발행 리듬 리마인드(6-2): 마지막 발행(RSS 실측) 2일↑ 공백 + 준비 글 있음 → 화면 내 텍스트만
            if not _due_html:
                try:
                    from app.services import autoqueue as _aq3
                    _st3 = _aq3.state(t)
                    _pubs3 = db.list_blog_publishes(t.id, limit=1)
                    if _st3.get("ready_unpub") and _pubs3:
                        from datetime import datetime as _dtr, timedelta as _tdr
                        _gap = ((_dtr.utcnow() + _tdr(hours=9)).date()
                                - _dtr.fromisoformat((_pubs3[0].get("published_at") or "")[:19]).date()).days
                        if _gap >= 2:
                            _due_html = ("<div class='flex items-center gap-3 bg-violet-50 border border-violet-200 rounded-2xl p-4 mb-5'>"
                                         f"<span class='text-violet-500'>{_ic('calendar', 'w-5 h-5 flex-shrink-0')}</span>"
                                         f"<div class='flex-1 text-sm text-violet-800'><b>{'이틀' if _gap == 2 else str(_gap) + '일'} 쉬었어요</b> — "
                                         "꾸준함이 쌓아온 신호가 아까워요. 오늘 글이 준비돼 있어요.</div>"
                                         "<a href='/me?tab=content' class='flex-shrink-0 bg-violet-600 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>준비된 글 보기</a></div>")
                except Exception:
                    pass
            main_inner = (greeting + _upsell + _due_html + _guide_card(t) + _briefing_card(t, _plan) + _notice_html
                          + _calendar_card(t, _plan)
                          + _blog_nudge + upload_section
                          + "<div class='mt-5'></div>" + _store_info_card(t))
    # 🆕 새로 추가한 '빈 새 가게'면 실수 대비 '뒤로가기(취소)' 배너
    if t.name == "새 가게" and len(db.list_user_stores(u["id"])) > 1 and not db.list_sets(tenant_id=t.id):
        _backban = ("<div class='flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-5'>"
                    "<span class='text-amber-500'>" + _ic("store", "w-5 h-5") + "</span>"
                    "<div class='flex-1 text-sm text-amber-800'><b>새 가게</b>를 추가했어요. 가게 이름을 넣고 자동 인식하세요. 잘못 누르셨나요?</div>"
                    "<form method=post action='/me/store/cancel'><button class='bg-white border border-amber-300 text-amber-700 text-sm font-bold px-4 py-2 rounded-xl hover:bg-amber-100 transition whitespace-nowrap'>← 뒤로가기</button></form></div>")
        main_inner = _backban + main_inner
    from app import landing
    _navitems = [("wand", "홈", "/me", "create"), ("book", "내 콘텐츠", "/me?tab=content", "content"),
                 ("chart", "리포트", "/me?tab=report", "report")]

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
            + "<div class='max-w-[1400px]'>" + banner + main_inner + "</div></main></div>"
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
        return RedirectResponse("/me?tab=report&ok=" + _q("블로그 연결을 해제했어요"), status_code=303)
    v = blogsync.verify_blog(raw)
    if not v["ok"]:
        return RedirectResponse("/me?tab=report&err=" + _q(v["error"]), status_code=303)
    db.set_tenant_blog(t.id, v["url"], v["blog_id"])
    msg = f"블로그 '{v['title'] or v['blog_id']}' 연결 완료! 이제 발행 확인·순위 매칭이 정확해져요"
    return RedirectResponse("/me?tab=report&ok=" + _q(msg), status_code=303)


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
    back = f"/kit/{piece.asset_id}/naver" if piece else "/me?tab=report"
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
    return RedirectResponse(back + "?ok=" + _q("발행 기록 완료! 이 글의 순위 추적이 시작돼요"), status_code=303)


def _seo_photo_name(tenant, blog) -> str:
    """(이미지 SEO 5-1) 다운로드 파일명 — {지역}-{업종핵심어}-{피사체}. 피사체는 사장님이 확인한
    사진 내용(confirmed)에서만(없으면 생략 — 날조 금지). 한글 파일명은 최신 브라우저 UTF-8 정상."""
    import re as _r
    region = seo._kw_shorten(getattr(tenant, "region", "") or "").replace(" ", "-")
    ind = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
    subject = ""
    m = _r.search(r"사진 내용\(사장님 확인[^)]*\):\s*([^\n]+)", (blog.payload or {}).get("gen_source") or "")
    if m:
        toks = [t for t in _r.findall(r"[가-힣A-Za-z0-9]{2,}", m.group(1)) if t not in ("사진", "모습", "장면")][:2]
        subject = "-".join(toks)
    _toks = [x for p in (region, ind, subject) if p for x in p.split("-") if x]
    _toks = list(dict.fromkeys(_toks))
    _toks = [t for t in _toks if not any(t != o and t in o for o in _toks)]   # 부분 포함 dedupe(2-2)
    name = _r.sub(r"[^가-힣A-Za-z0-9\-]", "", "-".join(_toks))
    return name or "photo"


def _photo_captions(tenant, blog, n: int) -> list[str]:
    """(이미지 SEO 5-2) 사진별 붙여넣기 캡션 — vision 분석의 [사진N] 묘사(사실)만 사용 +
    자연 변형 키워드 1회. 분석 없으면 빈 리스트(날조 캡션 금지)."""
    import re as _r
    srcnote = (blog.payload or {}).get("gen_source") or ""
    kw = seo._kw_shorten(((blog.payload or {}).get("target_keywords") or [""])[0] or "")
    imgs_ = (blog.payload or {}).get("image_paths") or []
    out = []
    _patched = False
    for i in range(1, n + 1):
        m = _r.search(rf"\[사진{i}\]\s*([^\n]+)", srcnote)
        desc = _r.sub(r"^[\d)*\-•\s]+", "", m.group(1)).strip().rstrip(".")[:60] if m else ""
        if len(desc) < 4:
            # (4-2a) 배치 응답 라인 누락·묘사부 공백 → 해당 사진만 단건 재분석 폴백(1회) + gen_source 캐시백.
            # 재실패 시 그 사진 캡션만 생략(빈 껍데기 문장 출력 금지).
            try:
                from app import vision as _vz
                p_ = imgs_[i - 1] if i - 1 < len(imgs_) else ""
                one = (_vz.analyze(p_, "") or "").strip() if (p_ and os.path.exists(p_)) else ""
                first = next((l for l in one.splitlines()
                              if len(_r.sub(r"^[\d)*\-•\s]+", "", l).strip()) >= 6), "")
                desc = _r.sub(r"^[\d)*\-•\s]+", "", first).strip().rstrip(".")[:60]
                if len(desc) >= 4:
                    srcnote += f"\n[사진{i}] {desc}"
                    _patched = True
            except Exception:
                desc = ""
        if len(desc) < 4:
            out.append("")                             # 렌더 가드가 걸러냄 — 빈 문장 미출력
            continue
        out.append(f"{desc} — {kw} 현장 사진입니다." if kw else f"{desc}.")
    if _patched:
        try:
            blog.payload["gen_source"] = srcnote[:8000]
            db.save_piece(blog)                        # 재분석 결과 캐시백(반복 비용 방지)
        except Exception:
            pass
    return out


def _caption_box(tenant, blog, n: int) -> str:
    """(이미지 SEO 5-2) 사진별 캡션 붙여넣기 박스 — 분석 없으면 렌더 생략."""
    caps = _photo_captions(tenant, blog, n)
    if not caps:
        return ""
    import re as _rg
    rows = "".join(
        f"<div class='flex items-start gap-2 py-1.5 border-b border-slate-100'>"
        f"<span class='text-xs font-bold text-slate-400 flex-shrink-0 mt-0.5'>사진{i + 1}</span>"
        f"<span class='flex-1 text-xs text-slate-600'>{esc(c)}</span>"
        f"<textarea id='cap{i}' class='hidden'>{esc(c)}</textarea>"
        f"<button type=button onclick=\"nvcp('cap{i}',this)\" class='flex-shrink-0 text-[11px] font-bold text-indigo-600'>복사</button></div>"
        for i, c in enumerate(caps)
        if c and not _rg.match(r"^\s*[—\-–]", c) and len(c.split("—")[0].strip()) >= 4)   # (4-2b) 빈 묘사 캡션 미출력
    return ("<div class='mt-3 bg-slate-50 rounded-xl p-3'>"
            "<div class='text-xs font-bold text-slate-500 mb-1'>사진 캡션 (사진 아래 붙여넣기)</div>" + rows + "</div>")


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
                _pace = ("<span class='text-emerald-600'>이번 주 목표 달성 ✓ — 꾸준함이 신호를 쌓고 있어요</span>" if cons["on_pace"]
                         else f"<span class='text-amber-600'>이번 주 {cons['this_week']}/{cons['weekly_target']}회 — 꾸준함이 쌓아온 신호가 아까워요</span>")
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
                             "<div class='text-[10px] text-slate-400 mt-1'>← 4주 전 · · 이번 주 →</div>"
                             "<p class='text-xs text-slate-500 mt-2'>꾸준한 발행이 네이버 신뢰를 쌓아요. "
                             f"주 {cons['weekly_target']}회 페이스를 유지해 봐요. (무조건 상위 보장은 아니에요)</p></div>")
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
                    f"<div class='text-xs text-slate-400'>{esc((p.get('published_at') or '')[:10])} · "
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
                # 네이버 통계 딥링크(방문자 B3) — 조회수·성별연령·유입검색어는 네이버만 보여준다(정직)
                + ("<div class='flex items-center gap-3 bg-emerald-50/60 border border-emerald-100 rounded-xl px-3.5 py-2.5 mt-3'>"
                   "<div class='flex-1 text-xs text-slate-600'><b class='text-slate-700'>조회수·성별·연령·유입 검색어</b>는 "
                   "네이버 블로그 통계에서만 볼 수 있어요. 특히 <b class='text-emerald-700'>유입 검색어</b>를 보세요 — "
                   "손님이 어떤 말로 검색해 들어왔는지가 곧 다음 글 소재예요.</div>"
                   f"<a href='https://admin.blog.naver.com/AnalyticsMainView.naver?blogId={esc(t.blog_id)}' target=_blank rel=noopener "
                   "class='flex-shrink-0 bg-emerald-600 text-white text-xs font-bold px-3.5 py-2 rounded-xl'>네이버 통계 열기 ↗</a></div>")
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
    rank_box = ((f"<div class='mb-4'><div class='text-xs font-bold text-slate-500 mb-1'>지도 노출 순위(분리 추적)</div>{rank_rows}</div>")
                if rank_rows else "")
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
    return (f"<div id='place' class='{fw} mt-5'>"
            "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>플레이스 최적화 (매장)</h2>"
            f"<p class='text-sm text-slate-400 mb-4'>동네매장은 지도 상위노출이 방문에 직결돼요 · 정보 완성 {s['done']}/{s['known']}"
            " · 리뷰는 <b>실제 방문 손님</b>에게만 정당하게 요청해요(가짜 리뷰 금지).</p>"
            + rank_box
            + "<div class='grid sm:grid-cols-2 gap-5'>"
            f"<div><div class='text-xs font-bold text-slate-500 mb-1'>정보 완성도 체크리스트</div>{chk}</div>"
            f"<div><div class='text-xs font-bold text-slate-500 mb-1'>리뷰 요청 키트</div>{rv}{qr}</div>"
            "</div>"
            + _print_block(t)
            + "</div>")


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
            + _step(s3, 3, "매장 QR·추적링크 붙이기 (손님 유입이 집계돼요)", "/me?tab=report#qr", "QR 받기")
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
                      f"<span class='text-[9px] text-slate-400'>{esc(h['at'][5:])}</span></div>")
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
    return RedirectResponse("/me?tab=report", status_code=303)


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
    return (f"<div class='bg-[#F5F3FF] border border-indigo-200 rounded-2xl p-5 mb-5'>"
            "<div class='flex items-center gap-2 mb-2'>"
            f"{_ic('message', 'w-4 h-4 text-indigo-600')}"
            "<span class='text-xs font-bold text-indigo-600'>오늘 아침 브리핑</span></div>"
            f"<div class='font-bold text-slate-900 mb-1'>{b.get('headline', '')}</div>"
            f"<div class='text-sm text-slate-700'><b>오늘 할 일 딱 하나</b> — {esc(b.get('task', ''))}</div>"
            f"<div class='text-xs text-slate-500 mt-1'>{esc(b.get('reason', ''))}</div>"
            f"<div class='text-xs text-indigo-500 mt-1.5'>{esc(b.get('partner_note', ''))}</div>"
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
    items = []
    for k in kws[:5]:
        det = place.rank_detail(k, t.name)
        cur = det["rank"]
        prev = db.get_prev_rank(t.id, k)            # 오늘 이전 순위(변화 계산)
        db.save_rank_snapshot(t.id, k, cur)         # 오늘 순위 기록
        item = {"kw": k, "rank": cur, "prev": prev,
                "rival": det["rival"], "leader": det["leader"]}
        if bid:
            from app.services import blogrank
            br = blogrank.blog_rank(k, bid)
            item["blog_rank"] = br["rank"]          # 1~30 | 0=미노출 | None=조회불가
            item["blog_prev"] = db.get_prev_rank(t.id, k, kind="blog_search")
            item["blog_url"] = br["url"]
            db.save_rank_snapshot(t.id, k, br["rank"], kind="blog_search")
        items.append(item)
    return JSONResponse({"items": items, "configured": place.configured(),
                         "blog_connected": bool(bid)})


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
    # 영상 3종 재생성: 기존 SHORT 폐기 → 즉시 백그라운드 재생성(자막 게이트 경유)
    from app.domain.models import ContentKind as _CK4
    for p in pieces:
        if p.kind == _CK4.SHORT:
            db.delete_piece(p.id, p.tenant_id)
    try:
        from app.services.ingest import _set_video_job, _spawn_video_bundle
        _set_video_job(asset_id, "registered", retried=False)
        asset = db.get_asset(asset_id)
        if asset:
            _spawn_video_bundle(tenant, asset, all_paths, blog.payload.get("brief") or {})
    except Exception:
        import logging
        logging.exception("[add-photos] 영상 재생성 예약 실패 asset=%s", asset_id)
    return RedirectResponse(f"/kit/{asset_id}/naver?ok=사진 {len(new_paths)}장 추가 — 슬롯 재배치·영상 재생성 중", status_code=303)


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


@app.get("/r/{code}")
def link_redirect(code: str, request: Request, utm_source: str = "", src: str = "", content: str = ""):
    """제휴/추적 단축링크 — 클릭 집계(행 단위: 채널·콘텐츠·시각·리퍼러·UA) 후 원본으로 이동.
    src={channel}&content={piece_id 앞8자} — 콘텐츠별 유입 실측(추적 P1). utm_source는 하위호환."""
    link = db.get_link(code)
    if not link or not link.get("target"):
        return RedirectResponse("/", status_code=302)
    # 익명 방문자 특성(방문자 B1) — 신원 파악 금지: 익명 쿠키(방문 구분)·기기·국가(CF 헤더)까지만
    ua = request.headers.get("user-agent", "")
    device = "mobile" if ("Mobi" in ua or "Android" in ua) else "pc"
    region = (request.headers.get("cf-ipcountry") or "").strip()[:8]   # 국가 단위(도시 아님 — 정직)
    vid = (request.cookies.get("ovid") or "").strip()[:32]
    new_cookie = not vid
    if new_cookie:
        vid = uuid.uuid4().hex[:16]
    db.incr_link_click(code, referrer=request.headers.get("referer", ""),
                       ua=ua, utm_source=utm_source,
                       content_id=content, channel=(src or utm_source),
                       visitor_id=vid, device=device, region=region)
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


def _result_naver_video(pieces, asset_id: str) -> str:
    """(결과 화면 1-1·1-2) 네이버 블로그 카드에 네이버용 영상 인라인 미리보기 — 키트 플레이어와 동일.
    생성 중이면 상태 문구(기존 pieces 폴링이 완성 시 화면을 갱신 → 자동 교체), 실패·부재면 생략."""
    try:
        from app.domain.models import ContentKind as _CKr
        short = next((p for p in pieces if p.kind == _CKr.SHORT and (p.payload or {}).get("naver_video")), None)
        nv = (short.payload.get("naver_video") or {}) if short else {}
        src_p = nv.get("body_path") or nv.get("path") or ""
        if src_p:
            return (f"<div class='mt-3'><div class='text-xs font-bold text-slate-400 mb-1'>네이버용 영상 (본문 삽입·클립 겸용)</div>"
                    f"<video src='/dl/{asset_id}/{os.path.basename(src_p)}' controls preload='none' "
                    "class='w-full max-h-72 rounded-xl bg-black'></video></div>")
        blog = next((p for p in pieces if p.kind == _CKr.BLOG), None)
        vj = (blog.payload.get("video_job") or {}) if blog else {}
        if vj.get("status") in ("registered", "running", "retrying"):   # 실패·부재는 생략(무한 '만드는 중' 방지)
            return ("<div class='mt-3 flex items-center gap-2 text-xs text-slate-400'>"
                    "<span class='inline-block w-3 h-3 border-2 border-slate-300 border-t-indigo-500 rounded-full animate-spin'></span>"
                    "영상 만드는 중이에요 (몇 분 걸려요) — 완성되면 여기에 나타나요</div>")
        return ""
    except Exception:
        return ""


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

    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []

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
            title = pl.get("title", "")
            sid = p.id[:5]
            body_part = _re.sub(r"\[사진(\d+)\]", r"⬇⬇ 여기에 사진\1 올리기 ⬇⬇", pl.get("body", "")).strip()
            blog_copy = title + "\n\n" + body_part
            topts = [t for t in (pl.get("title_options") or []) if t]
            opts_html = ""
            if len(topts) >= 2:
                chips = "".join(f"<button type=button onclick=\"pickTitle('{sid}',this)\" data-t=\"{esc(t)}\" "
                                "class='text-[11px] bg-slate-100 hover:bg-indigo-50 text-slate-600 px-2 py-1 rounded-lg mr-1 mb-1 text-left'>"
                                f"{esc(t[:26])}</button>" for t in topts)
                opts_html = (f"<div class='mb-2'><span class='text-[11px] text-slate-400'>제목 바꾸기 (검색 노출용 3안):</span>"
                             f"<div class='mt-1 flex flex-wrap'>{chips}</div></div>")
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
                     + f"<div class='mt-4 space-y-2'>{naver_btn}"
                     + f"<div class='flex gap-2'>{pack_btn(p.id, False)}<button type=button onclick=\"cp('cb{sid}',this)\" class='px-3.5 py-2.5 border border-slate-200 text-slate-600 hover:bg-slate-50 text-xs font-bold rounded-xl transition'>글 복사</button></div></div></div>")
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
        if not _s:                                         # 상태 기록 이전 구세트 — 하위호환(placeholder 생략)
            continue
        if _s in ("generating", "registered"):
            _inner = ("<div class='flex items-center gap-2 text-sm text-slate-500'>"
                      "<span class='inline-block w-4 h-4 border-2 border-slate-300 border-t-indigo-500 rounded-full animate-spin'></span>"
                      "만드는 중이에요 (몇 분 걸려요) — 완성되면 자동으로 나타나요</div>")
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
    js = ("<script>"
          "function omCopy(text){if(navigator.clipboard&&navigator.clipboard.writeText){return navigator.clipboard.writeText(text);}"
          "return new Promise(function(res,rej){var ta=document.createElement('textarea');ta.value=text;ta.setAttribute('readonly','');ta.style.position='fixed';ta.style.top='0';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();ta.setSelectionRange(0,text.length);var ok=false;try{ok=document.execCommand('copy');}catch(e){}document.body.removeChild(ta);ok?res():rej();});}"
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
          "function pickTitle(sid,btn){var t=btn.getAttribute('data-t');var el=document.getElementById('bt'+sid);if(el)el.textContent=t;var ta=document.getElementById('cb'+sid);if(ta)ta.value=t+'\\n\\n'+(ta.getAttribute('data-body')||'');}"
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
    if _recent and (_pending_ch or not any(p.kind.value == "short" for p in pieces)):
        _banner = ""
        if not any(p.kind.value == "short" for p in pieces):     # 배너는 영상 부재 시만(텍스트 재시도엔 카드가 상태 표시)
            _banner = ("<div class='bg-amber-50 border border-amber-100 text-amber-700 rounded-2xl p-3.5 mb-5 text-sm flex items-center gap-2'>"
                       "<div class='w-4 h-4 border-2 border-amber-300 border-t-amber-600 rounded-full animate-spin flex-shrink-0'></div>"
                       "유튜브 쇼츠·인스타 릴스 <b>영상 생성 중…</b> 완성되면 자동으로 나타나요 (이 화면 유지)</div>")
        _vid_poll = (_banner
                     + f"<script>(function(){{var base=null,n=0,aid='{asset_id}';"
                     "var iv=setInterval(async function(){n++;if(n>50){clearInterval(iv);return;}"
                     "try{var d=await (await fetch('/me/asset/'+aid+'/pieces')).json();"
                     "if(base===null){base=d.sig;return;}if(d.sig!==base){clearInterval(iv);location.reload();}}catch(_){}"
                     "},3000);})();</script>")
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
                f"<p class='text-xs text-slate-400 mt-2'>발행 시각: {esc((pub.get('published_at') or '')[:16].replace('T', ' '))} · 이 글의 순위를 추적 중이에요.</p></div>")
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
            + "<p class='text-xs text-amber-600 mb-3'><a href='/me?tab=report#blog' class='font-bold underline'>내 블로그를 연결</a>하면 "
            "발행 여부를 자동으로 확인해 드려요. 연결 전에는 아래에 발행 주소를 남겨주세요.</p>"
            + f"<form method=post action='/me/blog/published' class='flex gap-2'>"
            f"<input type=hidden name=piece_id value='{blog.id}'>"
            f"<input name=url placeholder='발행한 글 주소 붙여넣기 (https://blog.naver.com/...)' class='{inp}'>"
            f"<button class='{cbtn} bg-indigo-600 hover:bg-indigo-700 whitespace-nowrap'>발행함 ✓</button></form></div>")


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
    sname = tenant.name if tenant else "내 가게"
    title = blog.payload.get("title", "")
    body_marked = _re.sub(r"\[사진(\d+)\]", r"\n\n[📷 사진\1 위치]\n\n", blog.payload.get("body", "")).strip()
    _slot_refs = {int(n) for n in _re.findall(r"\[사진(\d+)\]", blog.payload.get("body", ""))}
    if _slot_refs and max(_slot_refs) > len(imgs):     # (정합 2-1) 슬롯 참조 > 사진 수 감지(상시)
        import logging as _lg3
        _lg3.getLogger("shopcast.kit").warning("[kit] 슬롯 참조(%d) > 사진 수(%d) asset=%s",
                                               max(_slot_refs), len(imgs), asset_id)
    photos = [im for im in imgs if im]                          # /dl이 R2로 서빙
    vid = next((p for p in pieces if p.kind.value == "short" and p.payload.get("video_path")), None)
    vurl = f"/dl/{asset_id}/{os.path.basename(vid.payload['video_path'])}" if vid else ""  # 블로그 본문 삽입용
    _nv = (vid.payload.get("naver_video") or {}) if vid else {}          # 네이버용 정보형 영상(있으면)
    def _media_exists(p_):
        """로컬 또는 R2 미러 존재 — 컨테이너 교체로 로컬만 사라진 경우 오탐 방지(근본수정 [결함2])."""
        if p_ and os.path.exists(p_):
            return True
        try:
            from app import storage as _st
            return bool(p_ and _st.r2_media_url(vid.tenant_id, os.path.basename(p_)))
        except Exception:
            return False
    _nv = _nv if (_nv.get("path") and _media_exists(_nv["path"])) else {}
    _fn_base = _seo_photo_name(tenant, blog)               # 이미지 SEO(5-1): 지역-업종-피사체
    photo_cells = "".join(
        f"<div class='relative'><img src='/dl/{asset_id}/{os.path.basename(im)}' class='w-full aspect-square object-cover rounded-xl border border-slate-200'>"
        f"<div class='absolute top-2 left-2 w-7 h-7 rounded-full bg-black/75 text-white text-sm font-bold flex items-center justify-center'>{i+1}</div>"
        f"<a href='/dl/{asset_id}/{os.path.basename(im)}' download='{_fn_base}-{i+1:02d}.jpg' class='absolute bottom-2 right-2 bg-white/95 text-slate-700 text-xs font-bold px-2 py-1 rounded-lg shadow hover:bg-white'>⬇ 저장</a></div>"
        for i, im in enumerate(photos))
    sec = "bg-white rounded-2xl border border-slate-200 shadow-sm p-5 mb-5"
    cbtn = "px-4 py-2.5 rounded-xl text-white text-sm font-bold transition active:scale-[.98]"
    body = (
        "<a href='javascript:history.back()' class='inline-block text-sm text-slate-500 font-bold mb-2'>← 결과로</a>"
        f"<div class='text-sm text-indigo-500 font-bold'>{esc(sname)}</div>"
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
        # 본문
        f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>2. 본문 <span class='text-emerald-600'>(사진 위치 표시 포함)</span></div>"
        f"<div class='bg-slate-50 rounded-xl p-4 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto mb-3'>{esc(body_marked)}</div>"
        f"<textarea id='nvB' class='hidden'>{esc(body_marked)}</textarea>"
        f"<button onclick=\"nvcp('nvB',this)\" class='{cbtn} bg-emerald-500 hover:bg-emerald-600 w-full'>전체 본문 복사</button></div>"
        # 사진
        + (f"<div class='{sec}'><div class='flex items-center justify-between mb-3'>"
           "<div class='text-xs font-bold text-slate-400'>3. 사진 <span class='text-slate-500'>(순서대로)</span></div>"
           f"<a href='/kit/{asset_id}/pack/{blog.id}' class='text-xs font-bold text-indigo-600'>⬇ 전체 ZIP 받기</a></div>"
           f"<div class='grid grid-cols-3 sm:grid-cols-4 gap-3'>{photo_cells}</div>"
           "<p class='text-xs text-slate-400 mt-2'>사진은 이 파일명 그대로, 캡션은 사진 아래 붙여넣으면 검색에 더 잘 잡혀요.</p>"
           f"<form method=post action='/me/set/{asset_id}/photos' enctype='multipart/form-data' class='flex items-center gap-2 mt-2'>"
           "<input type=file name=photos accept='image/*' multiple required class='text-xs flex-1'>"
           f"<button class='{cbtn} bg-slate-700 hover:bg-slate-800 whitespace-nowrap'>사진 추가</button></form>"
           "<p class='text-[11px] text-slate-400 mt-1'>과정 사진(물세척·재단 등)을 더 올리면 AI가 슬롯·캡션·영상을 다시 맞춰드려요 — 글 내용은 그대로예요.</p>"
           + _caption_box(tenant, blog, len(photos)) + "</div>" if photos else "")
        # 네이버용 영상(통합 블록 2-4) — 구 '동영상도 본문에' 블록 흡수, 쇼츠·릴스는 채널 카드 전용
        + ((f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>4. 네이버용 영상 <span class='text-emerald-600'>(블로그 첨부 · 클립 겸용)</span></div>"
            "<p class='text-xs text-slate-500 mb-3'>이 영상을 <b>본문 첫 소제목 아래</b>에 넣으세요 — 15초+ 영상은 검색 가점(D.I.A.+). "
            "같은 영상을 네이버 클립에도 올리면 지면이 하나 더 생겨요.</p>"
            f"<video src='/dl/{asset_id}/{os.path.basename(_nv.get('body_path') or _nv.get('path', ''))}' controls preload='none' "
            "class='w-full max-h-80 rounded-xl bg-black mb-3'></video>"
            f"<div class='text-sm font-bold text-slate-800 mb-1'>{esc(_nv.get('title', ''))}</div>"
            f"<textarea id='nvVT' class='hidden'>{esc(_nv.get('title', ''))}</textarea>"
            f"<div class='text-xs text-slate-500 whitespace-pre-wrap mb-2'>{esc(_nv.get('desc', ''))}</div>"
            f"<textarea id='nvVD' class='hidden'>{esc(_nv.get('desc', ''))}</textarea>"
            "<div class='flex flex-wrap gap-2'>"
            + ((f"<a href='/dl/{asset_id}/{os.path.basename(_nv['body_path'])}' download='{esc(_nv.get('filename_body') or _nv.get('filename', ''))}' class='{cbtn} bg-emerald-500 hover:bg-emerald-600 inline-block'>⬇ 본문 삽입용 (16:9)</a>")
               if _nv.get('body_path') else "")
            + f"<a href='/dl/{asset_id}/{os.path.basename(_nv.get('path', ''))}' download='{esc(_nv.get('filename_clip') or _nv.get('filename', 'naver-video.mp4'))}' class='{cbtn} bg-emerald-600 hover:bg-emerald-700 inline-block'>⬇ 클립 업로드용 (9:16)</a>"
            f"<button onclick=\"nvcp('nvVT',this)\" class='{cbtn} bg-indigo-600 hover:bg-indigo-700'>제목 복사</button>"
            f"<button onclick=\"nvcp('nvVD',this)\" class='{cbtn} bg-indigo-600 hover:bg-indigo-700'>설명 복사</button></div>"
            f"<div class='text-[11px] text-slate-400 mt-2'>파일명: {esc(_nv.get('filename', ''))} · 길이 약 {int(_nv.get('duration_sec') or 0)}초</div></div>") if _nv else "")
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
        "setTimeout(function(){btn.textContent=o;tt.style.opacity='0';},1600);}</script>")
    return HTMLResponse(_subscriber_page("네이버 블로그", body))


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


def _piece_pack_entries(piece, imgs, prefix=""):
    """채널 하나의 (zip경로, 소스) 목록 — 글.txt + 사진 + 영상 한 묶음."""
    import re as _re2
    k, pl = piece.kind.value, piece.payload
    # 이미지 SEO — 파일명에 지역+업종 키워드(네이버·구글 이미지검색이 파일명을 읽음)
    _kwbase = _re2.sub(r'[\\/:*?"<>|\s]+', "", ((pl.get("target_keywords") or [""])[0] or "")).strip("_")[:30] or "사진"
    ent = []

    def add(name, src):
        ent.append((f"{prefix}{name}", src))
    if k == "blog":
        txt = f"[제목]\n{pl.get('title','')}\n\n[본문]\n{pl.get('body','')}\n"
        if pl.get("tags"):
            txt += "\n[태그]\n" + " ".join(pl["tags"]) + "\n"
        add("네이버블로그_글.txt", ("text", txt))
        for i, im in enumerate(imgs, 1):
            add(f"{_kwbase}_{i}{os.path.splitext(im)[1] or '.jpg'}", im)
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


def _fetch_local_or_r2(path: str):
    """파일 바이트 — 로컬 없으면 R2에서 다운로드(이관 후 다운로드 보장). 실패 시 None."""
    try:
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        from app import storage as _st
        if path and _st.r2_configured():
            import urllib.request
            key = os.path.relpath(path, _st.STORAGE_DIR).replace(os.sep, "/")
            url = os.environ["R2_PUBLIC_URL"].rstrip("/") + "/" + key
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})   # r2.dev가 기본 UA 차단
            return urllib.request.urlopen(req, timeout=25).read()
    except Exception:
        return None
    return None


def _zip_bytes(entries) -> bytes:
    """ZIP을 메모리에서 생성(디스크 미사용). 로컬 삭제된 사진·영상은 R2에서 받아 포함."""
    import zipfile
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, src in entries:
            if isinstance(src, tuple) and src[0] == "text":
                z.writestr(arc, src[1])
            elif src:
                data = _fetch_local_or_r2(src)      # 로컬 또는 R2에서
                if data:
                    z.writestr(arc, data)
    return buf.getvalue()


def _zip_response(data: bytes, filename: str):
    from urllib.parse import quote
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)})


def _safe_title(pieces) -> str:
    """다운로드 파일명용 — 콘텐츠 제목(블로그 제목 우선)에서 파일명 금지문자 제거."""
    import re
    t = next((p.payload.get("title") for p in pieces if p.payload.get("title")), "") or "올린다콘텐츠"
    t = re.sub(r'[\\/:*?"<>|\n\r\t]', "", t).strip()[:40]
    return t or "올린다콘텐츠"


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
    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []
    data = _zip_bytes(_piece_pack_entries(piece, imgs))
    return _zip_response(data, f"{_safe_title(pieces)}_{_ch_folder(piece)}.zip")


@app.get("/kit/{asset_id}/pack-all")
def kit_pack_all(request: Request, asset_id: str):
    """5채널 전체 ZIP — 채널별 폴더로 정리."""
    u = auth.current_user(request)
    pieces = _owned_pieces(u, asset_id) if u else None
    if not pieces:
        return HTMLResponse(status_code=404)
    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []
    entries = []
    for p in pieces:
        entries += _piece_pack_entries(p, imgs, prefix=f"{_ch_folder(p)}/")
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
def admin_regen_channel(asset_id: str, kind: str = ""):
    """누락·실패 텍스트 채널(caption/x_post) 단건 재생성 — 표준 경로 재사용(전 게이트 경유).
    글·기존 정상 피스 불변. 이미 피스가 있으면 거부(임의 재생성 금지)."""
    from app.domain.models import ContentKind as _CK
    _map = {"caption": _CK.CAPTION, "x_post": _CK.X_POST}
    ck = _map.get(kind)
    if not ck:
        return JSONResponse({"ok": False, "error": "kind는 caption|x_post"}, status_code=400)
    pieces = db.get_set_pieces(asset_id)
    blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
    if not blog:
        return JSONResponse({"ok": False, "error": "블로그 피스 없음"}, status_code=404)
    if any(p.kind == ck for p in pieces):
        return JSONResponse({"ok": False, "error": "이미 존재 — 임의 재생성 금지"}, status_code=409)
    from app.services.ingest import _regen_text_piece, _set_channel_status, KIND_TO_CHANNEL
    tenant = db.get_tenant(blog.tenant_id)
    asset = db.get_asset(asset_id)
    if not (tenant and asset):
        return JSONResponse({"ok": False, "error": "tenant/asset 소실"}, status_code=404)
    ok = _regen_text_piece(tenant, asset, ck, blog)
    ch = KIND_TO_CHANNEL[ck]
    _set_channel_status(asset_id, {ch: {"status": "done" if ok else "failed"}})
    made = next((p for p in db.get_set_pieces(asset_id) if p.kind == ck), None)
    return JSONResponse({"ok": ok, "channel": ch,
                         "text": (made.payload.get("text") if made else None)})


@app.get("/admin/set/{asset_id}/pieces.json")
def admin_set_pieces_json(asset_id: str):
    """진단(읽기 전용) — 세트 피스들의 영상 관련 payload 요약(naver 2종·해시태그·자막·경로 존재 여부)."""
    out = []
    for p in db.get_set_pieces(asset_id):
        pl = p.payload or {}
        nv = pl.get("naver_video") or {}
        out.append({
            "kind": str(p.kind), "id": p.id[:8],
            "video_path": pl.get("video_path"),
            "video_exists": bool(pl.get("video_path")) and os.path.exists(pl.get("video_path") or ""),
            "duration_sec": pl.get("duration_sec"),
            "subtitles_n": len(pl.get("subtitles") or []),
            "naver_video": {k: nv.get(k) for k in
                            ("path", "body_path", "title", "filename", "filename_body", "filename_clip",
                             "hashtags", "duration_sec")} if nv else None,
            "naver_exists": {"clip": bool(nv.get("path")) and os.path.exists(nv.get("path") or ""),
                             "body": bool(nv.get("body_path")) and os.path.exists(nv.get("body_path") or "")} if nv else None,
            "video_job": (pl.get("video_job") or None) if p.kind and "BLOG" in str(p.kind) else None,
            "channel_status": (pl.get("channel_status") or None) if p.kind and "BLOG" in str(p.kind) else None,
        })
    return {"asset_id": asset_id, "pieces": out}


@app.api_route("/admin/disk", methods=["GET", "POST"])
def admin_disk(prune: str = ""):
    """디스크 진단 — 확장자별 사용량 + DB 미참조(고아) 미디어 집계.
    prune=1이면 고아 영상·커버·임시파일만 삭제(사진·DB·참조 파일 불변 — R2 무관하게 안전)."""
    import shutil as _sh
    from collections import defaultdict
    from app.storage import STORAGE_DIR
    refs = _referenced_media()
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
            # 고아 후보: 생성 산출물류만(mp4/png/wav/ass) — 원본 사진(jpg 등)은 건드리지 않음
            if ext in ("mp4", "png", "wav", "ass") and os.path.realpath(fp) not in refs:
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
        <p class='text-xs text-slate-400 mt-1.5'><b class='text-slate-500'>끌어서</b> 순서 변경 · <b class='text-slate-500'>＋</b> 로 여러 장 추가 · 올린 순서대로 영상·블로그에 배치돼요</p></div>
      <div><label class='{lb}'>4. 목적 <span class='text-slate-400 font-normal text-xs'>(선택)</span></label>
        <div class='flex flex-wrap gap-2'>{chips}</div></div>
      <div><label class='{lb}'>5. 사진 확인·정보 <span class='text-slate-400 font-normal text-xs'>(선택 · 넣을수록 글이 구체적으로 좋아져요)</span></label>
        <input type=hidden name=confirmed id=pg_confirmed><input type=hidden name=intent id=pg_intent><input type=hidden name=vision_analysis id=pg_vision>
        <input type=hidden name=answers id=pg_answers><input type=hidden name=experience id=pg_experience>
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
          "var PM={f:[],drag:-1};"
          "function pmSync(){var dt=new DataTransfer();PM.f.forEach(function(x){dt.items.add(x);});document.getElementById('up_photos').files=dt.files;}"
          "function pmDel(i){PM.f.splice(i,1);pmRender();if(typeof pdOffer==='function')pdOffer();}"
          "function pmAdd(){document.getElementById('up_photos').click();}"
          "function pmDrop(target){if(PM.drag<0)return;var it=PM.f.splice(PM.drag,1)[0];if(target>PM.f.length)target=PM.f.length;if(target<0)target=0;PM.f.splice(target,0,it);PM.drag=-1;pmRender();}"
          "function pmRender(){var pv=document.getElementById('up_preview');pv.innerHTML='';"
          "PM.f.forEach(function(x,i){var d=document.createElement('div');d.className='relative aspect-square cursor-move';d.draggable=true;"
          "d.ondragstart=function(e){PM.drag=i;e.dataTransfer.effectAllowed='move';};"
          "d.ondragover=function(e){e.preventDefault();d.classList.add('ring-2','ring-indigo-400');};"
          "d.ondragleave=function(){d.classList.remove('ring-2','ring-indigo-400');};"
          "d.ondrop=function(e){e.preventDefault();d.classList.remove('ring-2','ring-indigo-400');pmDrop(i);};"
          "var im=document.createElement('img');im.src=URL.createObjectURL(x);im.className='w-full h-full object-cover rounded-xl border border-slate-100 pointer-events-none';d.appendChild(im);"
          "d.insertAdjacentHTML('beforeend',"
          "\"<div class='absolute top-1 left-1 w-5 h-5 rounded-full bg-black/60 text-white text-[10px] font-bold flex items-center justify-center pointer-events-none'>\"+(i+1)+\"</div>\"+"
          "\"<button type=button onclick='pmDel(\"+i+\")' class='absolute top-1 right-1 w-5 h-5 rounded-full bg-rose-500 text-white text-xs leading-none flex items-center justify-center'>&times;</button>\");"
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
          "+'<div class=\"text-slate-700\">사진 <b>'+PM.f.length+'장</b> 준비됐어요. 정리(×삭제·＋추가)가 끝났으면 AI 확인을 시작할까요?</div>'"
          "+'<div class=\"flex items-center gap-2 mt-2\"><button type=\"button\" id=\"pg_start\" class=\"px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold\">이 사진들로 분석 시작</button>'"
          "+'<span class=\"text-[11px] text-slate-400\">안 해도 바로 만들 수 있어요</span></div></div>';"
          "document.getElementById('pg_start').onclick=function(){paidGuess();};}"
          "async function paidGuess(){var box=document.getElementById('pg_guess');if(!box||!PM.f.length)return;"
          "var seq=++_pgseq,fin=false;pdReady(false,'사진을 확인하는 중이에요 — 잠시만요');"
          "box.innerHTML='<div class=\"bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5\">'"
          "+'<div id=\"pg_pl\" class=\"text-xs font-bold text-slate-600 mb-1.5\">사진 분석 중…</div>'"
          "+'<div class=\"w-full h-1.5 bg-slate-200 rounded-full overflow-hidden\"><div id=\"pg_pb\" class=\"h-full bg-indigo-500 rounded-full\" style=\"width:15%;transition:width .5s\"></div></div></div>';"
          "var stg=['사진 분석 중…','무엇이 담겼는지 파악 중…','거의 다 됐어요…'],si=0,w=15;"
          "var st=setInterval(function(){var l=document.getElementById('pg_pl'),b=document.getElementById('pg_pb');"
          "if(!l||!b){clearInterval(st);return;}si=Math.min(si+1,2);w=Math.min(w+22,90);l.textContent=stg[si];b.style.width=w+'%';},2200);"
          "var n=Math.min(PM.f.length,6),tmo=Math.min(45000,25000+4000*n);"
          "var to=setTimeout(function(){if(fin||seq!==_pgseq)return;fin=true;clearInterval(st);"
          "box.innerHTML='';pdReady(true,'사진 확인이 오래 걸려 건너뛰었어요 — 바로 만들 수 있어요');},tmo);"
          "var fd=new FormData();fd.append('industry',(document.getElementById('s_industry')||{}).value||'');"
          "fd.append('purpose',(document.querySelector('input[name=purpose]:checked')||{}).value||'');"
          "PM.f.slice(0,6).forEach(function(f){fd.append('photos',f);});"
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
          "var st=[[0,'마케팅 전략가가 분석 중…'],[20,'카피라이터가 글 쓰는 중…'],[42,'SEO 편집장이 다듬는 중…'],[62,'영상 감독이 영상 만드는 중…'],[85,'영상 마무리 중…']];"
          "function setBar(v){var b=document.getElementById('gBar');if(b)b.style.width=v+'%';var g=document.getElementById('gPct');if(g)g.textContent=Math.round(v)+'%';var l=st[0][1];st.forEach(function(s){if(v>=s[0])l=s[1];});var gl=document.getElementById('gLabel');if(gl)gl.textContent=l;}"
          "var aid='';var p=0;var tick=setInterval(function(){var cap=aid?97:60;p=Math.min(p+(p<58?1.0:0.35),cap);setBar(p);},600);"
          "var base=0;try{base=(await (await fetch('/me/sets/count')).json()).n;}catch(_){}"
          "var fd=new FormData(f);try{if(window.PM&&PM.f&&PM.f.length){fd.delete('photos');PM.f.forEach(function(x){fd.append('photos',x);});}}catch(_){}"
          "try{await fetch(f.action,{method:'POST',body:fd});}catch(_){}"
          "function doneU(){return aid?('/me?made='+aid):'/me';}"
          "function done(url){clearInterval(iv);clearInterval(tick);location.href=url;}"
          "var n=0;var iv=setInterval(async function(){n++;if(n>120){done(doneU());return;}"
          "try{"
          "if(!aid){var d=await (await fetch('/me/sets/count')).json();if(d.n>base){aid=d.latest;if(p<62)p=62;setBar(p);}return;}"
          "var pj=await (await fetch('/me/asset/'+aid+'/pieces')).json();"
          "if(pj.n>=5){clearInterval(iv);clearInterval(tick);setBar(100);var gl=document.getElementById('gLabel');if(gl)gl.textContent='✅ 5채널 완성!';setTimeout(function(){location.href='/me?made='+aid;},700);}"
          "else if(n>70){done('/me?made='+aid);}"        # 영상이 너무 오래 걸리면 만들기 대시보드에 결과 표시(폴링은 보기에서 이어받음)
          "}catch(_){}"
          "},3000);return false;}"
          "</script>")
    gen_overlay = ("<div id='genOverlay' class='fixed inset-0 z-50 hidden items-center justify-center' style='background:rgba(15,23,42,.45);backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)'>"
                   "<div class='bg-white rounded-2xl p-6 w-72 max-w-[85vw] text-center shadow-2xl'>"
                   "<div id='gLabel' class='font-bold text-sm mb-3'>마케팅 전략가가 분석 중…</div>"
                   "<div class='w-full h-2 bg-slate-100 rounded-full overflow-hidden'><div id='gBar' class='h-full bg-indigo-500' style='width:0%;transition:width .4s'></div></div>"
                   "<div id='gPct' class='text-slate-400 text-xs mt-1.5'>0%</div>"
                   "<p class='text-xs text-slate-400 mt-3'>AI 전문가팀이 만드는 중… (20~60초)</p></div></div>")
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
                 s_map: str = Form(""), s_market: str = Form(""), s_brand: str = Form(""),
                 s_search: str = Form(""), target_kw: str = Form(""), angle: str = Form(""),
                 confirmed: str = Form(""), vision_analysis: str = Form(""),
                 answers: str = Form(""), experience: str = Form(""), intent: str = Form("")):
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
    # 생성은 시간이 오래 걸려(전략가→3채널→SEO편집) 요청을 붙잡으면 서버 타임아웃(500).
    # → 백그라운드 스레드에서 생성하고 요청은 즉시 반환. 완료되면 대시보드에 자동 표시.
    _ind = s_industry.strip()
    _record_usage(owner)                           # 쿼터 선예약 — 동시 업로드로 한도 우회 방지(B7)

    def _bg_generate():
        try:
            _prune_old_media(tenant.id, keep_recent=5)   # 생성 전 오래된 영상 정리(디스크 확보)
            # 자동 글감(auto): 타겟 미지정 업로드면 큐가 다음 글감(키워드·앵글)을 결정한다
            nonlocal target_kw, angle
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
                       "analysis": (vision_analysis or "").strip()[:4000],
                       "answers": _si.parse_answers(answers),
                       "experience": experience.strip()[:200],
                       "intent": intent.strip()[:40]}
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
                                 intake=_intake)
            if made and _q_claim:
                _bp = next((p for p in made if p.kind.value == "blog"), None)
                from app import db as _db3
                _db3.mark_writing(_q_claim["id"], "done", piece_id=(_bp.id if _bp else ""))
            elif _q_claim:
                from app import db as _db3
                _db3.rollback_writing(_q_claim["id"])
            if not made:
                _refund_usage(owner)               # 생성 결과 없음 → 예약 원복
        except Exception:
            _refund_usage(owner)                   # 실패 → 예약 원복
            import logging
            logging.exception("[upload-bg] 생성 실패 tenant=%s", tenant.id)
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
