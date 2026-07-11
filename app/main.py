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


async def _read_image_uploads(photos, limit: int = 10) -> list[tuple[bytes, str]]:
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


@app.post("/api/demo")
async def api_demo(request: Request, industry: str = Form(""), note: str = Form(""),
                   biz_type: str = Form("local"), marketplace: str = Form(""),
                   search_kw: str = Form(""), purpose: str = Form(""),
                   photos: list[UploadFile] = File(None)):
    """랜딩 데모 — 미가입자는 '실제 생성 티저(흐리게)'로 가입 유도. 로그인 회원은 작업실로."""
    u = auth.current_user(request)
    if u:                                            # 로그인 회원 → 작업실에서 실제 생성
        used = u.get("free_used") or 0
        free = (u.get("plan") or "free") == "free"
        if free and used >= FREE_LIMIT:
            return JSONResponse({"limit": True, "message": f"무료 {FREE_LIMIT}회를 모두 사용했어요. 요금제로 무제한 이용하세요."})
        left = (FREE_LIMIT - used) if free else None
        return JSONResponse({"go_dashboard": True,
                             "message": "내 작업실에서 사진을 올리면 바로 만들어드려요!"
                                        + (f" (무료 {left}회 남음)" if left is not None else "")})
    # 미로그인 → 실제 생성 후 '흐리게' 미리보기(티저)로 가입 유도
    if not (industry or "").strip():
        return JSONResponse({"require_signup": True, "message": "업종/상품을 입력하면 실제로 만들어 보여드려요!"})
    ip = (request.headers.get("cf-connecting-ip")
          or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "") or "unknown")
    if db.demo_ip_count(ip) >= 2:                    # 무료 미리보기 2회 → 그다음 가입 유도
        return JSONResponse({"require_signup": True,
                             "message": "무료 미리보기 2회를 다 보셨어요! 가입하면 5채널 전부 + 영상까지 무료로 만들어드려요 🎁"})
    imgs = await _read_image_uploads(photos)
    full_note = (note or "").strip()
    if purpose.strip():                              # 목적 → 생성 프롬프트에 반영(글·영상 톤↑)
        full_note = (full_note + f" | 콘텐츠 목적: {purpose.strip()}").strip(" |")
    try:
        from app.services import teaser as teaser_svc
        _t, _a, pieces, brief = teaser_svc.run_teaser(industry, biz_type, full_note, imgs)
    except Exception:
        import logging
        logging.exception("[teaser] 실패")
        return JSONResponse({"require_signup": True, "message": "가입하면 바로 만들어드려요!"})
    if not pieces:                                   # 크레딧 부족/일시 오류 → 정직 안내
        return JSONResponse({"require_signup": True, "message": "지금 생성이 잠시 붐벼요. 잠깐 뒤 다시 시도해 주세요 🙏"})
    db.incr_demo_ip(ip)
    remaining = max(0, 2 - db.demo_ip_count(ip))
    return JSONResponse({"teaser": True, "teaser_html": _teaser_html(pieces, brief, _a, remaining)})


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


def _teaser_html(pieces, brief, asset_id, remaining: int = 0) -> str:
    """미가입 무료 체험 결과 — 전체 공개(흐림 없음) + 복사·다운로드 + 내 사진으로 만든 실제 영상. 2회 후 가입 유도."""
    import re as _re
    by = {p.kind.value: p for p in pieces}
    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []
    thumbs = [x for x in (_img_thumb_data_uri(p) for p in imgs[:6]) if x]
    photos = (("<div class='flex gap-2 overflow-x-auto pb-1 mb-3'>"
               + "".join(f"<img src='{u}' class='h-24 w-24 object-cover rounded-lg flex-shrink-0'>" for u in thumbs)
               + "</div>") if thumbs else "")

    def copy_btn(cid, text):
        return (f"<textarea id='{cid}' class='hidden'>{esc(text)}</textarea>"
                f"<button type=button onclick=\"omCopy(document.getElementById('{cid}').value);this.textContent='✅ 복사됨'\" "
                "class='mt-2 px-3 py-1.5 bg-indigo-600 text-white text-xs font-bold rounded-lg'>📋 복사</button>")

    def card(label, inner):
        return f"<div class='bg-white rounded-2xl p-4 shadow-sm'><div class='font-bold text-sm text-slate-700 mb-1'>{label}</div>{inner}</div>"

    cards = []
    cap = by.get("caption")
    if cap:
        cards.append(card("📷 인스타그램",
            f"<div class='text-slate-700 text-sm whitespace-pre-wrap max-h-56 overflow-y-auto'>{esc(cap.payload.get('text',''))}</div>"
            + copy_btn("t_cap", cap.payload.get("text", ""))))
    blog = by.get("blog")
    if blog:
        body = _re.sub(r"\[사진\d+\]", "", blog.payload.get("body", "")).strip()
        cards.append(card("📝 네이버 블로그",
            f"<div class='font-bold text-slate-800 text-sm mb-1'>{esc(blog.payload.get('title',''))}</div>"
            f"<div class='text-slate-600 text-xs whitespace-pre-wrap max-h-56 overflow-y-auto'>{esc(body)}</div>"
            + copy_btn("t_blog", (blog.payload.get('title', '') + "\n\n" + body))))
    x = by.get("x_post")
    if x:
        cards.append(card("𝕏 X (트위터)",
            f"<div class='text-slate-700 text-sm whitespace-pre-wrap'>{esc(x.payload.get('text',''))}</div>"
            + copy_btn("t_x", x.payload.get("text", ""))))
    short = next((p for p in pieces if p.kind.value == "short" and p.payload.get("video_path")), None)
    if short:
        v = f"/d/{asset_id}/f/{os.path.basename(short.payload['video_path'])}"
        cards.append(card("▶️ 유튜브 쇼츠 · 릴스 <span class='text-xs text-emerald-600'>(내 사진으로 만든 영상)</span>",
            f"<video src='{v}' controls autoplay muted loop playsinline class='w-full rounded-xl bg-black' style='max-height:440px'></video>"
            f"<a href='{v}' download class='inline-block mt-2 px-3 py-1.5 bg-emerald-500 text-white text-xs font-bold rounded-lg'>⬇ 영상 다운로드</a>"))
    else:
        cards.append(card("▶️ 유튜브 쇼츠 · 릴스 <span class='text-xs text-emerald-600'>(내 사진으로 만든 영상)</span>",
            f"<div id='tvid' data-a='{asset_id}'>"
            "<div class='text-slate-500 text-sm py-6 text-center'>🎬 영상 만드는 중… <span class='text-slate-400'>(30~60초, 자동으로 나타나요)</span>"
            "<div class='w-full h-1.5 bg-slate-100 rounded-full overflow-hidden mt-3'><div class='h-full bg-emerald-400' style='width:100%;animation:tvp 1.4s ease-in-out infinite'></div></div></div></div>"
            "<style>@keyframes tvp{0%,100%{opacity:.35}50%{opacity:1}}</style>"
            "<script>(function(){var el=document.getElementById('tvid');if(!el||el._p)return;el._p=1;var a=el.dataset.a,n=0;"
            "var iv=setInterval(async function(){n++;if(n>45){clearInterval(iv);el.innerHTML=\"<div class='text-slate-500 text-sm py-4 text-center'>영상은 가입 후 '내 작업실'에서 받을 수 있어요</div>\";return;}"
            "try{var r=await fetch('/api/demo/video/'+a);var d=await r.json();if(d.ready){clearInterval(iv);"
            "el.innerHTML='<video src=\"'+d.url+'\" controls autoplay muted loop playsinline class=\"w-full rounded-xl bg-black\" style=\"max-height:440px\"></video>'"
            "+'<a href=\"'+d.url+'\" download class=\"inline-block mt-2 px-3 py-1.5 bg-emerald-500 text-white text-xs font-bold rounded-lg\">⬇ 영상 다운로드</a>';}}catch(e){}},3000);})();</script>"))

    grid = "<div class='grid md:grid-cols-2 gap-3 mb-4'>" + "".join(cards) + "</div>"
    zip_btn = f"<a href='/d/{asset_id}.zip' class='block text-center bg-slate-800 hover:bg-slate-900 text-white font-bold py-3 rounded-xl mb-3'>⬇ 전체 다운로드 (글+사진+영상 ZIP)</a>"
    if remaining > 0:
        cta = (f"<div class='text-center text-white text-sm'>무료 체험 <b class='text-emerald-300'>{remaining}회</b> 남았어요 · 마음껏 만들어보세요!</div>"
               "<div class='text-center text-slate-400 text-xs mt-1'>2회 다 쓰면 가입하고 무제한으로 계속 만들 수 있어요</div>")
    else:
        cta = ("<div class='text-center text-emerald-300 text-sm font-bold mb-2'>무료 체험 2회를 다 쓰셨어요! 마음에 드셨죠? 😊</div>"
               "<a href='/login/kakao' class='block text-center py-3 rounded-xl font-extrabold mb-2' style='background:#FEE500;color:#191600'>💬 카카오로 가입하고 계속 만들기</a>"
               "<a href='/login/google' class='block text-center py-3 rounded-xl font-bold bg-white text-slate-700'>구글로 가입</a>")
    return ("<div class='rounded-2xl p-4' style='background:rgba(255,255,255,.06)'>"
            "<div class='text-center text-white font-extrabold text-lg mb-1'>✨ 방금 만든 결과 (전체 공개)</div>"
            "<div class='text-center text-slate-300 text-xs mb-3'>글은 복사, 사진·영상은 다운로드해서 바로 쓰세요</div>"
            + photos + grid + zip_btn + cta + "</div>")


