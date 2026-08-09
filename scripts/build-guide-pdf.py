# 제품·사용 설명서 PDF 빌드 — 브랜드 디자인 HTML → Playwright 인쇄(A4).
# 사용: SHOPCAST_SECRET=test python3 scripts/build-guide-pdf.py
# 출력: assets/docs/ollinda_guide.pdf (랜딩 /docs/guide.pdf 로 서빙)
# 원칙: 코드가 실제로 하는 일·실측 값만 쓴다(날조 금지). 가격은 config 단일 소스.
# 사용법 화면은 assets/docs/manual/*.jpg — 예시 가게(달빛카페)로 실제 생성해 캡처한 실화면.
# 화면 갱신 시: 로컬 서버 + Playwright로 재캡처(2026-08-09 절차: 세션 기록 참조) 후 재빌드.
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SHOPCAST_SECRET", "build")
from app import config  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "assets", "docs", "ollinda_guide.pdf")
SHOTS = os.path.join(ROOT, "assets", "docs", "manual")

LOGO = ('<svg viewBox="0 0 32 32" style="width:44px;height:44px"><rect width="32" height="32" rx="9" fill="#6366F1"/>'
        '<path d="M8 21 L14 14 L18 18 L24 9" stroke="white" stroke-width="2.6" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/><circle cx="24" cy="9" r="2.3" fill="white"/></svg>')

P = f"{config.PRICE_BASIC:,}"
P2 = f"{config.PRICE_PRO:,}"
P3 = f"{config.AGENCY_FROM:,}"
L1, L2, L3 = f"{config.LIST_BASIC:,}", f"{config.LIST_PRO:,}", f"{config.LIST_AGENCY:,}"
Y1 = f"{config.yearly_monthly_equiv(config.PRICE_BASIC):,}"
Y2 = f"{config.yearly_monthly_equiv(config.PRICE_PRO):,}"

CSS = """
@page{size:A4;margin:0}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;color:#1e293b;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.page{width:210mm;height:296mm;padding:16mm 15mm;page-break-after:always;position:relative;background:#fff}
.page:last-child{page-break-after:auto}
h1{font-size:30px;font-weight:800;letter-spacing:-.5px}
h2{font-size:20px;font-weight:800;margin-bottom:12px;color:#0f172a}
p{font-size:12.5px;line-height:1.75;color:#475569}
.card{border:1px solid #E5E7EB;border-radius:14px;padding:13px 15px;background:#fff}
.hi{background:#F5F3FF;border:1px solid #DDD6FE;border-radius:14px;padding:13px 15px}
.muted{color:#94a3b8;font-size:10.5px}
.tag{display:inline-block;background:#EEF2FF;color:#6366F1;font-size:10.5px;font-weight:700;
 padding:3px 10px;border-radius:99px;margin-bottom:8px}
.foot{position:absolute;bottom:9mm;left:15mm;right:15mm;display:flex;justify-content:space-between;
 font-size:9.5px;color:#cbd5e1;border-top:1px solid #f1f5f9;padding-top:6px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#F9FAFB;font-weight:700;text-align:left;padding:8px 10px;border-bottom:2px solid #E5E7EB;color:#334155}
td{padding:8px 10px;border-bottom:1px solid #F1F5F9;color:#475569;vertical-align:top}
td .b{white-space:nowrap}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.step{display:flex;gap:10px;margin-bottom:9px}
.stepno{flex-shrink:0;width:22px;height:22px;border-radius:99px;background:#6366F1;color:#fff;
 font-size:11px;font-weight:800;display:flex;align-items:center;justify-content:center;margin-top:2px}
.b{color:#0f172a;font-weight:700}
.indigo{color:#6366F1}
.strike{text-decoration:line-through;color:#cbd5e1;font-weight:600}
.shots{display:flex;gap:8mm;justify-content:center;margin:8px 0 4px}
.shot{width:52mm;flex-shrink:0;text-align:center}
.shot img{width:100%;border:1px solid #E5E7EB;border-radius:12px;display:block}
.shot .cap{font-size:10px;color:#64748b;margin-top:5px;line-height:1.5}
.shot .capno{display:inline-block;width:16px;height:16px;border-radius:99px;background:#6366F1;color:#fff;
 font-size:9.5px;font-weight:800;line-height:16px;margin-right:3px}
"""


