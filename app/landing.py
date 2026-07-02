"""
랜딩 페이지 — 「올린다(Ollinda)」.
히어로 + 실제 작동 영상 + 셀프 체험 위젯(글 노출/영상 흐릿) + 기능 + 가격 + 문의/카톡 + 개발자/포트폴리오.
모바일 최적화 + SEO(OG/메타). Tailwind(CDN) + Pretendard.
"""
from __future__ import annotations

import os

BRAND = "올린다"
CONTACT_EMAIL = "etetetetet5ea@kakao.com"
# 공개 베이스 URL(카카오톡 미리보기 og:image는 반드시 절대 https URL이어야 함)
BASE = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")

# 올린다 로고 — 매출 '올린다'(상승 라인차트) 그라데이션 마크
LOGO = ('<svg viewBox="0 0 32 32" class="w-7 h-7 inline-block align-middle"><defs>'
        '<linearGradient id="lg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#6366f1"/>'
        '<stop offset="1" stop-color="#ec4899"/></linearGradient></defs>'
        '<rect width="32" height="32" rx="9" fill="url(#lg)"/>'
        '<path d="M8 21 L14 14 L18 18 L24 9" stroke="white" stroke-width="2.6" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="9" r="2.3" fill="white"/></svg>')

_STYLE = """
<style>
:root{--g1:#6366f1;--g2:#8b5cf6;--g3:#ec4899;}
*{scroll-behavior:smooth}
body{font-family:'Pretendard','Apple SD Gothic Neo',system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.grad-text{background:linear-gradient(110deg,#818cf8,#c084fc,#f472b6);-webkit-background-clip:text;background-clip:text;color:transparent}
.grad-btn{background:linear-gradient(110deg,var(--g1),var(--g2),var(--g3));background-size:200% auto;transition:.5s}
.grad-btn:hover{background-position:right center;box-shadow:0 12px 40px -8px rgba(139,92,246,.6)}
.blob{position:absolute;border-radius:9999px;filter:blur(80px);opacity:.5;animation:float 9s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0) translateX(0)}50%{transform:translateY(-30px) translateX(20px)}}
.reveal{opacity:0;transform:translateY(30px);transition:opacity .8s cubic-bezier(.2,.7,.2,1),transform .8s}
.reveal.show{opacity:1;transform:none}
.dot{animation:pulse 1.2s ease-in-out infinite}@keyframes pulse{0%,100%{opacity:.4}50%{opacity:1}}
.card-hover{transition:transform .35s,box-shadow .35s}
.card-hover:hover{transform:translateY(-8px);box-shadow:0 24px 50px -20px rgba(99,102,241,.45)}
.glass{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);backdrop-filter:blur(12px)}
.kakao-float{position:fixed;right:20px;bottom:20px;z-index:50;width:60px;height:60px;border-radius:9999px;
 background:#FEE500;display:flex;align-items:center;justify-content:center;box-shadow:0 10px 30px -8px rgba(0,0,0,.4);font-weight:800;color:#191600;font-size:13px}
</style>"""

