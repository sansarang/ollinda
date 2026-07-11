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
CONTACT_EMAIL = "etetetetet5ea@kakao.com"
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
</style>"""

_HEAD = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>올린다 — 네이버 검색 상위노출에 유리한 AI 마케팅</title>
<meta name=description content="사진만 올리면 AI가 네이버 블로그·플레이스 상위노출에 유리한 글을 씁니다. 인스타·유튜브·릴스·X까지 자동 생성(네이버는 초안 반자동 발행). 소상공인 AI 마케팅 올린다.">
<meta name=keywords content="AI 마케팅,소상공인 마케팅,셀러 마케팅,인스타 자동 업로드,네이버 블로그 자동,유튜브 쇼츠 자동,콘텐츠 자동화,SNS 대행,쿠팡 마케팅,올린다,Ollinda">
<meta name=robots content="index,follow,max-image-preview:large,max-snippet:-1">
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
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.min.css" rel=stylesheet>
<script src="https://cdn.tailwindcss.com"></script>
<script type=application/ld+json>{"@context":"https://schema.org","@type":"SoftwareApplication","name":"올린다","applicationCategory":"BusinessApplication","offers":{"@type":"Offer","price":"29000","priceCurrency":"KRW"}}</script>
""".replace("__BASE__", BASE) + _STYLE + """</head><body class="bg-white text-slate-800 overflow-x-hidden pb-20 sm:pb-0">"""

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
 const esc=s=>(s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
 // AI 선추측 확인(스마트 입력 PHASE 2) — 무료·유료 공용(대시보드에서도 사용).
 // onDone: 사용자가 응답(맞아요/수정 저장)한 순간 호출 — 무료 위젯의 생성버튼 활성화 트리거.
 window.intakeConfirmUI=function(box,guess,analysis,cid,vid,onDone){
   var c=document.getElementById(cid),v=document.getElementById(vid);
   if(v)v.value=analysis||'';
   if(!guess){box.innerHTML='';if(c)c.value='';return;}
   box.innerHTML='<div class="bg-[#EEF2FF] border border-indigo-100 rounded-xl px-3 py-2.5 text-sm">'
     +'<div class="text-[11px] font-bold text-indigo-500 mb-0.5">확인해주세요</div>'
     +'<div class="text-slate-700">이 사진, <b>'+esc(guess)+'</b>(으)로 보여요. 맞나요?</div>'
     +'<div class="flex gap-2 mt-2"><button type="button" data-g="ok" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold">맞아요</button>'
     +'<button type="button" data-g="fix" class="px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 text-xs font-bold">수정할게요</button></div></div>';
   function confirmedLine(v){return '<div class="text-xs text-indigo-600 font-bold py-1 truncate cursor-pointer" '
     +'title="'+esc(v)+'" onclick="this.classList.toggle(&quot;truncate&quot;)">확인됨: '+esc(v)+'</div>';}
   box.querySelector('[data-g=ok]').onclick=function(){if(c)c.value=guess;
     box.innerHTML=confirmedLine(guess);   // 1줄 요약(넘치면 … · 탭하면 전체)
     onDone&&onDone();};
   box.querySelector('[data-g=fix]').onclick=function(){
     box.innerHTML='<div class="flex gap-2"><input id="'+cid+'_edit" value="'+esc(guess)+'" class="flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-indigo-400">'
       +'<button type="button" class="px-3 rounded-xl bg-indigo-600 text-white text-xs font-bold">저장</button></div>';
     box.querySelector('button').onclick=function(){var nv=document.getElementById(cid+'_edit').value.trim();
       if(c)c.value=nv;box.innerHTML=nv?confirmedLine(nv):'';
       onDone&&onDone();};};
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
   var n=Math.min(files.length,6),tmo=Math.min(45000,25000+4000*n);
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
   var small=await Promise.all(Array.from(files).slice(0,6).map(shrink));
   if(fin||seq!==_gseq)return;
   small.forEach(function(f){fd.append('photos',f);});
   try{var r=await fetch('/api/intake/guess',{method:'POST',body:fd});var d=await r.json();
     if(fin||seq!==_gseq)return;fin=true;clearTimeout(to);clearInterval(st);
     window.__indGuess=(d.industry_guess||d.guess||'');
     if(d.industry_guess)window.demoQs&&demoQs();   // 사진이 알려준 업종으로 질문 갱신(상호명 입력 커버)
     if(d.guess){
       window.intakeConfirmUI(box,d.guess,d.analysis||'','d_confirmed','d_vision',
         function(){setDemoReady(true,'');});
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
     +'<div class="text-slate-700">사진 <b>'+DP.length+'장</b> 준비됐어요. 정리(×삭제·＋추가)가 끝났으면 AI 확인을 시작할까요?</div>'
     +'<div class="flex items-center gap-2 mt-2">'
     +'<button type="button" id="d_gstart" class="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-bold">이 사진들로 분석 시작</button>'
     +'<span class="text-[11px] text-slate-400">안 해도 바로 만들 수 있어요</span></div></div>';
   document.getElementById('d_gstart').onclick=function(){demoGuess();};}
 var DP=[];
 function dpSync(){try{var dt=new DataTransfer();DP.forEach(function(f){dt.items.add(f);});pf.files=dt.files;}catch(e){}}
 function dpReset(){var gb=document.getElementById('d_guessbox');if(gb)gb.innerHTML='';
   var c=document.getElementById('d_confirmed'),v=document.getElementById('d_vision');
   if(c)c.value='';if(v)v.value='';_gseq++;setDemoReady(true,'');}
 function dpRender(){var pv=document.getElementById('d_preview');if(!pv)return;
   var nm=document.getElementById('d_photoname');if(nm)nm.textContent=DP.length?('✓ '+DP.length+'장 선택'):'';
   pv.innerHTML='';
   if(!DP.length){pv.classList.add('hidden');dpReset();return;}
   pv.classList.remove('hidden');
   DP.slice(0,10).forEach(function(f,i){var w=document.createElement('div');w.className='relative flex-shrink-0 pt-1.5 pr-1.5';
     var im=document.createElement('img');im.src=URL.createObjectURL(f);im.className='h-24 w-24 object-cover rounded-lg';w.appendChild(im);
     var x=document.createElement('button');x.type='button';x.setAttribute('aria-label','사진 삭제');x.textContent='×';
     x.className='absolute top-0 right-0 w-5 h-5 rounded-full bg-slate-700 text-white text-xs leading-none flex items-center justify-center';
     x.onclick=function(){DP.splice(i,1);dpSync();dpRender();if(DP.length)dpChanged();};
     w.appendChild(x);pv.appendChild(w);});
   var add=document.createElement('button');add.type='button';add.onclick=function(){pf.click();};
   add.className='h-24 w-24 flex-shrink-0 rounded-lg border-2 border-dashed border-slate-300 text-slate-400 text-2xl mt-1.5';
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
    box.innerHTML=html;runScripts(box);box.scrollIntoView({behavior:'smooth',block:'nearest'});}
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


def _hero() -> str:
    return f"""
<section class="relative hero-bg overflow-hidden">
 <div class="hero-dots absolute inset-x-0 top-0 h-96 pointer-events-none"></div>
 <div class="relative max-w-6xl mx-auto px-5 pt-20 pb-16 text-center">
  <div class="reveal inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white border border-indigo-100 text-xs font-semibold text-indigo-600 mb-8">
   소상공인 · 온라인 셀러 전용 · AI 마케팅 자동화</div>
  <h1 class="reveal text-4xl sm:text-6xl font-bold tracking-tight leading-[1.12] text-slate-900">
   네이버에서 우리 가게,<br><span class="text-indigo-600">검색 상위에 뜨게</span></h1>
  <p class="reveal mt-7 text-lg text-slate-500 max-w-2xl mx-auto">사진만 올리면 AI가 <b class="text-slate-800">네이버 블로그·플레이스 상위노출에 유리한 글</b>을 씁니다.
   <b class="text-slate-800">인스타·유튜브·릴스·X</b>까지 덤으로 만들어 <b class="text-slate-800">매장 방문·구매</b>로 연결해요.</p>
  <p class="reveal mt-4 text-sm text-slate-400 max-w-xl mx-auto">C-Rank·D.I.A.+ 신호 반영 · 없는 가격·스펙 안 지어내는 정직한 글 · 실검색량 키워드</p>
  <div class="reveal mt-10 flex justify-center">
   <a href="/login/kakao" class="flex items-center justify-center px-10 py-4 rounded-2xl font-extrabold text-lg" style="background:#FEE500;color:#191600">카카오로 무료 시작</a></div>
  <p class="reveal mt-4 text-xs text-slate-400">구글 <a href="/login/google" class="text-slate-500 underline">간편가입</a> · 이메일 <a href="/signup" class="text-slate-500 underline">회원가입</a> · 이미 회원이면 <a href="/login" class="text-slate-500 underline">로그인</a></p>
  <!-- 두 미끼를 한눈에: 순위진단(왼쪽) + 무료 만들기(오른쪽) — 모바일은 세로 스택 -->
  <div class="reveal mt-12 max-w-4xl mx-auto grid lg:grid-cols-2 gap-5 items-start text-left">
   <div class="bg-white border-2 border-indigo-200 rounded-2xl shadow-sm p-5">
    <div class="flex items-center gap-2 text-slate-800 font-bold text-sm mb-1">{_icon('search', 'w-4 h-4 text-indigo-600')} 내 가게 순위 즉시 진단</div>
    <p class="text-xs text-slate-400 mb-3">지역·업종·상호만 — 네이버 현재 순위를 바로 확인</p>
    <div class="flex gap-2">
      <input id="rc_region" placeholder="지역(부산 동구)" class="w-1/3 rounded-xl border border-slate-200 px-2.5 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400">
      <input id="rc_ind" placeholder="업종" class="w-1/3 rounded-xl border border-slate-200 px-2.5 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400">
      <input id="rc_name" placeholder="상호" class="w-1/3 rounded-xl border border-slate-200 px-2.5 py-2.5 text-slate-800 text-sm outline-none focus:border-indigo-400"></div>
    <button onclick="rankCheck()" class="w-full mt-2.5 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-sm transition">현재 순위 확인</button>
    <div id="rc_out" class="text-slate-600 text-sm mt-3"></div>
   </div>
   {_hero_demo_card()}
  </div>
  <script>
  function fillDemo(){{var v=document.getElementById('rc_ind').value.trim();var d=document.getElementById('d_ind');
   if(d&&v&&!d.value)d.value=v;
   var top=window.__rcTop||{{}};var tk=document.getElementById('d_target_kw'),tv=document.getElementById('d_target_vol');
   if(tk)tk.value=top.kw||'';if(tv)tv.value=top.vol||'';
   var hint=document.getElementById('d_target_hint');
   if(hint){{if(top.kw){{hint.textContent="목표: 미노출 키워드 '"+top.kw+"'"+(top.vol?(' (월 '+top.vol.toLocaleString()+'회 검색)'):'')+" 잡는 글";hint.classList.remove('hidden');}}else{{hint.classList.add('hidden');}}}}
   var t=document.getElementById('herodemo');if(t)t.scrollIntoView({{behavior:'smooth',block:'center'}});
   if(d)d.focus();}}
  async function rankCheck(){{var o=document.getElementById('rc_out');o.textContent='조회 중…';
   var fd=new FormData();fd.append('region',document.getElementById('rc_region').value);
   fd.append('industry',document.getElementById('rc_ind').value);fd.append('name',document.getElementById('rc_name').value);
   try{{var r=await fetch('/api/rank-check',{{method:'POST',body:fd}});var d=await r.json();
   if(d.error){{o.textContent=d.error;return;}}
   var rows='';
   (d.caught||[]).forEach(function(s){{rows+='<div class="flex justify-between bg-slate-50 rounded-lg px-3 py-1.5 mt-1.5"><span class="text-slate-700">'+s.keyword+'</span><span class="text-emerald-600 font-bold">'+s.rank+'위</span></div>';}});
   (d.missing||[]).forEach(function(s){{var v=s.volume?(' <span class="text-slate-400">월 '+s.volume.toLocaleString()+'회</span>'):'';rows+='<div class="flex justify-between bg-slate-50 rounded-lg px-3 py-1.5 mt-1.5"><span class="text-slate-500">'+s.keyword+v+'</span><span class="text-slate-400 font-bold">미노출</span></div>';}});
   var mk='';
   window.__rcTop=(d.targets&&d.targets.length)?{{kw:d.targets[0].keyword,vol:d.targets[0].volume||0}}:null;
   (d.targets||[]).forEach(function(tg){{var v=tg.volume?(' (월 '+tg.volume.toLocaleString()+'회)'):'';
     mk+='<a href="'+tg.make_href+'" class="block bg-indigo-50 hover:bg-indigo-100 rounded-xl px-3.5 py-2.5 mt-2 text-indigo-700 font-bold text-sm transition">'+tg.keyword+v+' — 이 키워드 잡는 글 만들기 →</a>';}});
   o.innerHTML='<b class="text-slate-900">'+d.headline+'</b>'+rows
     +'<div class="text-slate-400 mt-2">'+d.subline+'</div>'+mk
     +'<button type="button" onclick="fillDemo()" class="block w-full text-left bg-white border border-indigo-200 hover:border-indigo-400 rounded-xl px-3.5 py-2.5 mt-2 text-indigo-700 font-bold text-sm transition">이 업종으로 바로 만들어보기 (가입 없이) →</button>'
     +'<a href="/login/kakao" class="inline-block text-indigo-600 underline font-bold mt-2">'+d.cta+' →</a>'
     +(d.estimated?' <span class="text-slate-400 text-xs">(추정)</span>':'');
   }}catch(e){{o.textContent='조회 실패 — 잠시 후 다시';}}}}
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
   <video src="/demo/local_short.mp4" controls autoplay muted loop playsinline preload="metadata" poster="/demo/og.png" class="w-full bg-black"></video>
   <div class="text-slate-600 text-sm px-5 py-3.5">초량 루마썬팅 — 사진 5장 → AI 자동 생성 열차단 썬팅 세로 영상 <b class="text-slate-800">(음성 나레이션 + BGM)</b>
   <span class="block text-xs text-slate-400 mt-1">실제 올린다 생성물 · 탭하면 소리가 나와요</span></div></div>
  {_naver_preview()}
 </div>
 <script>
 window.addEventListener('load',function(){{
   document.querySelectorAll('#video video').forEach(function(v){{v.muted=true;var p=v.play();if(p&&p.catch)p.catch(function(){{}});}});
 }});
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
     <label class="block bg-slate-50 border-2 border-dashed border-slate-200 rounded-xl px-4 py-3 text-center cursor-pointer hover:border-indigo-300 transition">
       <span class="text-slate-800 font-bold text-sm inline-flex items-center gap-2">{_icon('camera', 'w-4 h-4 text-indigo-600')} 사진 올리기</span>
       <span class="block text-slate-400 text-xs mt-0.5">가게·상품 사진 (여러 장 가능 · 선택)</span>
       <input id="d_photo" type="file" accept="image/*" multiple class="hidden"><span id="d_photoname" class="block text-indigo-600 text-xs mt-1 font-semibold"></span></label>
     <div id="d_preview" class="hidden flex gap-2 overflow-x-auto pb-1"></div>
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
    items = [("5", "개 채널 동시"), ("1", "장 사진이면 끝"), ("100", "점 상위노출 점검"), ("2", "개 모드 자동분기")]
    cells = "".join(f"<div class='reveal text-center'><div class='text-5xl font-bold text-indigo-600' data-count='{n}'>{n}</div>"
                    f"<div class='text-sm text-slate-500 mt-2 font-medium'>{l}</div></div>" for n, l in items)
    return (f"<section class='bg-white pt-20 pb-2'><div class='max-w-5xl mx-auto px-5'>"
            f"<div class='grid grid-cols-2 sm:grid-cols-4 gap-8'>{cells}</div></div></section>")


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
    # 순위 성장 미니 바차트 — 상승 표시만 초록
    bars = ("<div class='flex items-end gap-2 h-24 mb-4'>"
            "<div class='w-5 rounded-t bg-indigo-100 rise'></div>"
            "<div class='w-5 rounded-t bg-indigo-300 rise2'></div>"
            "<div class='w-5 rounded-t bg-indigo-600 rise3'></div>"
            "<div class='flex-1'></div><span class='text-emerald-600'>" + _icon("arrowup", "w-8 h-8") + "</span></div>")
    c1 = ("<div class='reveal card-hi p-6'>"
          "<div class='text-xs font-bold text-indigo-500 mb-3'>순위 성장 추적</div>" + bars +
          "<div class='flex items-center justify-between'><span class='font-semibold text-slate-800'>부산 동구 썬팅</span>"
          "<span class='text-sm'><b class='text-indigo-600 text-2xl font-bold align-middle'>2위</b> "
          "<span class='text-emerald-600 font-extrabold'>· 3계단 상승</span></span></div>"
          "<p class='text-slate-500 text-sm mt-2'>내 순위가 <b class='text-slate-800'>오르는 게 매주 숫자로</b> 보여요.</p></div>")
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
          "<div><div class='text-5xl font-bold text-indigo-600'><span data-count='37'>0</span><span class='text-xl'>회</span></div>"
          "<div class='text-slate-500 text-sm'>이 콘텐츠 보고 온 손님 <span class='text-slate-400'>(예시)</span></div></div></div>"
          "<p class='text-slate-500 text-sm mt-3'>QR·링크로 <b class='text-slate-800'>실제 유입이 숫자로</b> 잡혀요.</p></div>")
    c4 = ("<div class='reveal card p-6 flex flex-col'>"
          "<div class='text-xs font-bold text-slate-400 mb-3'>사진 자동 보정 · 실제 전/후</div>"
          "<div class='relative rounded-2xl overflow-hidden select-none mx-auto w-full' style='aspect-ratio:16/10;max-height:230px'>"
          "<img src='/demo/food-after.jpg' class='absolute inset-0 w-full h-full object-cover' alt='보정 후'>"
          "<img src='/demo/food-before.jpg' class='baclip absolute inset-0 w-full h-full object-cover' alt='보정 전'>"
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
            ("video", "글 → 영상 + 단어자막", "문장이 곧 장면. 말하는 단어가 차오르는 카라오케 자막·AI음성·켄번스 자동."),
            ("target", "검색 상위노출 + 점수", "C-Rank·D.I.A·릴스 알고리즘 반영, 100점 점검."),
            ("chart", "순위 성장 추적", "네이버 순위가 오르는 걸 매주 ‘5위→2위’로 확인.")]
    rest = [("image", "인스타 캐러셀 자동", "사진 1장 → 정보 슬라이드(저장·도달↑)"),
            ("grid", "쇼츠·릴스·피드 규격", "9:16·1:1·4:5 자동 출력"),
            ("store", "소상공인·셀러 자동분기", "지도·방문 ↔ 구매링크·검색어 자동 전환"),
            ("tag", "업종 무제한 자동", "어떤 업종이든 맞춤 톤 자동 생성"),
            ("link", "계정 1회 연결 자동발행", "비번 없이 연결, 발행 누르면 끝"),
            ("trophy", "경쟁 추월 + 성과 실측", "옆집 대비 순위 + QR 유입 집계"),
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
            f"<div class='grid sm:grid-cols-2 lg:grid-cols-4 gap-3 mt-5'>{small}</div></div></section>")


def _new_features() -> str:
    from app import config as _cfg
    fl = _cfg.PLAN_LIMITS["free"]
    b = _cfg.PLAN_LIMITS["basic"]
    cards = [
        ("trophy", "경쟁사 추적기", "옆집보다 위에 뜨고 있나요? 매일 자동 체크",
         "경쟁 매장 상호만 등록하면, 같은 키워드에서 <b>내 순위 vs 경쟁사 순위</b>를 매일 자동 비교해요. "
         "역전당하면 바로 알려드려요.",
         f"무료 {fl['competitor_scans']}회 체험 → 베이직 월 {b['competitor_scans']}회",
         "/login/kakao"),
        ("printer", "인쇄물 자동 생성", "메뉴판·전단지도 사진 한 장으로",
         "메뉴판·가격표·이벤트 전단·POP을 <b>사진 한 장과 항목만으로</b> 자동 디자인. "
         "가격은 입력하신 그대로 — 없는 가격 지어내지 않아요.",
         f"무료 {fl['print_items']}장 체험 → 베이직 월 {b['print_items']}장",
         "/login/kakao"),
    ]
    body = ""
    for ic, title, sub, desc, trial, href in cards:
        body += (f"<div class='reveal card p-8'>{_icon_chip(ic)}"
                 f"<div class='font-bold text-xl mb-1 text-slate-900'>{title}</div>"
                 f"<div class='text-indigo-600 text-sm font-bold mb-3'>{sub}</div>"
                 f"<p class='text-slate-500 text-sm leading-relaxed mb-4'>{desc}</p>"
                 f"<div class='text-xs text-slate-400 mb-4'>{trial}</div>"
                 f"<a href='{href}' class='block text-center bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-3 rounded-xl transition'>무료로 체험하기</a></div>")
    return ("<section class='bg-white py-24'><div class='max-w-5xl mx-auto px-5'>"
            "<h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900'>상위노출, 그다음까지</h2>"
            "<p class='reveal text-center text-slate-500 mb-14'>순위만 올리는 게 아니라 — 경쟁사를 이기고, 매장 밖 마케팅까지.</p>"
            f"<div class='grid sm:grid-cols-2 gap-6'>{body}</div></div></section>")


def _pricing() -> str:
    from app import config as _cfg
    b, p = _cfg.PRICE_BASIC, _cfg.PRICE_PRO
    by, py = _cfg.yearly_monthly_equiv(b), _cfg.yearly_monthly_equiv(p)   # 연결제 월 환산가(약 30%↓)
    af = _cfg.AGENCY_FROM
    L = _cfg.PLAN_LIMITS
    def _flim(plan):   # 신규기능 한도 표기(-1=무제한)
        d = L.get(plan, L["free"])
        cm = "무제한" if d["competitors_max"] == -1 else f"{d['competitors_max']}개"
        pi = "무제한" if d["print_items"] == -1 else f"월 {d['print_items']}장"
        return [f"경쟁사 추적 {cm}", f"인쇄물 {pi}"]
    plans = [("베이직", f"월 {b:,}원", f"월 8건 · 처음 시작용 · 연결제 시 월 {by:,}원",
              ["사진만 올리면 5채널 생성", "검색 상위노출에 유리한 구조로 작성", "사진 자동 보정 + 이미지 SEO"] + _flim("basic"),
              "basic", False),
             ("프로", f"월 {p:,}원", f"무제한 · 성과까지 · 연결제 시 월 {py:,}원",
              ["콘텐츠 무제한 생성", "순위 성장 추적 + 경쟁 추월", "성과 실측(QR·유입 집계)", "우선 생성 · 다중 가게"] + _flim("pro"),
              "pro", True),
             ("대행", f"월 {af//10000}만원~", "사진만 보내면 발행까지 대행",
              ["카톡으로 사진만 보내면 끝", "올린다 팀이 발행까지 운영 대행", "정기 발행 · 성과 리포트"] + _flim("agency"),
              "agency", False)]
    cards = ""
    for name, price, sub, feats, key, hot in plans:
        wrap = "relative border-2 border-indigo-500" if hot else "border border-slate-200"
        tag = ("<div class='absolute -top-3 left-1/2 -translate-x-1/2 bg-indigo-600 text-white text-xs font-bold px-3 py-1 rounded-full'>가장 인기</div>"
               if hot else "")
        lis = "".join(f"<li class='flex gap-2 items-start'><span class='text-indigo-500 mt-0.5'>{_icon('check', 'w-4 h-4')}</span><span>{f}</span></li>" for f in feats)
        btn = "bg-indigo-600 hover:bg-indigo-700 text-white" if hot else "bg-slate-100 hover:bg-slate-200 text-slate-700"
        href = "#contact" if key == "agency" else f"/billing?plan={key}"
        cta = "카톡으로 신청" if key == "agency" else "구독 시작"
        # 연결제(약 30%↓) 보조 링크 — basic/pro만
        annual = ("" if key == "agency" else
                  f"<a href='/billing?plan={key}_yearly' class='block text-center text-xs text-indigo-600 font-bold mt-2 hover:underline'>연 결제로 30% 아끼기 →</a>")
        cards += (f"<div class='reveal {wrap} bg-white rounded-2xl p-8 flex flex-col'>{tag}"
                  f"<div class='font-bold text-lg text-slate-500'>{name}</div>"
                  f"<div class='text-3xl font-bold mt-3 mb-1 text-slate-900'>{price}</div>"
                  f"<div class='text-xs text-slate-400 mb-3'>{sub}</div>"
                  f"<ul class='space-y-2.5 text-sm text-slate-600 flex-1 mt-2'>{lis}</ul>"
                  f"<a href='{href}' class='{btn} mt-7 text-center px-4 py-3.5 rounded-xl font-bold transition'>{cta}</a>{annual}</div>")
    return (f"<section id='pricing' class='bg-[#F9FAFB] py-24'><div class='max-w-5xl mx-auto px-5'>"
            f"<h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900'>합리적인 요금</h2>"
            f"<p class='reveal text-center text-slate-500 mb-14'>대행사 1/5 가격 — 손님 2~3명만 더 와도 본전.</p>"
            f"<div class='grid sm:grid-cols-3 gap-6 items-stretch pt-3'>{cards}</div></div></section>")


_QA = [("정말 사진만 올리면 되나요?", "네. 사진과 한 줄 설명만 주시면 AI가 5채널 콘텐츠를 만듭니다. 사진 1장만 있어도 자막·음성이 들어간 세로 숏폼까지 자동 생성됩니다."),
       ("쿠팡·11번가 셀러도 되나요?", "네. '온라인 셀러'로 설정하면 글 마무리가 지도 대신 구매 링크/검색어로, 키워드가 지역명 대신 상품·후기 키워드로 자동 전환됩니다. (쿠팡은 직링크 정책상 '검색어 유도'를 권장)"),
       ("제 SNS 비밀번호를 줘야 하나요?", "아니요. 공식 OAuth로 한 번만 권한을 허용하면 됩니다. 비밀번호는 저장하지 않습니다."),
       ("네이버 블로그도 되나요?", "글·사진을 완성해 드리고, 임시저장된 글을 네이버에서 발행만 누르시면 됩니다. (네이버는 공식 발행 API가 없어 반자동)"),
       ("업종이 특이해도 되나요?", "어떤 업종이든 AI가 맞춤 프로필을 자동 생성합니다.")]


def _faq() -> str:
    items = "".join(f"<details class='reveal card p-5'><summary class='font-semibold cursor-pointer text-slate-800'>{q}</summary><p class='text-slate-500 text-sm mt-2'>{a}</p></details>" for q, a in _QA)
    return f"<section id='faq' class='bg-white py-24'><div class='max-w-3xl mx-auto px-5'><h2 class='reveal text-3xl sm:text-4xl font-bold text-center mb-12 text-slate-900'>자주 묻는 질문</h2><div class='space-y-3'>{items}</div></div></section>"


def _seo_jsonld() -> str:
    """검색 리치결과용 구조화 데이터 — Organization + WebSite + FAQPage(구글 FAQ 노출)."""
    import json
    faq = {"@context": "https://schema.org", "@type": "FAQPage",
           "mainEntity": [{"@type": "Question", "name": q,
                           "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in _QA]}
    org = {"@context": "https://schema.org", "@type": "Organization", "name": "올린다",
           "url": BASE + "/", "logo": BASE + "/demo/og.png",
           "description": "소상공인·온라인 셀러를 위한 네이버 상위노출 최적화 AI 마케팅 콘텐츠 생성 서비스"}
    site = {"@context": "https://schema.org", "@type": "WebSite", "name": "올린다",
            "url": BASE + "/", "inLanguage": "ko-KR"}
    return "".join(f'<script type="application/ld+json">{json.dumps(x, ensure_ascii=False)}</script>'
                   for x in (org, site, faq))


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
 <p class="text-center text-slate-400 text-xs mt-3">또는 카카오톡 상담 버튼(우측 하단) · 이메일 {CONTACT_EMAIL}</p>
</div></section>"""


def _cta() -> str:
    return """
<section id="cta" class="bg-[#F5F3FF] py-28">
 <div class="max-w-3xl mx-auto px-5 text-center">
  <h2 class="reveal text-4xl sm:text-5xl font-bold leading-tight text-slate-900">오늘 사진 한 장,<br><span class="text-indigo-600">내일 손님으로</span></h2>
  <p class="reveal mt-6 text-slate-500 text-lg">지금 시작하면 첫 콘텐츠 세트를 무료로 만들어 드립니다.</p>
  <div class="reveal mt-10 flex flex-col sm:flex-row gap-3 justify-center">
   <a href="/login/kakao" class="px-9 py-4 rounded-2xl font-extrabold text-lg" style="background:#FEE500;color:#191600">카카오로 시작하기</a>
   <a href="/login/google" class="flex items-center justify-center gap-2 px-9 py-4 rounded-2xl font-extrabold text-lg bg-white border border-slate-200 text-slate-700"><svg width="22" height="22" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg> 구글로 시작하기</a></div>
 </div></section>"""


def _footer() -> str:
    return f"""
<footer class="bg-[#F9FAFB] border-t border-slate-200 text-slate-500 pt-14 pb-10">
 <div class="max-w-6xl mx-auto px-5">
  <div class="flex items-center gap-2 font-extrabold text-xl mb-6">{LOGO}<span class="text-slate-900">올린다</span></div>
  <div class="grid sm:grid-cols-2 gap-6 text-sm">
   <div>
    <div class="text-slate-400 text-xs mb-1">CEO</div><div class="font-bold text-slate-800 mb-2">Jung Young Jin</div>
    <div class="text-slate-400 text-xs">사업자등록번호</div><div class="mb-2">106-48-91586</div>
    <div class="text-slate-400 text-xs">Location</div><div>(우)50510 경남 양산시 주남로 288<br>영산대학교 양산캠퍼스 산학협력관 309호</div>
   </div>
   <div>
    <div class="card p-4 text-sm">
      <p class="font-semibold text-slate-800 mb-1">올린다는 이렇게 만들었습니다</p>
      <p class="text-slate-500 text-xs">실제 소상공인·중고차 매장 현장 요구에서 출발해, AI(글·비전·TTS·영상)와 네이버 상위노출 노하우를 결합해 개발했습니다.</p>
     </div>
    <div class="mt-4 flex gap-3 text-sm">
     <a href="#contact" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">문의하기</a>
     <a href="mailto:{CONTACT_EMAIL}" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">이메일</a>
     <a href="/privacy" class="px-4 py-2 rounded-xl bg-white border border-slate-200 hover:border-slate-300">개인정보처리방침</a></div>
   </div>
  </div>
  <div class="mt-8 pt-6 border-t border-slate-200 text-center text-xs text-slate-400 leading-relaxed">
    © 2026 올린다 (Ollinda) · 가피디자인 · 사업자등록번호 106-48-91586<br>
    문의 {CONTACT_EMAIL} · <a href="/privacy" class="underline hover:text-slate-600">개인정보처리방침</a> · SSL 보안 연결
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
    """모바일 하단 고정 CTA — 스크롤 어디서든 전환 유도(모바일 전환율 핵심)."""
    return ('<div class="fixed bottom-0 left-0 right-0 z-40 sm:hidden bg-white/95 backdrop-blur border-t border-slate-200 px-3 pt-3" '
            'style="padding-bottom:max(12px,env(safe-area-inset-bottom))">'
            '<a href="/login/kakao" onclick="trackEv(\'sticky_cta\',{})" '
            'class="block text-center py-3.5 rounded-xl font-extrabold text-white bg-indigo-600">무료로 시작하기</a></div>')


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
    <div class="mt-3 text-xs text-slate-400">PAS 오프닝 · 손님 스토리 · 실검색량 키워드 · 정직(없는 가격·스펙 안 씀) 자동 적용</div>
   </div></div>"""


def _why_rank() -> str:
    """왜 상위노출 되나 — 2026 알고리즘을 '알고' 만든다 + 채널별 최적화(#2·#5)."""
    chans = [
        ("pen", "네이버 블로그", "C-Rank·D.I.A.+ 반영, PAS 오프닝으로 체류↑, FAQ·표·사진배치"),
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
  <div class="text-center mb-4"><span class="reveal inline-block px-3 py-1 rounded-full bg-[#EEF2FF] border border-indigo-100 text-xs font-bold text-indigo-600">2026 최신 알고리즘 반영</span></div>
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">왜 우리 콘텐츠는 <span class="text-indigo-600">상위에 뜰까요?</span></h2>
  <p class="reveal text-center text-slate-500 mb-12 max-w-2xl mx-auto">그냥 글이 아닙니다. 채널마다 <b class="text-slate-800">노출 알고리즘이 다르다</b>는 걸 알고, 각각 다르게 최적화해서 만듭니다.</p>
  <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-8">{cards}</div>
  <div class="reveal card-hi p-6 text-center">
   <div class="text-sm text-slate-500 mb-1">네이버 검색광고 <b class="text-slate-800">실검색량</b> 연동</div>
   <div class="text-lg sm:text-xl font-bold text-slate-900">지어낸 키워드가 아니라, <span class="text-indigo-600 text-xl sm:text-2xl">‘셀프네일 월 4,540회’</span>처럼 진짜 뜨는 키워드로 씁니다</div>
   <div class="text-xs text-slate-400 mt-2">검색량 <b class="text-indigo-600 text-sm font-bold">500~5,000</b> 롱테일(경쟁↓·전환↑)을 실측으로 골라 반영</div>
  </div>
 </div></section>"""


def _rank_loop() -> str:
    """상위노출 실행 루프(상위노출 PHASE 6) — '진단만 하고 끝? 실제로 올려드립니다' 셀링포인트."""
    steps = [
        ("search", "1. 진단", "네이버 실측으로 <b class='text-slate-800'>놓치는 키워드</b>(월 검색량 포함)를 찾아요", "무료"),
        ("pen", "2. 타겟 생성", "그 키워드를 겨냥한 글을 바로 만들어요 — 후기·방법·가격 <b class='text-slate-800'>앵글 3종</b>으로 여러 검색블록 진입", "무료체험 → 플랜"),
        ("calendar", "3. 발행 일관성", "발행 캘린더가 <b class='text-slate-800'>주 N회 페이스</b>를 잡아줘요(C-Rank 지속성 신호) + 블로그 연결 시 실제 발행 자동 확인", "베이직·프로"),
        ("refresh", "4. 추적·학습", "발행 전후 순위를 자동 추적 — <b class='text-slate-800'>오른 키워드는 더 밀고, 정체는 앵글 재도전</b>을 앱이 먼저 제안", "프로"),
    ]
    cards = "".join(
        f"<div class='reveal {'card-hi' if i == 0 else 'card'} p-5'>{_icon_chip(ic)}"
        f"<div class='font-bold mb-1 text-slate-900'>{t} <span class='text-[10px] text-indigo-500 font-normal'>{plan}</span></div>"
        f"<p class='text-sm text-slate-500 leading-relaxed'>{d}</p></div>" for i, (ic, t, d, plan) in enumerate(steps))
    return f"""
<section class="bg-[#F9FAFB] py-24">
 <div class="max-w-6xl mx-auto px-5">
  <h2 class="reveal text-3xl sm:text-4xl font-bold text-center mb-3 text-slate-900">진단만 하고 끝? <span class="text-indigo-600">올린다는 실제로 올려드립니다</span></h2>
  <p class="reveal text-center text-slate-500 mb-12 max-w-2xl mx-auto">순위 보여주기로 끝나는 서비스가 아니에요. <b class="text-slate-800">진단 → 타겟 글 → 꾸준한 발행 → 추적·학습</b> 루프를 앱이 돌립니다.</p>
  <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">{cards}</div>
  <p class="reveal text-center text-xs text-slate-400 mt-8">※ 정직 원칙: 가짜 순위·"무조건 1위" 보장은 하지 않습니다. 실측 순위와 사실 기반 코칭만 제공해요.</p>
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
    <div class="text-xs font-bold text-indigo-600 mb-3">올린다 (PAS·손님스토리·손실회피)</div>
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
 </div></section>"""


def render() -> str:
    # 전환 논리 순서(랜딩 개선): ① 히어로(가치+CTA+진단) → ② 문제 공감(먼저 아프게) →
    # ③ 해결·증명(영상·블로그) + 체험 위젯(증명 직후) → ④ 작동 원리(채널 알고리즘+루프+글 비교) →
    # ⑤ 차별점(성과 가시화+정직) → ⑥ 전체 기능(숫자+핵심4) → ⑦ 신규 기능 → ⑧ 요금 → ⑨ 마지막 CTA
    return (_HEAD + _ga() + _seo_jsonld() + _nav()
            + _hero() + _problem()
            + _video() + _copy_compare()
            + _why_rank() + _rank_loop()
            + _results() + _honesty()
            + _stats() + _features() + _modes()
            + _new_features() + _pricing() + _faq() + _contact() + _cta() + _footer()
            + _sticky_cta() + _FOOT)


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
  <p><b>5. 사업자</b> — Jung Young Jin · 106-48-91586 · 경남 양산시 주남로 288 영산대 산학협력관 309호</p>
  <p><b>6. 문의</b> — {CONTACT_EMAIL}</p>
 </div></div>"""
    return _HEAD + _nav() + body + _footer() + _FOOT