def foot(n):
    return f"<div class='foot'><span>올린다 제품·사용 설명서</span><span>ollinda.kr · {n}</span></div>"


def shot(fname, no, cap):
    return (f"<div class='shot'><img src='file://{SHOTS}/{fname}'>"
            f"<div class='cap'><span class='capno'>{no}</span>{cap}</div></div>")


HTML = f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard-dynamic-subset.min.css" rel=stylesheet>
<style>{CSS}</style></head><body>

<!-- 표지 -->
<div class="page" style="display:flex;flex-direction:column;justify-content:center;background:
 radial-gradient(70% 40% at 50% 0%,rgba(99,102,241,.08),transparent 70%),#fff">
 <div style="display:flex;align-items:center;gap:12px;margin-bottom:26px">{LOGO}
  <span style="font-size:26px;font-weight:800">올린다</span></div>
 <h1 style="font-size:40px;line-height:1.25">네이버에서 우리 가게,<br><span class="indigo">검색 상위에 뜨게</span></h1>
 <p style="font-size:15px;margin-top:18px;max-width:130mm">사진만 올리면 AI가 네이버 상위노출에 유리한 글과 영상을 만들고,
  발행된 뒤에도 <span class="b">매일 순위를 실측으로 지켜보는</span> 소상공인 마케팅 서비스입니다.</p>
 <div style="margin-top:34px" class="hi">
  <p style="font-size:13px"><span class="b">실측 사례</span> — 올린다로 발행한 글이
   ‘부산 동구 썬팅업체’ 네이버 블로그검색에서 <span class="b indigo">발행 9일 만에 1위</span>
   <span class="muted">(2026년 8월 실측 · 개별 결과는 가게·키워드에 따라 다릅니다)</span></p></div>
 <p class="muted" style="position:absolute;bottom:18mm;left:15mm">ollinda.kr · 2026년 8월판 · 제품·사용 설명서</p>
</div>

<!-- 1. 왜 -->
<div class="page">
 <span class="tag">01 · 왜 올린다인가</span>
 <h2>글이 목표가 아닙니다. <span class="indigo">검색에서 보이는 것</span>이 목표입니다.</h2>
 <p>손님은 네이버에서 검색하고, 첫 화면에 보이는 가게로 갑니다. 그래서 올린다의 기준은 단 하나 —
  "이것이 사장님 가게의 검색 노출을 올리는가"입니다. 글·영상·페이지는 전부 그 수단입니다.</p>
 <div class="grid3" style="margin-top:14px">
  <div class="card"><p class="b" style="font-size:13px">시간이 없다</p><p style="font-size:11.5px;margin-top:4px">장사하며 블로그·인스타·영상까지 챙길 시간은 없습니다.</p></div>
  <div class="card"><p class="b" style="font-size:13px">대행은 비싸다</p><p style="font-size:11.5px;margin-top:4px">월 30~50만원을 내도 뭘 하는지, 효과가 있는지 깜깜합니다.</p></div>
  <div class="card"><p class="b" style="font-size:13px">뭘 쓸지 모른다</p><p style="font-size:11.5px;margin-top:4px">어떤 글이 검색에 뜨는지 알기 어렵습니다.</p></div>
 </div>
 <h2 style="margin-top:20px">올린다의 대답</h2>
 <div class="step"><div class="stepno">1</div><p><span class="b">사장님은 사진만 올립니다.</span> 글·캡션·영상·발행 준비는 올린다가 합니다.</p></div>
 <div class="step"><div class="stepno">2</div><p><span class="b">뭘 쓸지도 찾아옵니다.</span> 네이버 검색을 정찰해 "자리는 있는데 아직 답한 글이 없는 질문"을 글감으로 올립니다.</p></div>
 <div class="step"><div class="stepno">3</div><p><span class="b">발행 후에도 매일 지켜봅니다.</span> 전 글의 순위를 매일 실측하고, 떨어지면 고친 글을 먼저 가져옵니다.</p></div>
 <div class="hi" style="margin-top:12px">
  <p style="font-size:12px"><span class="b">대부분의 AI 툴과의 차이</span> — 글을 뱉고 끝나지 않습니다.
   발행한 뒤부터가 본편입니다: 매일 지켜보고, 떨어지는 순간 사장님보다 먼저 알아채고, 고쳐서 가져옵니다.</p></div>
 {foot(2)}
</div>