_HEAD = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>올린다 — 사진 한 장이면, 매출이 올라갑니다</title>
<meta name=description content="사장님은 사진만 올리세요. AI가 인스타·네이버블로그·유튜브·X 콘텐츠를 만들고 검색 상위에 뜨게 최적화해 자동 발행합니다. 소상공인 AI 마케팅 자동화 올린다.">
<meta name=keywords content="AI 마케팅,소상공인,인스타 자동,네이버 블로그 자동,콘텐츠 자동화,썬팅,카페,미용실,올린다">
<meta property=og:type content=website>
<meta property=og:title content="올린다 — 사진 한 장이면, 매출이 올라갑니다">
<meta property=og:description content="AI가 5개 채널 콘텐츠를 만들고 자동 발행. 소상공인 마케팅 자동화.">
<meta property=og:image content="__BASE__/demo/og.png">
<meta property=og:image:width content="1200">
<meta property=og:image:height content="630">
<meta property=og:url content="__BASE__/">
<meta name=twitter:card content=summary_large_image>
<meta name=twitter:image content="__BASE__/demo/og.png">
<link rel=canonical href="__BASE__/">
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.min.css" rel=stylesheet>
<script src="https://cdn.tailwindcss.com"></script>
<script type=application/ld+json>{"@context":"https://schema.org","@type":"SoftwareApplication","name":"올린다","applicationCategory":"BusinessApplication","offers":{"@type":"Offer","price":"39900","priceCurrency":"KRW"}}</script>
""".replace("__BASE__", BASE) + _STYLE + """</head><body class="bg-white text-slate-800">"""

_FOOT = """
<script>
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('show');io.unobserve(e.target)}}),{threshold:.12});
document.querySelectorAll('.reveal').forEach(el=>io.observe(el));
const cu=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){const el=e.target,t=+el.dataset.count;let n=0,st=Math.max(1,t/40);const id=setInterval(()=>{n+=st;if(n>=t){n=t;clearInterval(id)}el.textContent=Math.floor(n)},25);cu.unobserve(el)}}),{threshold:.5});
document.querySelectorAll('[data-count]').forEach(el=>cu.observe(el));
// 셀프 체험 위젯
(function(){const df=document.getElementById('demoForm');if(!df)return;
 const esc=s=>(s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
 df.addEventListener('submit',async e=>{e.preventDefault();
  const box=document.getElementById('demoResult');
  box.innerHTML='<div class="text-center text-slate-200 py-6"><span class="dot inline-block w-3 h-3 rounded-full bg-indigo-400 align-middle"></span> 확인 중…</div>';
  const ind=document.getElementById('d_ind').value.trim();
  const biz=(document.querySelector('input[name="d_biz"]:checked')||{}).value||'local';
  const fd=new FormData();fd.append('industry',ind);fd.append('biz_type',biz);
  try{const r=await fetch('/api/demo',{method:'POST',body:fd});const d=await r.json();
   if(d.go_dashboard){window.location.href='/me';return;}
   let cta;
   if(d.limit){cta='<a href="#pricing" class="block py-3 rounded-xl font-bold bg-white text-indigo-700">요금제 보기 →</a>';}
   else{cta='<a href="/login/google" class="flex items-center justify-center gap-2 py-3 rounded-xl font-extrabold mb-2 bg-white text-slate-700"><svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg> 구글로 가입하기</a>'
        +'<a href="/login/kakao" class="block py-3 rounded-xl font-extrabold" style="background:#FEE500;color:#191600">💬 카카오로 3초 가입</a>';}
   box.innerHTML='<div class="rounded-2xl p-5 text-center" style="background:rgba(255,255,255,.1)">'
    +'<div class="text-4xl mb-2">🎁</div>'
    +'<p class="text-white font-bold mb-1">'+esc(d.message||'가입하면 바로 만들어드려요!')+'</p>'
    +'<p class="text-slate-300 text-xs mb-4">사진은 가입 후 \\'내 작업실\\'에서 올리면 5채널이 자동 생성됩니다.</p>'
    +cta+'</div>';
   box.scrollIntoView({behavior:'smooth',block:'nearest'});
  }catch(err){box.innerHTML='<div class="text-rose-300 text-sm text-center">오류가 발생했어요. 잠시 후 다시.</div>';}
 });})();
// 문의 폼
(function(){const cf=document.getElementById('contactForm');if(!cf)return;
 cf.addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(cf);
  const btn=cf.querySelector('button');btn.textContent='보내는 중…';
  try{const r=await fetch('/api/contact',{method:'POST',body:fd});const d=await r.json();
   document.getElementById('contactMsg').textContent=d.ok?'✅ 문의가 접수되었습니다. 곧 연락드릴게요!':'⚠️ '+(d.error||'전송 실패');
   if(d.ok)cf.reset();}catch(e){document.getElementById('contactMsg').textContent='⚠️ 전송 실패';}
  btn.textContent='문의하기';});})();
</script></body></html>"""


def _nav() -> str:
    return f"""