@app.get("/api/demo/video/{asset_id}")
def demo_video_status(asset_id: str):
    """미가입 데모 영상 폴링 — 백그라운드 렌더 완료되면 URL 반환."""
    if not db.asset_is_demo(asset_id):
        return JSONResponse({"ready": False})
    for p in db.get_set_pieces(asset_id):
        vp = p.payload.get("video_path")
        if p.kind.value == "short" and vp and os.path.exists(vp):
            return JSONResponse({"ready": True, "url": f"/d/{asset_id}/f/{os.path.basename(vp)}"})
    return JSONResponse({"ready": False})


@app.get("/d/{asset_id}/f/{fname}")
def demo_media(asset_id: str, fname: str):
    """데모(무료 체험) 미디어 — is_demo 자산만 공개 서빙."""
    import re
    if not db.asset_is_demo(asset_id) or not re.fullmatch(r"[A-Za-z0-9._-]+", fname):
        return HTMLResponse(status_code=404)
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
def demo_zip(asset_id: str):
    """데모 전체 ZIP(글+사진+영상) — is_demo 자산만."""
    if not db.asset_is_demo(asset_id):
        return HTMLResponse(status_code=404)
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
        industry = (form.get("industry") or "").strip()
        region = (form.get("region") or "").strip()
        name = (form.get("name") or "").strip()
    except Exception:
        industry = region = name = ""
    if not (industry or name):
        return JSONResponse({"error": "업종 또는 상호를 입력해주세요."}, status_code=400)

    # ── 앞단 게이트 ① 동일 상호+지역 TTL 캐시 → 네이버 콜 자체를 절감(레이트리밋과 별개) ──
    ckey = f"{industry}|{region}|{name}".lower()
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

    result = diagnose.diagnose_rank(industry, region, name)
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


@app.get("/me/competitors", response_class=HTMLResponse)
def competitors_page(request: Request):
    """경쟁사 추적 대시보드 페이지(PHASE 4). 등록·현황·수동스캔·업그레이드 CTA."""
    from app import gating, config as _cfg
    from app.services import competitor
    u = auth.current_user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    t = _ensure_user_tenant(u)
    comps = db.list_competitors(t.id)
    rep = competitor.report(t, comps)
    usage = gating.usage_summary(db.get_user(u["id"]), "competitor_scans")
    cmax = _cfg.plan_limit(u.get("plan") or "free", "competitors_max")
    used_label = ("무제한" if usage["limit"] == -1 else f"{usage['used']}/{usage['limit']}회")
    cmax_label = ("무제한" if cmax == -1 else f"{cmax}개")

    alerts = "".join(
        f"<div class='bg-rose-50 border border-rose-200 text-rose-700 rounded-xl px-4 py-2.5 mb-2 text-sm'>⚠️ {esc(a)}</div>"
        for a in rep.get("alerts", []))
    cards = ""
    for c in rep.get("cards", []):
        rows = "".join(
            f"<div class='flex items-center justify-between border-b border-slate-100 py-1.5 text-sm'>"
            f"<span class='text-slate-500'>{esc(r['keyword'])}</span>"
            f"<span class='text-slate-700'>나 <b>{esc(r['my_label'])}</b> · 상대 <b>{esc(r['competitor_label'])}</b> "
            f"<span class='ml-1'>{esc(r['verdict'])}</span></span></div>"
            for r in c.get("rows", []))
        empty = "" if c.get("scanned") else "<div class='text-xs text-slate-400 py-2'>아직 스캔 전이에요. ‘지금 스캔’을 눌러보세요.</div>"
        cards += (f"<div class='bg-white rounded-2xl border border-slate-100 p-5 mb-3'>"
                  f"<div class='flex items-center justify-between mb-2'><div class='font-bold text-slate-800'>🥊 {esc(c['name'])}</div>"
                  f"<button onclick=\"delComp('{c['id']}')\" class='text-xs text-slate-400 hover:text-rose-500'>삭제</button></div>"
                  f"{rows}{empty}</div>")
    if not comps:
        cards = "<div class='bg-slate-50 rounded-2xl p-6 text-center text-slate-500 text-sm'>아직 등록한 경쟁사가 없어요. 옆집·경쟁 매장 상호를 등록하면 매일 자동으로 순위를 비교해 드려요.</div>"

    upgrade = ("" if usage["limit"] == -1 or usage["remaining"] > 0 else
               "<a href='/#pricing' class='block text-center bg-indigo-600 text-white font-bold py-3 rounded-xl mt-3'>업그레이드하고 더 추적하기 →</a>")

    inner = (
        f"<a href='/me' class='text-sm text-slate-500 font-bold'>← 내 작업실</a>"
        "<div class='flex items-center justify-between mt-2 mb-1'>"
        "<h1 class='text-2xl font-extrabold'>🥊 경쟁사 추적</h1>"
        f"<span class='text-xs text-slate-400'>이번 달 스캔 {used_label} · 경쟁사 {cmax_label}</span></div>"
        "<p class='text-slate-500 text-sm mb-5'>옆집보다 위에 뜨고 있는지, 매일 자동으로 체크해 드려요. (네이버 지역검색 상위 5위 기준)</p>"
        + alerts +
        "<div class='bg-white rounded-2xl border border-slate-100 p-5 mb-4'>"
        "<div class='font-bold text-slate-800 mb-2'>+ 경쟁사 등록</div>"
        "<input id='c_name' placeholder='경쟁사 상호(예: 옆집모터스)' class='w-full rounded-xl border px-3 py-2.5 mb-2 text-sm outline-none'>"
        "<input id='c_kw' placeholder='비교할 키워드(선택, 쉼표로 여러 개 · 비우면 자동)' class='w-full rounded-xl border px-3 py-2.5 mb-2 text-sm outline-none'>"
        "<button onclick='addComp()' class='w-full bg-slate-900 text-white font-bold py-2.5 rounded-xl text-sm'>등록</button>"
        "<div id='c_msg' class='text-xs mt-2'></div></div>"
        "<button onclick='scanNow()' class='w-full grad-btn text-white font-bold py-3 rounded-xl mb-4'>🔄 지금 스캔 (내 순위 vs 경쟁사)</button>"
        + cards + upgrade +
        "<script>"
        "async function addComp(){var n=document.getElementById('c_name').value,k=document.getElementById('c_kw').value;"
        "var m=document.getElementById('c_msg');if(!n){m.textContent='상호를 입력해주세요';m.className='text-xs mt-2 text-rose-500';return;}"
        "var fd=new FormData();fd.append('name',n);fd.append('keywords',k);"
        "var r=await fetch('/api/competitor',{method:'POST',body:fd});var d=await r.json();"
        "if(d.ok){location.reload();}else{m.textContent=d.error||'등록 실패';m.className='text-xs mt-2 text-rose-500';"
        "if(d.upgrade){m.innerHTML+=' <a href=\"/#pricing\" class=\"underline text-indigo-600\">업그레이드</a>';}}}"
        "async function delComp(id){if(!confirm('삭제할까요?'))return;await fetch('/api/competitor/'+id+'/delete',{method:'POST'});location.reload();}"
        "async function scanNow(){var b=event.target;b.textContent='스캔 중…';b.disabled=true;"
        "var r=await fetch('/api/competitor/scan',{method:'POST'});var d=await r.json();"
        "if(d.error){alert(d.error);b.disabled=false;b.textContent='🔄 지금 스캔';if(d.upgrade)location.href='/#pricing';return;}"
        "location.reload();}"
        "</script>")
    return HTMLResponse(_subscriber_page("", inner))


def _short_region(addr: str) -> str:
    """전체 주소 → '부산 동구' / '부산 동구 초량동'처럼 짧은 지역(키워드용)."""
    toks = (addr or "").split()
    if not toks:
        return ""
    sido = toks[0]
    for suf in ("특별자치도", "특별자치시", "광역시", "특별시", "자치도", "도", "시"):
        if sido.endswith(suf) and len(sido) > len(suf):
            sido = sido[:-len(suf)]
            break
    parts = [sido]
    if len(toks) > 1:
        parts.append(toks[1])                       # 구/군/시
    dong = next((t for t in toks[2:5] if t.endswith(("동", "읍", "면", "가", "리"))), "")
    if dong:
        parts.append(dong)
    return " ".join(parts)