<!-- 2. 사용법 ① 시작 -->
<div class="page">
 <span class="tag">02 · 사용법 ① 시작하기 (3분)</span>
 <h2>가입하고, 가게를 알려주세요</h2>
 <p>ollinda.kr에서 <span class="b">카카오·네이버·구글로 3초 가입</span>합니다. 비밀번호를 새로 만들 필요도,
  SNS 비밀번호를 줄 필요도 없습니다. 가입하면 가게 정보를 딱 3가지(상호·업종·사업형태)만 묻습니다.</p>
 <div class="shots">
  {shot('01-시작설정.jpg', 1, '가입 직후 — 시작 설정. 가게 이름으로 검색하면 자동 입력됩니다')}
  {shot('02-시작설정-작성.jpg', 2, '상호·업종 입력, 사업형태 선택 — 이것으로 준비 끝')}
  {shot('03-내작업실.jpg', 3, '내 작업실 — 네이버에서 우리 가게가 보이는 곳을 실측으로 보여줍니다')}
 </div>
 <p class="muted" style="text-align:center">※ 설명서의 화면은 예시 가게(달빛카페)로 실제 프로그램을 작동시켜 캡처한 것입니다.</p>
 {foot(3)}
</div>

<!-- 3. 사용법 ② 만들기 -->
<div class="page">
 <span class="tag">03 · 사용법 ② 만들기 (사진 올리면 끝)</span>
 <h2>사진을 올리면, 몇 분 뒤 발행 소재가 완성됩니다</h2>
 <div class="step"><div class="stepno">1</div><p><span class="b">사진 추가</span> — 폰으로 찍은 사진 여러 장(최대 30장)을 올립니다. 순서는 신경 안 쓰셔도 됩니다 — 내용에 맞는 위치에 AI가 배치합니다. 번호판·개인정보 가림은 항상 자동입니다.</p></div>
 <div class="step"><div class="stepno">2</div><p><span class="b">만드는 중 (보통 2~4분)</span> — 사진 보정 → 키워드·전략 선정 → 글·캡션 동시 작성 순서로 진행되고, 화면에 진행률이 표시됩니다. 기다리는 동안 다른 일 보셔도 됩니다.</p></div>
 <div class="step"><div class="stepno">3</div><p><span class="b">발행 소재 확인</span> — 네이버 블로그 글 + 인스타 캡션 + X까지 한 번에 완성됩니다. 상위노출 점수와 함께, 어떤 전략으로 썼는지도 보여줍니다.</p></div>
 <div class="shots">
  {shot('04-사진올리기.jpg', 1, "콘텐츠 만들기 — 사진 추가하고 '만들기'를 누르면 끝")}
  {shot('07-결과키트.jpg', 2, '완성된 발행 소재 — 전략 키워드·상위노출 점수·성과 추적 QR까지')}
  {shot('08-결과키트-본문.jpg', 3, '채널별 미리보기 — 실제 앱에 올라간 모습 그대로 보여줍니다')}
 </div>
 {foot(4)}
</div>

<!-- 4. 사용법 ③ 발행 -->
<div class="page">
 <span class="tag">04 · 사용법 ③ 발행하기 (복사 → 붙여넣기)</span>
 <h2>네이버 블로그엔 붙여넣기만 하면 됩니다</h2>
 <p>네이버는 공식 발행 API가 없어, 올린다는 <span class="b">복사·붙여넣기 키트</span>를 드립니다.
  제목·본문·태그를 복사하고, 사진을 순서대로 넣고, 발행 버튼만 누르면 끝 — PC와 모바일 각각의 요령도 화면에서 안내합니다.</p>
 <div class="shots">
  {shot('09-네이버발행키트.jpg', 1, '네이버 발행 키트 — 내 상황(PC·모바일)에 맞는 순서 안내')}
  {shot('10-네이버발행키트-2.jpg', 2, '제목·본문·태그를 버튼 한 번으로 복사')}
 </div>
 <div class="hi" style="margin-top:6px">
  <p style="font-size:12px"><span class="b">발행한 다음은 올린다가 합니다</span> — 블로그를 연결해두면 발행을 자동으로
   확인하고(2시간마다), 그때부터 색인·순위를 매일 실측합니다. 순위가 떨어지면 고친 글을 카드로 가져옵니다.
   자동 발행은 하지 않습니다 — 발행 버튼은 언제나 사장님 몫입니다.</p></div>
 {foot(5)}