<header class="sticky top-0 z-40 bg-white/85 backdrop-blur-md border-b border-slate-100">
 <div class="max-w-6xl mx-auto px-5 h-16 flex items-center justify-between">
  <a href="/" class="flex items-center gap-2 font-extrabold text-xl">{LOGO}<span class="grad-text">올린다</span></a>
  <nav class="hidden md:flex items-center gap-6 text-sm text-slate-500 font-medium">
   <a href="#video" class="hover:text-slate-900">작동 영상</a>
   <a href="#features" class="hover:text-slate-900">기능</a>
   <a href="#pricing" class="hover:text-slate-900">요금</a>
   <a href="#contact" class="hover:text-slate-900">문의</a></nav>
  <div class="flex items-center gap-2">
   <a href="/me" class="px-4 py-2 rounded-lg text-sm font-bold text-slate-600 hover:bg-slate-100">내 작업실</a>
   <a href="/login" class="px-4 py-2 rounded-lg text-sm font-bold text-slate-600 hover:bg-slate-100">로그인</a></div>
 </div></header>"""


def _hero() -> str:
    return f"""
<section class="relative overflow-hidden bg-slate-950 text-white">
 <div class="blob" style="width:420px;height:420px;background:#6366f1;top:-80px;left:-60px"></div>
 <div class="blob" style="width:360px;height:360px;background:#ec4899;top:40px;right:-40px;animation-delay:2s"></div>
 <div class="relative max-w-6xl mx-auto px-5 pt-20 pb-16 text-center">
  <div class="reveal inline-flex items-center gap-2 px-4 py-1.5 rounded-full glass text-xs font-semibold mb-6">
   <span class="dot w-2 h-2 rounded-full bg-emerald-400"></span> 소상공인 · 온라인 셀러 전용 · AI 마케팅 자동화</div>
  <h1 class="reveal text-4xl sm:text-6xl font-extrabold tracking-tight leading-[1.08]">
   사진 한 장이면,<br><span class="grad-text">매출이 올라갑니다</span></h1>
  <p class="reveal mt-6 text-lg text-slate-300 max-w-2xl mx-auto">동네 사장님도, <b class="text-white">쿠팡·11번가 셀러</b>도 사진만 올리세요. AI가 <b class="text-white">인스타·블로그·유튜브·X</b> 콘텐츠를 만들고,
   <b class="text-white">검색 상위</b>에 띄워 <b class="text-white">매장 방문·상세페이지 구매</b>로 연결합니다.</p>
  <div class="reveal mt-8 flex flex-col sm:flex-row gap-3 justify-center">
   <a href="/login/kakao" class="flex items-center justify-center px-8 py-4 rounded-2xl font-extrabold text-lg" style="background:#FEE500;color:#191600">💬 카카오로 가입</a>
   <a href="/login/google" class="flex items-center justify-center gap-2 px-8 py-4 rounded-2xl font-extrabold text-lg bg-white text-slate-700 shadow-lg"><svg width="22" height="22" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg> 구글로 로그인</a></div>
 </div></section>"""


def _video() -> str:
    return """