def _clean_kw(k: str) -> str:
    """주소범벅 키워드를 짧게 — '부산광역시 동구 …274번길 7-7 1층 105호 썬팅업체 추천' → '부산 동구 썬팅업체 추천'."""
    import re as _re
    if not _re.search(r"[0-9]|번길|[0-9]층|[0-9]호|대로|번지", k or ""):
        return k                               # 주소 안 낀 정상 키워드는 그대로
    region = _short_region(k)                  # 부산 동구 (+동)
    rset = set(region.split())
    tail = [t for t in (k or "").split()
            if t not in rset
            and not _re.search(r"[0-9]|번길|대로|^.+로$|^.+길$|광역시|특별시|특별자치|자치도|^.+도$", t)
            and t not in ("시", "군", "구", "읍", "면")]
    out = (region + " " + " ".join(tail)).strip()
    return out or region


def _detect_market(text: str) -> str:
    """URL/몰이름 → 마켓 코드(폼 마켓칸 자동 선택)."""
    t = (text or "").lower()
    if "coupang" in t or "쿠팡" in t:
        return "coupang"
    if "smartstore" in t or "스마트스토어" in t:
        return "smartstore"
    if "11st" in t or "11번가" in t or "elevenst" in t:
        return "11st"
    if "gmarket" in t or "지마켓" in t:
        return "gmarket"
    return ""


_KW_COLORS = {"그레이", "블랙", "화이트", "네이비", "베이지", "브라운", "핑크", "레드", "블루",
              "카키", "와인", "아이보리", "차콜", "옐로우", "그린", "퍼플", "오렌지", "민트"}
_KW_JUNK = {"정품", "무료배송", "당일발송", "신상", "특가", "선택", "옵션", "공용", "남녀공용",
            "freesize", "free", "세트", "택1", "단품"}


