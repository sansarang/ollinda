"""
랜딩 페이지 — 「올린다(Ollinda)」. 토스/당근 스타일 밝은 미니멀 리디자인.
원칙: 흰 배경(#FFFFFF) + 옅은 회색(#F9FAFB) 교차 · 포인트색 보라(#6366F1) 1개 ·
상승 표시만 초록 · 이모지 대신 라인 아이콘(SVG) · 흰 카드(1px #E5E7EB, 16px 라운드) ·
그라데이션 금지 · 기능·문구·데이터는 기존 그대로(비주얼만 개편).
모바일 최적화 + SEO(OG/메타). Tailwind(CDN) + Pretendard.
"""
from __future__ import annotations

import os

BRAND = "올린다"
CONTACT_EMAIL = "ollinda.2026@gmail.com"           # 공개 문의 메일(2026-08-11 사장님 지정)
# 사업자 표기 단일 소스 — 푸터·약관·개인정보·환불정책이 전부 이 값만 쓴다.
# 표면마다 따로 박으면 한 곳만 고쳐지는 사고가 난다(2026-08-10 실제 재발).
BIZ_CEO = "Jung Young Jin"
BIZ_REG_NO = "106-48-91586"
BIZ_ADDR = "경상남도 양산시 평산중앙3길 18"
BIZ_PHONE = "010-9796-9009"                        # 사장님 연락처(2026-08-11 지정)
# 공개 베이스 URL(카카오톡 미리보기 og:image는 반드시 절대 https URL이어야 함)
BASE = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")

# 올린다 로고 — 매출 '올린다'(상승 라인차트). 단색 보라(브랜드색 1개 원칙).
LOGO = ('<svg viewBox="0 0 32 32" class="w-7 h-7 inline-block align-middle">'
        '<rect width="32" height="32" rx="9" fill="#6366F1"/>'
        '<path d="M8 21 L14 14 L18 18 L24 9" stroke="white" stroke-width="2.6" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="9" r="2.3" fill="white"/></svg>')

# ── 라인 아이콘(Lucide 스타일 인라인 SVG) — 이모지 대체 ──
_ICON_PATHS = {
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>',
    "pen": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "chart": '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
    "trend": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "wallet": '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    "video": '<path d="m22 8-6 4 6 4V8Z"/><rect x="2" y="6" width="14" height="12" rx="2"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
    "store": '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 10v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20v3H2z"/>',
    "package": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="M3.3 7 12 12l8.7-5"/><path d="M12 22V12"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "tag": '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4Z"/><circle cx="7.5" cy="7.5" r=".5"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "wand": '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72Z"/><path d="m14 7 3 3"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    "trophy": '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
    "printer": '<polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 12h10"/>',
    "pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1 1 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "xcircle": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "checkcircle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "arrowup": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "play": '<polygon points="5 3 19 12 5 21 5 3"/>',
    "book": '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    "gift": '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
}


def _icon(name: str, cls: str = "w-6 h-6") -> str:
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" class="{cls}">{_ICON_PATHS.get(name, "")}</svg>')


def _icon_chip(name: str, tone: str = "indigo", size: str = "") -> str:
    """카드 상단 아이콘 — 연보라 원형 배경(#EEF2FF)으로 시선 유도(색 절제 유지)."""
    c = ("bg-[#EEF2FF] text-indigo-600" if tone == "indigo" else "bg-slate-100 text-slate-500")
    if size == "lg":
        return f"<div class='w-14 h-14 rounded-full {c} flex items-center justify-center mb-4'>{_icon(name, 'w-6 h-6')}</div>"
    return f"<div class='w-11 h-11 rounded-full {c} flex items-center justify-center mb-4'>{_icon(name, 'w-5 h-5')}</div>"


_STYLE = """
<style>
*{scroll-behavior:smooth}
body{word-break:keep-all;overflow-wrap:break-word}   /* 한글은 단어 단위로만 줄바꿈(모바일 띄어쓰기) */
body{font-family:'Pretendard','Apple SD Gothic Neo',system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.reveal{opacity:0;transform:translateY(16px);transition:opacity .6s cubic-bezier(.2,.7,.2,1),transform .6s}
.reveal.show{opacity:1;transform:none}
.card{background:#fff;border:1px solid #E5E7EB;border-radius:16px}
.rise{animation:rise 3s ease-in-out infinite}@keyframes rise{0%,100%{height:28%}50%{height:92%}}
.rise2{animation:rise 3s ease-in-out .4s infinite}
.rise3{animation:rise 3s ease-in-out .8s infinite}
.baclip{animation:baclip 5s ease-in-out infinite}@keyframes baclip{0%,14%{clip-path:inset(0 0 0 0)}50%,64%{clip-path:inset(0 100% 0 0)}100%{clip-path:inset(0 0 0 0)}}
.badiv{animation:badiv 5s ease-in-out infinite}@keyframes badiv{0%,14%{left:100%}50%,64%{left:0}100%{left:100%}}
/* 히어로 — 밝은 톤 유지 + 은은한 보라 그라데이션·도트 패턴(밋밋함 해소) */
.hero-bg{background:
 radial-gradient(60% 45% at 50% 0%,rgba(99,102,241,.10),transparent 70%),
 radial-gradient(40% 35% at 85% 20%,rgba(99,102,241,.06),transparent 70%),
 linear-gradient(180deg,#EEF2FF 0%,#FFFFFF 62%)}
.hero-dots{background-image:radial-gradient(rgba(99,102,241,.14) 1px,transparent 1px);background-size:22px 22px;
 -webkit-mask-image:linear-gradient(180deg,#000 0%,transparent 55%);mask-image:linear-gradient(180deg,#000 0%,transparent 55%)}
.card-hi{background:#F5F3FF;border:1px solid #DDD6FE;border-radius:16px}   /* 강조 카드(연보라) */
/* 무료 결과 확장(가로 레이아웃): 좁은 위젯 칸을 탈출해 뷰포트 기준 넓게(최대 1160px) 중앙 정렬 */
.result-expanded{width:100vw;margin-left:calc(50% - 50vw);padding:0 16px}
.result-inner{max-width:1160px;margin:0 auto}
/* 채널 카드 그리드: 데스크탑 auto-fit(화면 폭 따라 3~4열 자동), 모바일(<768px)은 가로 스와이프 유지 */
@media(min-width:768px){.tz-grid{display:grid !important;
 grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;align-items:stretch;overflow:visible}}
</style>"""