<section id="video" class="bg-slate-950 pb-16">
 <div class="max-w-4xl mx-auto px-5">
  <div class="reveal text-center mb-5">
   <h2 class="text-2xl sm:text-3xl font-extrabold text-white">실제 결과물, 직접 보세요</h2>
   <p class="text-slate-400 text-sm mt-1">사진만 올리면 채널별 콘텐츠가 자동으로. 장사 방식에 맞춰 다르게 나옵니다.</p></div>
  <div class="flex justify-center gap-2 mb-5">
   <button onclick="shopTab('local')" id="tab-local" class="px-5 py-2.5 rounded-full font-bold text-sm bg-emerald-500 text-white">🏪 소상공인 (매장 방문)</button>
   <button onclick="shopTab('seller')" id="tab-seller" class="px-5 py-2.5 rounded-full font-bold text-sm bg-white/10 text-slate-300">📦 온라인 셀러 (구매 유도)</button></div>
  <div id="pane-local" class="reveal">
   <div class="max-w-sm mx-auto rounded-3xl overflow-hidden border border-white/10 shadow-2xl bg-black">
    <video src="/demo/local_short.mp4" controls autoplay muted loop playsinline class="w-full"></video>
    <div class="bg-slate-900 text-slate-300 text-sm px-5 py-3">초량 루마썬팅 — 사진 → AI가 자동 생성한 세로 숏폼 + <b class="text-emerald-300">방문·연락 유도</b>. 실제 결과물.</div></div>
   <div class="max-w-3xl mx-auto mt-6 rounded-3xl overflow-hidden border border-white/10 shadow-2xl bg-black">
    <video src="/demo/process.mp4" controls muted loop playsinline class="w-full"></video>
    <div class="bg-slate-900 text-slate-300 text-sm px-5 py-3">＋ 전체 발행 과정 — 네이버 블로그·인스타·유튜브·X 한 번에 (지도·연락처 마무리)</div></div></div>
  <div id="pane-seller" class="reveal hidden max-w-sm mx-auto rounded-3xl overflow-hidden border border-white/10 shadow-2xl bg-black">
   <video src="/demo/seller_short.mp4" controls muted loop playsinline class="w-full"></video>
   <div class="bg-slate-900 text-slate-300 text-sm px-5 py-3">셀프 썬팅 키트 셀러 — 사진 1장 → AI가 자동 생성한 세로 숏폼(릴스/쇼츠) + <b class="text-amber-300">쿠팡 구매 유도</b>. 실제 프로그램 결과물.</div></div>
 </div>
 <script>
 function shopTab(m){
   document.getElementById('pane-local').classList.toggle('hidden', m!=='local');
   document.getElementById('pane-seller').classList.toggle('hidden', m!=='seller');
   document.getElementById('tab-local').className='px-5 py-2.5 rounded-full font-bold text-sm '+(m==='local'?'bg-emerald-500 text-white':'bg-white/10 text-slate-300');
   document.getElementById('tab-seller').className='px-5 py-2.5 rounded-full font-bold text-sm '+(m==='seller'?'bg-amber-500 text-white':'bg-white/10 text-slate-300');
 }
 </script></section>"""


def _demo_widget() -> str:
    return """