</div>

<!-- 5. 실측 사례 + 역할 분담 -->
<div class="page">
 <span class="tag">05 · 실측 사례</span>
 <h2>한 글의 여정 — 전부 실측 기록입니다</h2>
 <div class="hi" style="padding:18px">
  <p class="b" style="font-size:14px;margin-bottom:10px">‘부산 동구 썬팅업체’ 검색 — 부산 동구의 썬팅 전문점</p>
  <table>
   <tr><th style="width:24mm">날짜</th><th>기록</th></tr>
   <tr><td>7월 31일</td><td>올린다가 만든 글 발행</td></tr>
   <tr><td>8월 2일</td><td>네이버 블로그검색 <span class="b">12위</span> 첫 실측</td></tr>
   <tr><td>8월 9일</td><td><span class="b indigo" style="font-size:15px">1위</span></td></tr>
  </table>
  <p class="muted" style="margin-top:8px">2026년 8월 실측 · 개별 결과는 가게·키워드에 따라 다릅니다.</p></div>
 <h2 style="margin-top:18px">역할 분담 — 사장님이 하는 일은 다섯 가지뿐</h2>
 <table>
  <tr><th style="width:50%">사장님</th><th>올린다</th></tr>
  <tr><td>사진 올리기</td><td>분석·보정·개인정보 가림·글·영상 생성</td></tr>
  <tr><td>경험 한 줄 답하기(한 번만)</td><td>저장해두고 다음 글마다 자동으로 재사용 — 쌓일수록 질문이 줄어듭니다</td></tr>
  <tr><td>글감 카드에 "해요/안 해요" 답하기</td><td>검색 지면 정찰·빈자리 글감 발굴</td></tr>
  <tr><td>발행 버튼 누르기</td><td>발행 확인·색인·순위 매일 실측, 하락 시 개선판 제안</td></tr>
  <tr><td>영상 눈으로 확인</td><td>화면-자막 일치·품질 기계 검사</td></tr>
 </table>
 {foot(6)}
</div>

<!-- 6. 정직 원칙 -->
<div class="page">
 <span class="tag">06 · 정직 원칙</span>
 <h2>없는 것은 지어내지 않습니다</h2>
 <p>허위 콘텐츠는 손님의 신뢰를 잃고, 결국 가게의 검색 노출 자체를 죽입니다. 올린다는 사진과 사장님이 준 정보로만 씁니다.</p>
 <div class="grid2" style="margin-top:12px">
  <div class="card"><p class="b">가격 날조 안 함</p><p style="font-size:11.5px;margin-top:3px">20만원짜리를 3만원이라 쓰지 않습니다.</p></div>
  <div class="card"><p class="b">허위 스펙·효능 안 함</p><p style="font-size:11.5px;margin-top:3px">없는 성능을 지어내지 않습니다.</p></div>
  <div class="card"><p class="b">가짜 후기 안 함</p><p style="font-size:11.5px;margin-top:3px">‘내돈내산’ 사칭 없이 판매자 입장으로 씁니다.</p></div>
  <div class="card"><p class="b">측정도 정직하게</p><p style="font-size:11.5px;margin-top:3px">확인 안 된 노출은 표시하지 않습니다. 순위 보장("무조건 1위")도 하지 않습니다.</p></div>
 </div>
 <div class="hi" style="margin-top:12px">
  <p style="font-size:12px"><span class="b">비밀번호를 받지 않습니다</span> — 채널 연결은 공식 인증(OAuth)뿐입니다.
   SNS 비밀번호를 요구하는 마케팅 업체는 사장님 계정 전체를 위험에 빠뜨립니다.</p></div>
 <h2 style="margin-top:18px">경험은 자산이 됩니다</h2>
 <p>글을 만들 때 그 주제로 딱 한 가지만 여쭤봅니다. 답하신 경험은 저장되어 다음 글에 자동으로 들어가고,
  쌓일수록 질문이 줄어듭니다. 답이 없는 주제는 1인칭 경험을 지어내는 대신, 사실 기반 글로 먼저 나갑니다.</p>
 {foot(7)}
</div>