def _seller_search_kw(name: str, brand: str = "") -> str:
    """상품명 → '검색어 유도' 핵심 키워드(브랜드·괄호·색상·옵션 제거, 상품종류 위주)."""
    import re as _r
    n = _r.sub(r"\([^)]*\)|\[[^\]]*\]", " ", name or "")   # 괄호·대괄호 제거
    if brand:
        n = n.replace(brand, " ")
    n = _r.sub(r"[^0-9A-Za-z가-힣 ]", " ", n)
    words = [w for w in n.split() if len(w) >= 2 and w.lower() not in _KW_JUNK]
    while words and (words[-1] in _KW_COLORS or words[-1].lower() in _KW_JUNK):   # 뒤 색상·잡토큰 제거
        words.pop()
    return " ".join(words[-2:]) if len(words) >= 2 else (words[-1] if words else "")


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
              "style='background:#FEE500;color:#191600'>💬 카카오로 3초 가입</a>"
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
        "<a href='/login/kakao' class='block text-center py-3.5 rounded-xl font-extrabold mb-2.5' style='background:#FEE500;color:#191600'>💬 카카오로 시작하기</a>"
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
             + _stat("📦", len(sets), "bg-indigo-50 text-indigo-600", "만든 세트")
             + _stat("📡", n_pieces, "bg-violet-50 text-violet-600", "채널 발행물")
             + _stat("⭐", avg, "bg-sky-50 text-sky-600", "평균 점수") + "</div>")
    kw_html = ""
    if kws:
        def _chip(k):
            return f"<span class='inline-block bg-slate-100 text-slate-600 text-xs px-2.5 py-1 rounded-full mr-1 mb-1'>{esc(k)}</span>"
        head = "".join(_chip(k) for k in kws[:6])
        rest = "".join(_chip(k) for k in kws[6:])
        more_n = len(kws) - 6
        more_btn = (f"<button type=button onclick=\"var m=document.getElementById('kwmore');m.classList.toggle('hidden');this.textContent=m.classList.contains('hidden')?'더보기 +{more_n}':'접기';\" "
                    f"class='inline-block text-xs font-bold text-indigo-600 ml-1 align-middle'>더보기 +{more_n}</button>" if more_n > 0 else "")
        kw_html = ("<div class='mb-2'><div class='text-sm font-bold text-slate-600 mb-2'>🎯 노리는 키워드 "
                   f"<span class='text-xs text-slate-400 font-normal'>({len(kws)}개)</span></div>"
                   f"<div class='max-h-24 overflow-hidden'>{head}<span id='kwmore' class='hidden'>{rest}</span>{more_btn}</div></div>")
    # 🚀 before/after 순위 성장 카드 — 발행 후 자동 스냅샷 기반(성장 PHASE 2)
    ba = ""
    try:
        imp = db.improving_keywords(tenant_id)
        if imp:
            rows = "".join(
                f"<div class='flex items-center justify-between bg-emerald-50 rounded-xl px-3 py-2 mb-1.5'>"
                f"<span class='text-sm font-bold text-slate-700'>{esc(x['keyword'])}</span>"
                f"<span class='text-sm font-extrabold text-emerald-600'>"
                f"{(x['first'] if x['first'] else '밖')}위 → {(x['last'] if x['last'] else '밖')}위 ⬆️</span></div>"
                for x in imp[:3])
            ba = ("<div class='mb-4'><div class='text-sm font-bold text-slate-600 mb-2'>🚀 순위 성장</div>"
                  + rows + "</div>")
    except Exception:
        pass
    return ("<div class='bg-white rounded-3xl border border-slate-100 shadow-sm hover:shadow-md transition-shadow p-5 sm:p-6 mb-5'>"
            "<h2 class='font-extrabold text-slate-900 mb-4 text-base'>📈 성과 리포트</h2>"
            + ba + stats + kw_html
            + "<div class='mt-2'><button onclick='checkRank()' class='px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-600 text-xs font-bold rounded-xl transition'>🔎 키워드 순위 조회</button>"
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
    """가게 대표 목적지(플레이스/스토어)로 가는 추적 링크. 클릭 집계용. 목적지 없으면 상호 지도검색으로 폴백."""
    biz = getattr(t, "biz_type", "local") or "local"
    if biz == "seller":
        target, label = (getattr(t, "buy_url", "") or getattr(t, "map_url", "")), "스토어"
    else:
        target, label = (getattr(t, "map_url", "") or getattr(t, "buy_url", "")), "네이버 플레이스"
    if not target and getattr(t, "name", ""):        # 폴백: 상호로 네이버 지도 검색
        from urllib.parse import quote as _q
        target, label = "https://map.naver.com/p/search/" + _q(t.name), "네이버 지도"
    return db.ensure_track_link(t.id, target, label)


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
    """능동 코칭 — '오늘의 액션 1개'. DB만 사용(빠름). 상태 기반 우선순위."""
    import datetime
    sets = db.list_sets(tenant_id=t.id, limit=50)
    links = db.list_links(t.id)
    clicks = sum(int(l.get("clicks") or 0) for l in links)
    improving = []
    try:
        improving = db.improving_keywords(t.id)
    except Exception:
        pass
    if not sets:
        return {"emoji": "🚀", "text": "첫 콘텐츠를 만들어보세요! 사진 한 장이면 5채널이 완성돼요.",
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
        return {"emoji": "📅", "text": f"{days}일째 새 콘텐츠가 없어요. 꾸준함이 상위노출의 1순위예요 — 오늘 하나 올려요!",
                "cta": "새 콘텐츠 만들기", "href": "/me"}
    if improving:
        k = improving[0]["keyword"]
        return {"emoji": "📈", "text": f"‘{esc(k)}’ 순위가 오르고 있어요! 이 기세로 하나 더 올리면 상위 굳히기 각이에요.",
                "cta": "이어서 만들기", "href": "/me"}
    if clicks > 0:
        return {"emoji": "🎯", "text": f"추적 링크 클릭 {clicks}회 — 콘텐츠가 실제 손님을 부르고 있어요. 계속 올려요!",
                "cta": "성과 보기", "href": "/me?tab=report"}
    return {"emoji": "✨", "text": "오늘 콘텐츠 하나로 노출을 늘려보세요. 매주 2~3개가 상위노출의 정석이에요.",
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
                  "<div><div class='font-bold text-sm'>✨ AI 전문가팀이 콘텐츠를 만들고 있어요</div>"
                  "<div class='text-xs text-indigo-500'>20~60초 걸려요 · 완료되면 자동으로 나타나요 (이 화면 유지)</div></div></div>"
                  f"<script>(function(){{var base={_base_n},n=0;var iv=setInterval(async function(){{n++;if(n>40){{clearInterval(iv);location.reload();return;}}"
                  "try{var r=await fetch('/me/sets/count');var d=await r.json();if(d.n>base){clearInterval(iv);location.href='/me?ok='+encodeURIComponent('✨ 콘텐츠가 완성됐어요! 아래에서 확인하세요');}}catch(e){}"
                  "}},3000);})();</script>")
    # ① 가게/스토어 설정
    bopts = "".join(f"<option value='{k}'{' selected' if (t.biz_type or 'local') == k else ''}>{lab}</option>"
                    for k, lab in [("local", "🏪 동네 매장(방문 유도)"), ("seller", "📦 온라인 셀러(구매 유도)"),
                                   ("hybrid", "🔁 매장+온라인")])
    mkopts = "".join(f"<option value='{k}'{' selected' if (t.marketplace or '') == k else ''}>{v}</option>"
                     for k, v in [("", "마켓 선택(셀러)"), ("coupang", "쿠팡"), ("11st", "11번가"),
                                  ("smartstore", "스마트스토어"), ("gmarket", "지마켓"), ("self", "자사몰")])
    store_form = (
        f"<form method=post action='/me/store' class='grid sm:grid-cols-2 gap-2'>"
        f"<input id=sf_name name=name value=\"{esc(t.name)}\" placeholder='상호/브랜드 *' required class='{inp}'>"
        f"<input id=sf_industry name=industry value=\"{esc(t.industry)}\" placeholder='업종/상품 * (예: 카페, 캠핑 폴딩박스)' required class='{inp}'>"
        f"<input id=sf_region name=region value=\"{esc(t.region)}\" placeholder='지역 (매장)' class='{inp}'>"
        f"<select name=biz_type class='{inp} font-semibold'>{bopts}</select>"
        f"<input id=sf_phone name=phone value=\"{esc(t.phone)}\" placeholder='📞 전화 (매장)' class='{inp}'>"
        f"<input id=sf_address name=address value=\"{esc(t.address)}\" placeholder='📍 주소 (매장)' class='{inp}'>"
        f"<select name=marketplace class='{inp}'>{mkopts}</select>"
        f"<input name=brand_name value=\"{esc(t.brand_name)}\" placeholder='🏷 브랜드명 (셀러)' class='{inp}'>"
        f"<input name=search_kw value=\"{esc(t.search_kw)}\" placeholder='🔎 검색어 유도 (쿠팡 등)' class='{inp}'>"
        f"<input name=buy_url value=\"{esc(t.buy_url)}\" placeholder='🔗 상세페이지/스토어/제휴 링크' class='{inp}'>"
        f"<input name=map_url value=\"{esc(t.map_url)}\" placeholder='📍 네이버 플레이스 URL (매장)' class='{inp}'>"
        "<button class='bg-indigo-600 text-white font-bold py-2.5 rounded-xl sm:col-span-2'>저장</button></form>"
        "<p class='text-xs text-slate-400 mt-1 sm:col-span-2'>💡 링크를 넣으면 글 끝에 <b>클릭 링크</b>로 자동 삽입돼요 (블로그·유튜브·X는 바로 클릭, 인스타는 프로필 안내).</p>"
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
                  + _bopt("local", "🏪", "동네 매장", "방문·예약 유도 · 지도/연락처")
                  + _bopt("seller", "📦", "온라인 셀러", "구매링크·상품 키워드")
                  + "</div></div>")
    search_box = (
        "<div class='bg-indigo-50 rounded-xl p-3 mb-3'>"
        "<div class='text-xs font-bold text-indigo-700 mb-1'>🔍 가게 이름으로 검색하면 자동 입력돼요 (타이핑 최소)</div>"
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
    _chan_icon = {"instagram": "📷", "naver_blog": "📝", "x": "𝕏", "youtube": "▶️", "facebook": "👍", "marketplace": "🛒"}
    if sets:
        _cards = []
        for s in sets:
            ps = db.get_set_pieces(s["asset_id"])
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
                + f"<div class='flex-1 min-w-0'><div class='flex items-center gap-1 text-base leading-none mb-1.5'>{badges}</div>"
                + f"<div class='text-xs text-slate-400 font-medium'>{esc(s['created'])} · {s['n']}채널</div></div>"
                + f"<a href='/me?view={s['asset_id']}' class='px-3.5 py-2 bg-indigo-600 hover:bg-indigo-700 active:scale-[.98] text-white text-xs font-bold rounded-xl transition'>보기</a>"
                + f"<form method=post action='/me/set/{s['asset_id']}/delete' onsubmit=\"return confirm('이 콘텐츠를 삭제할까요?')\">"
                + "<button class='px-1.5 py-2 text-slate-300 hover:text-rose-500 text-base transition' title='삭제'>🗑</button></form></div>")
        hist = "<div class='grid sm:grid-cols-2 gap-3'>" + "".join(_cards) + "</div>"
    else:
        hist = "<p class='text-slate-400 text-sm py-6 text-center'>아직 만든 콘텐츠가 없어요. 위에서 사진 올려 만들어보세요.</p>"
    # ── 최초 1회 온보딩 vs 작동 대시보드 ──
    onboarded = bool((t.industry or "").strip())
    if not onboarded:
        _multi = len(db.list_user_stores(u["id"])) > 1
        # 실수로 '가게 추가'를 눌렀을 때 되돌리기 — 다른 가게가 있을 때만
        back_btn = (("<form method=post action='/me/store/cancel' class='mb-3'>"
                     "<button class='inline-flex items-center gap-1 text-sm font-bold text-slate-500 hover:text-slate-900 bg-white border border-slate-200 rounded-xl px-4 py-2 hover:bg-slate-50 transition'>"
                     "← 뒤로가기 <span class='text-slate-400 font-normal'>(실수로 추가했다면)</span></button></form>") if _multi else "")
        intro = ((f"<div class='bg-indigo-50 text-indigo-700 p-4 rounded-2xl mb-4 text-sm'>"
                  + ("🆕 <b>새 가게</b>를 추가했어요. <b>딱 3가지</b>만 알려주세요. (30초)</div>" if _multi
                     else "🎉 가입 완료! 시작하려면 <b>딱 3가지</b>만 알려주세요. (30초)</div>")))
        card = ("<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-5'>"
                "<h2 class='font-bold mb-3'>내 가게/상품 정보</h2>" + store_form_min + "</div>")
        return _subscriber_page(f"{esc(t.name)} · 시작 설정", banner + back_btn + intro + card)
    # 온보딩 완료 → 사진 올려 생성이 메인
    from app.services import pay as _pay
    _plan = u.get("plan") or "free"
    _pn = {"free": "무료", "basic": "베이직", "pro": "프로", "self": "프로", "agency": "대행"}.get(_plan, _plan)
    if _is_owner(u):
        _pn, _usage, _upbtn = "👑 사장님", "무제한 · 영구 라이선스", ""
    elif _plan == "free":
        _usage = f"무료 {u.get('free_used') or 0}/{FREE_LIMIT}회"
        _upbtn = ("<a href='/billing?plan=pro' class='ml-auto bg-white text-indigo-700 text-sm font-bold "
                  "px-4 py-2 rounded-xl'>업그레이드</a>")
    else:
        _cap = _pay.PLANS.get(_plan, {}).get("monthly", 0)
        _usage = f"이번달 {db.month_usage(u['id'])}" + (f"/{_cap}건" if _cap else "건(무제한)")
        _upbtn = ""
    plan_card = ("<div class='rounded-2xl p-4 mb-4 flex items-center gap-3 text-white' "
                 "style='background:linear-gradient(120deg,#334155,#4338ca)'>"
                 f"<div><div class='text-xs text-white/70'>내 플랜</div>"
                 f"<div class='font-bold'>{_pn} · {_usage}</div></div>{_upbtn}</div>")
    _sname = t.name if (t.name and t.name not in ("카카오회원", "구글회원", "회원", "내 가게")) else ""
    greeting = ("<div class='mb-6'>"
                + (f"<div class='inline-flex items-center gap-1.5 bg-indigo-50 text-indigo-700 text-sm font-bold px-3 py-1.5 rounded-full mb-3'>🏪 {esc(_sname)}</div>" if _sname else "")
                + "<div class='text-2xl sm:text-3xl font-extrabold text-slate-900 leading-tight'>사진만 올리면 "
                "<span style='background:linear-gradient(120deg,#6366f1,#ec4899);-webkit-background-clip:text;background-clip:text;color:transparent'>5채널 콘텐츠</span>가 완성돼요</div></div>")
    upload_section = ("<div class='bg-white rounded-3xl border border-slate-100 shadow-sm p-6 sm:p-7'>"
                      "<div class='mb-5'><div class='text-lg font-extrabold text-slate-900'>✨ 콘텐츠 만들기</div>"
                      "<div class='text-sm text-slate-400'>가게 이름·사진만 있으면 끝</div></div>"
                      + _upload_form_html(t, tok) + "</div>")
    content = ("<div id='myContent' class='bg-white rounded-3xl border border-slate-100 shadow-sm p-5'>"
               "<h2 class='font-bold text-slate-900 mb-1'>📋 내 콘텐츠</h2>"
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
                  + _statc("📦", "bg-emerald-100 text-emerald-600", len(_sets2), "만든 세트")
                  + _statc("🔍", "bg-amber-100 text-amber-600", _np, "채널 발행물")
                  + _statc("⭐", "bg-violet-100 text-violet-600", _avg, "평균 노출점수") + "</div>") if _sets2 else "")
    kw_card = ""
    if _kws2:
        _chips = "".join(f"<span class='inline-block bg-slate-100 text-slate-600 text-xs px-3 py-1.5 rounded-full mr-1.5 mb-1.5'>{esc(_clean_kw(k))}</span>" for k in _kws2[:9])
        kw_card = ("<div id='perfCard' class='bg-white rounded-3xl border border-slate-100 shadow-sm p-5'>"
                   "<h2 class='font-bold text-slate-900 mb-1'>📊 성과 리포트 · 최근 키워드</h2>"
                   f"<p class='text-xs text-slate-400 mb-3'>노리는 키워드 {len(_kws2)}개</p>{_chips}</div>")
    view = (request.query_params.get("view") or "").strip()
    tab = (request.query_params.get("tab") or "").strip()
    result_html = _result_html(u, view, back_href="/me?tab=content", back_label="◀ 내 콘텐츠") if view else None
    _sbadge = (f"<div class='inline-flex items-center gap-1.5 bg-indigo-50 text-indigo-700 text-sm font-bold px-3 py-1.5 rounded-full mb-4'>🏪 {esc(_sname)}</div>" if _sname else "")
    _fw = "bg-white rounded-3xl border border-slate-100 shadow-sm p-6 sm:p-8"
    # 사이드바 클릭 = 전체 폭 단일 패널 전환 (내 콘텐츠 / 리포트 / 결과 / 만들기)
    if result_html:                                        # 콘텐츠 결과 (전체 폭)
        active = "content"
        main_inner = _sbadge + f"<div class='{_fw}'>{result_html}</div>"
    elif tab == "content":                                # 📋 내 콘텐츠 (전체 폭)
        active = "content"
        main_inner = (_sbadge + f"<div class='{_fw}'>"
                      "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>📋 내 콘텐츠</h2>"
                      "<p class='text-sm text-slate-400 mb-5'>‘보기’를 누르면 결과가 크게 나와요.</p>" + hist + "</div>")
    elif tab == "report":                                 # 📊 성과 리포트 · 최근 키워드 + 순위(자동) (전체 폭)
        active = "report"
        _kwbox = ((f"<div class='{_fw}'><h2 class='text-2xl font-extrabold text-slate-900 mb-1'>📊 성과 리포트 · 최근 키워드</h2>"
                   f"<p class='text-sm text-slate-400 mb-5'>노리는 키워드 {len(_kws2)}개</p>{_chips}</div>") if _kws2 else "")
        # 🔎 키워드 순위 — 페이지 열면 자동 조회(네이버 지역검색)
        _rankbox = (f"<div class='{_fw} mt-5'>"
                    "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>🔎 키워드 순위</h2>"
                    "<p class='text-sm text-slate-400 mb-4'>네이버 지역검색 기준 · 참고용(위치·기기별 차이)</p>"
                    "<div id='rankbox' class='text-sm'><div class='flex items-center gap-2 text-slate-400'>"
                    "<div class='w-4 h-4 border-2 border-slate-200 border-t-indigo-500 rounded-full animate-spin'></div>조회 중…</div></div>"
                    "<script>(async function(){var b=document.getElementById('rankbox');if(!b)return;"
                    "try{var d=await (await fetch('/me/rank')).json();"
                    "if(!d.configured){b.innerHTML='<span class=\"text-slate-400\">네이버 키가 설정되면 순위가 표시됩니다.</span>';return;}"
                    "if(!d.items||!d.items.length){b.innerHTML='<span class=\"text-slate-400\">아직 타겟 키워드가 없어요. 콘텐츠를 만들면 채워져요.</span>';return;}"
                    "function st(it){var r=it.rank;return (r===null)?'<span class=\"text-slate-400\">조회불가</span>':(r>=1?('<span class=\"text-emerald-600 font-bold\">네이버 지역 '+r+'위</span>'):'<span class=\"text-slate-400\">5위 밖</span>');}"
                    "function chg(it){var c=it.rank,p=it.prev;if(c===null)return '';if(p===null||p===undefined)return '<span class=\"text-indigo-500 text-xs font-bold ml-2\">🆕 첫 측정</span>';var cc=(c===0?6:c),pp=(p===0?6:p);if(cc<pp)return '<span class=\"text-emerald-600 text-xs font-bold ml-2\">⬆️ '+(pp-cc)+'계단</span>';if(cc>pp)return '<span class=\"text-rose-500 text-xs font-bold ml-2\">⬇️ '+(cc-pp)+'계단</span>';return '<span class=\"text-slate-400 text-xs ml-2\">— 유지</span>';}"
                    "function riv(it){if(it.rank===1)return '<div class=\"text-xs text-emerald-600 mt-1 font-semibold\">👑 이 키워드 1위!</div>';if(it.rank>1&&it.rival)return '<div class=\"text-xs text-amber-600 mt-1\">🎯 <b>'+it.rival+'</b>만 넘으면 '+(it.rank-1)+'위</div>';if((it.rank===0)&&it.leader)return '<div class=\"text-xs text-slate-400 mt-1\">현재 1위: '+it.leader+' — 콘텐츠 꾸준히 올리면 진입해요</div>';return '';}"
                    "b.innerHTML=d.items.map(function(it){return '<div class=\"border-b border-slate-100 py-2.5\"><div class=\"flex items-center justify-between\"><span class=\"text-slate-700 font-medium\">'+it.kw+'</span><span class=\"whitespace-nowrap\">'+st(it)+chg(it)+'</span></div>'+riv(it)+'</div>';}).join('');"
                    "}catch(e){b.innerHTML='<span class=\"text-rose-400\">조회 실패</span>';}})();</script></div>")
        # 🎯 성과 실측 — 추적 링크/QR로 '이 콘텐츠 보고 온 손님' 집계
        _tl = _ensure_track_link(t)
        _clicks = sum(int(l.get("clicks") or 0) for l in db.list_links(t.id))
        _trackbox = ""
        if _tl:
            _base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
            _short = f"{_base}/r/{_tl['code']}"
            _trackbox = (
                f"<div class='{_fw} mt-5'>"
                "<h2 class='text-2xl font-extrabold text-slate-900 mb-1'>🎯 성과 실측 · 내 손님 추적</h2>"
                "<p class='text-sm text-slate-400 mb-4'>이 링크·QR을 <b>인스타 프로필·명함·매장</b>에 넣으면, 여기로 온 손님 수가 집계돼요.</p>"
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
        main_inner = _sbadge + stats_row + _trackbox + _rankbox + _kwbox
    else:                                                 # ✨ 만들기 (기본) — 완성되면 여기(만들기 대시보드)에 결과 표시
        active = "create"
        _made = (request.query_params.get("made") or "").strip()
        _made_html = ""
        if _made:                                         # 방금 생성 완료 → 만들기 화면에 결과 인라인 표시(내콘텐츠엔 이미 저장됨)
            _rh = _result_html(u, _made, back_href="/me", back_label="＋ 새로 만들기 ↓")
            if _rh:
                _made_html = f"<div class='{_fw} mb-6'>{_rh}</div>"
        if _made_html:
            main_inner = _made_html + upload_section
        else:
            _act = _daily_action(t)
            _coach = ("<div class='flex items-center gap-3 bg-gradient-to-r from-indigo-50 to-violet-50 border border-indigo-100 rounded-2xl p-4 mb-5'>"
                      f"<div class='text-2xl'>{_act['emoji']}</div>"
                      "<div class='flex-1 min-w-0'><div class='text-xs font-bold text-indigo-500 mb-0.5'>오늘의 액션</div>"
                      f"<div class='text-sm text-slate-700 font-medium'>{_act['text']}</div></div>"
                      f"<a href='{_act['href']}' class='flex-shrink-0 bg-indigo-600 text-white text-sm font-bold px-4 py-2 rounded-xl hover:bg-indigo-700 transition'>{_act['cta']}</a></div>")
            main_inner = greeting + _coach + upload_section
    # 🆕 새로 추가한 '빈 새 가게'면 실수 대비 '뒤로가기(취소)' 배너
    if t.name == "새 가게" and len(db.list_user_stores(u["id"])) > 1 and not db.list_sets(tenant_id=t.id):
        _backban = ("<div class='flex items-center gap-3 bg-amber-50 border border-amber-200 rounded-2xl p-4 mb-5'>"
                    "<span class='text-xl'>🆕</span>"
                    "<div class='flex-1 text-sm text-amber-800'><b>새 가게</b>를 추가했어요. 가게 이름을 넣고 자동 인식하세요. 잘못 누르셨나요?</div>"
                    "<form method=post action='/me/store/cancel'><button class='bg-white border border-amber-300 text-amber-700 text-sm font-bold px-4 py-2 rounded-xl hover:bg-amber-100 transition whitespace-nowrap'>← 뒤로가기</button></form></div>")
        main_inner = _backban + main_inner
    from app import landing
    _navitems = [("🏠", "홈", "/me", "create"), ("📄", "내 콘텐츠", "/me?tab=content", "content"),
                 ("📊", "리포트", "/me?tab=report", "report"),
                 ("🥊", "경쟁사", "/me/competitors", "competitors")]

    def _navlink(i, l, h, key):
        cls = ("bg-indigo-50 text-indigo-700" if key == active
               else "text-slate-500 hover:bg-slate-50 hover:text-slate-900")
        return (f"<a href='{h}' class='flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold {cls} transition'>"
                f"<span class='text-base'>{i}</span>{l}</a>")

    # 🏪 다중 가게 전환기 + 가게 추가
    _stores = db.list_user_stores(u["id"])

    def _storeitem(st):
        on = (st.id == t.id)
        nm = esc(st.name) if getattr(st, "name", "") and st.name not in ("내 가게", "카카오회원", "구글회원") else "내 가게"
        cls = "bg-indigo-600 text-white" if on else "bg-slate-50 text-slate-600 hover:bg-slate-100"
        chk = "<span class='ml-auto text-xs'>✓</span>" if on else ""
        return (f"<form method=post action='/me/store/switch'><input type=hidden name=tenant_id value='{st.id}'>"
                f"<button class='w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-semibold {cls} transition text-left'>"
                f"<span>🏪</span><span class='truncate'>{nm}</span>{chk}</button></form>")
    _storebox = ("<div class='mb-5'><div class='text-[11px] font-bold text-slate-400 px-2 mb-1.5'>내 가게</div>"
                 "<div class='space-y-1'>" + "".join(_storeitem(s) for s in _stores) + "</div>"
                 "<form method=post action='/me/store/add'>"
                 "<button class='w-full mt-1.5 flex items-center justify-center gap-1 px-3 py-2 rounded-xl text-sm font-bold text-indigo-600 border border-dashed border-indigo-200 hover:bg-indigo-50 transition'>＋ 가게 추가</button></form></div>")
    sidebar = ("<aside class='hidden lg:flex flex-col w-56 flex-shrink-0 border-r border-slate-100 bg-white p-4 sticky top-0 h-screen'>"
               f"<a href='/' class='flex items-center gap-2 font-extrabold text-lg mb-6 px-2'>{landing.LOGO}<span>올린다</span></a>"
               + _storebox
               + "<nav class='space-y-1'>" + "".join(_navlink(*n) for n in _navitems)
               + f"</nav><div class='mt-auto px-3 pt-4 border-t border-slate-100'><div class='text-xs text-slate-400 mb-1'>{_pn}</div>"
               "<a href='/logout' class='text-sm font-semibold text-slate-400 hover:text-slate-700'>↩ 로그아웃</a></div></aside>")
    _mobnav = ("<div class='flex lg:hidden items-center gap-2 mb-4 overflow-x-auto'>"
               + "".join(_navlink(*n) for n in _navitems)
               + "<a href='/logout' class='ml-auto text-sm text-slate-400 whitespace-nowrap'>로그아웃</a></div>")
    page = (landing._HEAD
            + "<div class='flex min-h-screen bg-slate-100'>" + sidebar
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
             lat: str = Form(""), lon: str = Form("")):
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
    return RedirectResponse("/me?ok=설정을 저장했어요", status_code=303)


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
    items = []
    for k in kws[:5]:
        det = place.rank_detail(k, t.name)
        cur = det["rank"]
        prev = db.get_prev_rank(t.id, k)            # 오늘 이전 순위(변화 계산)
        db.save_rank_snapshot(t.id, k, cur)         # 오늘 순위 기록
        items.append({"kw": k, "rank": cur, "prev": prev,
                      "rival": det["rival"], "leader": det["leader"]})
    return JSONResponse({"items": items, "configured": place.configured()})


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
    return JSONResponse({"n": len(pieces) if pieces else 0})


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
def link_redirect(code: str, request: Request, utm_source: str = ""):
    """제휴/추적 단축링크 — 클릭 집계(행 단위+채널) 후 원본으로 이동(PHASE 6)."""
    link = db.get_link(code)
    if not link or not link.get("target"):
        return RedirectResponse("/", status_code=302)
    db.incr_link_click(code, referrer=request.headers.get("referer", ""),
                       ua=request.headers.get("user-agent", ""), utm_source=utm_source)
    target = link["target"]
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    return RedirectResponse(target, status_code=302)


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
                f"<button type=button onclick=\"cp('{cid}',this)\" class='mt-1 px-3 py-1.5 bg-indigo-600 text-white text-xs font-bold rounded-lg'>📋 복사</button>")

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
        return ("<div class='w-9 h-9 rounded-full flex items-center justify-center text-white text-sm font-bold flex-shrink-0' "
                f"style='background:linear-gradient(120deg,#6366f1,#ec4899)'>{esc(sname[:1])}</div>")

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
        return f"<div class='text-xs font-bold text-slate-400 mb-2 flex items-center flex-wrap'>{label}{badge}</div>"
    naver_btn = (f"<a href='/kit/{asset_id}/naver' target='_blank' class='block text-center py-3 rounded-xl text-white text-sm font-extrabold "
                 "shadow-md hover:brightness-110 active:scale-[.99] transition' style='background:#03c75a'>🟢 네이버 블로그에 올리기 →</a>")
    cards = ""
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
            block = (_hd("📝 네이버 블로그", pl) + f"<div class='{wrap} p-5'>"
                     f"<div id='bt{sid}' class='text-lg font-extrabold text-slate-900 leading-snug mb-1.5'>{esc(title)}</div>"
                     + opts_html
                     + "<div class='flex items-center gap-2 text-xs text-slate-400 border-b border-slate-100 pb-2 mb-3'>" + _av()
                     + f"<span>{esc(sname)} 블로그 · 방금 전</span></div>"
                     + f"<div class='max-h-72 overflow-y-auto'>{_blog_body(pl.get('body',''))}</div>"
                     + f"<textarea id='cb{sid}' data-body=\"{esc(body_part)}\" class='hidden'>{esc(blog_copy)}</textarea>"
                     + f"<div class='mt-4 space-y-2'>{naver_btn}"
                     + f"<div class='flex gap-2'>{pack_btn(p.id, False)}<button type=button onclick=\"cp('cb{sid}',this)\" class='px-3.5 py-2.5 border border-slate-200 text-slate-600 hover:bg-slate-50 text-xs font-bold rounded-xl transition'>📋 글 복사</button></div></div></div>")
        elif k == "x_post":
            xt = pl.get("text", "")
            xvid = (f"<div class='relative mt-2'><video src='{vurl}' controls autoplay muted loop playsinline preload='metadata' poster='{first_img}' class='w-full rounded-xl bg-black' style='max-height:360px'></video>"
                    "<button type=button onclick='omUnmute(this)' class='om-unmute absolute top-3 left-1/2 -translate-x-1/2 z-10 bg-black/80 text-white text-xs font-extrabold px-3.5 py-2 rounded-full shadow-lg'>🔇 탭하여 소리 켜기</button></div>" if vurl else "")
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
            lab = "▶️ 유튜브 쇼츠" if p.channel.value == "youtube" else "🎬 인스타 릴스"
            dur = int(pl.get("duration_sec") or 0)
            durb = (f"<div class='absolute top-2 right-2 bg-black/70 text-white text-[11px] font-bold px-1.5 py-0.5 rounded'>{dur // 60}:{dur % 60:02d}</div>" if dur else "")
            durb += ("<div class='absolute top-2 left-2 bg-black/70 text-white text-[11px] font-bold px-1.5 py-0.5 rounded'>"
                     + ("▶️ 쇼츠" if p.channel.value == "youtube" else "🎬 릴스") + "</div>")
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
            block = (_hd(f"🛒 {esc(mk)} 판매 콘텐츠", pl) + f"<div class='{wrap} p-4'>"
                     "<div class='text-xs font-bold text-slate-400 mb-1.5'>상품명 (검색 최적화 · 3안)</div>" + names_html
                     + "<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>상세페이지</div>"
                     + f"<div class='text-xs text-slate-600 whitespace-pre-wrap max-h-40 overflow-y-auto border border-slate-100 rounded-lg p-2'>{esc(detail)}</div>"
                     + (f"<div class='text-xs font-bold text-slate-400 mt-3 mb-1'>검색 태그</div><div>{tags_html}</div>" if tags_html else "")
                     + f"<div class='mt-3 flex gap-2'>{_cp('c_md' + p.id[:5], detail, '상세 복사')}{pack_btn(p.id, False)}</div></div>")
        if block:
            grp = ("video" if k == "short" else "sell" if k == "marketplace" else "text")
            cards += f"<div class='break-inside-avoid mb-6 om-card' data-ch='{grp}'>" + block + "</div>"
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
                "<div class='text-sm font-bold text-indigo-700 mb-1'>🤖 AI 전문가 팀이 제작했어요</div>"
                "<div class='text-xs text-indigo-500'>🎯 마케팅 전략가 → ✍️ 카피라이터 → 🔍 SEO 편집장 → 🎬 영상 감독</div>"
                + (f"<div class='text-xs text-slate-500 mt-2'>핵심 전략 키워드: <b>{esc(brief.get('core_keyword',''))}</b> · 앵글: {esc(brief.get('angle',''))}</div>" if brief else "")
                + "</div>")
    all_btn = (f"<a href='/kit/{asset_id}/pack-all' class='block text-center text-white font-extrabold py-4 rounded-2xl mb-5 "
               "shadow-xl shadow-indigo-500/30 hover:shadow-indigo-500/50 transition' "
               "style='background:linear-gradient(120deg,#6366f1,#8b5cf6,#ec4899)'>⬇ 5채널 전체 한 번에 받기 "
               "<span class='opacity-80 font-medium text-sm'>· 글+사진+영상 (채널별 폴더)</span></a>")
    thumbs = "".join(f"<img src='/dl/{asset_id}/{os.path.basename(im)}' class='h-24 w-24 object-cover rounded-lg border border-slate-100'>"
                     for im in imgs if im)
    photos_strip = (("<div class='bg-white rounded-2xl border border-slate-100 shadow-sm p-4 mb-4'>"
                     "<div class='font-bold text-sm mb-2'>📷 내가 올린 사진</div>"
                     f"<div class='flex gap-2 flex-wrap'>{thumbs}</div></div>") if thumbs else "")
    store_hd = (f"<div class='text-sm text-indigo-500 font-bold'>🏪 {esc(sname)}</div>"
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
    if _recent and not any(p.kind.value == "short" for p in pieces):
        _vid_poll = ("<div class='bg-amber-50 border border-amber-100 text-amber-700 rounded-2xl p-3.5 mb-5 text-sm flex items-center gap-2'>"
                     "<div class='w-4 h-4 border-2 border-amber-300 border-t-amber-600 rounded-full animate-spin flex-shrink-0'></div>"
                     "🎬 유튜브 쇼츠·인스타 릴스 <b>영상 생성 중…</b> 완성되면 자동으로 나타나요 (이 화면 유지)</div>"
                     f"<script>(function(){{var base={len(pieces)},n=0,aid='{asset_id}';"
                     "var iv=setInterval(async function(){n++;if(n>50){clearInterval(iv);return;}"
                     "try{var d=await (await fetch('/me/asset/'+aid+'/pieces')).json();if(d.n>base){clearInterval(iv);location.reload();}}catch(_){}"
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
                "<div class='flex-1 min-w-0'><div class='text-xs font-bold text-slate-700'>🎯 성과 추적 링크·QR</div>"
                "<div class='text-[11px] text-slate-400 mb-1'>콘텐츠·프로필에 넣으면 여기로 온 손님이 리포트에 집계돼요</div>"
                f"<input readonly value='{_short}' id='rtrk' class='w-full text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1 text-slate-600'></div>"
                "<button type=button onclick=\"omCopy(document.getElementById('rtrk').value);this.textContent='✅'\" class='flex-shrink-0 bg-indigo-600 text-white text-xs font-bold px-3 py-2 rounded-lg'>복사</button></div>")
    except Exception:
        track_box = ""
    # 채널 필터(탭) — 카드가 많을 때 글/영상/판매로 걸러보기
    _fbtns = [("all", "전체"), ("text", "📝 글")]
    if any(p.kind.value == "short" for p in pieces):
        _fbtns.append(("video", "🎬 영상"))
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


@app.get("/kit/{asset_id}/naver", response_class=HTMLResponse)
def kit_naver(request: Request, asset_id: str):
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
    imgs = next((p.payload.get("image_paths") for p in pieces if p.payload.get("image_paths")), []) or []
    tenant = db.get_tenant(pieces[0].tenant_id)
    sname = tenant.name if tenant else "내 가게"
    title = blog.payload.get("title", "")
    body_marked = _re.sub(r"\[사진(\d+)\]", r"\n\n[📷 사진\1 위치]\n\n", blog.payload.get("body", "")).strip()
    photos = [im for im in imgs if im]                          # /dl이 R2로 서빙
    vid = next((p for p in pieces if p.kind.value == "short" and p.payload.get("video_path")), None)
    vurl = f"/dl/{asset_id}/{os.path.basename(vid.payload['video_path'])}" if vid else ""  # 블로그 본문 삽입용
    photo_cells = "".join(
        f"<div class='relative'><img src='/dl/{asset_id}/{os.path.basename(im)}' class='w-full aspect-square object-cover rounded-xl border border-slate-200'>"
        f"<div class='absolute top-2 left-2 w-7 h-7 rounded-full bg-black/75 text-white text-sm font-bold flex items-center justify-center'>{i+1}</div>"
        f"<a href='/dl/{asset_id}/{os.path.basename(im)}' download class='absolute bottom-2 right-2 bg-white/95 text-slate-700 text-xs font-bold px-2 py-1 rounded-lg shadow hover:bg-white'>⬇ 저장</a></div>"
        for i, im in enumerate(photos))
    sec = "bg-white rounded-2xl border border-slate-200 shadow-sm p-5 mb-5"
    cbtn = "px-4 py-2.5 rounded-xl text-white text-sm font-bold transition active:scale-[.98]"
    body = (
        "<a href='javascript:history.back()' class='inline-block text-sm text-slate-500 font-bold mb-2'>← 결과로</a>"
        f"<div class='text-sm text-emerald-600 font-bold'>🏪 {esc(sname)}</div>"
        "<h1 class='text-2xl font-extrabold text-slate-900 mb-1'>네이버 블로그에 올리기</h1>"
        "<p class='text-slate-400 text-sm mb-5'>① 제목·본문 복사해서 붙여넣기 → ② 사진을 순서대로 저장 → ③ 본문 <b>[📷 사진N 위치]</b>에 네이버 사진버튼으로 올리기</p>"
        # 제목
        f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>1. 제목</div>"
        f"<div class='text-lg font-extrabold text-slate-900 mb-3'>{esc(title)}</div>"
        f"<textarea id='nvT' class='hidden'>{esc(title)}</textarea>"
        f"<button onclick=\"nvcp('nvT',this)\" class='{cbtn} bg-slate-900 hover:bg-slate-800'>📋 제목 복사</button></div>"
        # 본문
        f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>2. 본문 <span class='text-emerald-600'>(사진 위치 표시 포함)</span></div>"
        f"<div class='bg-slate-50 rounded-xl p-4 text-sm text-slate-700 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto mb-3'>{esc(body_marked)}</div>"
        f"<textarea id='nvB' class='hidden'>{esc(body_marked)}</textarea>"
        f"<button onclick=\"nvcp('nvB',this)\" class='{cbtn} bg-emerald-500 hover:bg-emerald-600 w-full'>📋 전체 본문 복사</button></div>"
        # 사진
        + (f"<div class='{sec}'><div class='flex items-center justify-between mb-3'>"
           "<div class='text-xs font-bold text-slate-400'>3. 사진 <span class='text-slate-500'>(순서대로)</span></div>"
           f"<a href='/kit/{asset_id}/pack/{blog.id}' class='text-xs font-bold text-indigo-600'>⬇ 전체 ZIP 받기</a></div>"
           f"<div class='grid grid-cols-3 sm:grid-cols-4 gap-3'>{photo_cells}</div></div>" if photos else "")
        # 4. 동영상 본문 삽입 (D.I.A.+ 가점) — #1
        + (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>4. 동영상도 본문에 넣기 <span class='text-emerald-600'>(상위노출 유리)</span></div>"
           "<p class='text-xs text-slate-500 mb-3'>네이버는 <b>15초+ 동영상이 들어간 글에 가점(D.I.A.+)</b>을 줍니다. 아래 영상을 받아 본문 중간(예: 첫 소제목 아래)에 넣어보세요.</p>"
           f"<a href='{vurl}' download class='{cbtn} bg-indigo-600 hover:bg-indigo-700 inline-block'>⬇ 동영상 받기</a></div>" if vurl else "")
        # 5. 발행 후 마무리 — 사진 6장 권장(#3) + 서치어드바이저 색인(#3)
        + (f"<div class='{sec}'><div class='text-xs font-bold text-slate-400 mb-2'>{'5' if vurl else '4'}. 발행 후 — 상위노출 마무리</div>"
           "<ul class='text-xs text-slate-600 space-y-1.5 mb-3 list-none'>"
           + (f"<li>📷 사진은 <b>6장 이상</b>이면 더 유리해요 (지금 {len(photos)}장). 다음엔 더 올려보세요.</li>"
              if len(photos) < 6 else "<li>📷 사진 6장+ ✓ 좋아요.</li>")
           + "<li>🎬 직접 찍은 동영상까지 넣으면 D.I.A.+ 가점.</li>"
           + "<li>⚡ 발행 직후 <b>서치어드바이저에 URL 색인 요청</b>하면 검색 반영이 수일→수시간으로 빨라져요.</li></ul>"
           f"<a href='https://searchadvisor.naver.com/console/board/registration' target='_blank' rel='noopener' class='{cbtn} bg-slate-900 hover:bg-slate-800 inline-block'>🔗 서치어드바이저 색인 요청 →</a></div>")
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
        f"<input name=phone placeholder='📞 전화 (매장)' class='{inp}'>"
        f"<input name=hours placeholder='🕐 영업시간 (매장)' class='{inp}'>"
        f"<input name=address placeholder='📍 주소 (매장)' class='{inp}'>"
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
            f"<input name=phone value=\"{esc(t.phone)}\" placeholder='📞 전화번호' class='{inp}'>"
            f"<input name=hours value=\"{esc(t.hours)}\" placeholder='🕐 영업시간' class='{inp}'>"
            f"<input name=address value=\"{esc(t.address)}\" placeholder='📍 주소' class='{inp}'>"
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
            + f"<a href='/u/{tok}' class='px-3 py-1.5 bg-emerald-500 text-white text-xs font-semibold rounded-lg'>📷 사진 올리기</a>"
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
def _upload_form_html(tenant, token: str) -> str:
    """모던·간결 생성 카드 — 가게이름/링크 자동인식 + 사진 + 형태 + 목적 → 5채널 생성."""
    bt = (tenant.biz_type or "local")
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
    biz_toggle = ("<div class='grid grid-cols-2 gap-2.5'>" + _bz("local", "🏪", "동네 매장")
                  + _bz("seller", "📦", "온라인 셀러") + "</div>")
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
    _store_summary = (f"🏪 <b>{_nm}</b> · {_ind0} <span class='ml-1 text-indigo-500 font-bold'>✏️ 정보 수정 ▾</span>"
                      if _nm else "2. 내 가게 / 상품 정보")
    form = f"""<form method=post action='/u/{token}/upload' enctype='multipart/form-data' onsubmit='return showGen(event)' class='space-y-6'>
      <input type=hidden name=s_name id=s_name value="{_nm}"><input type=hidden name=s_industry id=s_industry value="{_ind0}"><input type=hidden name=s_biz id=s_biz value='{bt}'>
      <div><label class='{lb}'>1. 어떤 장사인가요?</label>{biz_toggle}</div>
      <details {_store_open} class='rounded-2xl border border-slate-100 bg-slate-50/50 p-4'><summary id=storeSummary class='{lb} mb-0 cursor-pointer select-none'>{_store_summary}</summary>
        <div id=lk_hint2 class='text-xs text-indigo-500 font-semibold mt-3 mb-1.5'></div>
        <div class='flex gap-2'>
          <input id=lk_q value="{_nm}" placeholder='가게 이름 (자동 인식)' class='{inp} flex-1'>
          <button type=button onclick='lookupStore()' class='px-5 bg-slate-900 hover:bg-slate-800 text-white rounded-xl font-bold text-sm whitespace-nowrap transition'>자동 인식</button></div>
        <div id=lk_result class='text-xs mt-2 mb-2 text-slate-400'>{_hint}</div>
        <div id=sf_local class='grid grid-cols-2 gap-2'>
          <input name=s_region id=s_region value="{_rg}" placeholder='지역 (예: 부산 동구)' class='{inp}'>
          <input name=s_tel id=s_tel value="{_tel0}" placeholder='전화번호' class='{inp}'>
          <input name=s_address id=s_address value="{_addr}" placeholder='주소' class='{inp} col-span-2'>
          <input name=s_map id=s_map value="{_map0}" placeholder='네이버 플레이스 URL (선택)' class='{inp} col-span-2'></div>
        <div id=sf_seller class='grid grid-cols-2 gap-2 hidden'>
          <input name=s_buy id=s_buy value="{esc(getattr(tenant,'buy_url','') or '')}" placeholder='🔗 내 스토어/상품 링크 (손님이 갈 곳) *필수' class='{inp} col-span-2'>
          <input name=s_market id=s_market value="{esc(getattr(tenant,'marketplace','') or '')}" placeholder='마켓 (쿠팡·스마트스토어·11번가)' class='{inp}'>
          <input name=s_brand id=s_brand value="{esc(getattr(tenant,'brand_name','') or '')}" placeholder='브랜드명' class='{inp}'>
          <input name=s_search id=s_search value="{esc(getattr(tenant,'search_kw','') or '')}" placeholder='검색어 유도 (예: 폴딩박스)' class='{inp} col-span-2'></div></details>
      <div><label class='{lb}'>3. 사진 <span class='text-slate-400 font-normal text-xs'>(끌어서 순서 변경 · × 삭제)</span>
        <span class='inline-block ml-1 bg-indigo-50 text-indigo-600 text-[11px] font-bold px-2 py-0.5 rounded-full'>✨ 자동 전문가 보정</span></label>
        <div id=up_preview class='grid grid-cols-3 sm:grid-cols-4 gap-2'></div>
        <input type=file name=photos id=up_photos accept='image/*' multiple required class='hidden'>
        <p class='text-xs text-slate-400 mt-1.5'>💡 <b class='text-slate-500'>끌어서</b> 순서 변경 · <b class='text-slate-500'>＋</b> 로 여러 장 추가 · 올린 순서대로 영상·블로그에 배치돼요</p></div>
      <div><label class='{lb}'>4. 목적 <span class='text-slate-400 font-normal text-xs'>(선택)</span></label>
        <div class='flex flex-wrap gap-2'>{chips}</div></div>
      <div><label class='{lb}'>5. 사진 설명·요청 <span class='text-slate-400 font-normal text-xs'>(선택 · AI가 더 정확해져요)</span></label>
        <input name=photo_desc maxlength=120 placeholder='이 사진은? (예: 겨울 열차단 썬팅, 시공 완료된 검은 SUV)' class='{inp} mb-2'>
        <input name=note maxlength=50 oninput="var c=document.getElementById('reqc');if(c)c.textContent=this.value.length+'/50';" placeholder='꼭 반영할 요청 (예: 급매 강조 / 차분한 톤)' class='{inp}'>
        <div class='text-right text-xs text-slate-400 mt-1'><span id=reqc>0/50</span></div></div>
      <button class='w-full py-4 rounded-2xl text-white font-extrabold text-lg shadow-xl shadow-indigo-500/30 hover:shadow-indigo-500/50 transition' style='background:linear-gradient(120deg,#6366f1,#8b5cf6,#ec4899)'>✨ 5채널 콘텐츠 생성하기</button>
      <p class='text-center text-xs text-slate-400'>인스타·네이버·유튜브·X + 영상을 AI가 자동 생성 (20~40초)</p></form>"""
    js = ("<script>"
          "function bizFields(v){var l=document.getElementById('sf_local'),s=document.getElementById('sf_seller');if(l&&s){if(v==='seller'){l.classList.add('hidden');s.classList.remove('hidden');}else{s.classList.add('hidden');l.classList.remove('hidden');}}"
          "var q=document.getElementById('lk_q'),h=document.getElementById('lk_hint2');"
          "if(v==='seller'){if(q)q.placeholder='🔗 내 상품/스토어 링크 붙여넣기 (또는 상품명)';if(h)h.innerHTML='💡 내 상품 링크를 붙이면 그게 손님이 갈 <b>판매 링크</b>가 돼요. 링크 없으면 상품명으로 검색(정보만) 후 <b>내 링크는 직접 입력</b>.';}"
          "else{if(q)q.placeholder='가게 이름 (자동 인식)';if(h)h.innerHTML='';}}"
          "var PM={f:[],drag:-1};"
          "function pmSync(){var dt=new DataTransfer();PM.f.forEach(function(x){dt.items.add(x);});document.getElementById('up_photos').files=dt.files;}"
          "function pmDel(i){PM.f.splice(i,1);pmRender();}"
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
          "(function(){var inp=document.getElementById('up_photos');if(inp){inp.addEventListener('change',function(){Array.from(inp.files||[]).forEach(function(x){PM.f.push(x);});pmRender();});pmRender();}bizFields((document.getElementById('s_biz')||{}).value||'local');})();"
          "function fillStore(d){document.getElementById('s_name').value=d.name||'';document.getElementById('s_industry').value=d.industry||'';"
          "var bz=(d.type==='seller')?'seller':'local';document.getElementById('s_biz').value=bz;bizFields(bz);"
          "document.getElementById('s_region').value=d.region||'';document.getElementById('s_tel').value=d.tel||'';if(d.buy_url){document.getElementById('s_buy').value=d.buy_url;}"
          "document.getElementById('s_address').value=d.address||'';"
          "var mp=document.getElementById('s_map');if(mp)mp.value=d.map_url||'';document.getElementById('lk_q').value=d.name||document.getElementById('lk_q').value;"
          "var mk=document.getElementById('s_market');if(mk&&d.market)mk.value=d.market;var br=document.getElementById('s_brand');if(br&&d.brand)br.value=d.brand;var sk=document.getElementById('s_search');if(sk&&d.search_kw)sk.value=d.search_kw;"
          "var rb=document.querySelector('input[name=biztype][value=\"'+bz+'\"]');if(rb)rb.checked=true;"
          "var kind=(bz==='seller')?'📦 온라인 셀러':'🏪 동네 매장';"
          "document.getElementById('lk_result').innerHTML='<span class=\"text-emerald-600 font-semibold\">✓ '+(d.name||'')+' · '+(d.industry||'')+(d.region?(' · '+d.region):'')+' 선택됨 (저장)</span>';"
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
          "var st=[[0,'🎯 마케팅 전략가가 분석 중…'],[20,'✍️ 카피라이터가 글 쓰는 중…'],[42,'🔍 SEO 편집장이 다듬는 중…'],[62,'🎬 영상 감독이 영상 만드는 중…'],[85,'🎬 영상 마무리 중…']];"
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
                   "<div id='gLabel' class='font-bold text-sm mb-3'>🎯 마케팅 전략가가 분석 중…</div>"
                   "<div class='w-full h-2 bg-slate-100 rounded-full overflow-hidden'><div id='gBar' class='h-full' style='width:0%;transition:width .4s;background:linear-gradient(90deg,#6366f1,#ec4899)'></div></div>"
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
                 s_search: str = Form("")):
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
            if _ind:
                from app.industries import ensure_profile
                ensure_profile(_ind)
            made = ingest_upload(tenant, files, full_note)
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
        if p.payload.get("video_path") and os.path.exists(p.payload["video_path"]):
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