<section class="bg-slate-950 pb-20"><div class="max-w-3xl mx-auto px-5">
 <div class="glass rounded-3xl p-6 sm:p-8 text-left">
  <div class="text-center mb-4"><div class="text-white font-extrabold text-lg">🎬 내 가게로 무료 2회 만들어보기</div>
   <p class="text-slate-300 text-sm mt-1">가입하면 내 사진으로 5채널 콘텐츠·영상을 바로 만들어드려요</p></div>
  <form id="demoForm" class="space-y-3">
   <input id="d_ind" placeholder="업종/상품 (예: 꽃집, 헬스장, 캠핑 폴딩박스...)" class="w-full rounded-xl px-4 py-3 text-slate-800 outline-none">
   <div class="flex gap-2 text-sm">
     <label class="flex-1"><input type="radio" name="d_biz" value="local" checked class="peer hidden"><div class="text-center py-2.5 rounded-xl bg-white/10 text-slate-200 peer-checked:bg-emerald-500 peer-checked:text-white font-bold cursor-pointer">🏪 동네 매장</div></label>
     <label class="flex-1"><input type="radio" name="d_biz" value="seller" class="peer hidden"><div class="text-center py-2.5 rounded-xl bg-white/10 text-slate-200 peer-checked:bg-amber-500 peer-checked:text-white font-bold cursor-pointer">📦 온라인 셀러</div></label>
   </div>
   <button class="grad-btn w-full py-3.5 rounded-xl text-white font-extrabold text-lg">✨ 무료로 만들어보기</button></form>
  <div id="demoResult" class="mt-5"></div>
  <p class="text-center text-slate-500 text-xs mt-3">가입한 회원만 무료 2회 · 사진은 가입 후 ‘내 작업실’에서 업로드</p>
 </div></div></section>"""


def _stats() -> str:
    items = [("5", "개 채널 동시"), ("1", "장 사진이면 끝"), ("100", "점 상위노출 점검"), ("2", "개 모드 자동분기")]
    cells = "".join(f"<div class='reveal text-center'><div class='text-5xl font-extrabold grad-text' data-count='{n}'>0</div>"
                    f"<div class='text-sm text-slate-500 mt-2 font-medium'>{l}</div></div>" for n, l in items)
    return f"<section class='max-w-5xl mx-auto px-5 py-16'><div class='grid grid-cols-2 sm:grid-cols-4 gap-8'>{cells}</div></section>"


def _problem() -> str:
    pains = [("⏰", "시간이 없다", "장사하기도 바쁜데 매일 인스타·블로그·영상까지 올릴 시간이 없죠."),
             ("💸", "대행사는 비싸다", "월 30~50만원 대행료, 결과는 깜깜이. 부담만 큽니다."),
             ("🤷", "뭘 올릴지 모른다", "찍긴 했는데 어떻게 써야 검색에 뜨고 손님이 올지 막막합니다.")]
    cards = "".join(f"<div class='reveal card-hover bg-white rounded-3xl border border-slate-100 p-7 shadow-sm'>"
                    f"<div class='text-4xl mb-4'>{e}</div><div class='font-bold text-xl mb-2'>{t}</div>"
                    f"<p class='text-slate-500 text-sm'>{d}</p></div>" for e, t, d in pains)
    return f"<section class='bg-slate-50 py-20'><div class='max-w-5xl mx-auto px-5'><h2 class='reveal text-3xl sm:text-4xl font-extrabold text-center mb-3'>마케팅, 이래서 못 하셨죠?</h2><p class='reveal text-center text-slate-500 mb-12'>사장님 99%가 겪는 문제 — 올린다가 해결합니다.</p><div class='grid sm:grid-cols-3 gap-6'>{cards}</div></div></section>"


def _modes() -> str:
    """두 종류 고객(소상공인 vs 온라인 셀러)에 맞춰 결과물이 자동으로 달라짐을 설명."""
    local = [("🎯 목표", "동네 손님을 <b>매장 방문·전화·예약</b>으로"),
             ("🔑 키워드", "<b>지역명</b> 중심 (예: ‘부산 초량 썬팅 추천’)"),
             ("📝 글 마무리", "<b>지도 + 영업시간 + 연락처</b> 자동 삽입"),
             ("📣 주력 채널", "네이버 블로그·플레이스 → 인스타")]
    seller = [("🎯 목표", "검색·SNS 손님을 <b>상세페이지 구매</b>로"),
              ("🔑 키워드", "<b>상품·후기</b> 중심 (예: ‘폴딩박스 추천·내돈내산’)"),
              ("📝 글 마무리", "<b>구매 링크 / 쿠팡 검색어</b> 자동 삽입"),
              ("📣 주력 채널", "인스타 릴스·유튜브 쇼츠 → 블로그 후기")]
    def col(title, sub, items, accent):
        rows = "".join(f"<div class='flex gap-3 py-2.5 border-t border-slate-100'>"
                       f"<div class='text-sm font-bold text-slate-500 w-24 shrink-0'>{k}</div>"
                       f"<div class='text-sm text-slate-700'>{v}</div></div>" for k, v in items)
        return (f"<div class='reveal card-hover bg-white rounded-3xl border border-slate-100 p-7 shadow-sm'>"
                f"<div class='inline-flex items-center gap-2 text-xs font-bold px-3 py-1 rounded-full {accent} mb-3'>{title}</div>"
                f"<p class='text-slate-500 text-sm mb-2'>{sub}</p>{rows}</div>")
    cols = (col("🏪 동네 매장 (소상공인)", "썬팅집·카페·미용실·식당·꽃집…", local, "bg-emerald-100 text-emerald-700")
            + col("📦 온라인 셀러", "쿠팡·11번가·스마트스토어·자사몰…", seller, "bg-amber-100 text-amber-700"))
    return ("<section class='py-20'><div class='max-w-5xl mx-auto px-5'>"
            "<h2 class='reveal text-3xl sm:text-4xl font-extrabold text-center mb-3'>"
            "<span class='grad-text'>내 장사 방식</span>에 딱 맞게</h2>"
            "<p class='reveal text-center text-slate-500 mb-12'>매장이냐 온라인 판매냐에 따라 글 마무리·키워드·CTA가 자동으로 달라집니다. 설정은 한 번이면 끝.</p>"
            f"<div class='grid sm:grid-cols-2 gap-6'>{cols}</div></div></section>")


def _features() -> str:
    feats = [("📸", "사진 한 장 → 5채널", "인스타·네이버·유튜브·릴스·X를 한 번에."),
             ("🎬", "글 → 영상 + 단어자막", "문장이 곧 장면. 말하는 단어가 차오르는 카라오케 자막·AI음성·켄번스 자동."),
             ("🖼️", "인스타 캐러셀 자동", "사진 1장 → 정보 슬라이드 카드 묶음(저장·도달↑)."),
             ("📐", "쇼츠·릴스·피드 규격", "9:16·1:1·4:5 자동 출력으로 모든 채널 커버."),
             ("🏪📦", "소상공인·셀러 자동분기", "매장은 지도·방문, 셀러는 구매링크·검색어로 자동 전환."),
             ("🎯", "검색 상위노출 + 점수", "C-Rank·D.I.A·릴스 알고리즘 반영, 100점 점검."),
             ("🏷️", "업종 무제한 자동", "어떤 업종이든 AI가 맞춤 톤 자동 생성."),
             ("🔗", "계정 1회 연결 자동발행", "비번 없이 연결, 발행 누르면 끝.")]
    cards = "".join(f"<div class='reveal card-hover bg-white rounded-3xl border border-slate-100 p-6'>"
                    f"<div class='w-12 h-12 rounded-2xl bg-gradient-to-br from-indigo-100 to-fuchsia-100 flex items-center justify-center text-2xl mb-4'>{e}</div>"
                    f"<div class='font-bold text-lg mb-1.5'>{t}</div><p class='text-slate-500 text-sm'>{d}</p></div>" for e, t, d in feats)
    return f"<section id='features' class='py-20'><div class='max-w-6xl mx-auto px-5'><h2 class='reveal text-3xl sm:text-4xl font-extrabold text-center mb-3'>올린다가 <span class='grad-text'>다 합니다</span></h2><p class='reveal text-center text-slate-500 mb-12'>생성부터 최적화·발행·관리까지.</p><div class='grid sm:grid-cols-2 lg:grid-cols-4 gap-5'>{cards}</div></div></section>"


def _pricing() -> str:
    plans = [("셀프", "월 39,900원", ["사진만 올리면 5채널 생성", "상위노출 최적화·점수", "검수·발행·다운로드", "구독자 대시보드·이력"], False),
             ("대행", "월 299,000원", ["올린다 팀이 운영까지 대행", "주 3회+ 정기 발행", "성과 리포트", "카톡 1:1 관리"], True),
             ("건당", "6,500원", ["구독 없이 1건만", "필요할 때만 결제", "콘텐츠 1세트(5채널)"], False)]
    cards = ""
    for name, price, feats, hot in plans:
        wrap = "relative ring-2 ring-indigo-500 shadow-2xl scale-[1.04]" if hot else "border border-slate-100"
        tag = "<div class='absolute -top-3 left-1/2 -translate-x-1/2 grad-btn text-white text-xs font-bold px-3 py-1 rounded-full'>가장 인기</div>" if hot else ""
        lis = "".join(f"<li class='flex gap-2 items-start'><span class='text-indigo-500 mt-0.5'>✓</span><span>{f}</span></li>" for f in feats)
        btn = "grad-btn text-white" if hot else "bg-slate-100 hover:bg-slate-200"
        cards += (f"<div class='reveal card-hover {wrap} bg-white rounded-3xl p-8 flex flex-col'>{tag}"
                  f"<div class='font-bold text-lg text-slate-500'>{name}</div><div class='text-3xl font-extrabold my-3'>{price}</div>"
                  f"<ul class='space-y-2.5 text-sm text-slate-600 flex-1 mt-2'>{lis}</ul>"
                  f"<a href='/signup' class='{btn} mt-7 text-center px-4 py-3.5 rounded-2xl font-bold'>시작하기</a></div>")
    return f"<section id='pricing' class='bg-slate-50 py-20'><div class='max-w-5xl mx-auto px-5'><h2 class='reveal text-3xl sm:text-4xl font-extrabold text-center mb-3'>합리적인 요금</h2><p class='reveal text-center text-slate-500 mb-14'>대행사 1/3 가격으로, 결과는 더 확실하게.</p><div class='grid sm:grid-cols-3 gap-6 items-stretch pt-3'>{cards}</div></div></section>"


def _faq() -> str:
    qa = [("정말 사진만 올리면 되나요?", "네. 사진과 한 줄 설명만 주시면 AI가 5채널 콘텐츠를 만듭니다. 사진 1장만 있어도 자막·음성이 들어간 세로 숏폼까지 자동 생성됩니다."),
          ("쿠팡·11번가 셀러도 되나요?", "네. '온라인 셀러'로 설정하면 글 마무리가 지도 대신 구매 링크/검색어로, 키워드가 지역명 대신 상품·후기 키워드로 자동 전환됩니다. (쿠팡은 직링크 정책상 '검색어 유도'를 권장)"),
          ("제 SNS 비밀번호를 줘야 하나요?", "아니요. 공식 OAuth로 한 번만 권한을 허용하면 됩니다. 비밀번호는 저장하지 않습니다."),
          ("네이버 블로그도 되나요?", "글·사진을 완성해 드리고, 임시저장된 글을 네이버에서 발행만 누르시면 됩니다. (네이버는 공식 발행 API가 없어 반자동)"),
          ("업종이 특이해도 되나요?", "어떤 업종이든 AI가 맞춤 프로필을 자동 생성합니다.")]
    items = "".join(f"<details class='reveal bg-white rounded-2xl border border-slate-100 p-5'><summary class='font-semibold cursor-pointer'>{q}</summary><p class='text-slate-500 text-sm mt-2'>{a}</p></details>" for q, a in qa)
    return f"<section class='py-20'><div class='max-w-3xl mx-auto px-5'><h2 class='reveal text-3xl sm:text-4xl font-extrabold text-center mb-10'>자주 묻는 질문</h2><div class='space-y-3'>{items}</div></div></section>"


def _contact() -> str:
    f = "w-full border border-slate-200 rounded-xl px-4 py-3 text-sm"
    return f"""