<!-- 7. 요금 -->
<div class="page">
 <span class="tag">07 · 요금 (런칭 특가)</span>
 <h2>대행 한 달 비용이면, 올린다는 몇 달</h2>
 <p>홍보 영상 외주는 편당 5~15만원, 블로그 대행은 월 30~50만원입니다. 올린다는 글·영상·순위 관측까지 통째로 제공합니다.</p>
 <table style="margin-top:12px">
  <tr><th>플랜</th><th>월 요금(런칭가)</th><th>주요 제공</th></tr>
  <tr><td class="b">라이트</td><td><span class="strike">{L1}원</span> <span class="b">{P}원</span><br><span class="muted">연결제 시 월 {Y1}원</span></td>
      <td>월 콘텐츠 6세트(블로그+인스타+X) · 실사 무빙 영상 2편 · 상위노출 구조+품질 자동검사 · 사진 보정+개인정보 가림</td></tr>
  <tr><td class="b indigo">스탠다드 ★</td><td><span class="strike">{L2}원</span> <span class="b">{P2}원</span><br><span class="muted">연결제 시 월 {Y2}원</span></td>
      <td>월 12세트 + 영상 8편 · 네이버 클립 전용 영상 · 음성 나레이션 · 순위 추적+미노출 자동 개선 · 성과 실측(QR·유입)</td></tr>
  <tr><td class="b">프로</td><td><span class="strike">{L3}원</span> <span class="b">{P3}원</span></td>
      <td>월 20세트 + 영상 무제한 · 우선 생성 · 다중 가게 · 전담 지원</td></tr>
 </table>
 <p class="muted" style="margin-top:8px">가입 없이 무료 미리보기 2회 · 가입하면 5채널 전부+영상 무료 2회 · 언제든 해지 가능(해지 후 다음 결제일부터 미청구).</p>
 <h2 style="margin-top:16px">자주 묻는 질문</h2>
 <table>
  <tr><td class="b" style="width:58mm">정말 사진만 올리면 되나요?</td><td>네. 사진과 한 줄 설명이면 5채널 콘텐츠가 만들어집니다.</td></tr>
  <tr><td class="b">네이버 블로그도 되나요?</td><td>글을 완성해 드리고 발행만 누르시면 됩니다(네이버는 공식 API가 없어 반자동).</td></tr>
  <tr><td class="b">비밀번호를 줘야 하나요?</td><td>아니요. 공식 인증(OAuth)만 사용하고 비밀번호는 저장하지 않습니다.</td></tr>
  <tr><td class="b">쿠팡·스마트스토어 셀러도 되나요?</td><td>네. 셀러 모드에선 지도 대신 구매 링크·상품 키워드로 자동 전환됩니다.</td></tr>
  <tr><td class="b">해지는 어떻게 하나요?</td><td>언제든 문의(이메일 포함)로 요청하시면 즉시 처리되고, 다음 결제일부터 청구되지 않습니다.</td></tr>
 </table>
 {foot(8)}
</div>

<!-- 8. 시작 -->
<div class="page" style="display:flex;flex-direction:column;justify-content:center;text-align:center;background:
 radial-gradient(70% 40% at 50% 100%,rgba(99,102,241,.08),transparent 70%),#fff">
 <div style="display:flex;justify-content:center;margin-bottom:18px">{LOGO}</div>
 <h1 style="font-size:34px">오늘 사진 한 장,<br><span class="indigo">내일 손님으로</span></h1>
 <p style="font-size:14px;margin-top:14px">ollinda.kr 에서 가입 없이 무료로 만들어보세요.<br>카카오·네이버·구글로 3초 만에 시작할 수 있습니다.</p>
 <div style="margin-top:30px;font-size:11px;color:#94a3b8;line-height:1.9">
  올린다 (Ollinda) · 대표 Jung Young Jin · 사업자등록번호 106-48-91586<br>
  경남 양산시 주남로 288 영산대학교 양산캠퍼스 산학협력관 309호<br>
  문의 etetetetet5ea@kakao.com · ollinda.kr</div>
</div>
</body></html>"""


async def main():
    from playwright.async_api import async_playwright
    html_path = "/tmp/ollinda-guide.html"
    open(html_path, "w").write(HTML)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto("file://" + html_path, wait_until="networkidle")
        await pg.wait_for_timeout(1500)   # 폰트·이미지 로드 대기
        await pg.pdf(path=OUT, format="A4", print_background=True)
        await b.close()
    print("✅", OUT, os.path.getsize(OUT) // 1024, "KB")


asyncio.run(main())