_HEAD = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>올린다 — 네이버 검색 상위노출에 유리한 AI 마케팅</title>
<meta name=description content="사진만 올리면 AI가 네이버 블로그·플레이스 상위노출에 유리한 글을 씁니다. 인스타·유튜브·릴스·X까지 자동 생성(네이버는 초안 반자동 발행). 소상공인 AI 마케팅 올린다.">
<meta name=keywords content="AI 마케팅,소상공인 마케팅,셀러 마케팅,인스타 자동 업로드,네이버 블로그 자동,유튜브 쇼츠 자동,콘텐츠 자동화,SNS 대행,쿠팡 마케팅,올린다,Ollinda">
<meta name=robots content="index,follow,max-image-preview:large,max-snippet:-1">
<meta name=naver-site-verification content="f47963c25c7d743ec0c7d363d552b5ba7440475a">
<meta name=google-site-verification content="FAbw24lXqzxXm0zs2IGBwr6QXZUFdvaXrhBLi8pygzA">
<meta name=author content="올린다 (Ollinda)">
<meta name=theme-color content="#6366F1">
<meta property=og:site_name content="올린다">
<meta property=og:locale content="ko_KR">
<meta property=og:type content=website>
<meta property=og:title content="올린다 — 네이버 검색 상위노출에 유리한 AI 마케팅">
<meta property=og:description content="사진만 올리면 네이버 상위노출에 유리한 글 + 5채널 콘텐츠. 소상공인 마케팅.">
<meta property=og:image content="__BASE__/demo/og.png">
<meta property=og:image:width content="1200">
<meta property=og:image:height content="630">
<meta property=og:url content="__BASE__/">
<meta name=twitter:card content=summary_large_image>
<meta name=twitter:image content="__BASE__/demo/og.png">
<link rel=canonical href="__BASE__/">
<link rel=icon href="/favicon.svg" type="image/svg+xml">
<link rel=icon href="/favicon.ico" sizes="any">
<link rel=apple-touch-icon href="/apple-touch-icon.png">
<link rel=manifest href="/static/manifest.webmanifest">
<link rel=preconnect href="https://cdn.jsdelivr.net" crossorigin>
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css" rel=stylesheet>
<link href="/static/landing.css" rel=stylesheet>
""".replace("__BASE__", BASE) + _STYLE

# head 마감 + body 시작 — GA처럼 <head> 안에 있어야 하는 스크립트는 _HEAD_META와 이 사이에 끼운다
_BODY_OPEN = """</head><body class="bg-white text-slate-800 overflow-x-hidden pb-20 sm:pb-0">"""
_HEAD_META = _HEAD
_HEAD = _HEAD_META + _BODY_OPEN

_FOOT = """
<script>
function omCopy(text){if(navigator.clipboard&&navigator.clipboard.writeText){return navigator.clipboard.writeText(text);}
 return new Promise(function(res,rej){var ta=document.createElement('textarea');ta.value=text;ta.setAttribute('readonly','');ta.style.position='fixed';ta.style.top='0';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();try{ta.setSelectionRange(0,text.length);}catch(e){}var ok=false;try{ok=document.execCommand('copy');}catch(e){}document.body.removeChild(ta);ok?res():rej();});}
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('show');io.unobserve(e.target)}}),{threshold:.12});
document.querySelectorAll('.reveal').forEach(el=>io.observe(el));
const cu=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){const el=e.target,t=+el.dataset.count;let n=0,st=Math.max(1,t/40);const id=setInterval(()=>{n+=st;if(n>=t){n=t;clearInterval(id)}el.textContent=Math.floor(n)},25);cu.unobserve(el)}}),{threshold:.5});
document.querySelectorAll('[data-count]').forEach(el=>cu.observe(el));
// 셀프 체험 위젯 + 스마트 입력(무료·유료 공용 헬퍼)
(function(){
 // 큰따옴표 포함(버그2-a): 추측 텍스트에 "가 있으면 value="…" 속성이 깨져 수정 입력란이 안 뜨던 원인
 const esc=s=>(s||'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
 // AI 선추측 확인(스마트 입력 PHASE 2) — 무료·유료 공용(대시보드에서도 사용).
 // onDone: 사용자가 응답(맞아요/수정 저장)한 순간 호출 — 무료 위젯의 생성버튼 활성화 트리거.
 // opts(vision-intent): {interp:해석, conf:'high|low', choices:[의도], learned:학습 기본값, iid:intent hidden id}
 // 원칙: 전략은 묻지 않는다 / 사실(이 사진이 무엇에 관한 것인가)은 모호하면 묻는다.
 window.intakeConfirmUI=function(box,guess,analysis,cid,vid,onDone,opts){
   opts=opts||{};
   var c=document.getElementById(cid),v=document.getElementById(vid),
       it=opts.iid?document.getElementById(opts.iid):null;
   if(v)v.value=analysis||'';
   if(!guess){box.innerHTML='';if(c)c.value='';return;}
   function confirmedLine(v){return '<div class="text-xs text-indigo-600 font-bold py-1 truncate cursor-pointer" '
     +'title="'+esc(v)+'" onclick="this.classList.toggle(&quot;truncate&quot;)">확인됨: '+esc(v)+'</div>';}
   function settle(val,intent){if(c)c.value=val;if(it)it.value=intent||'';
     box.innerHTML=confirmedLine(val);onDone&&onDone();}
   var choices=(opts.choices||[]).slice(0,3);
   // ③ 학습 기본값(3-2): 묻지 않고 "○○로 준비할게요 (변경)" 표시만 — 변경 탭하면 이지선다
   if(opts.learned&&choices.length){
     var lv=guess+' — '+opts.learned;
     if(c)c.value=lv;if(it)it.value=opts.learned;
     box.innerHTML='<div class="bg-[#EEF2FF] border border-indigo-100 rounded-xl px-3 py-2.5 text-sm">'
       +'<div class="text-slate-700 truncate" title="'+esc(guess)+'"><b>'+esc(opts.learned)+'</b>(으)로 준비할게요.'
       +' <button type="button" data-g="chg" class="text-xs text-indigo-500 underline font-semibold">변경</button></div></div>';
     box.querySelector('[data-g=chg]').onclick=function(){renderChoices();};
     onDone&&onDone();
     return;
   }
   // ② 저확신 이지선다(2-1·2-2): 사실 확인 — 이 사진이 어떤 이야기인지
   function renderChoices(){
     var h='<div class="bg-[#EEF2FF] border border-indigo-100 rounded-xl px-3 py-2.5 text-sm">'
       +'<div class="text-[11px] font-bold text-indigo-500 mb-0.5">확인해주세요</div>'
       +'<div class="text-slate-700">이 사진, <b>'+esc(guess)+'</b>(으)로 보여요. 어떤 이야기인가요?</div>'
       +'<div class="flex flex-wrap gap-2 mt-2">';
     choices.forEach(function(ch,i){h+='<button type="button" data-ci="'+i+'" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold">'+esc(ch)+'</button>';});
     h+='<button type="button" data-g="fix" class="px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 text-xs font-bold">직접 입력</button></div></div>';
     box.innerHTML=h;
     box.querySelectorAll('[data-ci]').forEach(function(b){b.onclick=function(){
       var ch=choices[+b.dataset.ci];settle(guess+' — '+ch,ch);};});
     box.querySelector('[data-g=fix]').onclick=fixFlow;
   }
   if(opts.conf==='low'&&choices.length){renderChoices();var _fx=1;}
   else{
     // ① 고확신(기존): 맞아요/수정할게요 — 해석은 확인을 거쳐야 사실이 됨
     var disp=guess+(opts.interp?(' — '+opts.interp):'');
     box.innerHTML='<div class="bg-[#EEF2FF] border border-indigo-100 rounded-xl px-3 py-2.5 text-sm">'
       +'<div class="text-[11px] font-bold text-indigo-500 mb-0.5">확인해주세요</div>'
       +'<div class="text-slate-700">이 사진, <b>'+esc(disp)+'</b>(으)로 보여요. 맞나요?</div>'
       +'<div class="flex gap-2 mt-2"><button type="button" data-g="ok" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold">맞아요</button>'
       +'<button type="button" data-g="fix" class="px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 text-xs font-bold">수정할게요</button></div></div>';
     box.querySelector('[data-g=ok]').onclick=function(){settle(disp,opts.interp||'');};
     box.querySelector('[data-g=fix]').onclick=fixFlow;
   }
   function fixFlow(){
     // DOM으로 직접 조립(버그2-a 재발 방지) — 추측 텍스트에 어떤 특수문자가 있어도 입력란이 항상 뜬다
     box.innerHTML='';
     var row=document.createElement('div');row.className='flex gap-2';
     var inp=document.createElement('input');inp.id=cid+'_edit';
     inp.className='flex-1 min-w-0 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400';
     inp.placeholder='사진 속 내용을 직접 알려주세요 (예: 네잎클로버 키링, 자개 소재)';
     inp.value=guess;
     var sv=document.createElement('button');sv.type='button';sv.textContent='저장';
     sv.className='px-3 rounded-xl bg-indigo-600 text-white text-xs font-bold';
     function save(){var nv=inp.value.trim();
       if(c)c.value=nv;box.innerHTML=nv?confirmedLine(nv):'';onDone&&onDone();}
     sv.onclick=save;
     inp.addEventListener('keydown',function(e){if(e.key==='Enter'){e.preventDefault();save();}});
     row.appendChild(inp);row.appendChild(sv);box.appendChild(row);
     // 미저장 경로 명시: 저장 안 하면 원래 AI 추측을 그대로 쓰고 진행
     var skip=document.createElement('button');skip.type='button';
     skip.className='block mt-1.5 text-[11px] text-slate-400 underline';
     skip.textContent='수정 없이 AI 추측 그대로 진행';
     skip.onclick=function(){if(c)c.value=guess;box.innerHTML=confirmedLine(guess);onDone&&onDone();};
     box.appendChild(skip);
     inp.focus();};
 };
 // 업종별 스마트 질문 렌더(PHASE 3) — 무료·유료 공용. 답은 window.__intakeAnswers에 수집.
 // hint: 사진 추측 텍스트(버그2 — 상호명 입력 시 서버가 업종 추론). 재렌더 시 기존 답변 보존.
 window.__intakeAnswers={};
 window.intakeQuestionsUI=async function(box,industry,bizType,purpose,expId,hint){
   if(!box)return;
   if(!(industry||'').trim()){box.innerHTML='';return;}
   try{
     var r=await fetch('/api/intake/questions?industry='+encodeURIComponent(industry)+'&biz_type='+(bizType||'local')+'&purpose='+encodeURIComponent(purpose||'')+'&hint='+encodeURIComponent(hint||''));
     var d=await r.json();var qs=d.questions||[];if(!qs.length){box.innerHTML='';return;}
     var prev=window.__intakeAnswers||{};                       // 질문 교체 시 답변 유지(초기화 금지)
     var oldExp=(document.getElementById(expId)||{}).value||'';
     // 기본 접힘(컴팩트) — 첫 화면엔 사진·업종·목적·버튼만. 선택 입력이라 원하면 펼침.
     var h='<details class="bg-slate-50 border border-slate-200 rounded-xl p-3"><summary class="text-xs font-bold text-slate-600 cursor-pointer select-none">'
       +'더 좋은 글 만들기 <span class="text-slate-400 font-normal">('+esc(d.hint||'선택')+')</span></summary>'
       +'<div class="mt-2 grid grid-cols-2 gap-2 items-end">';   // 질문 2×2, 셀 하단 정렬(높이 통일)
     qs.forEach(function(q,i){
       // 라벨 1줄 고정(truncate, 전체는 title) — 2줄 넘침으로 그리드 지저분해지는 것 방지
       h+='<div><div class="text-xs font-semibold text-slate-600 mb-1 truncate" title="'+esc(q.q)+'">'+esc(q.q)+'</div>';
       if(q.type==='choice'){h+='<div class="flex flex-wrap gap-1.5">'+(q.options||[]).map(function(o){
         var on=((prev[q.id]||'').split(', ').indexOf(o)>=0);
         return '<button type="button" data-iq="'+esc(q.id)+'" data-v="'+esc(o)+'" class="iq-opt px-2 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 text-xs font-semibold'+(on?' ring-2 ring-indigo-400 bg-indigo-50':'')+'">'+esc(o)+'</button>';}).join('')+'</div>';}
       else{h+='<input data-iqt="'+esc(q.id)+'" value="'+esc(prev[q.id]||'')+'" placeholder="'+esc(q.ph||'')+'" class="w-full rounded-lg border border-slate-200 px-2.5 py-2 text-sm outline-none focus:border-indigo-400">';}
       h+='</div>';});
     var ex=d.experience||{};
     h+='<div class="col-span-2"><div class="text-xs font-semibold text-indigo-600 mb-1 truncate" title="'+esc(ex.q||'')+'">'+esc(ex.q||'')+'</div>'
       +'<input id="'+expId+'" value="'+esc(oldExp)+'" placeholder="'+esc(ex.ph||'')+'" class="w-full rounded-lg border border-indigo-200 px-2.5 py-2 text-sm outline-none focus:border-indigo-400"></div>';
     h+='</div></details>';
     box.innerHTML=h;window.__intakeAnswers=prev;
     box.querySelectorAll('.iq-opt').forEach(function(b){b.onclick=function(){
       var k=b.dataset.iq;var on=b.classList.toggle('ring-2');b.classList.toggle('ring-indigo-400');b.classList.toggle('bg-indigo-50');
       var cur=(window.__intakeAnswers[k]||'').split(', ').filter(Boolean);
       if(on)cur.push(b.dataset.v);else cur=cur.filter(function(x){return x!==b.dataset.v;});
       window.__intakeAnswers[k]=cur.join(', ');};});
     box.querySelectorAll('[data-iqt]').forEach(function(inp){inp.oninput=function(){window.__intakeAnswers[inp.dataset.iqt]=inp.value;};});
   }catch(e){box.innerHTML='';}
 };
 const df=document.getElementById('demoForm');if(!df)return;
 const pf=document.getElementById('d_photo');
 // 질문 갱신(버그2) — 업종 입력/사진 추측 변경 시 hint(사진 추측) 포함해 재조회
 window.demoQs=function(){window.intakeQuestionsUI&&intakeQuestionsUI(
   document.getElementById('d_questions'),
   (document.getElementById('d_ind')||{}).value||'',
   (document.querySelector('input[name=d_biz]:checked')||{}).value||'local',
   (document.getElementById('d_purpose')||{}).value||'',
   'd_exp', window.__indGuess||'');};
 // 생성버튼 가드(버그2) — 사진 확인 응답 전엔 비활성 + 이유 문구. 실패·타임아웃 시 건너뛰기 폴백.
 function setDemoReady(ok,msg){var b=document.getElementById('d_submit'),h=document.getElementById('d_submit_hint');
   if(b)b.disabled=!ok;if(h){h.textContent=msg||'';h.classList.toggle('hidden',!msg);}}
 function demoSkipLink(box){var s=document.createElement('button');s.type='button';
   s.className='block mx-auto mt-1.5 text-[11px] text-slate-400 underline';s.textContent='확인 건너뛰고 진행';
   s.onclick=function(){box.innerHTML='';setDemoReady(true,'');};box.appendChild(s);}
 var _gseq=0;
 async function demoGuess(){var files=pf&&pf.files;
   var box=document.getElementById('d_guessbox');if(!box)return;
   if(!files||!files.length){box.innerHTML='';setDemoReady(true,'');return;}
   var seq=++_gseq,fin=false;
   setDemoReady(false,'사진을 확인하는 중이에요 — 잠시만요');
   // 진행률(버그1) — 단계 라벨 + 애니메이션 바 → 완료 시 '확인해주세요' 카드로 전환
   box.innerHTML='<div class="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5">'
     +'<div id="d_gpg_l" class="text-xs font-bold text-slate-600 mb-1.5">사진 분석 중…</div>'
     +'<div class="w-full h-1.5 bg-slate-200 rounded-full overflow-hidden"><div id="d_gpg_b" class="h-full bg-indigo-500 rounded-full" style="width:15%;transition:width .5s"></div></div></div>';
   var stages=['사진 분석 중…','무엇이 담겼는지 파악 중…','거의 다 됐어요…'],si=0,w=15;
   var st=setInterval(function(){var l=document.getElementById('d_gpg_l'),b=document.getElementById('d_gpg_b');
     if(!l||!b){clearInterval(st);return;}si=Math.min(si+1,stages.length-1);w=Math.min(w+22,90);
     l.textContent=stages[si];b.style.width=w+'%';},2200);
   // 타임아웃(버그1): 장수 비례(25s + 4s/장, 최대 45s) — 다중 사진 업로드·분석 현실 반영
   var n=Math.min(files.length,8),tmo=Math.min(45000,25000+4000*n);
   var to=setTimeout(function(){if(fin||seq!==_gseq)return;fin=true;clearInterval(st);
     box.innerHTML='';setDemoReady(true,'사진 확인이 오래 걸려 건너뛰었어요 — 바로 만들 수 있어요');},tmo);
   var fd=new FormData();fd.append('industry',(document.getElementById('d_ind')||{}).value||'');
   // 업로드 가속(버그1): 전송 전 1280px JPEG로 축소(모바일 수 MB 원본 → 수백 KB). 실패 시 원본.
   async function shrink(f){try{if(!/^image\\//.test(f.type||''))return f;
     var bmp=await createImageBitmap(f);var mx=Math.max(bmp.width,bmp.height);
     if(mx<=1280&&f.size<1500000)return f;
     var s=Math.min(1,1280/mx),cv=document.createElement('canvas');
     cv.width=Math.round(bmp.width*s);cv.height=Math.round(bmp.height*s);
     cv.getContext('2d').drawImage(bmp,0,0,cv.width,cv.height);
     var b=await new Promise(function(r){cv.toBlob(r,'image/jpeg',0.85);});
     return b?new File([b],(f.name||'p').replace(/\\.[^.]+$/,'')+'.jpg',{type:'image/jpeg'}):f;
   }catch(e){return f;}}
   var small=await Promise.all(Array.from(files).slice(0,30).map(shrink));
   if(fin||seq!==_gseq)return;
   small.forEach(function(f){fd.append('photos',f);});
   try{var r=await fetch('/api/intake/guess',{method:'POST',body:fd});var d=await r.json();
     if(fin||seq!==_gseq)return;fin=true;clearTimeout(to);clearInterval(st);
     window.__indGuess=(d.industry_guess||d.guess||'');
     if(d.industry_guess)window.demoQs&&demoQs();   // 사진이 알려준 업종으로 질문 갱신(상호명 입력 커버)
     if(d.guess){
       window.intakeConfirmUI(box,d.guess,d.analysis||'','d_confirmed','d_vision',
         function(){setDemoReady(true,'');},
         {interp:d.interpretation||'',conf:d.confidence||'',choices:d.choices||[],learned:d.learned_intent||''});
       demoSkipLink(box);
       setDemoReady(false,'위 사진 확인(맞아요/수정) 후 만들 수 있어요');
     }else{box.innerHTML='';setDemoReady(true,'');}
   }catch(e){if(fin||seq!==_gseq)return;fin=true;clearTimeout(to);clearInterval(st);
     box.innerHTML='';setDemoReady(true,'');}}
 // 사진 관리(개선2) — 개별 삭제(×)·추가(+)·장수 실시간 갱신. 0장이면 초기 상태로.
 // 분석 전 동의(개선1) — 올리자마자 자동 분석하지 않음. 사용자가 사진 정리를 끝내고
 // '분석 시작'을 눌러야 vision 실행(잘못 올린 사진에 비용·시간 낭비 방지). 안 눌러도 만들기는 가능.
 function dpChanged(){var box=document.getElementById('d_guessbox');if(!box||!DP.length)return;
   var c=document.getElementById('d_confirmed'),v=document.getElementById('d_vision');
   if(c)c.value='';if(v)v.value='';_gseq++;setDemoReady(true,'');   // 목록 바뀜 → 이전 분석·확인 무효화
   box.innerHTML='<div class="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 text-sm">'
     +'<div class="text-slate-700">사진 <b>'+DP.length+'장</b> 준비됐어요. <b>3초 뒤 자동으로 AI 확인</b>을 시작해요 — 사진을 정리하면 다시 미뤄져요.</div>'
     +'<div class="flex items-center gap-2 mt-2">'
     +'<button type="button" id="d_gstart" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold">지금 바로 시작</button>'
     +'<span class="text-[11px] text-slate-400">안 해도 바로 만들 수 있어요</span></div></div>';
   document.getElementById('d_gstart').onclick=function(){demoGuess();};
   // 분석 자동 시작(3초 디바운스) — 목록이 바뀌면 _gseq가 올라 예약 무효
   var _das=_gseq;setTimeout(function(){if(_das===_gseq&&DP.length)demoGuess();},3000);}
 var DP=[];
 function dpSync(){try{var dt=new DataTransfer();DP.forEach(function(f){dt.items.add(f);});pf.files=dt.files;}catch(e){}}
 function dpReset(){var gb=document.getElementById('d_guessbox');if(gb)gb.innerHTML='';
   var c=document.getElementById('d_confirmed'),v=document.getElementById('d_vision');
   if(c)c.value='';if(v)v.value='';_gseq++;setDemoReady(true,'');}
 // 버그1: 0장=점선 박스(d_photobox) ↔ 1장+=박스 자체가 썸네일 그리드(d_preview)로 전환 — 분리 영역 없음
 function dpRender(){var pv=document.getElementById('d_preview');if(!pv)return;
   var pb=document.getElementById('d_photobox');
   var nm=document.getElementById('d_photoname');if(nm)nm.textContent=DP.length?('✓ '+DP.length+'장 선택'):'';
   pv.innerHTML='';
   if(!DP.length){pv.classList.add('hidden');if(pb)pb.classList.remove('hidden');dpReset();return;}
   pv.classList.remove('hidden');if(pb)pb.classList.add('hidden');
   DP.slice(0,10).forEach(function(f,i){var w=document.createElement('div');w.className='relative';
     var im=document.createElement('img');im.src=URL.createObjectURL(f);im.className='w-full aspect-square object-cover rounded-lg';w.appendChild(im);
     var x=document.createElement('button');x.type='button';x.setAttribute('aria-label','사진 삭제');x.textContent='×';
     x.className='absolute top-1 right-1 w-5 h-5 rounded-full bg-slate-700/80 text-white text-xs leading-none flex items-center justify-center';
     x.onclick=function(){DP.splice(i,1);dpSync();dpRender();if(DP.length)dpChanged();};
     w.appendChild(x);pv.appendChild(w);});
   var add=document.createElement('button');add.type='button';add.onclick=function(){pf.click();};
   add.className='w-full aspect-square rounded-lg border-2 border-dashed border-slate-300 text-slate-400 text-2xl flex items-center justify-center';
   add.textContent='＋';add.setAttribute('aria-label','사진 추가');pv.appendChild(add);}
 if(pf)pf.addEventListener('change',function(){Array.from(pf.files||[]).forEach(function(f){DP.push(f);});
   dpSync();dpRender();if(DP.length)dpChanged();});
 df.addEventListener('submit',async e=>{e.preventDefault();
  var sb=document.getElementById('d_submit');if(sb&&sb.disabled)return;   // 확인 전 Enter 제출 방지
  const box=document.getElementById('demoResult');
  const ind=document.getElementById('d_ind').value.trim();
  if(!ind){box.innerHTML='<div class="text-slate-500 text-sm text-center py-3">업종/상품을 입력해주세요.</div>';return;}
  box.innerHTML='<div class="card p-5">'
    +'<div id="pgLabel" class="text-slate-800 font-bold text-sm text-center mb-3">마케팅 전략가가 분석 중…</div>'
    +'<div class="w-full h-2.5 bg-slate-100 rounded-full overflow-hidden"><div id="pgBar" class="h-full bg-indigo-500" style="width:0%;transition:width .4s"></div></div>'
    +'<div id="pgPct" class="text-slate-400 text-xs text-center mt-1">0%</div></div>';
  var _st=[[0,'마케팅 전략가가 분석 중…'],[25,'카피라이터가 글 쓰는 중…'],[55,'SEO 편집장이 다듬는 중…'],[80,'영상 감독이 마무리 중…']];
  var _pct=0;var _pg=setInterval(function(){_pct=Math.min(_pct+(_pct<70?2:0.5),95);var b=document.getElementById('pgBar');if(!b){clearInterval(_pg);return;}b.style.width=_pct+'%';document.getElementById('pgPct').textContent=Math.round(_pct)+'%';var l=_st[0][1];_st.forEach(function(s){if(_pct>=s[0])l=s[1];});document.getElementById('pgLabel').textContent=l;},500);
  const biz=(document.querySelector('input[name="d_biz"]:checked')||{}).value||'local';
  const fd=new FormData();fd.append('industry',ind);fd.append('biz_type',biz);
  fd.append('purpose',(document.getElementById('d_purpose')||{}).value||'');
  fd.append('target_kw',(document.getElementById('d_target_kw')||{}).value||'');
  fd.append('target_vol',(document.getElementById('d_target_vol')||{}).value||'');
  fd.append('confirmed',(document.getElementById('d_confirmed')||{}).value||'');
  fd.append('vision_analysis',(document.getElementById('d_vision')||{}).value||'');
  fd.append('answers',JSON.stringify(window.__intakeAnswers||{}));
  fd.append('experience',(document.getElementById('d_exp')||{}).value||'');
  if(pf&&pf.files)Array.from(pf.files).slice(0,10).forEach(function(f){fd.append('photos',f);});
  // 결과 렌더 — innerHTML 주입 script는 실행되지 않으므로(버그1 원인②) 재생성해 실행
  function runScripts(el){el.querySelectorAll('script').forEach(function(s){
    var n=document.createElement('script');n.textContent=s.textContent;s.replaceWith(n);});}
  function renderTeaser(html){clearInterval(_pg);
    box.classList.add('result-expanded');   // 결과는 위젯 폭 탈출 → 화면 가로 활용(최대 1160px 중앙)
    box.innerHTML='<div class="result-inner">'+html+'</div>';
    runScripts(box);box.scrollIntoView({behavior:'smooth',block:'nearest'});}
  function renderFail(msg){clearInterval(_pg);
    box.innerHTML='<div class="card p-4 text-center"><div class="text-sm font-bold text-slate-700 mb-1">'+esc(msg||'생성에 문제가 있었어요')+'</div>'
      +'<div class="text-xs text-slate-400">잠시 후 아래 버튼으로 다시 만들어보세요.</div>'
      +'<button type="button" onclick="document.getElementById(\\'demoForm\\').requestSubmit()" class="mt-3 px-4 py-2 rounded-xl bg-indigo-600 text-white text-xs font-bold">다시 시도</button></div>';}
  try{const r=await fetch('/api/demo',{method:'POST',body:fd});const d=await r.json();
   if(d.job){ // 백그라운드 생성(버그1: CF 100초 타임아웃 회피) → 폴링, 진행바는 계속
     var tries=0;var pv=setInterval(async function(){tries++;
       if(tries>100){clearInterval(pv);renderFail('생성이 너무 오래 걸려요');return;}
       try{var rr=await fetch('/api/demo/result/'+d.job);var dd=await rr.json();
         if(dd.ready&&dd.teaser_html){clearInterval(pv);
           var _b2=document.getElementById('pgBar');if(_b2)_b2.style.width='100%';
           setTimeout(function(){renderTeaser(dd.teaser_html);},300);}
         else if(dd.error){clearInterval(pv);renderFail(dd.error);}
       }catch(e){}},3000);
     return;}
   clearInterval(_pg);var _b=document.getElementById('pgBar');if(_b)_b.style.width='100%';
   if(d.teaser){renderTeaser(d.teaser_html);return;}
   if(d.go_dashboard){window.location.href='/me';return;}
   // 한도 소진(reason=ip_limit): '말없이 가입으로 점프' 금지 — 왜 생성이 안 되는지 먼저 명확히 안내
   if(d.require_signup&&d.reason==='ip_limit'){
     box.innerHTML='<div class="card p-5 text-center">'
      +'<div class="inline-block bg-amber-50 text-amber-700 text-[11px] font-bold px-2.5 py-1 rounded-full mb-2">안내</div>'
      +'<p class="text-slate-900 font-extrabold text-base mb-1">무료 미리보기 2회를 모두 사용하셨어요</p>'
      +'<p class="text-slate-500 text-xs mb-4">이 기기(네트워크)에서 이미 2번 만들어보셨어요. 가입하면 <b class="text-slate-700">무료 2회가 새로</b> 생기고 5채널 전부 + 영상까지 열려요.</p>'
      +'<a href="/login/kakao" class="block py-3 rounded-xl font-extrabold mb-2" style="background:#FEE500;color:#191600">카카오로 3초 가입</a>'
      +'<a href="/login/google" class="block py-3 rounded-xl font-bold bg-white border border-slate-200 text-slate-700">구글로 가입</a></div>';
     box.scrollIntoView({behavior:'smooth',block:'nearest'});return;}
   let cta;
   if(d.limit){cta='<a href="#pricing" class="block py-3 rounded-xl font-bold bg-indigo-600 text-white">요금제 보기 →</a>';}
   else{cta='<a href="/login/kakao" class="block py-3 rounded-xl font-extrabold mb-2" style="background:#FEE500;color:#191600">카카오로 3초 가입</a>'
        +'<a href="/login/google" class="block py-3 rounded-xl font-bold bg-white border border-slate-200 text-slate-700">구글로 가입</a>';}
   box.innerHTML='<div class="card p-5 text-center">'
    +'<p class="text-slate-900 font-bold mb-1">'+esc(d.message||'가입하면 바로 만들어드려요!')+'</p>'
    +'<p class="text-slate-500 text-xs mb-4">가입 후 \\'내 작업실\\'에서 사진을 올리면 5채널이 자동 생성됩니다.</p>'
    +cta+'</div>';
   box.scrollIntoView({behavior:'smooth',block:'nearest'});
  }catch(err){renderFail('생성 요청에 문제가 있었어요');}
 });})();
// 문의 폼
(function(){const cf=document.getElementById('contactForm');if(!cf)return;
 cf.addEventListener('submit',async e=>{e.preventDefault();const fd=new FormData(cf);
  const btn=cf.querySelector('button');btn.textContent='보내는 중…';
  try{const r=await fetch('/api/contact',{method:'POST',body:fd});const d=await r.json();
   document.getElementById('contactMsg').textContent=d.ok?'문의가 접수되었습니다. 곧 연락드릴게요!':(d.error||'전송 실패');
   if(d.ok)cf.reset();}catch(e){document.getElementById('contactMsg').textContent='전송 실패';}
  btn.textContent='문의하기';});})();
</script></body></html>"""


def _nav() -> str:
    return f"""
<header class="sticky top-0 z-40 bg-white/90 backdrop-blur-md border-b border-slate-100">
 <div class="max-w-6xl mx-auto px-5 h-16 flex items-center justify-between">
  <a href="/" class="flex items-center gap-2 font-extrabold text-xl text-slate-900">{LOGO}<span>올린다</span></a>
  <nav class="hidden md:flex items-center gap-6 text-sm text-slate-500 font-medium">
   <a href="#video" class="hover:text-slate-900">작동 영상</a>
   <a href="#results" class="hover:text-slate-900">성과</a>
   <a href="#features" class="hover:text-slate-900">기능</a>
   <a href="#pricing" class="hover:text-slate-900">요금</a>
   <a href="#contact" class="hover:text-slate-900">문의</a></nav>
  <div class="flex items-center gap-2">
   <a href="/me" class="px-4 py-2 rounded-xl text-sm font-bold text-white bg-indigo-600 hover:bg-indigo-700 transition">내 작업실 →</a></div>
 </div></header>"""


# 구글 G 로고(공식 4색) — 히어로·CTA 버튼 공용
_GOOGLE_G = ('<svg width="20" height="20" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>')


# URL 경로 → 저장소 실파일 (버전 파라미터 산출용)
_ASSET_FILES = {
    "/docs/guide.pdf": ("assets", "docs", "ollinda_guide.pdf"),
    "/docs/intro.mp4": ("assets", "docs", "ollinda_intro.mp4"),
    "/demo/local_short.mp4": ("app", "static", "demo", "local_short.mp4"),
    "/demo/short_poster.jpg": ("app", "static", "demo", "short_poster.jpg"),
}


def _v(url_path: str) -> str:
    """미디어 자산 캐시 무효화 — 파일이 바뀌면 주소도 바뀐다(?v=수정시각).
    2026-08-09 실사고: 소개 영상을 교체했는데 브라우저가 같은 주소의 옛 파일을 캐시로 재생."""
    parts = _ASSET_FILES.get(url_path)
    if not parts:
        return url_path
    p = os.path.join(os.path.dirname(__file__), "..", *parts)
    try:
        return f"{url_path}?v={int(os.path.getmtime(p))}"
    except OSError:
        return url_path


def _naver_login_available() -> bool:
    from app import naver_auth
    return naver_auth.configured()


def _naver_hero_btn() -> str:
    """히어로 네이버 버튼 — 개발자센터 키 설정 시에만 노출(미설정=미노출, 허위 버튼 금지)."""
    if not _naver_login_available():
        return ""
    return ('<a href="/login/naver" class="flex items-center justify-center px-10 py-4 rounded-2xl '
            'font-extrabold text-lg text-white w-full sm:w-auto" style="background:#03C75A">'
            '<span class="font-black mr-1.5">N</span>네이버로 무료 시작</a>')


def _naver_cta_btn() -> str:
    if not _naver_login_available():
        return ""
    return ('<a href="/login/naver" class="flex items-center justify-center px-9 py-4 rounded-2xl '
            'font-extrabold text-lg text-white" style="background:#03C75A">'
            '<span class="font-black mr-1.5">N</span>네이버로 시작하기</a>')


def _hero() -> str:
    return f"""
<section class="relative hero-bg overflow-hidden">
 <div class="hero-dots absolute inset-x-0 top-0 h-96 pointer-events-none"></div>
 <div class="relative max-w-6xl mx-auto px-5 pt-20 pb-16 text-center">
  <!-- ★ 2026-08-14 타깃·메시지 재조정(조사 근거).
       조사: 자영업자 10명 중 6명 이상이 '마케팅 지출 대비 효과를 체감 못 함'(2024).
       상처는 '성장 실패'가 아니라 **확인 불가**다. 대행의 고질적 불만도 '깜깜이'다.
       그래서 약속(늘려드립니다)이 아니라 계기판(확인해드립니다)으로 말한다 —
       손실 회피가 이익 추구를 이기고, 통제감 상실이 가장 큰 불만이기 때문이다.
       타깃도 실측대로 좁힌다: 서이추 138건에서 서로이웃 차단율이 카센터·정비 0%,
       디테일링 22%인 반면 썬팅은 64%였다. 문이 열린 정비계열을 앞에 세운다. -->
  <div class="reveal inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white border border-indigo-100 text-xs font-semibold text-indigo-600 mb-8">
   카센터 · 정비 · 디테일링 · 썬팅 · 중고차 사장님을 위해</div>
  <h1 class="reveal text-4xl sm:text-6xl font-bold tracking-tight leading-[1.12] text-slate-900">
   광고비 쓰신 만큼,<br><span class="text-indigo-600">효과 있었는지 아세요?</span></h1>
  <p class="reveal mt-7 text-lg text-slate-500 max-w-2xl mx-auto">올린다는 사진만 올리면 글과 영상을 만들고,
   <b class="text-slate-800">그 글이 지금 검색 몇 위인지 매일 확인해</b> 알려드립니다.
   <b class="text-slate-800">떨어지면 고친 글을 먼저</b> 가져와요.</p>

  <!-- ★ 2026-08-13 사장님 지시: 증거를 첫 화면으로.
       예전엔 여기가 C-Rank·D.I.A.+·PAS 같은 만드는 사람 말이었고, 실측 1위 사례는
       스크롤 한참 아래(79번째 문단)에 있었다. 방문 40명 중 버튼을 누른 사람이 0명이었다.
       사장님이 가장 먼저 묻는 것은 "진짜 되나?"다 — 설명이 아니라 결과가 믿음을 만든다. -->
  <div class="reveal mt-8 inline-flex flex-wrap items-center justify-center gap-x-3 gap-y-2
              bg-white border-2 border-indigo-200 rounded-2xl px-5 py-4 text-left shadow-sm">
   <div class="text-sm text-slate-500">‘부산 동구 썬팅업체’ 검색</div>
   <div class="flex items-center gap-2 text-sm font-bold text-slate-700">
    <span class="text-slate-400">7/31 발행</span>
    <span class="text-slate-300">→</span><span>8/2 12위</span>
    <span class="text-slate-300">→</span>
    <span class="text-indigo-600 text-xl font-extrabold">8/9 1위</span></div>
   <div class="w-full text-xs text-slate-400">실제 이용 가게 · 2026년 8월 실측 · 개별 결과는 가게·키워드에 따라 달라요</div>
  </div>

  <!-- ★ 2026-08-14 사장님 지시: 첫 화면에 상호 입력칸.
       스크롤하기 전에 '내 가게'를 넣게 한다 — 남의 사례를 읽는 것과 내 가게 이름이
       결과에 뜨는 것은 다른 일이다.
       ★ 진단 경로는 하나만 쓴다: 여기서 값만 받아 _try의 rankCheck()로 넘긴다.
         입력·판정 로직을 두 벌로 만들면 그게 다음 사고 예약이다(canonical 원칙). -->
  <div class="reveal mt-9 max-w-lg mx-auto">
   <form onsubmit="heroCheck();return false;" class="flex flex-col sm:flex-row gap-2">
    <input id="hero_name" placeholder="상호를 입력하세요 (예: 초량 루마썬팅)" autocomplete="organization"
     class="w-full sm:flex-1 min-w-0 rounded-2xl border-2 border-indigo-200 px-4 py-3.5 text-slate-800 text-base outline-none focus:border-indigo-500 shadow-sm">
    <button type="submit"
     class="w-full sm:w-auto px-7 py-3.5 rounded-2xl font-extrabold text-base bg-indigo-600 hover:bg-indigo-700 text-white transition shadow-lg shadow-indigo-200 whitespace-nowrap">
     내 가게 확인하기</button></form>
   <p class="text-sm text-slate-400 mt-3">가입 없이 · 카드 없이 · <b class="text-slate-600">지금 몇 위인지</b>
    바로 확인해드려요</p>
  </div>
  <script>
  function heroCheck(){{
   var v=(document.getElementById('hero_name')||{{}}).value||'';
   v=v.trim();
   var n=document.getElementById('rc_name');
   if(n) n.value=v;                       // 값만 넘긴다 — 판정은 _try의 rankCheck() 하나뿐
   var t=document.getElementById('try');
   if(t) t.scrollIntoView({{behavior:'smooth',block:'start'}});
   if(!v){{ if(n) setTimeout(function(){{n.focus();}},400); return; }}
   setTimeout(function(){{ if(typeof rankCheck==='function') rankCheck(); }},450);
  }}
  </script>

  <!-- ★ 2026-08-13 사장님 지적: 처음엔 '3초 만에 결과 보기'라고 썼다. 실측하니 126초였고,
       보여주는 것도 완성본이 아니라 블로그 글 도입부였다. 날조 배지 2건을 지운 자리에
       내가 세 번째 거짓말을 넣은 셈이다. 걸리는 시간과 보여주는 범위를 그대로 적는다. -->
  <div class="reveal mt-6 flex flex-col items-center gap-3">
   <p class="text-xs text-slate-400">사진까지 올리면 <b class="text-slate-500">블로그 글 도입부</b>도 만들어 드려요 ·
    약 2분 · 완성본과 영상은 가입 후 무료 2회</p>
   <div class="flex flex-col sm:flex-row gap-2 items-center mt-2">
    <span class="text-sm text-slate-400">이미 마음 정하셨다면</span>
    <a href="/login/kakao" class="flex items-center justify-center px-6 py-2.5 rounded-xl font-bold text-sm" style="background:#FEE500;color:#191600">카카오로 무료 시작</a>{_naver_hero_btn()}
    <a href="/login/google" class="flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl font-bold text-sm bg-white border border-slate-200 text-slate-700">{_GOOGLE_G} 구글로 시작</a></div>
  </div>
  <p class="reveal mt-4 text-sm text-slate-500">이미 회원이면 <a href="/login" class="inline-block px-1 py-1 text-indigo-600 font-bold underline underline-offset-4">회원 로그인</a></p>
  <!-- ★ 2026-08-13: 순위진단·무료체험 위젯은 두 번째 화면(_try)으로 옮겼다.
       첫 화면은 '무슨 일이 일어나는지'만 보여준다 — 입력칸 여섯 개가 첫 화면에 있으면
       읽기도 전에 일을 시키는 꼴이라 사장님이 그냥 나간다. -->
  <script>
  function fillDemo(){{var v=document.getElementById('rc_ind').value.trim();var d=document.getElementById('d_ind');
   if(d&&v&&!d.value)d.value=v;
   var top=window.__rcTop||{{}};var tk=document.getElementById('d_target_kw'),tv=document.getElementById('d_target_vol');
   if(tk)tk.value=top.kw||'';if(tv)tv.value=top.vol||'';
   var hint=document.getElementById('d_target_hint');
   if(hint){{if(top.kw){{hint.textContent="목표: 미노출 키워드 '"+top.kw+"'"+(top.vol?(' (월 '+top.vol.toLocaleString()+'회 검색)'):'')+" 잡는 글";hint.classList.remove('hidden');}}else{{hint.classList.add('hidden');}}}}
   var t=document.getElementById('herodemo');if(t)t.scrollIntoView({{behavior:'smooth',block:'center'}});
   if(d)d.focus();}}
  function rcSetMode(m){{window.__rcMode=m;var seller=(m==='seller');
   var r=document.getElementById('rc_region'),i=document.getElementById('rc_ind'),n=document.getElementById('rc_name');
   var bl=document.getElementById('rc_mlocal'),bs=document.getElementById('rc_mseller'),sub=document.getElementById('rc_sub');
   var on=['border-indigo-500','bg-indigo-50','text-indigo-700'],off=['border-slate-200','bg-white','text-slate-500'];
   [bl,bs].forEach(function(b,idx){{var act=(idx===1)===seller;
     on.forEach(function(c){{b.classList.toggle(c,act);}});off.forEach(function(c){{b.classList.toggle(c,!act);}});}});
   r.classList.toggle('hidden',seller);
   i.placeholder=seller?'상품 키워드(블루투스 이어폰)':'업종';
   n.placeholder=seller?'스토어/브랜드명':'상호';
   sub.textContent=seller?'상품 키워드·스토어명만 — 네이버 쇼핑 현재 순위를 바로 확인':'지역·업종·상호만 — 네이버 현재 순위를 바로 확인';}}
  // ★ 동명 가게 구분(2026-08-12): 상호로 후보를 먼저 찾아 주소를 보여주고 사용자가 고른다.
  //   남의 가게 순위를 내 순위로 보고하는 허위 양성을 막는 장치.
  async function rankCheck(){{
   var pick=document.getElementById('rc_pick');
   if(window.__rcMode!=='seller' && !window.__rcAddr && !window.__rcPicked){{
     var nm=document.getElementById('rc_name').value.trim();
     if(nm.length>=2){{
       var o0=document.getElementById('rc_out');o0.textContent='가게 찾는 중…';
       try{{
         var cf=new FormData();cf.append('name',nm);cf.append('region',document.getElementById('rc_region').value);
         var cr=await (await fetch('/api/store-candidates',{{method:'POST',body:cf}})).json();
         var cs=(cr.candidates||[]);
         if(cs.length>1){{                                   // 후보 2곳 이상 = 사용자가 골라야 한다
           o0.textContent='';
           var h='<div class="text-xs text-slate-500 mb-1.5">같은 이름의 가게가 여러 곳이에요. <b class="text-slate-700">내 가게를 골라주세요</b></div>';
           cs.forEach(function(c,i){{
             h+='<button type="button" onclick="rcPick('+i+')" class="block w-full text-left bg-white border border-slate-200 hover:border-indigo-400 rounded-xl px-3 py-2 mt-1.5 transition">'
               +'<div class="text-sm font-bold text-slate-800">'+c.name+'</div>'
               +'<div class="text-xs text-slate-400">'+(c.address||'')+(c.category?(' · '+c.category):'')+'</div></button>';
           }});
           h+='<button type="button" onclick="rcPick(-1)" class="block w-full text-center text-xs text-slate-400 mt-2 underline">내 가게가 없어요 — 그냥 진단하기</button>';
           pick.innerHTML=h;pick.classList.remove('hidden');window.__rcCands=cs;return;
         }}
         if(cs.length===1)window.__rcAddr=cs[0].address||'';   // 후보 1곳이면 그걸로 확정
       }}catch(e){{}}
     }}
   }}
   pick.classList.add('hidden');
   var o=document.getElementById('rc_out');o.textContent='조회 중…';
   var fd=new FormData();fd.append('region',document.getElementById('rc_region').value);
   fd.append('industry',document.getElementById('rc_ind').value);fd.append('name',document.getElementById('rc_name').value);
   if(window.__rcAddr)fd.append('addr',window.__rcAddr);
   if(window.__rcMode==='seller')fd.append('mode','seller');
   try{{var r=await fetch('/api/rank-check',{{method:'POST',body:fd}});var d=await r.json();
   if(d.error){{o.textContent=d.error;return;}}
   var rows='';
   (d.caught||[]).forEach(function(s){{rows+='<div class="flex justify-between bg-slate-50 rounded-lg px-3 py-1.5 mt-1.5"><span class="text-slate-700">'+s.keyword+'</span><span class="text-emerald-600 font-bold">'+s.rank+'위</span></div>';}});
   (d.missing||[]).forEach(function(s){{var v=s.volume?(' <span class="text-slate-400">월 '+s.volume.toLocaleString()+'회</span>'):'';rows+='<div class="flex justify-between bg-slate-50 rounded-lg px-3 py-1.5 mt-1.5"><span class="text-slate-500">'+s.keyword+v+'</span><span class="text-slate-400 font-bold">'+(d.miss_label||'미노출')+'</span></div>';}});
   var mk='';
   window.__rcTop=(d.targets&&d.targets.length)?{{kw:d.targets[0].keyword,vol:d.targets[0].volume||0}}:null;
   (d.targets||[]).forEach(function(tg){{var v=tg.volume?(' (월 '+tg.volume.toLocaleString()+'회)'):'';
     mk+='<a href="'+tg.make_href+'" class="block bg-indigo-50 hover:bg-indigo-100 rounded-xl px-3.5 py-2.5 mt-2 text-indigo-700 font-bold text-sm transition">'+tg.keyword+v+' — 이 키워드 잡는 글 만들기 →</a>';}});
   o.innerHTML='<b class="text-slate-900">'+d.headline+'</b>'+rows
     +'<div class="text-slate-400 mt-2">'+d.subline+'</div>'+mk
     +'<button type="button" onclick="fillDemo()" class="block w-full text-left bg-white border border-indigo-200 hover:border-indigo-400 rounded-xl px-3.5 py-2.5 mt-2 text-indigo-700 font-bold text-sm transition">이 업종으로 바로 만들어보기 (가입 없이) →</button>'
     +'<div id="rc_lead" class="mt-3 pt-3 border-t border-slate-100"><div class="text-xs text-slate-500 mb-1.5">📩 이 진단 리포트를 이메일로 받아보시겠어요?</div>'
     +'<div class="flex gap-1.5"><input id="rc_email" type="email" inputmode="email" placeholder="이메일 주소" class="flex-1 min-w-0 rounded-xl border border-slate-200 px-2.5 py-2 text-sm outline-none focus:border-indigo-400">'
     +'<button onclick="sendReport()" class="px-3 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm whitespace-nowrap">받기</button></div>'
     +'<div id="rc_leadmsg" class="text-xs text-emerald-600 mt-1.5"></div></div>'
     +'<a href="/login/kakao" class="inline-block text-indigo-600 underline font-bold mt-3">'+d.cta+' →</a>'
     +(d.estimated?' <span class="text-slate-400 text-xs">(추정)</span>':'');
   // ⚡ 진단이 끝나면 곧바로 '내 가게용 제목'을 AI가 지어 이어 붙인다(2026-08-13).
   //    자기 가게 이름이 박힌 진짜 결과를 봐야 '나도 써봐야겠다'가 된다.
   try{{ if(typeof instantTitles==='function') instantTitles(); }}catch(e){{}}
   }}catch(e){{o.textContent='조회 실패 — 잠시 후 다시';}}}}
  function rcPick(i){{
   var cs=window.__rcCands||[];
   if(i>=0&&cs[i]){{window.__rcAddr=cs[i].address||'';document.getElementById('rc_name').value=cs[i].name;}}
   else{{window.__rcAddr='';}}                        // '내 가게가 없어요' — 이름만으로 진단
   window.__rcPicked=true;                            // 다시 물어보지 않음
   document.getElementById('rc_pick').classList.add('hidden');
   rankCheck();
  }}
  // 상호를 바꾸면 이전 선택을 버린다(다른 가게를 이전 주소로 판정하는 사고 방지)
  document.addEventListener('input',function(e){{
   if(e.target&&e.target.id==='rc_name'){{window.__rcAddr='';window.__rcPicked=false;
     var p=document.getElementById('rc_pick');if(p)p.classList.add('hidden');}}
  }});
  async function sendReport(){{var em=document.getElementById('rc_email').value.trim();
   var m=document.getElementById('rc_leadmsg');if(!em||em.indexOf('@')<0){{m.style.color='#e11';m.textContent='이메일을 확인해주세요';return;}}
   m.style.color='#059669';m.textContent='보내는 중…';
   var fd=new FormData();fd.append('email',em);
   fd.append('region',document.getElementById('rc_region').value);fd.append('industry',document.getElementById('rc_ind').value);
   fd.append('name',document.getElementById('rc_name').value);if(window.__rcMode==='seller')fd.append('mode','seller');
   try{{var r=await fetch('/api/rank-report',{{method:'POST',body:fd}});var d=await r.json();
     if(d.error){{m.style.color='#e11';m.textContent=d.error;return;}}
     if(window.trackEv)trackEv('lead_capture',{{}});
     document.getElementById('rc_lead').innerHTML='<div class="text-sm text-emerald-600 font-bold">✅ 접수됐어요! '+(d.sent?'리포트를 이메일로 보냈습니다.':'곧 연락드릴게요.')+'</div>';
   }}catch(e){{m.style.color='#e11';m.textContent='잠시 후 다시 시도해주세요';}}}}
  </script>
 </div></section>"""


def _video() -> str:
    return f"""
<section id="video" class="bg-white py-20">
 <div class="max-w-4xl mx-auto px-5">
  <div class="reveal text-center mb-8">
   <h2 class="text-2xl sm:text-3xl font-bold text-slate-900">실제 결과물, 직접 보세요</h2>
   <p class="text-slate-500 text-sm mt-2">사진 5장만 올리면 <b class="text-slate-800">음성 영상</b>과 <b class="text-slate-800">네이버 블로그 글</b>이 자동으로. 아래는 실제 생성 결과입니다.</p></div>
  <div class="reveal max-w-sm mx-auto card overflow-hidden">
   <video src="{_v('/demo/local_short.mp4')}" controls muted loop playsinline preload="metadata" poster="{_v('/demo/short_poster.jpg')}" class="w-full bg-black"></video>
   <div class="text-slate-600 text-sm px-5 py-3.5">초량 루마썬팅 — 사진 5장 → AI 자동 생성 시공 과정 세로 영상 <b class="text-slate-800">(음성 나레이션 + 자막 · 번호판 자동 가림)</b>
   <span class="block text-xs text-slate-400 mt-1">실제 올린다 생성물 · 탭하면 소리가 나와요</span></div></div>
  {_naver_preview()}
 </div>
 <script>
 // 화면에 들어왔을 때만 재생 시작(모바일 데이터·초기 로딩 절약) — 진입 즉시 1.2MB 자동 다운로드 방지
 (function(){{var vs=document.querySelectorAll('#video video');if(!vs.length)return;
  var vio=new IntersectionObserver(function(es){{es.forEach(function(e){{var v=e.target;
    if(e.isIntersecting){{v.muted=true;var p=v.play();if(p&&p.catch)p.catch(function(){{}});}}
    else{{v.pause();}}}});}},{{threshold:.35}});
  vs.forEach(function(v){{vio.observe(v);}});}})();
 </script></section>"""


def _flow() -> str:
    """★ 2026-08-13 사장님 지시 — 첫 화면에서 '실제로 무슨 일이 일어나는지'를 끝까지 보여준다.

    왜 바꿨나: 기존 랜딩은 18섹션·272문단이었고, 첫 화면은 설명뿐이었다. 방문 40명 중
    버튼을 누른 사람이 0명. 사장님 말씀 그대로 — 가입부터 글·영상 생성, 그 글이 어떻게
    실측되고, 그다음 무슨 글을 쓰는지까지가 한 화면에서 보여야 한다.
    설명 대신 실물만 놓는다(실제 생성 영상·실제 쓴 글·실측 순위·실제 다음 글감).
    """
    step = ("<div class='shrink-0 w-7 h-7 rounded-full bg-indigo-600 text-white text-sm "
            "font-extrabold flex items-center justify-center'>{}</div>")

    # ① 사진 올리기 — 보정 전/후 실물
    s1 = (f"<div class='reveal card p-5'>"
          f"<div class='flex items-center gap-2 mb-3'>{step.format(1)}"
          f"<div class='font-bold text-slate-900'>사진만 올립니다</div></div>"
          f"<div class='grid grid-cols-2 gap-2'>"
          f"<div><img src='{_v('/demo/food-before.jpg')}' loading='lazy' decoding='async' "
          f"class='w-full rounded-xl object-cover' style='aspect-ratio:4/3' alt='폰으로 찍은 사진'>"
          f"<div class='text-[11px] text-slate-400 text-center mt-1'>폰으로 찍은 사진</div></div>"
          f"<div><img src='{_v('/demo/food-after.jpg')}' loading='lazy' decoding='async' "
          f"class='w-full rounded-xl object-cover' style='aspect-ratio:4/3' alt='올린다 자동 보정'>"
          f"<div class='text-[11px] text-indigo-600 font-bold text-center mt-1'>자동 보정 후</div></div></div>"
          f"<p class='text-slate-500 text-sm mt-3'>보정·번호판 가림까지 자동으로 합니다.</p></div>")

    # ② 글·영상 생성 — 실제 생성물
    s2 = (f"<div class='reveal card p-5'>"
          f"<div class='flex items-center gap-2 mb-3'>{step.format(2)}"
          f"<div class='font-bold text-slate-900'>글과 영상이 나옵니다</div></div>"
          f"<video src='{_v('/demo/local_short.mp4')}' controls muted loop playsinline "
          f"preload='metadata' poster='{_v('/demo/short_poster.jpg')}' "
          f"class='w-full max-w-[190px] mx-auto rounded-xl bg-black'></video>"
          f"<div class='mt-3 bg-[#F9FAFB] border border-slate-200 rounded-xl p-3'>"
          f"<div class='text-[11px] text-slate-400 mb-1'>네이버 블로그 · 실제 생성된 글</div>"
          f"<div class='text-sm font-bold text-slate-800 leading-snug'>부산 동구 썬팅업체 후기, "
          f"포터2 냉동탑차 열차단 시공 팩트정리</div></div>"
          f"<p class='text-slate-500 text-sm mt-3'>영상은 나레이션·자막까지 · 실제 올린다 생성물입니다.</p></div>")

    # ③ 실측 — 발행 후 순위가 어떻게 움직였나
    def _tl(date, label, hot=False):
        dot = "bg-indigo-600" if hot else "bg-slate-300"
        txt = "text-slate-900 font-extrabold" if hot else "text-slate-600"
        return (f"<div class='flex items-center gap-3 py-1'>"
                f"<span class='w-2.5 h-2.5 rounded-full {dot} shrink-0'></span>"
                f"<span class='text-xs text-slate-400 w-11 shrink-0'>{date}</span>"
                f"<span class='text-sm {txt}'>{label}</span></div>")
    s3 = (f"<div class='reveal card-hi p-5'>"
          f"<div class='flex items-center gap-2 mb-3'>{step.format(3)}"
          f"<div class='font-bold text-slate-900'>매일 순위를 재드립니다</div></div>"
          f"<div class='text-sm text-slate-500 mb-2'>‘부산 동구 썬팅업체’ 검색</div>"
          f"<div class='border-l-2 border-indigo-100 ml-1 pl-3'>"
          + _tl("7/31", "글 발행")
          + _tl("8/2", "네이버 블로그검색 <b>12위</b>")
          + _tl("8/9", "<span class='text-indigo-600 text-lg'>1위</span>", hot=True)
          + f"</div>"
          f"<p class='text-slate-500 text-sm mt-3'>발행 9일 만에 1위. 글은 쓰는 날이 아니라 "
          f"<b class='text-slate-700'>떨어지는 날</b>이 문제라, 떨어지면 고친 글을 먼저 내밉니다.</p>"
          f"<p class='text-[11px] text-slate-400 mt-1'>자동 발행은 하지 않아요 — 발행 버튼은 언제나 사장님 몫</p>"
          f"<p class='text-[11px] text-slate-400 mt-1'>실제 이용 가게 · 2026년 8월 실측 · "
          f"결과는 가게·검색어에 따라 달라요</p></div>")

    # ④ 다음 글감 — 이 루프가 계속 돈다
    s4 = (f"<div class='reveal card p-5'>"
          f"<div class='flex items-center gap-2 mb-3'>{step.format(4)}"
          f"<div class='font-bold text-slate-900'>다음에 쓸 글을 찾아옵니다</div></div>"
          f"<div class='bg-[#F9FAFB] border border-slate-200 rounded-xl p-3 space-y-2'>"
          f"<div class='text-[11px] text-slate-400'>먼저 쓰면 좋은 이야기</div>"
          f"<div class='text-sm text-slate-800 font-semibold leading-snug'>"
          f"겨울에 시공해도 괜찮은지 궁금해하는 분들이 많아요</div>"
          f"<div class='text-xs text-slate-400'>아직 이 질문에 답한 글이 없어요</div>"
          f"<div class='flex gap-1.5 pt-1'>"
          f"<span class='px-2.5 py-1 rounded-lg bg-indigo-600 text-white text-xs font-bold'>이걸로 쓸래요</span>"
          f"<span class='px-2.5 py-1 rounded-lg bg-white border border-slate-200 text-slate-500 text-xs'>저희는 안 해요</span>"
          f"</div></div>"
          f"<p class='text-slate-500 text-sm mt-3'>손님이 찾는데 답이 없는 자리를 찾아 올려드려요. "
          f"고르기만 하시면 ①로 돌아갑니다.</p>"
          f"<p class='text-[11px] text-slate-400 mt-1'>실제 화면 구성 — 글감 내용은 가게마다 달라요</p></div>")

    return (f"<section class='bg-[#F9FAFB] py-16'><div class='max-w-6xl mx-auto px-5'>"
            f"<h2 class='reveal text-2xl sm:text-3xl font-bold text-center text-slate-900 mb-2'>"
            f"가입하면 이렇게 <span class='text-indigo-600'>돌아갑니다</span></h2>"
            f"<p class='reveal text-center text-slate-500 mb-10'>사진 올리는 것 말고는 올린다가 합니다.</p>"
            f"<div class='grid sm:grid-cols-2 lg:grid-cols-4 gap-4 items-start'>{s1}{s2}{s3}{s4}</div>"
            f"</div></section>")


def _try() -> str:
    """★ 2026-08-13 사장님 지시 — 두 번째 화면 = 지금 바로 무료로 써보는 자리.

    전환 설계: 처음 온 사장님이 2분(전체 생성 실측 126초)을 기다려주지 않는다.
    그래서 순서를 바꾼다 —
      ① 상호만 넣으면 → 지금 내 가게 순위(실데이터)
      ② 이어서 AI가 **내 가게 이름이 박힌 글 제목**을 즉석으로(짧은 호출 1회)
      ③ 그게 마음에 들면 전체(글·영상) 생성으로 — 이때 2분은 기꺼이 기다린다
    '내 것'을 먼저 보여줘야 '나도 써봐야겠다'가 된다.
    """
    return f"""
<section id="try" class="bg-white py-16 border-t border-slate-100">
 <div class="max-w-4xl mx-auto px-5">
  <div class="reveal text-center mb-8">
   <span class="inline-block px-3 py-1 rounded-full bg-[#EEF2FF] text-indigo-600 text-xs font-bold mb-3">가입 없이 · 무료</span>
   <h2 class="text-2xl sm:text-3xl font-bold text-slate-900">우리 가게, 지금 몇 위일까요?</h2>
   <p class="text-slate-500 text-sm mt-2">상호만 넣으면 <b class="text-slate-800">현재 순위</b>를 바로 확인해드리고,
    <b class="text-slate-800">아직 못 잡은 검색어</b>로 쓰면 좋을 글 제목까지 지어드려요.</p></div>
  <div class="reveal max-w-2xl mx-auto bg-white border-2 border-indigo-200 rounded-2xl shadow-sm p-5">
   <div class="flex gap-1.5 mb-3 text-xs font-bold">
     <button type="button" id="rc_mlocal" onclick="rcSetMode('local')" class="px-3 py-1.5 rounded-lg border border-indigo-500 bg-indigo-50 text-indigo-700 transition">동네 매장</button>
     <button type="button" id="rc_mseller" onclick="rcSetMode('seller')" class="px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-500 transition">온라인 셀러</button></div>
   <p id="rc_sub" class="text-xs text-slate-400 mb-3">지역·업종·상호만 — 네이버 현재 순위를 바로 확인</p>
   <div class="flex flex-col sm:flex-row gap-2">
     <input id="rc_region" placeholder="지역(부산 동구)" class="w-full sm:flex-1 min-w-0 rounded-xl border border-slate-200 px-2.5 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400">
     <input id="rc_ind" placeholder="업종" class="w-full sm:flex-1 min-w-0 rounded-xl border border-slate-200 px-2.5 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400">
     <input id="rc_name" placeholder="상호" class="w-full sm:flex-1 min-w-0 rounded-xl border border-slate-200 px-2.5 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400"></div>
   <button onclick="rankCheck()" class="w-full mt-2.5 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm transition">내 가게 확인하기</button>
   <div id="rc_pick" class="hidden mt-3"></div>
   <div id="rc_out" class="text-slate-600 text-sm mt-3"></div>
   <div id="it_out" class="hidden mt-3"></div>
  </div>
  <div class="reveal max-w-2xl mx-auto mt-5">{_hero_demo_card()}</div>
 </div>
 <script>
 // ⚡ 진단이 끝나면, 아직 못 잡은 검색어로 '내 가게용 제목'을 AI가 즉석 생성해 이어 붙인다.
 //    (2026-08-13) 전체 생성은 2분이라 첫 방문자를 못 잡는다 — 짧은 호출로 '내 것'을 먼저 보여준다.
 async function instantTitles(){{
  var box=document.getElementById('it_out'); if(!box) return;
  var top=window.__rcTop||{{}};
  var fd=new FormData();
  fd.append('name',(document.getElementById('rc_name')||{{}}).value||'');
  fd.append('industry',(document.getElementById('rc_ind')||{{}}).value||'');
  fd.append('region',(document.getElementById('rc_region')||{{}}).value||'');
  fd.append('keyword',top.kw||'');
  box.className='mt-3'; box.innerHTML='<div class="text-xs text-slate-400">지금 쓰면 좋을 글 제목을 만들고 있어요…</div>';
  try{{
   var r=await fetch('/api/instant-titles',{{method:'POST',body:fd}});
   var d=await r.json();
   if(!d.ok||!(d.titles||[]).length){{ box.className='hidden'; return; }}
   var lis=d.titles.map(function(t){{
     return '<div class="flex items-start gap-2 bg-white border border-slate-200 rounded-xl px-3 py-2.5">'
       +'<span class="text-indigo-500 mt-0.5">✎</span><span class="text-sm text-slate-800 font-semibold">'+
       t.replace(/[<>]/g,'')+'</span></div>';}}).join('');
   box.innerHTML='<div class="bg-[#F9FAFB] border border-slate-200 rounded-2xl p-4">'
     +'<div class="text-xs font-bold text-indigo-600 mb-2">AI가 방금 지은 — 내 가게가 지금 쓰면 좋을 글</div>'
     +'<div class="space-y-2">'+lis+'</div>'
     +'<div class="text-[11px] text-slate-400 mt-2">없는 가격·성능은 넣지 않았어요</div>'
     +'<button type="button" onclick="document.getElementById(\\'herodemo\\').scrollIntoView({{behavior:\\'smooth\\',block:\\'center\\'}})" '
     +'class="w-full mt-3 py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-bold">이 글 전체로 만들어보기 →</button></div>';
  }}catch(e){{ box.className='hidden'; }}
 }}
 </script></section>"""


def _hero_demo_card() -> str:
    """무료 만들기 위젯 — 히어로에서 순위진단과 나란히(두 미끼를 한눈에). 기능 동일, 위치만 이동."""
    inp = "w-full rounded-xl border border-slate-200 px-3 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400"
    return f"""
   <div id="herodemo" class="bg-white border-2 border-indigo-200 rounded-2xl shadow-sm p-5">
    <div class="flex items-center gap-2 text-slate-800 font-bold text-sm mb-1">{_icon('camera', 'w-4 h-4 text-indigo-600')} 내 사진으로 지금 만들어보기</div>
    <p class="text-xs text-slate-400 mb-3">사진 올리고 업종만 고르면 <b class="text-slate-600">진짜로 생성</b>해서 바로 보여드려요 · 가입 없이</p>
    <div id="d_target_hint" class="hidden bg-[#EEF2FF] text-indigo-700 text-xs font-bold rounded-xl px-3 py-2 mb-2"></div>
    <form id="demoForm" class="space-y-2">
     <input type=hidden id="d_target_kw"><input type=hidden id="d_target_vol">
     <!-- 사진 0장: 점선 박스 / 1장+: 박스가 썸네일 그리드로 전환(d_preview) — 분리 영역 제거(모바일 UX) -->
     <label id="d_photobox" class="block bg-slate-50 border-2 border-dashed border-slate-200 rounded-xl px-4 py-3 text-center cursor-pointer hover:border-indigo-300 transition">
       <span class="text-slate-800 font-bold text-sm inline-flex items-center gap-2">{_icon('camera', 'w-4 h-4 text-indigo-600')} 사진 올리기</span>
       <span class="block text-slate-400 text-xs mt-0.5">가게·상품 사진 (여러 장 가능 · 선택)</span>
       <input id="d_photo" type="file" accept="image/*" multiple class="hidden"><span id="d_photoname" class="block text-indigo-600 text-xs mt-1 font-semibold"></span></label>
     <div id="d_preview" class="hidden grid grid-cols-3 gap-2 bg-slate-50 border-2 border-dashed border-slate-200 rounded-xl p-3"></div>
     <div id="d_guessbox"></div>
     <input type=hidden id="d_confirmed"><input type=hidden id="d_vision">
     <!-- 업종칸은 항상 빈칸 시작(하드코딩 금지) — 값이 채워지는 유일한 경로는
          fillDemo(): 순위진단 위젯에 사용자가 직접 입력한 업종 복사(그것도 빈칸일 때만) -->
     <!-- 가로·컴팩트(폼 개선): 업종+목적 한 줄 — 모바일 포함 항상 2열(세로 스크롤 최소화) -->
     <div class="grid grid-cols-2 gap-2">
      <input id="d_ind" placeholder="업종/상품 (예: 꽃집, 헬스장...)" class="{inp}"
        onblur="window.demoQs&&demoQs()"
        oninput="clearTimeout(window.__dqt);window.__dqt=setTimeout(function(){{window.demoQs&&demoQs();}},800)">
      <select id="d_purpose" class="{inp} bg-white" onchange="window.demoQs&&demoQs()">
        <option value="">목적 (선택)</option>
        <option value="방문 유도">매장 방문·예약 유도</option>
        <option value="판매 전환">구매·판매 전환</option>
        <option value="신상품 홍보">신상품·신메뉴 홍보</option>
        <option value="이벤트·할인">이벤트·할인 알림</option>
        <option value="신뢰·후기">신뢰·후기 쌓기</option>
      </select>
     </div>
     <div id="d_questions"></div>
     <div class="flex gap-2 text-sm">
       <label class="flex-1"><input type="radio" name="d_biz" value="local" checked class="peer hidden"><div class="text-center py-2.5 rounded-xl bg-white border border-slate-200 text-slate-500 peer-checked:border-indigo-500 peer-checked:bg-indigo-50 peer-checked:text-indigo-700 font-bold cursor-pointer transition">동네 매장</div></label>
       <label class="flex-1"><input type="radio" name="d_biz" value="seller" class="peer hidden"><div class="text-center py-2.5 rounded-xl bg-white border border-slate-200 text-slate-500 peer-checked:border-indigo-500 peer-checked:bg-indigo-50 peer-checked:text-indigo-700 font-bold cursor-pointer transition">온라인 셀러</div></label>
     </div>
     <button id="d_submit" class="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm transition disabled:opacity-40 disabled:cursor-not-allowed">실제로 만들어보기</button>
     <div id="d_submit_hint" class="hidden text-center text-xs text-slate-400"></div></form>
    <div id="demoResult" class="mt-4"></div>
    <p class="text-center text-slate-400 text-xs mt-2">가입 없이 미리보기 · 가입하면 <b class="text-slate-600">5채널 전부 + 영상</b> 무료 2회</p>
   </div>"""


def _stats() -> str:
    """(제거 2026-08-09) 기능 개수 나열은 증거가 아니다 — 실사용 가게 수·누적 발행 수 같은
    실측 숫자가 의미 있어지면 그때 실숫자로 부활시킨다(정직 원칙)."""
    return ""


def _experience_strip() -> str:
    """경험 자산화 — 대행사와의 결정적 차이: 매번 묻지 않는다. 한 번 답하면 계속 쓴다."""
    return f"""
<section class="bg-white py-20">
 <div class="max-w-4xl mx-auto px-5">
  <div class="reveal card-hi p-8 sm:p-10 text-center">
   <div class="mx-auto w-12 h-12 rounded-full bg-white text-indigo-600 flex items-center justify-center mb-4">{_icon('message', 'w-6 h-6')}</div>
   <h2 class="text-2xl sm:text-3xl font-bold text-slate-900 mb-3">한 번 답하면, <span class="text-indigo-600">계속 씁니다</span></h2>
   <p class="text-slate-600 text-sm sm:text-base max-w-2xl mx-auto">글을 만들 때 그 주제로 <b class="text-slate-800">딱 한 가지</b>만 여쭤봐요.
    답하신 경험은 저장돼서 다음 글에 자동으로 들어가고 — <b class="text-slate-800">쌓일수록 질문이 줄어듭니다.</b>
    나중엔 사진만 던지셔도 돼요.</p>
   <p class="text-xs text-slate-400 mt-4">답이 없는 주제는 지어내지 않습니다 — 경험 없이도 쓸 수 있는 사실 기반 글로 먼저 나가요.</p>
  </div>
 </div></section>"""


def _problem() -> str:
    pains = [("clock", "시간이 없다", "장사하기도 바쁜데 매일 인스타·블로그·영상까지 올릴 시간이 없죠."),
             ("wallet", "대행사는 비싸다", "월 30~50만원 대행료, 결과는 깜깜이. 부담만 큽니다."),
             ("help", "뭘 올릴지 모른다", "찍긴 했는데 어떻게 써야 검색에 뜨고 손님이 올지 막막합니다.")]
    cards = "".join(f"<div class='reveal card p-7'>{_icon_chip(ic, 'slate')}"
                    f"<div class='font-bold text-xl mb-2 text-slate-900'>{t}</div>"
                    f"<p class='text-slate-500 text-sm'>{d}</p></div>" for ic, t, d in pains)
    return (f"<section class='bg-[#F9FAFB] py-24'><div class='max-w-5xl mx-auto px-5'>"
            f"<h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900'>마케팅, 이래서 못 하셨죠?</h2>"
            f"<p class='reveal text-center text-slate-500 mb-14'>사장님 99%가 겪는 문제 — 올린다가 해결합니다.</p>"
            f"<div class='grid sm:grid-cols-3 gap-6'>{cards}</div></div></section>")


def _results() -> str:
    """성과가 '눈에 보이는' 킬러 기능 쇼케이스(순위상승·경쟁추월·성과QR·사진보정·코칭)."""
    qr = ("<svg width='84' height='84' viewBox='0 0 88 88' class='rounded-lg'>"
          "<rect width='88' height='88' fill='#fff'/>"
          "<rect x='10' y='10' width='20' height='20' fill='none' stroke='#1e1b4b' stroke-width='4'/><rect x='16' y='16' width='8' height='8' fill='#1e1b4b'/>"
          "<rect x='58' y='10' width='20' height='20' fill='none' stroke='#1e1b4b' stroke-width='4'/><rect x='64' y='16' width='8' height='8' fill='#1e1b4b'/>"
          "<rect x='10' y='58' width='20' height='20' fill='none' stroke='#1e1b4b' stroke-width='4'/><rect x='16' y='64' width='8' height='8' fill='#1e1b4b'/>"
          "<g fill='#4338ca'><rect x='40' y='12' width='6' height='6'/><rect x='50' y='20' width='6' height='6'/><rect x='40' y='40' width='6' height='6'/>"
          "<rect x='52' y='46' width='6' height='6'/><rect x='62' y='44' width='6' height='6'/><rect x='44' y='60' width='6' height='6'/>"
          "<rect x='60' y='64' width='6' height='6'/><rect x='70' y='54' width='6' height='6'/><rect x='40' y='72' width='6' height='6'/></g></svg>")
    # 실측 사례(예시·목업 아님) — 같은 글 하나의 발행→순위 여정, 전부 실측 날짜(2026-08 gowatch·본체 기록).
    # 가게 실명은 동의 전 비공개(지역·업종만). 날짜·순위를 지어내지 않는다.
    def _tl(date, label, hot=False):
        dot = "bg-indigo-600" if hot else "bg-indigo-200"
        txt = "text-slate-900 font-extrabold" if hot else "text-slate-600"
        return (f"<div class='flex items-center gap-3 py-1.5'>"
                f"<span class='w-2.5 h-2.5 rounded-full {dot} shrink-0'></span>"
                f"<span class='text-xs text-slate-400 w-12 shrink-0'>{date}</span>"
                f"<span class='text-sm {txt}'>{label}</span></div>")
    c1 = ("<div class='reveal card-hi p-6'>"
          "<div class='text-xs font-bold text-indigo-500 mb-3'>실측 사례 — 실제 이용 가게의 한 글</div>"
          "<div class='font-semibold text-slate-800 mb-2'>‘부산 동구 썬팅업체’ 검색</div>"
          "<div class='border-l-2 border-indigo-100 ml-1 pl-3'>"
          + _tl("7/31", "글 발행 (올린다 생성)")
          + _tl("8/2", "네이버 블로그검색 <b>12위</b> 첫 실측")
          + _tl("8/9", "<span class='text-indigo-600 text-xl'>1위</span>", hot=True)
          + "</div>"
          "<p class='text-slate-500 text-sm mt-3'>부산 동구의 썬팅 전문점 — 발행 9일 만에 "
          "<b class='text-slate-800'>네이버 블로그검색 1위</b>. 올린다는 이 여정을 매일 실측으로 지켜봅니다.</p>"
          "<p class='text-[11px] text-slate-400 mt-2'>2026년 8월 실측 · 개별 결과는 가게·키워드에 따라 달라요</p></div>")
    c2 = ("<div class='reveal card p-6'>"
          "<div class='text-xs font-bold text-slate-400 mb-3'>경쟁 추월</div>"
          "<div class='space-y-2'>"
          "<div class='flex items-center gap-2 text-sm text-slate-400'><span class='w-6 text-center'>1</span>A썬팅</div>"
          "<div class='flex items-center gap-2 text-sm bg-indigo-50 border border-indigo-200 rounded-lg px-2 py-1.5'><span class='w-6 text-center text-indigo-600 font-bold'>2</span><b class='text-slate-900'>내 가게</b><span class='ml-auto text-indigo-600 text-xs font-bold'>하나만 더!</span></div>"
          "<div class='flex items-center gap-2 text-sm text-slate-400'><span class='w-6 text-center'>3</span>B카센터</div></div>"
          "<p class='text-slate-500 text-sm mt-3'><b class='text-slate-800'>“A썬팅만 넘으면 1위”</b> — 추월 타깃을 콕 집어줘요.</p></div>")
    c3 = ("<div class='reveal card p-6'>"
          "<div class='text-xs font-bold text-slate-400 mb-3'>성과 실측 · 내 손님 추적</div>"
          "<div class='flex items-center gap-4'><div class='rounded-lg border border-slate-200 p-1'>" + qr + "</div>"
          # ★ 2026-08-13 정직 게이트: 예전엔 0→37로 세는 '손님 수'였다. 작게 (예시)를 달았어도
          #   화면에 남는 인상은 '37명이 왔다'는 실적이다 — 우리에겐 그런 실적이 아직 없다.
          #   숫자를 지어내지 말고, 무엇이 잡히는지(구조)만 사실대로 보여준다.
          "<div><div class='text-2xl font-bold text-slate-900 leading-snug'>이 QR로 들어온 손님이<br>몇 명인지 잡힙니다</div>"
          "<div class='text-slate-500 text-sm mt-1'>발행한 글·영상마다 자동 생성</div></div></div>"
          "<p class='text-slate-500 text-sm mt-3'>QR·링크로 <b class='text-slate-800'>실제 유입이 숫자로</b> 잡혀요.</p></div>")
    c4 = ("<div class='reveal card p-6 flex flex-col'>"
          "<div class='text-xs font-bold text-slate-400 mb-3'>사진 자동 보정 · 실제 전/후</div>"
          "<div class='relative rounded-2xl overflow-hidden select-none mx-auto w-full' style='aspect-ratio:16/10;max-height:230px'>"
          "<img src='/demo/food-after.jpg' loading='lazy' decoding='async' class='absolute inset-0 w-full h-full object-cover' alt='보정 후'>"
          "<img src='/demo/food-before.jpg' loading='lazy' decoding='async' class='baclip absolute inset-0 w-full h-full object-cover' alt='보정 전'>"
          "<div class='badiv absolute top-0 bottom-0 w-0.5 bg-white/90 shadow'></div>"
          "<span class='absolute bottom-2 left-2 bg-black/55 text-white text-[10px] font-bold px-2 py-0.5 rounded'>폰 사진</span>"
          "<span class='absolute bottom-2 right-2 bg-indigo-600 text-white text-[10px] font-bold px-2 py-0.5 rounded'>올린다 보정</span></div>"
          "<p class='text-slate-500 text-sm mt-3'>폰으로 대충 찍어도 <b class='text-slate-800'>전문가 톤·먹음직</b>하게 자동 보정.</p></div>")
    c5 = ("<div class='reveal card p-6 flex flex-col justify-center'>"
          "<div class='text-xs font-bold text-slate-400 mb-3'>능동 코칭</div>"
          "<div class='flex items-center gap-3 bg-slate-50 border border-slate-100 rounded-2xl p-4'>"
          "<span class='text-indigo-600'>" + _icon("trend", "w-6 h-6") + "</span>"
          "<div class='flex-1'><div class='text-[11px] font-bold text-indigo-600'>오늘의 액션</div>"
          "<div class='text-sm text-slate-800 font-medium'>순위 오르는 중! 하나 더 올리면 1위 각이에요.</div></div></div>"
          "<p class='text-slate-500 text-sm mt-3'>뭘 할지 <b class='text-slate-800'>앱이 먼저 알려줘요</b> — 직원처럼.</p></div>")
    return ("<section id='results' class='bg-white py-24'>"
            "<div class='max-w-6xl mx-auto px-5'>"
            "<div class='reveal text-center mb-4'>"
            "<span class='inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-slate-50 border border-slate-200 text-xs font-semibold text-slate-500'>글만 뽑는 툴과 다른 점</span>"
            "<h2 class='text-3xl sm:text-5xl font-bold mt-6 leading-tight text-slate-900'>만드는 건 기본.<br>올린다는 <span class='text-indigo-600'>성과가 눈에 보입니다.</span></h2>"
            "<p class='text-slate-500 mt-5 max-w-2xl mx-auto'>순위가 오르고, 손님이 오는 게 <b class='text-slate-800'>숫자로</b> 보여요. 그래서 한 번 쓰면 못 끊습니다.</p></div>"
            "<div class='grid lg:grid-cols-3 gap-5 mt-12'>" + c1 + c2 + c3 + "</div>"
            "<div class='grid sm:grid-cols-2 gap-5 mt-5'>" + c4 + c5 + "</div>"
            "<div class='reveal text-center mt-14'>"
            "<a href='/login/kakao' class='inline-block bg-indigo-600 hover:bg-indigo-700 text-white font-extrabold px-8 py-4 rounded-2xl text-lg transition'>내 가게 순위 올리기 →</a></div>"
            "</div></section>")


def _modes() -> str:
    """두 종류 고객(소상공인 vs 온라인 셀러)에 맞춰 결과물이 자동으로 달라짐을 설명."""
    local = [("목표", "동네 손님을 <b>매장 방문·전화·예약</b>으로"),
             ("키워드", "<b>지역명</b> 중심 (예: ‘부산 초량 썬팅 추천’)"),
             ("글 마무리", "<b>지도 + 영업시간 + 연락처</b> 자동 삽입"),
             ("주력 채널", "네이버 블로그·플레이스 → 인스타")]
    seller = [("목표", "검색·SNS 손님을 <b>상세페이지 구매</b>로"),
              ("키워드", "<b>상품·후기</b> 중심 (예: ‘폴딩박스 추천·내돈내산’)"),
              ("글 마무리", "<b>구매 링크 / 쿠팡 검색어</b> 자동 삽입"),
              ("주력 채널", "인스타 릴스·유튜브 쇼츠 → 블로그 후기")]
    def col(icon, title, sub, items):
        rows = "".join(f"<div class='flex gap-3 py-2.5 border-t border-slate-100'>"
                       f"<div class='text-sm font-bold text-slate-400 w-24 shrink-0'>{k}</div>"
                       f"<div class='text-sm text-slate-700'>{v}</div></div>" for k, v in items)
        return (f"<div class='reveal card p-7'>"
                f"<div class='inline-flex items-center gap-2 text-sm font-bold text-slate-900 mb-1'>"
                f"<span class='text-indigo-600'>{_icon(icon, 'w-5 h-5')}</span>{title}</div>"
                f"<p class='text-slate-400 text-sm mb-3'>{sub}</p>{rows}</div>")
    cols = (col("store", "동네 매장 (소상공인)", "썬팅집·카페·미용실·식당·꽃집…", local)
            + col("package", "온라인 셀러", "쿠팡·11번가·스마트스토어·자사몰…", seller))
    return ("<section class='bg-[#F9FAFB] py-24'><div class='max-w-5xl mx-auto px-5'>"
            "<h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900'>"
            "<span class='text-indigo-600'>내 장사 방식</span>에 딱 맞게</h2>"
            "<p class='reveal text-center text-slate-500 mb-14'>매장이냐 온라인 판매냐에 따라 글 마무리·키워드·CTA가 자동으로 달라집니다. 설정은 한 번이면 끝.</p>"
            f"<div class='grid sm:grid-cols-2 gap-6'>{cols}</div></div></section>")


def _features() -> str:
    """핵심 4개는 크게, 나머지 8개는 한 줄 리스트로 압축(12카드 밋밋함 해소)."""
    core = [("camera", "사진 한 장 → 5채널", "인스타·네이버·유튜브·릴스·X를 한 번에."),
            ("video", "사진 → 실사 무빙 영상", "정지 사진이 촬영 영상처럼 움직입니다(AI 카메라워크). 사람 목소리급 나레이션 + 단어 카라오케 자막까지 자동."),
            ("target", "검색에 잘 뜨는 구조로", "네이버·릴스가 좋아하는 형태(C-Rank·D.I.A.+)로 쓰고, 내보내기 전 100점 자동 점검."),
            ("chart", "순위 성장 추적", "네이버 순위가 오르는 걸 매주 ‘5위→2위’로 확인.")]
    rest = [("image", "인스타 캐러셀 자동", "사진 1장 → 정보 슬라이드(저장·도달↑)"),
            ("grid", "쇼츠·릴스·피드 규격", "9:16·1:1·4:5 자동 출력"),
            ("store", "소상공인·셀러 자동분기", "지도·방문 ↔ 구매링크·검색어 자동 전환"),
            ("tag", "업종 무제한 자동", "어떤 업종이든 맞춤 톤 자동 생성"),
            ("link", "계정 1회 연결 자동발행", "비번 없이 연결, 발행 누르면 끝"),
            ("chart", "성과 실측", "발행 후 순위·QR 유입 자동 집계"),
            ("wand", "사진 자동 보정", "폰 사진을 전문가 톤으로, 음식은 먹음직하게"),
            ("cpu", "쓸수록 똑똑해짐", "순위 오른 키워드를 학습해 다음 콘텐츠 강화")]
    big = "".join(f"<div class='reveal card p-7'>{_icon_chip(ic, size='lg')}"
                  f"<div class='font-bold text-xl mb-2 text-slate-900'>{t}</div><p class='text-slate-500 text-sm'>{d}</p></div>"
                  for ic, t, d in core)
    small = "".join(f"<div class='reveal flex items-center gap-3 bg-[#F9FAFB] border border-slate-200 rounded-xl px-4 py-3'>"
                    f"<span class='flex-shrink-0 w-9 h-9 rounded-full bg-[#EEF2FF] text-indigo-600 flex items-center justify-center'>{_icon(ic, 'w-4 h-4')}</span>"
                    f"<div class='min-w-0'><div class='font-bold text-sm text-slate-800'>{t}</div>"
                    f"<div class='text-xs text-slate-400 truncate'>{d}</div></div></div>"
                    for ic, t, d in rest)
    return (f"<section id='features' class='bg-white py-24'><div class='max-w-6xl mx-auto px-5'>"
            f"<h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900'>올린다가 <span class='text-indigo-600'>다 합니다</span></h2>"
            f"<p class='reveal text-center text-slate-500 mb-14'>생성부터 최적화·발행·관리까지.</p>"
            f"<div class='grid sm:grid-cols-2 lg:grid-cols-4 gap-5'>{big}</div>"
            # ★ 2026-08-13 사장님 지시(4순위): 랜딩이 18섹션·272문단이라 폰에서 20번 넘게
            #   스크롤해야 끝났다. 기능 나열은 '결정에 필요한 정보'가 아니라 '이미 마음먹은
            #   사람이 확인하는 정보'다 — 지우지 말고 접어서, 궁금한 사람만 펴 보게 한다.
            f"<details class='reveal mt-6 group'>"
            f"<summary class='cursor-pointer list-none text-center text-sm font-bold text-indigo-600 "
            f"hover:underline py-3'>기능 8가지 더 보기 <span class='group-open:hidden'>▾</span>"
            f"<span class='hidden group-open:inline'>▴</span></summary>"
            f"<div class='grid sm:grid-cols-2 lg:grid-cols-4 gap-3 mt-3'>{small}</div>"
            f"</details></div></section>")


def _new_features() -> str:
    """(UI 정리) 경쟁사·인쇄물 소개 섹션 제거 — 3단(사진→글→순위) 구조."""
    return ""


def _pricing() -> str:
    from app import config as _cfg
    b, p = _cfg.PRICE_BASIC, _cfg.PRICE_PRO
    by, py = _cfg.yearly_monthly_equiv(b), _cfg.yearly_monthly_equiv(p)   # 연결제 월 환산가(약 30%↓)
    af = _cfg.AGENCY_FROM
    lb, lp, la = _cfg.LIST_BASIC, _cfg.LIST_PRO, _cfg.LIST_AGENCY        # 정가(취소선) — 판매가는 런칭가
    L = _cfg.PLAN_LIMITS
    def _flim(plan):   # 신규기능 한도 표기(-1=무제한)
        d = L.get(plan, L["free"])
        cm = "무제한" if d["competitors_max"] == -1 else f"{d['competitors_max']}개"
        pi = "무제한" if d["print_items"] == -1 else f"월 {d['print_items']}장"
        _ = (cm, pi)                     # (UI 정리) 경쟁사·인쇄물 행 제거 — 백엔드 한도는 유지
        return []
    def _pr(list_won: int, sale_won: int) -> str:      # 정가 취소선 + 런칭가(2026-07-30 개편)
        return (f"<span class='line-through text-slate-300 text-lg font-semibold mr-1.5'>{list_won:,}원</span>"
                f"월 {sale_won:,}원")
    plans = [("라이트", _pr(lb, b), f"월 6세트 · 처음 시작용 · 연결제 시 월 {by:,}원",
              ["월 콘텐츠 6세트(블로그+인스타+X)", "실사 무빙 영상 2편(사진이 촬영 영상처럼 움직임)",
               "검색 상위노출 구조 + 품질 자동검사",
               "사진 자동 보정 + 번호판·개인정보 가림"] + _flim("basic"),
              "basic", False),
             ("스탠다드", _pr(lp, p), f"월 12세트 · 성과까지 · 연결제 시 월 {py:,}원",
              ["월 콘텐츠 12세트 + 실사 무빙 영상 8편", "네이버 클립 전용 영상(검색 첫 화면 진입)",
               "사람 목소리급 나레이션(단어 단위 자막 싱크)",
               "순위 성장 추적 · 미노출 자동 개선",
               "성과 실측(QR·유입 집계)", "이길 키워드 자동 선정(승산 분석)"] + _flim("pro"),
              "pro", True),
             ("프로", _pr(la, af), "월 20세트 · 영상 무제한 · 최우선",
              ["월 콘텐츠 20세트 + 실사 무빙 영상 무제한", "네이버 클립 전용 영상(검색 첫 화면 진입)",
               "우선 생성 · 다중 가게",
               "전담 지원(카톡 우선 응대)"] + _flim("agency"),
              "agency", False)]
    cards = ""
    for name, price, sub, feats, key, hot in plans:
        wrap = "relative border-2 border-indigo-500" if hot else "border border-slate-200"
        # ★ 2026-08-13 정직 게이트: 예전엔 '가장 인기' 배지였다. 유료 고객이 0명인데
        #   인기라고 쓰는 것은 날조된 사회적 증거다(헌법: 날조로 게이트를 통과시키지 않는다).
        #   실제로 팔린 뒤에 데이터로 붙일 배지다. 그 전까지는 '구성'만 사실대로 말한다.
        tag = ("<div class='absolute -top-3 left-1/2 -translate-x-1/2 bg-indigo-600 text-white text-xs font-bold px-3 py-1 rounded-full'>성과 추적 포함</div>"
               if hot else "")
        lis = "".join(f"<li class='flex gap-2 items-start'><span class='text-indigo-500 mt-0.5'>{_icon('check', 'w-4 h-4')}</span><span>{f}</span></li>" for f in feats)
        btn = "bg-indigo-600 hover:bg-indigo-700 text-white" if hot else "bg-slate-100 hover:bg-slate-200 text-slate-700"
        href = f"/billing?plan={key}"
        cta = "구독 시작"
        # 연결제(약 30%↓) 보조 링크 — basic/pro만
        annual = ("" if False else
                  f"<a href='/billing?plan={key}_yearly' class='block text-center text-xs text-indigo-600 font-bold mt-2 hover:underline'>연 결제로 30% 아끼기 →</a>")
        cards += (f"<div class='reveal {wrap} bg-white rounded-2xl p-8 flex flex-col'>{tag}"
                  f"<div class='font-bold text-lg text-slate-500'>{name}</div>"
                  f"<div class='text-3xl font-bold mt-3 mb-1 text-slate-900'>{price}</div>"
                  f"<div class='text-xs text-slate-400 mb-3'>{sub}</div>"
                  f"<ul class='space-y-2.5 text-sm text-slate-600 flex-1 mt-2'>{lis}</ul>"
                  f"<a href='{href}' class='{btn} mt-7 text-center px-4 py-3.5 rounded-xl font-bold transition'>{cta}</a>{annual}</div>")
    return (f"<section id='pricing' class='bg-[#F9FAFB] py-24'><div class='max-w-5xl mx-auto px-5'>"
            f"<h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900'>합리적인 요금 <span class='text-indigo-600 text-xl align-middle'>런칭 특가</span></h2>"
            # ★ 2026-08-14 가격 앵커(조사 근거): 크몽 블로그 대행 실판매가 38만/58만/77만원
            #   (한 서비스만 856건 거래). 지불 의사는 이미 있다 — 비싸서 안 사는 게 아니다.
            #   '월 13만원'만 있으면 비싸고, 대행가 옆에 두면 싸다. 앵커가 있어야 판단이 선다.
            f"<div class='reveal max-w-2xl mx-auto mb-8 bg-white border border-slate-200 rounded-2xl p-5 text-center'>"
            f"<div class='text-sm text-slate-400 mb-2'>지금 시장에서 같은 일을 맡기면</div>"
            f"<div class='flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-slate-500'>"
            f"<span>블로그 대행 <b class='text-slate-700 line-through decoration-slate-300'>월 38~77만원</b></span>"
            f"<span class='text-slate-300'>+</span>"
            f"<span>홍보 영상 <b class='text-slate-700 line-through decoration-slate-300'>편당 5~15만원</b></span></div>"
            f"<div class='text-lg font-bold text-slate-900 mt-3'>올린다는 둘 다 포함해 "
            f"<span class='text-indigo-600'>월 12만 9천원부터</span></div>"
            f"<div class='text-[11px] text-slate-400 mt-2'>대행 시세는 2026년 8월 공개 마켓 실판매가 기준</div></div>"
            f"<p class='reveal text-center text-slate-500 mb-14'>"
            f"올린다는 실사 무빙 영상까지 통째로, 지금 가격은 런칭 기간 한정입니다.</p>"
            f"<div class='grid sm:grid-cols-3 gap-6 items-stretch pt-3'>{cards}</div>"
            f"<p class='reveal text-center text-xs text-slate-400 mt-8'>언제든 해지 가능 — 해지 후 다음 결제일부터 청구되지 않아요 · 남은 기간은 그대로 이용</p>"
            f"</div></section>")


_QA = [("정말 사진만 올리면 되나요?", "네. 사진과 한 줄 설명만 주시면 AI가 5채널 콘텐츠를 만듭니다. 사진 1장만 있어도 자막·음성이 들어간 세로 숏폼까지 자동 생성됩니다."),
       ("쿠팡·11번가 셀러도 되나요?", "네. '온라인 셀러'로 설정하면 글 마무리가 지도 대신 구매 링크/검색어로, 키워드가 지역명 대신 상품·후기 키워드로 자동 전환됩니다. (쿠팡은 직링크 정책상 '검색어 유도'를 권장)"),
       ("제 SNS 비밀번호를 줘야 하나요?", "아니요. 공식 OAuth로 한 번만 권한을 허용하면 됩니다. 비밀번호는 저장하지 않습니다."),
       ("네이버 블로그도 되나요?", "글·사진을 완성해 드리고, 임시저장된 글을 네이버에서 발행만 누르시면 됩니다. (네이버는 공식 발행 API가 없어 반자동)"),
       ("업종이 특이해도 되나요?", "어떤 업종이든 AI가 맞춤 프로필을 자동 생성합니다."),
       ("해지는 어떻게 하나요?", "언제든 해지할 수 있습니다. 문의하기(이메일 포함)로 요청하시면 바로 처리해 드리고, 해지 후 다음 결제일부터는 청구되지 않습니다. 이미 결제한 기간은 그대로 이용 가능합니다.")]


def _docs_download() -> str:
    """제품설명서 PDF·소개 영상 다운로드 스트립(2026-07-31 사장님 지시) — 영업·검토용 자료 제공."""
    return (
        "<section class='bg-white py-14'><div class='max-w-3xl mx-auto px-5'>"
        "<div class='reveal bg-[#EEF2FF] border border-indigo-100 rounded-3xl p-8 text-center'>"
        "<h3 class='text-xl font-bold text-slate-900 mb-2'>천천히 검토하고 싶으세요?</h3>"
        "<p class='text-sm text-slate-500 mb-6'>제품설명서와 1분 소개 영상을 받아서 보시고, 팀·가족과 상의 후 시작하셔도 됩니다.</p>"
        "<div class='flex flex-wrap justify-center gap-3'>"
        f"<a href='{_v('/docs/guide.pdf')}' class='px-5 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-bold transition'>"
        "📄 제품설명서 PDF 받기</a>"
        f"<a href='{_v('/docs/intro.mp4')}' class='px-5 py-3 rounded-xl bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 text-sm font-bold transition'>"
        "🎬 1분 소개 영상 받기</a>"
        "</div></div></div></section>")


def _faq() -> str:
    items = "".join(f"<details class='reveal card p-5'><summary class='font-semibold cursor-pointer text-slate-800'>{q}</summary><p class='text-slate-500 text-sm mt-2'>{a}</p></details>" for q, a in _QA)
    return f"<section id='faq' class='bg-white py-24'><div class='max-w-3xl mx-auto px-5'><h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-12 text-slate-900'>자주 묻는 질문</h2><div class='space-y-3'>{items}</div></div></section>"


def _seo_jsonld() -> str:
    """검색 리치결과용 구조화 데이터 — Organization + WebSite + FAQPage(구글 FAQ 노출)
    + SoftwareApplication(가격은 config 단일 소스 — 하드코딩 가격이 실판매가와 어긋났던 결함 봉합)."""
    import json
    from app import config as _cfg
    faq = {"@context": "https://schema.org", "@type": "FAQPage",
           "mainEntity": [{"@type": "Question", "name": q,
                           "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in _QA]}
    org = {"@context": "https://schema.org", "@type": "Organization", "name": "올린다",
           "url": BASE + "/", "logo": BASE + "/demo/og.png",
           "description": "소상공인·온라인 셀러를 위한 네이버 상위노출 최적화 AI 마케팅 콘텐츠 생성 서비스"}
    site = {"@context": "https://schema.org", "@type": "WebSite", "name": "올린다",
            "url": BASE + "/", "inLanguage": "ko-KR"}
    app_ = {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "올린다",
            "applicationCategory": "BusinessApplication", "operatingSystem": "Web",
            "offers": {"@type": "AggregateOffer", "priceCurrency": "KRW",
                       "lowPrice": str(_cfg.PRICE_BASIC), "highPrice": str(_cfg.AGENCY_FROM),
                       "offerCount": "3"}}
    return "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>'
                   for x in (org, site, faq, app_))


def _contact() -> str:
    f = "w-full border border-slate-200 rounded-xl px-4 py-3 text-sm outline-none focus:border-indigo-400"
    return f"""
<section id="contact" class="bg-[#F9FAFB] py-24"><div class="max-w-3xl mx-auto px-5">
 <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">문의하기</h2>
 <p class="reveal text-center text-slate-500 mb-10">올린다 도입·대행 상담을 무료로 도와드립니다.</p>
 <form id="contactForm" class="reveal card p-6 grid sm:grid-cols-2 gap-3">
  <input name="company" placeholder="상호/회사명 *" required class="{f}">
  <input name="manager" placeholder="담당자 *" required class="{f}">
  <input name="phone" placeholder="연락처 *" required class="{f}">
  <input name="email" type="email" placeholder="이메일 *" required class="{f}">
  <textarea name="message" placeholder="문의 내용" rows=3 class="{f} sm:col-span-2"></textarea>
  <button class="bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-3.5 rounded-xl sm:col-span-2 transition">문의하기</button>
  <p id="contactMsg" class="text-center text-sm text-slate-600 sm:col-span-2"></p>
 </form>
 <p class="text-center text-slate-400 text-xs mt-3">{_kakao_contact_line()}이메일 {CONTACT_EMAIL}</p>
</div></section>"""


def _kakao_channel_url() -> str:
    """카카오톡 채널(상담) URL — 환경변수로만 켠다. 없으면 관련 UI·문구를 일절 내지 않는다
    (존재하지 않는 '우측 하단 상담 버튼'을 안내하던 허위 카피 봉합, 2026-08-09)."""
    return os.environ.get("KAKAO_CHANNEL_URL", "").strip()


def _kakao_contact_line() -> str:
    return "카카오톡 상담 버튼(우측 하단) · " if _kakao_channel_url() else ""


def _kakao_float() -> str:
    """우측 하단 카카오톡 상담 플로팅 버튼 — KAKAO_CHANNEL_URL 설정 시에만 렌더.
    모바일은 하단 스티키 CTA 위로 띄운다(safe-area 포함)."""
    url = _kakao_channel_url()
    if not url:
        return ""
    return (f'<a href="{url}" target="_blank" rel="noopener" aria-label="카카오톡 상담" '
            'onclick="trackEv(\'kakao_chat\',{})" '
            'class="fixed right-4 z-50 w-14 h-14 rounded-full shadow-lg flex items-center justify-center '
            'bottom-24 sm:bottom-6" style="background:#FEE500">'
            '<svg viewBox="0 0 24 24" class="w-7 h-7" fill="#191600">'
            '<path d="M12 3C6.48 3 2 6.54 2 10.9c0 2.8 1.86 5.26 4.66 6.65-.15.52-.97 3.36-1 3.58 0 0-.02.17.09.24.11.07.24.02.24.02.32-.04 3.66-2.4 4.24-2.81.57.08 1.16.12 1.77.12 5.52 0 10-3.54 10-7.9S17.52 3 12 3z"/></svg></a>')


def _cta() -> str:
    return f"""
<section id="cta" class="bg-[#F5F3FF] py-28">
 <div class="max-w-3xl mx-auto px-5 text-center">
  <h2 class="reveal text-4xl sm:text-5xl font-bold leading-tight text-slate-900">오늘 사진 한 장,<br><span class="text-indigo-600">내일 손님으로</span></h2>
  <p class="reveal mt-6 text-slate-500 text-lg">지금 시작하면 첫 콘텐츠 세트를 무료로 만들어 드립니다.</p>
  <div class="reveal mt-10 flex flex-col sm:flex-row gap-3 justify-center">
   <a href="/login/kakao" class="px-9 py-4 rounded-2xl font-extrabold text-lg" style="background:#FEE500;color:#191600">카카오로 시작하기</a>{_naver_cta_btn()}
   <a href="/login/google" class="flex items-center justify-center gap-2 px-9 py-4 rounded-2xl font-extrabold text-lg bg-white border border-slate-200 text-slate-700"><svg width="22" height="22" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg> 구글로 시작하기</a></div>
 </div></section>"""


def _mail_order_no() -> str:
    """통신판매업 신고번호 — 발급 전엔 표기하지 않는다(없는 번호를 지어내지 않는다).
    발급되면 SHOPCAST_MAIL_ORDER_NO 환경변수로 즉시 표기."""
    return os.environ.get("SHOPCAST_MAIL_ORDER_NO", "").strip()


def _footer() -> str:
    mo = _mail_order_no()
    mo_row = (f'<div class="text-slate-400 text-xs">통신판매업 신고번호</div><div class="mb-2">{mo}</div>'
              if mo else "")
    mo_line = f" · 통신판매업 {mo}" if mo else ""
    return f"""
<footer class="bg-[#F9FAFB] border-t border-slate-200 text-slate-500 pt-14 pb-10">
 <div class="max-w-6xl mx-auto px-5">
  <div class="flex items-center gap-2 font-extrabold text-xl mb-6">{LOGO}<span class="text-slate-900">올린다</span></div>
  <div class="grid sm:grid-cols-2 gap-6 text-sm">
   <div>
    <div class="text-slate-400 text-xs mb-1">CEO</div><div class="font-bold text-slate-800 mb-2">{BIZ_CEO}</div>
    <div class="text-slate-400 text-xs">사업자등록번호</div><div class="mb-2">{BIZ_REG_NO}</div>
    {mo_row}
    <div class="text-slate-400 text-xs">Location</div><div>{BIZ_ADDR}</div>
   </div>
   <div>
    <div class="card p-4 text-sm">
      <p class="font-semibold text-slate-800 mb-1">올린다는 이렇게 만들었습니다</p>
      <p class="text-slate-500 text-xs">실제 소상공인·중고차 매장 현장 요구에서 출발해, AI(글·비전·TTS·영상)와 네이버 상위노출 노하우를 결합해 개발했습니다.</p>
     </div>
    <div class="mt-4 flex flex-wrap gap-3 text-sm">
     <a href="#contact" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">문의하기</a>
     <a href="mailto:{CONTACT_EMAIL}" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">이메일</a>
     <a href="/terms" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">이용약관</a>
     <a href="/privacy" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">개인정보처리방침</a></div>
   </div>
  </div>
  <div class="mt-8 pt-6 border-t border-slate-200 text-center text-xs text-slate-400 leading-relaxed">
    © 2026 올린다 (Ollinda) · 가피디자인 · 사업자등록번호 {BIZ_REG_NO}{mo_line}<br>
    문의 {CONTACT_EMAIL} · {BIZ_PHONE} · <a href="/terms" class="underline hover:text-slate-600">이용약관</a> · <a href="/privacy" class="underline hover:text-slate-600">개인정보처리방침</a> · <a href="/refund" class="underline hover:text-slate-600">환불정책</a> · SSL 보안 연결
  </div>
 </div></footer>"""


def _ga() -> str:
    """GA4(있으면) + 전환 이벤트 자동 추적(가입 클릭·데모 제출·스티키 CTA). 키 없으면 no-op."""
    import os
    gid = os.environ.get("GA_MEASUREMENT_ID", "").strip()
    ga = ""
    if gid:
        ga = (f'<script async src="https://www.googletagmanager.com/gtag/js?id={gid}"></script>'
              '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
              f'gtag("js",new Date());gtag("config","{gid}");</script>')
    tracker = ("<script>function trackEv(n,p){try{if(window.gtag)gtag('event',n,p||{});}catch(e){}}"
               "document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[href^=\"/login\"]');"
               "if(a){var m=a.href.indexOf('kakao')>-1?'kakao':(a.href.indexOf('google')>-1?'google':'login');trackEv('signup_click',{method:m});}});"
               "document.addEventListener('submit',function(e){if(e.target&&e.target.id==='demoForm')trackEv('demo_submit',{});});</script>")
    return ga + tracker


def _sticky_cta() -> str:
    """모바일 하단 고정 CTA — 스크롤 어디서든 전환 유도(모바일 전환율 핵심).
    iOS Safari 플로팅 주소창이 bottom:0 요소를 덮어 탭을 가로채는 실기기 버그 대응:
    env(safe-area-inset-bottom)은 브라우저 UI를 포함하지 않으므로 +12px 여유를 더해
    터치 타겟 전체를 주소창 위로 띄운다. onclick의 location.href는 폴백(기본 내비 실패 대비),
    href는 그대로 유지(JS 꺼져도 동작)."""
    return ('<div class="fixed bottom-0 left-0 right-0 z-50 sm:hidden bg-white/95 backdrop-blur border-t border-slate-200 px-3 pt-3" '
            'style="padding-bottom:max(28px,calc(env(safe-area-inset-bottom) + 12px))">'
            '<a href="/login/kakao" '
            'onclick="trackEv(\'sticky_cta\',{});window.location.href=\'/login/kakao\';return false;" '
            'class="block text-center py-3.5 rounded-xl font-extrabold text-white bg-indigo-600 '
            'active:scale-[.98] active:bg-indigo-700 transition" '
            'style="-webkit-tap-highlight-color:rgba(79,70,229,.25)">무료로 시작하기</a></div>')


def _naver_preview() -> str:
    """실제 생성된 네이버 블로그 글 미리보기(스크린 녹화 대신 진짜 새 카피를 보여줌 = 신뢰)."""
    title = "부산 동구 썬팅업체 후기, 포터2 냉동탑차 열차단 시공 팩트정리"
    body = (
        "화물차 타시는 사장님, 한여름 앞유리로 쏟아지는 햇빛에 팔뚝이 익는 느낌 받아보신 적 있으시죠? "
        "<span class='text-indigo-500 text-xs'>(← 검색 유입 손님 공감 = 이탈 방지)</span><br>"
        "오후 배송 돌 때 서쪽 햇빛 눈부심에 신호등이 순간 안 보이면 진짜 아찔합니다. "
        "그래서 오늘은 직접 시공한 <b>현대 포터2 냉동탑차 열차단 썬팅</b>을 처음부터 끝까지 보여드릴게요.<br><br>"
        "<b>■ 오늘의 케이스 — 포터2 냉동탑차 앞유리·측면</b><br>"
        "매일 장거리 배송 도는 냉동탑차 사장님 요청은 명확했어요. ‘더위랑 눈부심만 잡아달라.’ "
        "화물차는 유리 면적이 넓어 열차단 성능이 더 중요하죠. (내비·후방카메라는 옵션으로 함께) "
        "<span class='text-indigo-500 text-xs'>(← 손님 스토리 + 과정 = 신뢰·체류)</span>")
    tags = ["부산동구썬팅", "열차단썬팅", "포터2썬팅", "화물차썬팅"]
    tag_html = "".join(f"<span class='inline-block bg-slate-100 text-slate-500 text-xs px-2 py-1 rounded-full mr-1 mb-1'>#{t}</span>" for t in tags)
    return f"""
  <div class="reveal max-w-2xl mx-auto mt-6 card overflow-hidden">
   <div class="border-b border-slate-100 text-slate-600 text-sm font-bold px-5 py-3 flex items-center gap-2"><span class="bg-[#03c75a] text-white rounded px-1.5 text-xs font-extrabold">blog</span> 네이버 블로그 — AI가 쓴 실제 글 (사진 5장 기반)</div>
   <div class="p-6 text-left">
    <div class="text-lg font-bold text-slate-900 mb-2 leading-snug">{title}</div>
    <div class="text-xs text-slate-400 border-b border-slate-100 pb-2 mb-3">초량 루마썬팅 블로그 · 방금 전 · 조회 12</div>
    <p class="text-sm text-slate-700 leading-relaxed">{body}</p>
    <div class="mt-4">{tag_html}</div>
    <div class="mt-3 text-xs text-slate-400">손님 고민으로 시작 · 실제 검색되는 말 사용 · 없는 가격·스펙은 쓰지 않음 — 자동 적용</div>
   </div></div>"""


def _why_rank() -> str:
    """왜 상위노출 되나 — 2026 알고리즘을 '알고' 만든다 + 채널별 최적화(#2·#5)."""
    chans = [
        ("pen", "네이버 블로그", "네이버가 좋아하는 글 구조(C-Rank·D.I.A.+) · 손님 고민으로 시작해 끝까지 읽게 · 자주 묻는 질문·표·사진 배치"),
        ("play", "유튜브 쇼츠", "검색 키워드 제목, 30~45초·완주율·루프로 재노출"),
        ("video", "인스타 릴스", "3초 훅 + '저장·공유' 유도(도달 최강 신호), 해시태그 3~5개"),
        ("message", "X (트위터)", "외부링크 대신 검색 유도(도달 페널티 회피) + 답글 유발"),
        ("package", "쿠팡·스토어", "검색 최적화 상품명 3안 + 상세페이지 + 마켓 태그"),
    ]
    cards = "".join(
        f"<div class='reveal card p-5'>{_icon_chip(ic)}<div class='font-bold mb-1 text-slate-900'>{t}</div>"
        f"<p class='text-sm text-slate-500 leading-relaxed'>{d}</p></div>" for ic, t, d in chans)
    return f"""
<section class="bg-white py-24">
 <div class="max-w-6xl mx-auto px-5">
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">아무 말이나 <span class="text-indigo-600">쓰지 않습니다</span></h2>
  <p class="reveal text-center text-slate-500 mb-8 max-w-2xl mx-auto">손님이 <b class="text-slate-800">실제로 검색하는 말</b>을 찾아서, 그 말에 답하는 글을 씁니다.
   지어낸 말로 채우면 아무도 안 찾아옵니다.</p>
  <!-- ★ 2026-08-13: 여기가 랜딩에서 가장 어려운 대목이었다. C-Rank·D.I.A.+·PAS·롱테일 같은
       만드는 사람 말이 그대로 나와 있었다(헌법: 사장님 화면에 주방 용어 금지).
       채널별 상세는 지우지 않고 접어둔다 — 알고 싶은 사람만 펴 보면 된다. -->
  <details class="reveal">
   <summary class="cursor-pointer list-none text-center text-sm font-bold text-indigo-600 hover:underline py-3">
    채널마다 어떻게 다르게 만드나요? ▾</summary>
   <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-4 mb-6">{cards}</div>
   <div class="card-hi p-5 text-center">
    <div class="text-base font-bold text-slate-900">네이버 검색광고 데이터를 그대로 씁니다</div>
    <div class="text-sm text-slate-500 mt-1">한 달에 몇 명이 그 말을 검색하는지 실제 숫자를 보고 고릅니다 — 지어낸 말로 쓰지 않습니다</div>
   </div>
  </details>
 </div></section>"""


def _vacantq() -> str:
    """빈자리 글감 — '뭘 올릴지 모른다'(문제 섹션)의 직접 해답. 실제 대시보드 카드 UI 재현."""
    mock = ("<div class='reveal card p-5 text-left'>"
            "<div class='text-sm font-bold text-slate-800 mb-0.5'>먼저 쓰면 좋은 이야기</div>"
            "<div class='text-xs text-slate-400 mb-3'>손님들이 찾는데 아직 답이 없는 것들이에요</div>"
            "<div class='space-y-2'>"
            "<div class='bg-[#EEF2FF] border border-indigo-100 rounded-xl px-3.5 py-3'>"
            "<div class='text-sm text-slate-800 font-medium'>겨울에 시공해도 괜찮은지 궁금해하는 분들이 많아요</div>"
            "<div class='text-xs text-indigo-500 mt-1'>아직 이 질문에 답한 글이 없어요 — 사진만 올리시면 저희가 써요</div></div>"
            "<div class='bg-[#EEF2FF] border border-indigo-100 rounded-xl px-3.5 py-3'>"
            "<div class='text-sm text-slate-800 font-medium'>시공 후 관리법을 찾는 검색이 늘고 있어요</div>"
            "<div class='flex gap-2 mt-1.5'><span class='text-xs font-bold text-indigo-600'>이걸로 쓸래요</span>"
            "<span class='text-xs text-slate-400'>저희는 안 해요</span></div></div></div>"
            "<div class='text-[11px] text-slate-400 mt-3'>실제 화면 구성 — 글감 내용은 가게마다 달라요</div></div>")
    return f"""
<section class="bg-white py-24">
 <div class="max-w-5xl mx-auto px-5">
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">뭘 쓸지도, <span class="text-indigo-600">저희가 찾아옵니다</span></h2>
  <p class="reveal text-center text-slate-500 mb-12 max-w-2xl mx-auto">네이버 검색을 정찰해서 <b class="text-slate-800">자리는 있는데 아직 아무도 답하지 않은 질문</b>을 찾아 글감으로 올려드려요. 사장님은 고르기만 하면 됩니다 — "저희는 안 해요" 한 번이면 그 얘긴 다시 안 꺼내요.</p>
  <div class="grid sm:grid-cols-2 gap-8 items-center">
   {mock}
   <div class="reveal space-y-4">
    <div class="flex gap-3"><span class="text-indigo-600 shrink-0 mt-0.5">{_icon('search', 'w-5 h-5')}</span>
     <p class="text-sm text-slate-600"><b class="text-slate-800">검색 지면 정찰</b> — 손님들이 실제로 치는 검색어 중, 첫 화면에 블로그 자리가 있는 판만 골라요.</p></div>
    <div class="flex gap-3"><span class="text-indigo-600 shrink-0 mt-0.5">{_icon('target', 'w-5 h-5')}</span>
     <p class="text-sm text-slate-600"><b class="text-slate-800">빈자리 판별</b> — 그 질문에 답한 글이 없으면, 먼저 쓰는 가게가 그 자리를 가져갑니다.</p></div>
    <div class="flex gap-3"><span class="text-indigo-600 shrink-0 mt-0.5">{_icon('camera', 'w-5 h-5')}</span>
     <p class="text-sm text-slate-600"><b class="text-slate-800">사장님은 사진만</b> — 글감을 고르고 사진을 올리면, 글·영상·발행 준비는 올린다가 해요.</p></div>
   </div>
  </div>
 </div></section>"""


def _rank_loop() -> str:
    """관측-적응 루프 전면 — '글 쓰고 끝'과의 결정적 차이. 발행 후가 본편이다."""
    steps = [
        ("chart", "1. 매일 실측", "발행된 <b class='text-slate-800'>모든 글의 네이버 순위</b>를 매일 자동으로 확인해요 — 사장님이 발행한 글도, 예전 글도 전부."),
        ("scan", "2. 변화 감지", "순위가 떨어지거나 검색에서 사라지면 <b class='text-slate-800'>사장님보다 먼저</b> 알아챕니다."),
        ("pen", "3. 고쳐서 제안", "떨어진 글의 <b class='text-slate-800'>개선판을 실제로 만들어</b> 카드로 가져와요. 자동 발행은 하지 않아요 — 발행 버튼은 언제나 사장님 몫."),
        ("refresh", "4. 회복 확인", "개선판을 발행하면 <b class='text-slate-800'>회복되는지 다시 실측</b>으로 지켜봐요. 효과 있던 방법은 다음 글에 학습됩니다."),
    ]
    cards = "".join(
        f"<div class='reveal {'card-hi' if i == 2 else 'card'} p-5'>{_icon_chip(ic)}"
        f"<div class='font-bold mb-1 text-slate-900'>{t}</div>"
        f"<p class='text-sm text-slate-500 leading-relaxed'>{d}</p></div>" for i, (ic, t, d) in enumerate(steps))
    return f"""
<section class="bg-[#F9FAFB] py-24">
 <div class="max-w-6xl mx-auto px-5">
  <div class="text-center mb-4"><span class="reveal inline-block px-3 py-1 rounded-full bg-white border border-indigo-100 text-xs font-bold text-indigo-600">글 뽑는 도구가 아니라, 지켜보는 직원</span></div>
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">글은 쓰는 날이 아니라 <span class="text-indigo-600">떨어지는 날</span>이 문제입니다</h2>
  <p class="reveal text-center text-slate-500 mb-12 max-w-2xl mx-auto">대부분의 AI 툴은 글을 뱉고 끝나요. 올린다는 <b class="text-slate-800">발행한 뒤부터가 본편</b>입니다 — 매일 지켜보다가, 떨어지면 고친 글을 먼저 내밉니다.</p>
  <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">{cards}</div>
  <p class="reveal text-center text-xs text-slate-400 mt-8">※ 정직 원칙: 가짜 순위·"무조건 1위" 보장은 하지 않습니다. 실측 순위와 사실 기반 제안만 드려요.</p>
 </div></section>"""


def _briefing_sell() -> str:
    """매일 아침 브리핑 셀링(브리핑 PHASE 5) — '혼자 고민 안 해도 돼요' 파트너 포지셔닝."""
    steps = [("message", "아침 8시, 브리핑 도착", "\"'부산 국밥'에서 옆집이 사장님보다 위예요. 오늘 이 키워드 글 한 편이면 추격이 시작돼요.\""),
             ("camera", "사장님은 사진 3장만", "오늘 할 일은 딱 하나로 줄여드려요. 글·영상·발행 준비는 올린다가 해요."),
             ("trend", "저녁엔 성과 피드백", "\"오늘 올린 글로 3명이 들어왔어요. 순위는 4위→2위로 움직이는 중.\"")]
    cards = "".join(
        f"<div class='reveal card p-5'>{_icon_chip(ic)}<div class='font-bold mb-1 text-slate-900'>{t}</div>"
        f"<p class='text-sm text-slate-500 leading-relaxed'>{d}</p></div>" for ic, t, d in steps)
    return f"""
<section class="bg-[#F5F3FF] py-24">
 <div class="max-w-6xl mx-auto px-5">
  <div class="text-center mb-4"><span class="reveal inline-block px-3 py-1 rounded-full bg-white border border-indigo-100 text-xs font-bold text-indigo-600">AI 사장님 파트너</span></div>
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">매일 아침, 올린다가 <span class="text-indigo-600">먼저 알려드려요</span></h2>
  <p class="reveal text-center text-slate-500 mb-12 max-w-2xl mx-auto">오늘 뭘 올릴지 혼자 고민하지 마세요. 순위·경쟁·검색 데이터를 보고 <b class="text-slate-800">오늘 할 일 딱 하나</b>를 브리핑해드려요.</p>
  <div class="grid sm:grid-cols-3 gap-4">{cards}</div>
  <p class="reveal text-center text-xs text-slate-400 mt-6">※ 브리핑은 실측 데이터만 사용해요 — 특별한 변화가 없는 날엔 정직하게 "변화 없음"이라고 알려드려요.</p>
 </div></section>"""


def _copy_compare() -> str:
    """'그냥 글' vs '팔리는 글' before/after(#4)."""
    before = ("안녕하세요~ 저희 루마썬팅입니다 😊<br>오늘도 열심히 시공했어요!<br>"
              "저희는 좋은 필름으로 정성껏 작업합니다.<br>많은 관심 부탁드려요~")
    after = ("운전할 때 앞유리 햇빛에 눈 시리고, 신호 대기만 해도 얼굴 화끈거린 적 있으시죠?<br>"
             "<span class='text-indigo-500'>(← 검색해서 들어온 손님 공감 = 이탈 방지)</span><br>"
             "오늘 오신 검은 SUV 손님도 그 고민이었어요. 그래서 열차단 세라믹으로 시공한 과정, 그대로 보여드릴게요…<br>"
             "<span class='text-indigo-500'>(← 손님 스토리 + 과정 = 신뢰·체류)</span>")
    return f"""
<section class="bg-[#F9FAFB] py-24">
 <div class="max-w-5xl mx-auto px-5">
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">‘그냥 글’ vs <span class="text-indigo-600">‘팔리는 글’</span></h2>
  <p class="reveal text-center text-slate-500 mb-12">같은 사진, 같은 가게. 글이 다르면 결과가 다릅니다.</p>
  <div class="grid sm:grid-cols-2 gap-5">
   <div class="reveal rounded-2xl p-6 bg-white border border-slate-200">
    <div class="text-xs font-bold text-slate-400 mb-3">흔한 AI 글</div>
    <p class="text-sm text-slate-500 leading-relaxed">{before}</p></div>
   <div class="reveal rounded-2xl p-6 bg-white border-2 border-indigo-500">
    <div class="text-xs font-bold text-indigo-600 mb-3">올린다 — 손님 고민으로 시작하는 글</div>
    <p class="text-sm text-slate-700 leading-relaxed">{after}</p></div>
  </div>
 </div></section>"""


def _honesty() -> str:
    """정직 원칙 — 신뢰 = 전환(#3)."""
    badges = [("xcircle", "가격 날조 안 함", "20만원짜리를 3만원이라 안 씁니다"),
              ("xcircle", "허위 스펙 안 함", "없는 성능·효능 지어내지 않습니다"),
              ("xcircle", "가짜 후기 안 함", "‘내돈내산’ 사칭 없이 판매자 시연으로"),
              ("shield", "표시광고법 안전", "믿고 배포해도 되는 콘텐츠")]
    cells = "".join(f"<div class='reveal text-center'>"
                    f"<div class='mx-auto w-11 h-11 rounded-xl bg-white border border-slate-200 text-slate-400 flex items-center justify-center mb-3'>{_icon(ic, 'w-5 h-5')}</div>"
                    f"<div class='font-bold text-sm mb-1 text-slate-800'>{t}</div><div class='text-xs text-slate-500'>{d}</div></div>"
                    for ic, t, d in badges)
    return f"""
<section class="bg-[#F9FAFB] py-24">
 <div class="max-w-4xl mx-auto px-5 text-center">
  <h2 class="reveal text-3xl sm:text-4xl font-bold mb-3 text-slate-900">없는 건 <span class="text-indigo-600">지어내지 않습니다</span></h2>
  <p class="reveal text-slate-500 mb-14 max-w-xl mx-auto">허위 콘텐츠는 차라리 안 만드는 게 낫습니다. 손님을 속이면 신뢰를 잃으니까요. <b class="text-slate-700">올린다는 사진과 사장님이 준 정보로만</b> 정직하게 씁니다.</p>
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-6">{cells}</div>
  <!-- ★ 2026-08-13: 경험 자산 약속(_experience_strip)을 여기로 압축했다. 섹션을 지우면서
       약속까지 지우면 안 된다 — 이건 꾸밈이 아니라 우리가 지키기로 한 계약이다. -->
  <div class="reveal card p-5 mt-12 flex items-center gap-4 text-left max-w-2xl mx-auto">
   <span class="shrink-0 w-11 h-11 rounded-xl bg-[#EEF2FF] text-indigo-600 flex items-center justify-center">{_icon('check', 'w-5 h-5')}</span>
   <div><div class="font-bold text-sm text-slate-800">한 번 답하면, 계속 씁니다</div>
   <div class="text-xs text-slate-500 mt-0.5">글을 만들 때 그 주제로 딱 한 가지만 여쭤봐요. 답하신 경험은 다음 글에 자동으로 들어가고, 쌓일수록 질문이 줄어듭니다. 답이 없는 주제는 지어내지 않고 사실 기반 글로 먼저 나갑니다.</div></div>
  </div>
  <div class="reveal card p-5 mt-4 flex items-center gap-4 text-left max-w-2xl mx-auto">
   <span class="shrink-0 w-11 h-11 rounded-xl bg-[#EEF2FF] text-indigo-600 flex items-center justify-center">{_icon('shield', 'w-5 h-5')}</span>
   <div><div class="font-bold text-sm text-slate-800">SNS 비밀번호는 받지 않습니다</div>
   <div class="text-xs text-slate-500 mt-0.5">채널 연결은 공식 인증(OAuth)으로만 해요. 비밀번호를 달라는 마케팅 업체는 사장님 계정을 통째로 위험에 빠뜨립니다.</div></div>
  </div>
 </div></section>"""


def _visit_bar(visits: int, today: int = 0) -> str:
    """방문자 수 표시(2026-08-11 사장님 지시) — 날조 없이 서버 실집계값만.
    ★ 일 방문자 100명 넘을 때까지는 숨긴다(초기 작은 숫자 역효과 방지, 사장님 지시).
    노출 시엔 누적 총량을 보여준다. OLLINDA_VISITS_BASE(env)로 과거 GA 실적 기준선 추가 가능."""
    import os as _os
    if (today or 0) < 100:                       # 하루 100명 넘어야 노출
        return ""
    total = (visits or 0) + int(_os.environ.get("OLLINDA_VISITS_BASE", "0") or 0)
    return (f"<div class='text-center text-xs text-slate-400 py-2 bg-slate-50 border-b border-slate-100'>"
            f"지금까지 <b class='text-indigo-600'>{total:,}명</b>이 올린다를 둘러봤어요</div>")


def render(visits: int = 0, today: int = 0) -> str:
    # 전환 논리 순서(2026-08-09 재배치 — 코드가 실제로 하는 일 순서로 판다):
    # ① 히어로(가치+CTA+진단·체험) → ② 문제 공감 → ③ 빈자리 글감(문제의 직접 해답: 뭘 쓸지 찾아옴)
    # → ④ 실물 증명(영상·블로그)+글 비교 → ⑤ 성과·실측 타임라인(증거를 앞으로)
    # → ⑥ 관측-적응 루프(지켜보는 직원 — 핵심 차별) → ⑦ 채널 알고리즘 → ⑧ 브리핑
    # → ⑨ 경험 자산(질문이 줄어듦) → ⑩ 정직+비밀번호 신뢰 → ⑪ 기능·모드 → ⑫ 요금 → ⑬ 마지막 CTA
    # gtag는 <head> 안이 구글 권장 위치(서치콘솔 GA 소유확인도 head 기준) — body로 내리지 말 것
    return (_HEAD_META + _ga() + _BODY_OPEN + _seo_jsonld() + _nav()
            + _visit_bar(visits, today)
            # ★ 2026-08-13 사장님 지시: "랜딩이 너저분하다 · 실제로 하는 것만 간단명료하게"
            #   18섹션 → 8섹션. 순서 = ①무슨 일이 일어나는지(실물 4단계) ②지금 내 가게로
            #   무료로 해보기 ③진짜 결과물 ④정직 원칙 ⑤요금 ⑥FAQ·문의.
            #   뺀 것(_problem·_copy_compare·_rank_loop·_why_rank·_briefing_sell·
            #   _experience_strip·_features·_modes·_vacantq)은 '설명·중복·기능 나열'이다.
            #   기능 나열은 결정에 필요한 정보가 아니라 이미 결정한 사람이 확인하는 정보다.
            #   ※ 함수는 지우지 않는다 — 되돌릴 수 있어야 한다.
            + _hero() + _flow() + _try()
            + _video() + _honesty()
            + _pricing() + _docs_download() + _faq() + _contact() + _cta() + _footer()
            + _sticky_cta() + _kakao_float() + _FOOT)


def terms() -> str:
    """이용약관 — 유료 구독 판매 사이트의 기본 고지(전자상거래법). 문구는 운영자 법률 검토 대상."""
    body = f"""
<div class="max-w-3xl mx-auto px-5 py-16">
 <a href="/" class="text-indigo-600 text-sm">← 홈</a>
 <h1 class="text-3xl font-bold mt-4 mb-8 text-slate-900">이용약관</h1>
 <div class="space-y-4 text-sm text-slate-600 leading-relaxed">
  <p><b>제1조 (목적)</b> — 본 약관은 올린다(이하 "서비스")의 이용 조건과 회사·이용자의 권리·의무를 정합니다.</p>
  <p><b>제2조 (서비스 내용)</b> — 서비스는 이용자가 업로드한 사진·정보를 바탕으로 AI 마케팅 콘텐츠(글·이미지·영상)를
   생성하고, 이용자가 연결한 채널에 발행을 지원하며, 검색 노출 현황을 제공합니다.
   네이버 블로그는 공식 발행 API가 없어 반자동(복사·붙여넣기) 방식으로 지원합니다.</p>
  <p><b>제3조 (요금·결제)</b> — 유료 플랜은 월 단위 자동 결제이며, 가격은 사이트 요금 안내에 따릅니다.
   가격 변경 시 기존 구독자에게는 사전 고지합니다.</p>
  <p><b>제4조 (해지·환불)</b> — 이용자는 언제든 해지를 요청할 수 있습니다(문의하기·이메일).
   해지 시 다음 결제일부터 청구되지 않으며, 이미 결제한 이용 기간은 그대로 이용할 수 있습니다.
   결제 후 7일 이내·서비스 미사용 시 전액 환불을 요청할 수 있습니다.</p>
  <p><b>제5조 (콘텐츠 책임)</b> — 생성 콘텐츠의 최종 발행 여부는 이용자가 결정하며, 발행된 콘텐츠에 대한
   법적 책임은 발행 주체인 이용자에게 있습니다. 서비스는 사실 기반 생성(없는 가격·스펙·후기를 지어내지 않음)을
   원칙으로 하나, 발행 전 확인을 권장합니다.</p>
  <p><b>제6조 (검색 노출)</b> — 서비스는 검색 노출에 유리한 구조의 콘텐츠와 실측 데이터를 제공할 뿐,
   특정 순위·노출을 보장하지 않습니다.</p>
  <p><b>제7조 (사업자 정보)</b> — {BIZ_CEO} · 사업자등록번호 {BIZ_REG_NO} ·
   {BIZ_ADDR} · 문의 {CONTACT_EMAIL}</p>
 </div></div>"""
    return _HEAD + _nav() + body + _footer() + _FOOT


def privacy() -> str:
    body = f"""
<div class="max-w-3xl mx-auto px-5 py-16">
 <a href="/" class="text-indigo-600 text-sm">← 홈</a>
 <h1 class="text-3xl font-bold mt-4 mb-8 text-slate-900">개인정보처리방침</h1>
 <div class="space-y-4 text-sm text-slate-600 leading-relaxed">
  <p>올린다(이하 "서비스")는 이용자의 개인정보를 중요시하며 관련 법령을 준수합니다.</p>
  <p><b>1. 수집 항목</b> — 이메일, 가게 정보, 업로드 사진/메모, 연결한 SNS 발행 권한 토큰(비밀번호 미수집).</p>
  <p><b>2. 이용 목적</b> — 콘텐츠 생성 및 이용자가 연결한 채널 게시(발행) 대행.</p>
  <p><b>3. SNS 연동</b> — 공식 OAuth 사용, 게시 권한 토큰만 보관. 언제든 연결 해제 가능.</p>
  <p><b>4. 보관·파기</b> — 해지/요청 시 지체 없이 파기.</p>
  <p><b>5. 사업자</b> — {BIZ_CEO} · {BIZ_REG_NO} · {BIZ_ADDR}</p>
  <p><b>6. 문의</b> — {CONTACT_EMAIL}</p>
 </div></div>"""
    return _HEAD + _nav() + body + _footer() + _FOOT


def intro() -> str:
    """홍보 전용 초경량 페이지(/intro) — 쪽지·서이추 등 외부 링크로 유입되는 첫 화면.
    영상 하나 + CTA 하나만. 조회·재생·가입클릭은 GA로 계측(_ga의 signup_click 리스너 재사용)."""
    body = f"""
<div class="min-h-screen flex flex-col items-center justify-center px-5 py-10">
 <div class="w-full max-w-xl text-center">
  <div class="flex items-center justify-center gap-2 font-extrabold text-2xl mb-3">{LOGO}<span class="text-slate-900">올린다</span></div>
  <h1 class="text-xl sm:text-2xl font-bold text-slate-900 mb-2">사진만 올리면, 네이버에 올라갈 글이 됩니다</h1>
  <p class="text-sm text-slate-500 mb-6">소상공인 사장님을 위한 AI 마케팅 직원 — 1분 영상으로 확인하세요.</p>
  <video controls preload="metadata" playsinline class="w-full rounded-2xl border border-slate-200 shadow-lg mb-6"
   src="{_v('/docs/intro.mp4')}" onplay="trackEv('intro_video_play',{{}})"></video>
  <a href="/" class="block w-full text-center py-4 rounded-xl font-extrabold text-white bg-indigo-600 hover:bg-indigo-700 transition">무료로 시작하기</a>
  <div class="mt-4 text-sm text-slate-500">궁금하신 점은 편하게 연락 주세요<br>
   <a href="mailto:{CONTACT_EMAIL}" class="text-indigo-600 font-semibold">{CONTACT_EMAIL}</a>
   <span class="text-slate-300 mx-1">·</span>
   <a href="tel:{BIZ_PHONE.replace('-','')}" class="text-indigo-600 font-semibold">{BIZ_PHONE}</a></div>
  <p class="text-xs text-slate-400 mt-3"><a href="/login" class="underline">이미 회원이신가요? 로그인</a></p>
 </div>
</div>"""
    return _HEAD_META + _ga() + _BODY_OPEN + body + _FOOT


def refund() -> str:
    """환불정책 전용 페이지 — 결제사(PG/패들) 도메인 심사 요건. 문안은 이용약관 제4조와
    동일 정책이어야 한다(두 페이지가 다른 말을 하면 그 자체가 심사 탈락 사유)."""
    body = f"""
<div class="max-w-3xl mx-auto px-5 py-16">
 <a href="/" class="text-indigo-600 text-sm">← 홈</a>
 <h1 class="text-3xl font-bold mt-4 mb-8 text-slate-900">환불정책</h1>
 <div class="space-y-4 text-sm text-slate-600 leading-relaxed">
  <p><b>제1조 (전액 환불·청약철회)</b> — 결제 후 7일 이내이고 서비스를 사용하지 않은 경우,
   전액 환불을 요청할 수 있습니다.</p>
  <p><b>제2조 (구독 해지)</b> — 이용자는 언제든 해지를 요청할 수 있습니다(문의하기·이메일).
   해지 시 다음 결제일부터 청구되지 않으며, 이미 결제한 이용 기간은 그대로 이용할 수 있습니다.
   이미 결제한 월 요금은 일할 환불 대신 남은 기간의 서비스 이용으로 제공됩니다.</p>
  <p><b>제3조 (회사 귀책)</b> — 회사의 귀책 사유로 서비스를 정상 제공하지 못한 경우,
   관련 법령에 따라 환불 또는 이용 기간 연장으로 보상합니다.</p>
  <p><b>제4조 (신청 방법·처리)</b> — 환불은 문의하기 또는 이메일({CONTACT_EMAIL})로
   요청할 수 있습니다. 확인 후 원 결제 수단으로 환급하며, 전자상거래법 등 관련 법령이
   정한 기한 내에 지체 없이 처리합니다.</p>
  <p><b>제5조 (사업자 정보)</b> — {BIZ_CEO} · 사업자등록번호 {BIZ_REG_NO} ·
   {BIZ_ADDR} · 문의 {CONTACT_EMAIL}</p>
 </div></div>"""
    return _HEAD + _nav() + body + _footer() + _FOOT