<section id="contact" class="bg-slate-50 py-20"><div class="max-w-3xl mx-auto px-5">
 <h2 class="reveal text-3xl sm:text-4xl font-extrabold text-center mb-2">문의하기</h2>
 <p class="reveal text-center text-slate-500 mb-8">올린다 도입·대행 상담을 무료로 도와드립니다.</p>
 <form id="contactForm" class="reveal bg-white rounded-3xl border border-slate-100 shadow-sm p-6 grid sm:grid-cols-2 gap-3">
  <input name="company" placeholder="상호/회사명 *" required class="{f}">
  <input name="manager" placeholder="담당자 *" required class="{f}">
  <input name="phone" placeholder="연락처 *" required class="{f}">
  <input name="email" type="email" placeholder="이메일 *" required class="{f}">
  <textarea name="message" placeholder="문의 내용" rows=3 class="{f} sm:col-span-2"></textarea>
  <button class="grad-btn text-white font-bold py-3.5 rounded-xl sm:col-span-2">문의하기</button>
  <p id="contactMsg" class="text-center text-sm text-emerald-600 sm:col-span-2"></p>
 </form>
 <p class="text-center text-slate-400 text-xs mt-3">또는 카카오톡 상담 버튼(우측 하단) · 이메일 {CONTACT_EMAIL}</p>
</div></section>"""


def _cta() -> str:
    return """
<section id="cta" class="relative overflow-hidden bg-slate-950 text-white py-24">
 <div class="blob" style="width:380px;height:380px;background:#6366f1;top:-60px;left:20%"></div>
 <div class="relative max-w-3xl mx-auto px-5 text-center">
  <h2 class="reveal text-4xl sm:text-5xl font-extrabold leading-tight">오늘 사진 한 장,<br><span class="grad-text">내일 손님으로</span></h2>
  <p class="reveal mt-5 text-slate-300 text-lg">지금 시작하면 첫 콘텐츠 세트를 무료로 만들어 드립니다.</p>
  <div class="reveal mt-9 flex flex-col sm:flex-row gap-3 justify-center">
   <a href="/login/kakao" class="px-9 py-4 rounded-2xl font-extrabold text-lg" style="background:#FEE500;color:#191600">💬 카카오로 가입</a>
   <a href="/login/google" class="flex items-center justify-center gap-2 px-9 py-4 rounded-2xl font-extrabold text-lg bg-white text-slate-700"><svg width="22" height="22" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg> 구글로 로그인</a></div>
 </div></section>"""


def _footer() -> str:
    return f"""
<footer class="bg-slate-900 text-slate-300 pt-14 pb-10">
 <div class="max-w-6xl mx-auto px-5">
  <div class="flex items-center gap-2 font-extrabold text-xl mb-5">{LOGO}<span class="text-white">올린다</span></div>
  <div class="grid sm:grid-cols-2 gap-6 text-sm">
   <div>
    <div class="text-slate-400 text-xs mb-1">CEO</div><div class="font-bold text-white mb-2">Jung Young Jin</div>
    <div class="text-slate-400 text-xs">사업자등록번호</div><div class="mb-2">106-48-91586</div>
    <div class="text-slate-400 text-xs">Location</div><div>(우)50510 경남 양산시 주남로 288<br>영산대학교 양산캠퍼스 산학협력관 309호</div>
   </div>
   <div>
    <details class="bg-white/5 rounded-2xl p-4">
     <summary class="cursor-pointer font-bold text-white flex items-center gap-2">🏅 개발자 포트폴리오 <span class="text-xs text-slate-400">(보안전문가)</span></summary>
     <div class="mt-3 text-sm text-slate-300 space-y-2">
      <p class="font-semibold text-white">Jung Young Jin — 보안전문가 / 풀스택·AI 개발자</p>
      <ul class="space-y-1 text-slate-300 list-disc pl-5">
       <li>정보보안기사 · 침해사고 대응(DFIR)·모의해킹(웹/시스템) 다수 수행</li>
       <li>OWASP Top 10 기반 취약점 진단 및 시큐어코딩 컨설팅</li>
       <li>AI 멀티에이전트 자동화 시스템 설계·구축 (LLM·비전·TTS·영상)</li>
       <li>FastAPI·OAuth·결제·멀티테넌트 SaaS 아키텍처 풀스택 개발</li>
       <li>제조(프레스 금형) 도메인 + 보안 + AI 융합 — 현장형 솔루션 전문</li>
      </ul>
      <p class="text-xs text-slate-400">"공격자의 시선으로 만들고, 사장님의 매출로 증명합니다."</p>
     </div></details>
    <div class="mt-4 flex gap-3 text-sm">
     <a href="#contact" class="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20">문의하기</a>
     <a href="mailto:{CONTACT_EMAIL}" class="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20">이메일</a>
     <a href="/privacy" class="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20">개인정보처리방침</a></div>
   </div>
  </div>
  <div class="mt-8 pt-6 border-t border-white/10 text-center text-xs text-slate-500">© 2026 올린다 (Ollinda) · {CONTACT_EMAIL}</div>
 </div></footer>"""


def _kakao_float() -> str:
    return ('<a href="https://pf.kakao.com/_EGrPX/chat" target="_blank" rel="noopener" '
            'class="kakao-float" title="카카오톡 상담">TALK</a>')


def render() -> str:
    return (_HEAD + _nav() + _hero() + _video() + _demo_widget() + _stats() + _problem()
            + _modes() + _features() + _pricing() + _faq() + _contact() + _cta() + _footer()
            + _kakao_float() + _FOOT)


def privacy() -> str:
    body = f"""
<div class="max-w-3xl mx-auto px-5 py-16">
 <a href="/" class="text-indigo-600 text-sm">← 홈</a>
 <h1 class="text-3xl font-extrabold mt-4 mb-8">개인정보처리방침</h1>
 <div class="space-y-4 text-sm text-slate-600 leading-relaxed">
  <p>올린다(이하 "서비스")는 이용자의 개인정보를 중요시하며 관련 법령을 준수합니다.</p>
  <p><b>1. 수집 항목</b> — 이메일, 가게 정보, 업로드 사진/메모, 연결한 SNS 발행 권한 토큰(비밀번호 미수집).</p>
  <p><b>2. 이용 목적</b> — 콘텐츠 생성 및 이용자가 연결한 채널 게시(발행) 대행.</p>
  <p><b>3. SNS 연동</b> — 공식 OAuth 사용, 게시 권한 토큰만 보관. 언제든 연결 해제 가능.</p>
  <p><b>4. 보관·파기</b> — 해지/요청 시 지체 없이 파기.</p>
  <p><b>5. 사업자</b> — Jung Young Jin · 106-48-91586 · 경남 양산시 주남로 288 영산대 산학협력관 309호</p>
  <p><b>6. 문의</b> — {CONTACT_EMAIL}</p>
 </div></div>"""
    return _HEAD + _nav() + body + _footer() + _FOOT
