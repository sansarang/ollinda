"""
숏폼(릴스/쇼츠) 생성기 v3 — '글 → 씬' 자동변환 + 씬별 TTS 싱크 + PIL 자막(키워드 강조)
+ 켄번스 모션 + 훅/아웃트로 카드 + AI 이미지 자동채움 + 사업형태(셀러/소상공인) 템플릿.

비디오스튜류 벤치마크 반영:
  A1 본문(내레이션)을 문장 단위 '씬'으로 분할
  A2 씬별 TTS 길이를 측정해 씬 지속시간 자동 결정(자막·음성 싱크)
  B3 PIL 자막(Pretendard, 핵심 키워드 색강조)  B4 켄번스 줌  B5 0~3초 훅 + CTA 아웃트로
  C6 사진 부족 시 AI 이미지 자동 생성으로 채움   C7 셀러=구매 CTA / 소상공인=방문 CTA
  D8 9:16 세로(1080x1920)
실패 시 기존 슬라이드쇼로 graceful 폴백(영상이 아예 안 나오는 일은 없게).
"""
from __future__ import annotations

import os
import re
import re as _re
import shutil
import subprocess
import uuid

from app.domain.models import (Asset, Channel, ContentKind, ContentPiece,
                               ContentStatus, Tenant)
from app.generators.base import Generator
from app.generators.text_claude import MODEL, _call_llm, _parse_sections
from app.industries import resolve_industry, industry_brief
from app.strategies import resolve_strategy, buy_block
from app.formats import pick_format, format_directive
from app.media import bgm as bgm_lib
from app.media import tts as tts_lib
from app.media import ai_image
from app import seo

try:                                    # HEIC(아이폰 기본 포맷) 지원 — 없으면 조용히 통과(V2)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

import threading as _threading
# 동시 렌더 상한 — ffmpeg 폭주(업로드 N건=프로세스 N개) 방지. PHASE 1: 기본 1건 직렬화(디스크·CPU 안전).
RENDER_SEM = _threading.BoundedSemaphore(int(os.environ.get("SHOPCAST_RENDER_CONCURRENCY", "1")))
_RENDER_FLOOR_MB = int(os.environ.get("SHOPCAST_RENDER_FLOOR_MB", "120"))   # 이하면 렌더 보류(만차 502 차단). 출력 mp4 ~10MB라 120MB=충분한 안전마진(볼륨 445MB)


def _disk_free_mb(path: str = None) -> "int | None":
    """볼륨(스토리지) 여유 공간 MB. 실패 시 None(게이트는 None을 통과로 취급)."""
    try:
        import shutil as _sh
        _p = path or os.environ.get("SHOPCAST_STORAGE", "storage")
        os.makedirs(_p, exist_ok=True)
        return int(_sh.disk_usage(_p).free / 1e6)
    except Exception:
        return None

W, H, FPS = 1080, 1920, 30
XFADE = 0.25             # 씬 전환 크로스페이드(초) — 검은 플래시 제거(영상강화 PHASE 4)
MAX_SCENES = 6           # 씬(=문장) 최대 — TTS 호출/길이 제어
_WRAP_GLUE = {"안", "못", "왜", "다", "더", "꼭", "잘", "첫", "새", "이", "그", "저"}   # 다음 어절과 분리 금지 선행어
# 앞 어절과 분리 금지 후행어(의존명사·보조용언 계열) — '못 보는 / 건' 류 의미 단위 분리 방지
_TRAIL_GLUE = {"건", "것", "수", "줄", "때", "데", "점", "중", "뒤", "후", "전", "만", "지", "채", "김에", "대로"}
MAX_AI_FILL = 2          # 사진 부족 시 AI 이미지 생성 최대 장수(비용 제어)
MIN_SCENE, MAX_SCENE = 2.2, 9.0   # 씬 길이 클램프(초) — 음성이 잘리지 않게 상한 넉넉히
PER_IMAGE_SECONDS = 3
MAX_SHORT_SECONDS = 58

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_SYS_FONTS = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
]


from dataclasses import dataclass, field


@dataclass
class SceneScript:
    """자막 소스 계약(근본수정) — 렌더러는 이 타입'만' 받는다. 문자열 아무거나 못 받게 해
    내부 프롬프트·브리프·vision 원문·라벨이 자막 경로에 도달하는 배선을 구조적으로 차단.
    source: 'caption_llm'(쇼츠·릴스 = 캡션 생성기의 시청자용 최종 출력)
            | 'body_excerpt'(네이버 영상 = 게이트 통과 본문 발췌)"""
    hook: str
    sentences: list
    outro: str
    source: str = "caption_llm"
    evidence: str = ""            # 인용 근거 대조용(폼 경험담·본문) — 게이트가 창작 인용 검출에 사용


# 내부 텍스트 시그니처(지시문·라벨) — 자막에 하나라도 보이면 렌더 차단
_SUBTITLE_BAN = __import__("re").compile(
    r"서술하라|하라\(|하지 마라|지어내지|반드시 |프롬프트|= 사실\)|사장님 확인|사장님 제공|"
    r"\[사진 내용|\[반영 규칙|\[입력 정보|\[가게\]|\[경험 중심|D\.I\.A|C-Rank|아래 형식|대괄호 머리표")


def _strip_labels(t: str) -> str:
    """원문자·번호·구조 라벨 스트립(①②③, 1., STEP N, '결과 먼저:' 류) — 시청자 자막에 노출 금지."""
    import re as _r
    t = (t or "").strip()
    t = _r.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩▶►▸◆◇●■□★☆※≡»›\-–—•·\s]+", "", t)   # 선두 불릿·특수마커(글 리스트 서식 유출 차단)
    t = _r.sub(r"^(\d+[.)]|STEP ?\d+[:.]?|훅 ?\d[:.]?)\s*", "", t, flags=_r.I)   # '1.'·'1)'만(2019년식은 보존)
    t = _r.sub(r"^\d*\s*안?\s*\([^)]{2,12}\)\s*[:：]?\s*", "", t)   # '2안(손실회피):' 류 후보 라벨
    t = _r.sub(r"^(결과 먼저|문제 제기|호기심 갭|손실 회피)\s*[:：]\s*", "", t)
    t = _r.sub(r"\s*[\(（][^)）]{0,12}(형|공식|유형|호기심|손실|반전)[^)）]{0,6}[\)）]\s*$", "", t)   # 말미 '(손실 회피형)' 류 라벨 제거
    return t.strip()


# 🗣 겁주기·저격 금지 — ★ 생성(프롬프트)과 검사(정규식)가 '같은 목록'을 본다(2026-08-01).
#   실측 사고: 프롬프트엔 '호구' 금지를 넣었는데 검사는 '호구 잡'만 봐서 "호구 될까 불안하다면"이
#   그대로 영상에 구워졌다. 규칙이 두 곳에 따로 있으면 반드시 어긋난다.
#   전 업종 공통(업종 어휘가 아니라 '불안을 파는 화법'을 막는다).
# 📺 영상에서 '글'을 가리키는 말 금지(2026-08-01 사장님 지적) — 영상만 보는 사람에게는
#   앞뒤가 끊긴 말이 된다. 마무리 안내(본문에서 확인하세요)는 아웃트로가 따로 담당한다.
SELFREF_PATTERNS = (
    r"이 글", r"이 포스팅", r"이 게시글", r"글 하나면", r"본문에서 확인", r"아래 글",
    r"위에서 말씀", r"앞서 말씀", r"글 끝",
)
FEAR_PATTERNS = (
    r"호구",                                   # 호구 잡/될까/안 잡힙니다 … 형태 불문
    r"사기\s?당", r"속(지|을까|는다)",
    r"모르면\s?(손해|당|늦)", r"안\s?보면\s?(손해|후회)",
    r"당하지\s?않", r"낚이", r"바가지", r"덤터기",
    r"허위\s?매물\s?(걱정|불안)", r"불안하[다신]",
)
RIVAL_PATTERNS = (
    r"비싸게", r"딴 데", r"다른 (업체|가게|집)", r"타 ?업체", r"(남들|다들)[^.]{0,12}(비싼|비싸)",
)


def fear_ban_line() -> str:
    """프롬프트에 넣을 금지 문구 — 검사 목록과 같은 뿌리에서 만든다(어긋남 방지)."""
    return ("겁주기·공포 마케팅 금지: 호구·사기·속는다·모르면 손해·당하지 않으려면·낚이다·"
            "바가지·덤터기·허위매물 걱정·불안하다 — 이런 말은 소비자 경고 콘텐츠의 화법이다. "
            "불안을 파는 대신 '우리가 뭘 갖췄는지'를 보여줘라.")


_RIVAL_JAB = __import__("re").compile("(" + "|".join(FEAR_PATTERNS + RIVAL_PATTERNS) + ")")
# 자기참조는 아웃트로에서는 정상이므로 본문 씬 검사에만 쓴다(별도 정규식).
_SELFREF = __import__("re").compile("(" + "|".join(SELFREF_PATTERNS) + ")")


# 상호 접미 사전 — 자막 속 '가게명처럼 보이는' 연속 한글어 추출용(업체명 정합 게이트 4-1)
_SHOP_SUFFIX = __import__("re").compile(
    r"([가-힣A-Za-z0-9]{2,}(?:상사|모터스|스토어|공업사|카센터|디테일링|스튜디오|랩핑|썬팅|테크|샵))")


_NUM_CLAIM = None   # 지연 컴파일(아래) — 가격·주행거리·연식 수치 주장 패턴


def _num_claim_check(text: str, source: str) -> str:
    """수치 주장(N만원·N만km·N년식·N원대)의 수치부가 근거(source)에 있어야 — 시세 추정 등
    '폼에 없는 수치'가 자막·제목에 실리는 것 차단(주안모터스 '신차 1300만 원대' 실증 재발 방지)."""
    global _NUM_CLAIM
    import re as _r
    if _NUM_CLAIM is None:
        _NUM_CLAIM = _r.compile(r"(\d[\d,.]*)\s*(만\s?원대?|만원대?|만\s?[kK]m|년식|원대)")
    for m in _NUM_CLAIM.finditer(text or ""):
        num = m.group(1).replace(",", "")
        if num and num not in (source or "").replace(",", ""):
            return f"근거 없는 수치({m.group(0).strip()})"
    # 비교 프레임의 미근거 고유명사(경쟁 모델·타 제품) 차단 — 'XX중고/XX시세/XX보다/XX 말고' 날조.
    # 도달형 표현어는 대부분 2자·조사결합이라 무해, '캐스퍼중고가격' 류 경쟁 모델 날조만 겨냥.
    src_flat = (source or "").replace(" ", "")
    for m in _r.finditer(r"([가-힣A-Za-z]{2,}?)(중고가격|중고시세|중고차|중고|시세)", text or ""):
        ent = m.group(1)
        if ent not in ("신차", "이", "그", "저", "요즘", "동급", "무사고", "이런", "저런", "우리", "저희") and ent not in src_flat:
            return f"근거 없는 비교 대상({m.group(0)})"
    return ""


# ── 가격 의미 게이트(VG3) — 서류 판독값(출고가·취득가)이 '판매가'로 승격되는 의미오류 차단 ──
# 업종 중립: '취득/원가' 문맥의 가격 ≠ 판매가(재판매업 일반 의미). 하드코딩 업종어 0.
_ACQUIRE_TERMS = ("출고", "취득", "신차", "원가", "정가", "공급가", "도매", "감정", "기준가", "매입", "출고가")
_SALE_TERMS = ("판매", "매물", "팝니", "가격은", "판매가", "실매물", "내놓", "에 나온", "특가", "할인가")
_PRICE_RE = __import__("re").compile(r"(\d[\d,]*\s*만\s?원|\d[\d,]{2,}\s*원)")
# VG4: 자막이 '읽어야 할 시각 증거'(수치·기록·일치·계기판 등)를 지시하는지 — 지시하면 과확대 크롭 금지.
_EVIDENCE_REF = __import__("re").compile(
    r"(\d[\d,]*\s*(?:km|㎞|만\s?km|만원|원|년식)|일치|기록부|점검부|성능부|계기판|주행거리|확인해)")

# ── TTS 숫자 발화 정규화(자막 원문 불변, 발화 텍스트만) + 주행거리 단일화 ──
_KR_DIG = ["영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
_KR_POS = ["", "십", "백", "천"]
_KR_BIG = ["", "만", "억", "조"]


def _num_to_kr(n: int) -> str:
    """정수 → 사이-한국어 수사. 12272→'만 이천이백칠십이', 2900→'이천구백', 2022→'이천이십이'."""
    if n == 0:
        return "영"
    parts, gi = [], 0
    while n > 0:
        grp = n % 10000
        if grp:
            if grp == 1 and gi > 0:
                parts.append(_KR_BIG[gi])                 # 만/억 앞 '일' 생략(만, 억)
            else:
                g, ui, digs = grp, 0, []
                while g > 0:
                    d = g % 10
                    if d:
                        digs.append(("" if (d == 1 and ui > 0) else _KR_DIG[d]) + _KR_POS[ui])
                    g //= 10
                    ui += 1
                parts.append("".join(reversed(digs)) + _KR_BIG[gi])
        n //= 10000
        gi += 1
    return " ".join(reversed(parts))


def _speechify(text: str) -> str:
    """자막 원문은 그대로 두고, TTS로 넘길 '발화 텍스트'만 생성 — 수량 숫자를 한국어 수사로, 단위 정규화.
    ('12,272km'→'만 이천이백칠십이 킬로미터', '2,900만원'→'이천구백만 원', '2022년식'→'이천이십이 년식').
    낱자 유형(전화·차대번호 등 하이픈/장문 숫자열)은 변환 예외(패턴 판별, 특정값 하드코딩 없음)."""
    import re as _r
    if not text:
        return text
    _n = lambda s: _num_to_kr(int(s.replace(",", "")))     # noqa: E731

    def _rep(pat, fmt):
        nonlocal text
        text = _r.sub(pat, lambda m: fmt(m), text)
    # 순서 중요: 만원→원, 만km→km, 년식→년
    _rep(r"(?<![\d-])(\d[\d,]*)\s*만\s*원", lambda m: _n(m.group(1)) + "만 원")
    _rep(r"(?<![\d-])(\d[\d,]{1,})\s*원", lambda m: _n(m.group(1)) + " 원")
    _rep(r"(?<![\d-])(\d[\d,]*)\s*만\s*(?:km|㎞|키로)", lambda m: _n(m.group(1)) + "만 킬로미터")
    _rep(r"(?<![\d-])(\d[\d,]{1,})\s*(?:km|㎞|키로)", lambda m: _n(m.group(1)) + " 킬로미터")
    _rep(r"(?<![\d-])(\d{4})\s*년\s*식", lambda m: _n(m.group(1)) + " 년식")
    _rep(r"(?<![\d-])(\d[\d,]*)\s*년(?!식)", lambda m: _n(m.group(1)) + " 년")
    _rep(r"(?<![\d-])(\d[\d,]*)\s*(개|명|장|회|분|시간|퍼센트|명분)", lambda m: _n(m.group(1)) + " " + m.group(2))
    return text


def _speech_number_left(text: str) -> str:
    """발화 게이트 — 변환 안 된 4자리+ 수량 숫자가 남아 있으면 그 값 반환(반려용). 하이픈 숫자열(전화·VIN)은 예외."""
    import re as _r
    m = _r.search(r"(?<![\d-])\d{4,}(?![\d-])", text or "")
    return m.group(0) if m else ""


def _normalize_mileage(text: str, canonical: str) -> str:
    """주행거리 단일화 — 자막·본문의 km 수치를 canonical 하나로 통일(오판독값 제거). canonical='12,272km' 형태."""
    import re as _r
    if not (text and canonical):
        return text
    cm = _r.search(r"[\d,]{2,}", canonical)
    if not cm:
        return text
    cval = cm.group(0)
    return _r.sub(r"([\d,]{2,})(\s*(?:km|㎞|키로))", lambda m: cval + m.group(2), text)


def _resolve_sale_price(gen_source: str, body: str = "") -> str:
    """딜러가 '명시'한 판매가만 반환 — 서류 유래 수치(출고가·취득가)는 절대 아님. 없으면 ''(가격 카드 금지).
    사용자 원칙: 판매가가 명시되지 않으면 가격은 적지 않는다. 1순위 딜러노트(gen_source), 2순위 본문(판매 문맥만)."""
    def _scan(text: str, require_sale: bool) -> str:
        if not text:
            return ""
        for m in _PRICE_RE.finditer(text):
            s, e = m.start(), m.end()
            ctx = text[max(0, s - 24):min(len(text), e + 8)]   # 앞 절 포함(출고(취득)가격(부가세 제외): N 형태 커버)
            if any(a in ctx for a in _ACQUIRE_TERMS):     # 출고가·취득가 문맥 → 판매가 아님
                continue
            if require_sale and not any(t in ctx for t in _SALE_TERMS):
                continue
            return m.group(0).replace(" ", "")
        return ""
    return _scan(gen_source or "", require_sale=False) or _scan(body or "", require_sale=True)


def _price_semantics_violation(text: str, sale_price: str) -> str:
    """VG3: 자막·카드에 '판매가처럼' 실린 가격이 실제 판매가와 다르고 항목 라벨(출고가 등)도 없으면 위반.
    라벨 붙은 대비 수치('신차 출고가 3,040만원')는 허용. sale_price='' 이면 라벨 없는 가격은 전부 위반."""
    if not text:
        return ""
    sp = (sale_price or "").replace(",", "").replace(" ", "")
    for m in _PRICE_RE.finditer(text):
        num = m.group(0).replace(",", "").replace(" ", "")
        if sp and num == sp:
            continue                                      # 판매가 일치 → OK
        ctx = text[max(0, m.start() - 20):m.start()]
        if any(a in ctx for a in _ACQUIRE_TERMS):         # 항목 라벨(출고가 등) 있음 → 대비 맥락 허용
            continue
        return f"라벨 없는 불일치 가격({m.group(0).strip()})"
    return ""


def _subtitle_gate(script: "SceneScript", source: str = "", biz_name: str = "",
                   title: str = "") -> str:
    """자막 게이트(렌더 직전) — 위반 사유 반환(통과 시 '').
    검사: 내부 텍스트 시그니처 / 명령형 어미 / 근거 없는 따옴표 인용(source 대조) /
    경쟁·가격 저격 톤 / 수치 주장 근거 대조(가격·주행거리·연식 — 제목 포함) /
    (번호 라벨은 사전 스트립 후에도 남으면 실패)."""
    import re as _r
    _joined = _r.sub(r"[{}]", "", " ".join([title or ""] + [script.hook] + list(script.sentences)))
    _nc = _num_claim_check(_joined, source)
    if _nc:
        return _nc
    for t in [script.hook] + list(script.sentences) + [script.outro]:
        for line in (t or "").split("\n"):
            line = _r.sub(r"[{}]", "", line).strip()   # 강조 마킹 제거 후 검사(전 항목 동일 적용)
            if not line:
                continue
            if _SUBTITLE_BAN.search(line):
                return f"내부 텍스트 시그니처: '{line[:40]}'"
            if _r.search(r"(하라|마라)[.)!」\"']?$", line):
                return f"명령형 어미: '{line[:40]}'"
            if _r.search(r"^[①②③④⑤⑥⑦⑧⑨⑩]|^\d+[.)]\s|^STEP ?\d", line, _r.I):
                return f"번호·구조 라벨 노출: '{line[:40]}'"
            if _r.search(r"[▶►▸◆◇●■□★☆※≡»›]|[｜|]{2,}|[�]", line):   # 글 리스트 서식·불릿·깨진 특수문자 유출
                return f"서식 마커 노출: '{line[:40]}'"
            # 과장·단정 어투(구어체 확장) — 본문 근거 없는 '짱짱·끝납니다·최고·완벽' 류 차단(보장 표현 금지 연장)
            _exag = _r.search(r"(짱짱|끝장|끝내줍|최고예요|최고입니다|최강|완벽[해합]|무조건|대박|압도적|초특급|끝납니다|백퍼센트|백프로|갑오브갑|무적)", line)
            if _exag and _exag.group(1).replace("예요", "").replace("입니다", "")[:2] not in (source or ""):
                return f"과장·단정 표현: '{_exag.group(0)}'"
            if _RIVAL_JAB.search(line):
                return f"경쟁·가격 저격 톤: '{line[:40]}'"
            # 업체명 정합(4-1): 자막에 상호형 명칭이 등장하면 프로필 실값과 일치해야 통과
            # ('루마모터스' 유형 오기가 영상·TTS로 재발하는 열린 문 봉쇄 — TTS 대본=자막 동일 소스라 1곳으로 충분)
            if biz_name:
                _bn = biz_name.replace(" ", "")
                # 지역+업종 키워드 복합어(예 '부산동구썬팅')는 상호가 아님 → 면제. source에 있으면(키워드·본문) 통과.
                _srcf = (source or "").replace(" ", "")
                for cand in _SHOP_SUFFIX.findall(line):      # 공백 없는 연속어만(단어 경계 존중 — 오탐 방지)
                    _c = cand.replace(" ", "")
                    if _c in _bn or _bn in _c:
                        continue
                    if _c in _srcf:                          # 본문·키워드에 있는 지역+업종 복합어 → 상호 아님(면제)
                        continue
                    if _r.match(r"^(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주|[가-힣]{2,3}(시|군|구|동|읍|면))", _c):
                        continue                             # 지역명으로 시작 = 상호 아니라 지역 키워드
                    return f"업체명 불일치: '{cand}' ≠ 프로필 '{biz_name}'"
            # 근거 없는 따옴표 인용(창작 발화) — 인용 내용의 구별 토큰이 입력(경험담·본문)에 없으면 실패
            for q in _r.findall(r"[\"“]([^\"”]{6,60})[\"”]", line):
                toks = [w for w in _r.findall(r"[가-힣A-Za-z0-9]{3,}", q)][:8]
                if toks and source and not any(w in source for w in toks):
                    return f"근거 없는 인용: '{q[:36]}'"
    return ""


def _per_image(n: int) -> float:
    n = max(n, 1)
    return min(PER_IMAGE_SECONDS, MAX_SHORT_SECONDS / n)


def _font_path(weight: str = "Bold") -> str | None:
    p = os.path.join(_FONT_DIR, f"Pretendard-{weight}.otf")
    if os.path.exists(p):
        return p
    for f in _SYS_FONTS:
        if os.path.exists(f):
            return f
    return None


def _pil_font(size: int, weight: str = "Bold"):
    from PIL import ImageFont
    fp = _font_path(weight)
    try:
        return ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


# ── 글말→영상말 변환(자막 구어화) ─────────────────────────────
# 발췌(사실) → 변환(압축·어미만) → 사실 보존 검사 → 기존 자막 게이트 → 렌더.
# 변환은 '빼기'만 가능: 새 명사·수치가 나타나면 그 문장은 차단하고 발췌 원문을 유지한다.
_SPOKEN_FUNC = {"오늘", "지금", "바로", "이렇게", "정말", "함께", "그리고", "그래서", "그럼",
                "이제", "먼저", "여기", "저희", "이번", "한번", "해서", "까지", "부터", "왜냐",
                "어떻게", "무엇", "얼마나", "합니다", "했습니다", "됩니다", "있습니다", "인데요",
                "하는", "하면", "해요", "돼요", "이에요", "예요", "인가요", "일까요", "할까요",
                # 어미·부정 활용(사실성 무관 — '않습니다'→'않아요' 오탐 방지, Haiku 실전 관측)
                "않아요", "않죠", "않고", "않게", "않는", "않을까요", "했어요", "됐어요", "있어요",
                "해드려요", "드려요", "볼까요", "주세요", "하세요", "이라서", "라서", "이라", "이랑", "하고",
                "더했어요", "했는데", "했으니", "하니까", "되니까", "보니까", "말씀드릴게요", "말씀드립니다",
                "봤어요", "봐야", "보세요", "골라야", "고르기", "그대로",
                "번째", "번째로", "첫째", "둘째", "셋째", "먼저", "다음", "이렇게", "저렇게", "무엇을", "어디에",
                "이번엔", "이번", "그래서", "그러니", "그런데", "오늘은", "이제는", "요즘엔", "이런", "저런", "그런"}


def _cut_word(s: str, n: int) -> str:
    """어절 경계 절단 — '…실차 확인이 답입' 같은 어절 중간 잘림 방지(초과 시 마지막 완전 어절까지)."""
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    return cut[:cut.rfind(" ")].rstrip(" ,·—-") if " " in cut else cut


def video_qc(video_path: str, n_frames: int = 3) -> dict:
    """🎞 렌더 결과 화면 자동 검사(업종 중립, 사장님 승인 2026-07-28) — 프레임 추출 → vision 판정.
    잡는 것: ①핵심 피사체(차·제품·음식·시공물 등) 어색한 잘림 ②자막 깨짐·겹침 ③PII(번호판·전화·얼굴) 노출.
    실패는 조용히({"ok": None}) — 영상 파이프라인을 막지 않고 기록만."""
    import base64
    import json as _j
    import logging as _lg
    import tempfile
    log = _lg.getLogger("shopcast.video")
    if not (video_path and os.path.exists(video_path) and shutil.which("ffmpeg")):
        return {"ok": None, "reason": "영상/ffmpeg 없음"}
    try:
        pr = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                             "-of", "csv=p=0", video_path], capture_output=True, text=True, timeout=20)
        dur = max(1.0, float((pr.stdout or "3").strip() or 3))
        imgs = []
        with tempfile.TemporaryDirectory() as td:
            for i in range(n_frames):
                ts = dur * (i + 1) / (n_frames + 1)
                fp = os.path.join(td, f"q{i}.jpg")
                subprocess.run(["ffmpeg", "-y", "-ss", f"{ts:.1f}", "-i", video_path, "-vframes", "1",
                                "-vf", "scale=540:-2", "-q:v", "5", fp],
                               capture_output=True, timeout=30)
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        imgs.append(("image/jpeg", base64.b64encode(f.read()).decode()))
        if not imgs:
            return {"ok": None, "reason": "프레임 추출 실패"}
        from app import llm
        raw = llm.call_task(
            "vision",
            f"세로 영상에서 뽑은 프레임 {len(imgs)}장이다. 전체 프레임을 보고 JSON 한 줄로만 답하라.\n"
            "① crop: 핵심 피사체(차량·제품·음식·시공물·사람 등 그 프레임의 주인공)가 어색하게 잘려 "
            "무엇인지 알아보기 어려운 프레임이 있으면 true (블러 배경 위 온전한 피사체는 정상=false)\n"
            "② subtitle: 자막이 화면 밖으로 나가거나 겹치거나 깨져 읽기 어려우면 true\n"
            "③ pii: 차량 번호판 글자·전화번호·사람 얼굴이 가림(모자이크) 없이 식별되면 true\n"
            '형식: {"crop":false,"subtitle":false,"pii":false,"note":"한 줄 요약"}',
            300, images=imgs)
        m = re.search(r"\{.*\}", raw or "", re.S)
        d = _j.loads(m.group(0)) if m else {}
        out = {"ok": not (d.get("crop") or d.get("subtitle") or d.get("pii")),
               "crop": bool(d.get("crop")), "subtitle": bool(d.get("subtitle")),
               "pii": bool(d.get("pii")), "note": str(d.get("note") or "")[:120]}
        if out["pii"]:
            log.error("[video-qc] ⚠️ PII 노출 의심: %s (%s)", video_path, out["note"])
        elif not out["ok"]:
            log.warning("[video-qc] 화면 결함 의심: %s", out)
        return out
    except Exception as e:
        return {"ok": None, "reason": repr(e)[:100]}


def _parse_emphasis(text: str) -> tuple[str, list]:
    """자막 강조 마킹 {어절} 파싱 → (마킹 제거 텍스트, 강조 어절 목록[최대 1 — 남발 금지]).
    마킹은 기존 어절을 감싸는 표시일 뿐 — 텍스트 자체는 사실 게이트를 통과한 그대로."""
    import re as _r
    emph = _r.findall(r"\{([^{}]{1,20})\}", text or "")[:1]
    clean = _r.sub(r"[{}]", "", text or "")
    # 조사 스트립(강조는 명사 핵심만 — '{테이프로}' 강조 실사고 2026-07-27): 남는 어간 2자+일 때만
    _JOSA = ("에서부터", "으로부터", "이라도", "까지", "부터", "에서", "으로", "이랑", "처럼",
             "조차", "마저", "한테", "에게", "보다", "만큼", "라도", "대로",
             "로", "와", "과", "은", "는", "이", "가", "을", "를", "에", "도", "의", "만", "랑")
    out = []
    for e in (e.strip() for e in emph if e.strip()):
        for j in _JOSA:
            if e.endswith(j) and len(e) - len(j) >= 2:
                e = e[:-len(j)]
                break
        out.append(e)
    return clean, out


def _fact_guard(line: str, source: str) -> str:
    """변환 출력의 명사·수치가 발췌 원문(source)에 전부 근거하는지 — 새 정보 등장 시 사유 반환.
    어미 변형('중요할까'→'중요할까요')은 어간 프리픽스 매칭으로 허용.
    강조 마킹({})은 제거 후 검사 — 마킹 안 토큰도 동일한 근거 검사를 받는다(주입 통로 차단)."""
    import re as _rg
    line = _rg.sub(r"[{}]", "", line or "")
    for num in _rg.findall(r"\d+", line):
        if num not in source:
            return f"수치 날조({num})"
    # 서술어(동사·형용사 활용)는 사실이 아니라 표현 — 명사 검사에서 제외(오탐 차단).
    # 사실 보존 대상 = 고유명사·수치(차종·필름명·지역·업체명·숫자). '익혀갑니다·불안감이죠'는 서술.
    # 서술어(동사·형용사·연결어미) 광범위 스킵 — 사실 보존은 고유명사·수치 대상(수치·비교대상은 별도 게이트).
    # 활용 어미를 못 잡아 '봅시다·나올까·비싸지'를 날조로 오판하던 상시 폴백 차단(위험 비대칭: 동사 통과는 무해).
    _PRED = _rg.compile(
        r"(니다|습니다|세요|해요|어요|아요|여요|워요|와요|봐요|줘요|대요|래요|게요|나요|가요|데요|"
        r"였|았|었|겠|더|든|줘|봐|와|워|려|랴|"
        r"드려요|드립니다|이죠|이에요|예요|네요|군요|을게요|ㄹ게요|십시오|거예요|되죠|하죠|고요|"
        r"진|더라|거든요|잖아요|는데요|는데|지만|으며|면서|니까|어서|아서|해서|다가|"
        r"다면|라면|려면|으셔|으세요|으시|시면|시죠|시다|ㅂ시다|읍시다|갑니다|봅시다|보죠|하시죠|"
        r"까|죠|지|고|서|면|은|는|을|여|해|봐|와|워|줘|대|래|네|군|나|가|데|"
        r"을까|ㄹ까|던가|든지|거나|든가|을지|ㄹ지|길래|더니|는지|"
        r"없이|있게|없게|같이|처럼|만큼|토록|도록|채로|대로|듯이|듯|"
        r"려요|려고|려는|겠어요|길|든요|구요|더라고요|잖아|더군요)$")
    # 명사 근거 검사(단순·견고): 토큰의 2자 어간이 본문 어디에도 없으면 날조 후보.
    # 한국어 활용어미를 정규식으로 완전 열거하는 것은 불가능(붙여야·햇빛에·나올까 상시 오탐) →
    # '2자 어간이 본문에 실존하는가'로 판정. 위험 비대칭(동사 통과 무해, 고유명사 날조는 2자 어간이 본문에 없음).
    _srcflat = (source or "")
    for tok in _rg.findall(r"[가-힣]{3,}", line):
        if tok in _SPOKEN_FUNC or _PRED.search(tok):
            continue                                  # 기능어·서술어(활용형)는 통과
        if tok[:2] in _srcflat:                        # 2자 어간이 본문에 실존 → 통과(활용·조사 결합 허용)
            continue
        return f"근거 없는 표현({tok})"
    return ""


def _to_spoken(sentences: list, source: str) -> list:
    """발췌 문장들을 짧은 구어체 영상 문장으로 변환(Gemini 경로 — 저지능 작업).
    사실 추가 금지 — 문장 단위로 사실 보존 검사, 실패 문장은 발췌 원문 유지(날조 재유입 차단).
    LLM 실패 시 전체 원문 유지 — 영상 생성 흐름을 막지 않는다."""
    import logging as _lg
    log = _lg.getLogger("shopcast.video")
    if not sentences:
        return sentences
    from app import llm as _llm
    prompt = ("아래는 블로그 본문에서 발췌한 문장들이다. 각 문장을 '영상 카피'로 바꿔라.\n"
              "규칙:\n"
              "- 같은 사실만 담아라. 새 정보·수치·명사 추가 절대 금지 — 압축·재배열·어미 변환만 허용.\n"
              "- 씬당 하나의 메시지만. 핵심 숫자·단어를 문장 맨 앞으로(예: '830만 원. 신차가 1,327만이던 그 모닝입니다').\n"
              "- 어미는 씬마다 변화를 줘라: 명사 종결·질문·청유를 섞고 '~입니다' 연속 금지. 과장·보장 표현 금지.\n"
              "- 한 문장당 22자 내외(최대 28자).\n"
              "- 각 문장에서 가장 중요한 숫자·핵심명사(차종·메뉴·제품명 등) 어절 하나만 중괄호로 감싸라(예: {830만 원}). 문장당 최대 1개, 없으면 안 감싸도 된다. 중괄호 안 어절은 원문에 있는 그대로만.\n"
              "- 입력과 같은 개수의 줄로, 순서 그대로, 번호·라벨·따옴표 없이 한 줄씩만 출력.\n\n"
              + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences)))
    try:
        raw = _llm.call_task("spoken", prompt, max_tokens=600)   # 기본 Claude Haiku(제약 준수형) → 실패 시 Gemini 역폴백
    except Exception as e:
        log.warning("[spoken] 변환 호출 실패 — 발췌 원문 유지: %r", repr(e)[:100])
        return sentences
    import re as _rg
    lines = [_rg.sub(r"^\s*\d+[.)]\s*", "", ln).strip().strip('"“”')
             for ln in (raw or "").splitlines() if ln.strip()]
    if len(lines) != len(sentences):
        log.warning("[spoken] 줄 수 불일치(%d→%d) — 발췌 원문 유지", len(sentences), len(lines))
        return sentences
    out = []
    for orig, conv in zip(sentences, lines):
        bad = _fact_guard(conv, source) if conv else "빈 출력"
        _plain = conv.replace("{", "").replace("}", "")
        if bad or len(_plain) > 35:
            log.warning("[spoken] 문장 차단(%s) — 원문 유지: %r", bad or "길이 초과", conv[:40])
            out.append(orig)
        else:
            out.append(conv)
    return out


# ── 대본 단위 자막 생성(씬별 발췌 → 한 편의 이야기) ──────────────
# 씬마다 독립 발췌하면 문장은 통과해도 이어 붙이면 서사가 끊긴다(예고 후 미이행·중복·순서 점프).
# 본문 전문 + 씬 수를 넣어 1콜로 대본 전체를 쓰고, 대본 게이트(중복·예고-이행)와
# 사실 게이트(전체 본문 대조)를 통과해야 채택. 실패 시 기존 발췌 방식 폴백(영상 흐름 불차단).
_FORESHADOW = None   # 예고형 문장 패턴(지연 컴파일)


def _norm_line(s: str) -> set:
    import re as _r
    return set(_r.findall(r"[가-힣A-Za-z0-9]{2,}", (s or "").replace("{", "").replace("}", "")))


def _script_gate(lines: list) -> str:
    """대본 게이트 — 위반 사유 반환(통과 시 ''). ① 씬 간 유사 문장 중복(자카드>0.6)
    ② 예고('단점부터/솔직히 말씀드릴게요' 류) 뒤 씬이 실제 내용(구체 서술)인지."""
    import re as _r
    global _FORESHADOW
    if _FORESHADOW is None:
        _FORESHADOW = _r.compile(r"(말씀드릴게요|말씀드립니다|공개합니다|알려드릴게요|보여드릴게요|짚어볼게요)[.!?]?$")
    toks = [_norm_line(s) for s in lines]
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if toks[i] and toks[j]:
                jac = len(toks[i] & toks[j]) / len(toks[i] | toks[j])
                if jac > 0.6:
                    return f"씬 중복(유사 {jac:.1f}): '{lines[i][:20]}'≈'{lines[j][:20]}'"
    for i, s in enumerate(lines):
        plain = s.replace("{", "").replace("}", "").strip()
        if _FORESHADOW.search(plain) and len(plain) <= 20:      # 내용 없는 예고형
            nxt = (lines[i + 1] if i + 1 < len(lines) else "").replace("{", "").replace("}", "")
            if not nxt or (_FORESHADOW.search(nxt.strip()) and len(nxt.strip()) <= 20):
                return f"예고 후 미이행: '{plain[:24]}' 다음 씬에 내용 없음"
            if len(_norm_line(nxt)) < 2:
                return f"예고 후 미이행: '{plain[:24]}'"
    return ""


def _cap_lines(sentences: list, max_lines: int = 3, budget: float = 9.0, imgs: list = None):
    """씬당 3줄 초과 강제 분할(코드 강제) — 긴 문장을 절 경계로 나눠 각 조각이 3줄 이내가 되게.
    강조 마킹 {} 균형 보존.

    ★ imgs를 주면 (자막, 사진) 쌍을 함께 돌려준다(2026-08-02 실측 결함).
      '분할 조각은 같은 사진을 쓴다'고 주석에 적혀 있었지만 실제로는 아무도 사진을 늘려주지
      않았다 — 호출부가 imgs[:len(sent)]로 자르는 구조라, 9장으로 12줄이 나오면 뒤 3줄은
      사진 없이 남았다(실측: 주안모터스 네이버 영상 12씬 vs 사진 9장).
      화면-자막 일치는 사장님이 '절대 불변'이라 하신 원칙이다 — 분할이 그걸 깨면 안 된다."""
    import re as _r
    cap = max_lines * budget                          # 3줄 ≈ 가중치 30
    def _w(s):
        s = s.replace("{", "").replace("}", "")
        return sum(1.0 if ("가" <= c <= "힣" or "一" <= c <= "鿿") else 0.55 for c in s)
    out, src = [], []                       # src[i] = 이 조각이 나온 원본 문장 번호
    for _si, s in enumerate(sentences):
        s = (s or "").strip()
        if not s:
            continue
        _n0 = len(out)
        if _w(s) <= cap:
            out.append(s)
            src += [_si] * (len(out) - _n0)
            continue
        # 절 경계 분할(쉼표·강한 연결어미) 후 cap 이하로 재그룹
        parts = _r.split(r"(?<=[,，、])\s+|(?<=지만)\s+|(?<=는데)\s+|(?<=으며)\s+|(?<=니까)\s+|(?<=어서)\s+|(?<=해서)\s+|(?<=면서)\s+", s)
        cur = ""
        for p in [x.strip() for x in parts if x.strip()]:
            if cur and _w(cur + " " + p) > cap:
                out.append(cur.strip(" ,"))
                cur = p
            else:
                cur = (cur + " " + p).strip() if cur else p
        if cur.strip():
            # 여전히 초과하면 어절 경계로 하드 분할
            rest = cur.strip()
            while _w(rest) > cap:
                ws = rest.split(" ")
                acc = ""
                for j, w in enumerate(ws):
                    if _w(acc + " " + w) > cap and acc:
                        # 숫자·단위 경계(830만 | 원)에서 끊지 않기 — 직전 어절이 수/만/억 류면 한 어절 더 포함
                        if _r.search(r"(\d|만|억|천|년|월|일)$", acc) and j < len(ws):
                            acc = (acc + " " + w).strip()
                        # ★ 수식어에서 끊지 않기(2026-08-02 실측: '…오렌지색 고전압'에서 끊겨
                        #   무엇이 고전압인지 없는 조각이 자막으로 구워졌다). 색·형·식·용·급·의로
                        #   끝나는 말은 뒤에 올 이름을 기다리는 말이다 — 한 어절 더 붙인다.
                        #   언어 규칙만(업종 무관), 한 번만 늘려 상한을 크게 넘지 않게 한다.
                        elif _r.search(r"(색|형|식|용|급|의|적|성)$", acc) and j < len(ws):
                            acc = (acc + " " + w).strip()
                        break
                    acc = (acc + " " + w).strip()
                out.append(acc.strip(" ,"))
                rest = " ".join(ws[len(acc.split(" ")):]).strip()
            if rest:
                out.append(rest.strip(" ,"))
        src += [_si] * (len(out) - _n0)          # 이 문장이 만든 조각 전부에 같은 사진을 물린다
    # 고립 말미 조각(예: '시운전해 보시고') 병합 — 앞 줄과 합쳐 어중간한 조각 방지(3줄 소폭 초과 허용)
    merged, msrc = [], []
    for _k, s in enumerate(out):
        if merged and _w(s) < 8 and _w(merged[-1]) + _w(s) <= cap + 8:
            merged[-1] = (merged[-1].rstrip(" ,") + " " + s).strip()
        else:
            merged.append(s)
            msrc.append(src[_k] if _k < len(src) else (msrc[-1] if msrc else 0))
    # 중괄호 균형 복구(분할로 한쪽만 남으면 제거)
    fixed = []
    for s in merged:
        if s.count("{") != s.count("}"):
            s = s.replace("{", "").replace("}", "")
        fixed.append(s)
    if imgs is None:
        return fixed
    _im = [imgs[i] if i < len(imgs) else (imgs[-1] if imgs else "") for i in msrc]
    return fixed, _im


def _seam_dedup(hook: str, sent: list, outro: str) -> list:
    """훅 카드↔첫 씬, 마지막 씬↔아웃트로 카드의 이음매 중복 제거(같은 말 연속 재생 방지)."""
    def _sim(a, b):
        ta, tb = _norm_line(a), _norm_line(b)
        return (len(ta & tb) / len(ta | tb)) if (ta and tb) else 0.0
    def _sim2(a, b):   # 어간 프리픽스 인지 유사도(모닝≈모닝인데)
        ta, tb = list(_norm_line(a)), list(_norm_line(b))
        if not (ta and tb):
            return 0.0
        inter = sum(1 for x in ta if any(x[:2] == y[:2] and (x in y or y in x or x[:3] == y[:3]) for y in tb))
        return inter / max(len(ta), len(tb))
    out, seen = [], []
    for x in sent:
        if _sim(hook, x) > 0.5 or _sim2(hook, x) >= 0.6:   # 훅과 겹치는 씬(어간 인지) 전부 제거
            continue
        if any(_sim(x, y) > 0.6 or _sim2(x, y) >= 0.7 for y in seen):   # 내부 중복 제거
            continue
        out.append(x); seen.append(x)
    if len(out) >= 2 and _sim((outro or "").split("\n")[0], out[-1]) > 0.5:
        out = out[:-1]                                  # 아웃트로와 겹치는 마지막 씬 제거
    return out


def _dedup_lines(lines: list) -> list:
    """대본 강등 폴백 — 유사 중복 씬 제거 + 내용 없는 예고형 씬 제거(사실 우선: 영상은 살린다).
    순서 보존, 첫 등장만 유지."""
    global _FORESHADOW
    if _FORESHADOW is None:
        import re as _r
        _FORESHADOW = _r.compile(r"(말씀드릴게요|말씀드립니다|공개합니다|알려드릴게요|보여드릴게요|짚어볼게요)[.!?]?$")
    out, seen = [], []
    for s in lines:
        plain = (s or "").replace("{", "").replace("}", "").strip()
        if not plain:
            continue
        t = _norm_line(plain)
        if any((t and st and len(t & st) / len(t | st) > 0.6) for st in seen):
            continue                                   # 유사 중복 제거
        if _FORESHADOW.search(plain) and len(plain) <= 20:
            continue                                   # 내용 없는 예고형 제거
        out.append(s)
        seen.append(t)
    return out


def _kw_shorten_nolocal(kw: str, region: str) -> str:
    """폴백 훅용 — 키워드에서 지역 토큰(시·군·구·동 이름)을 제거(셀러·병행)."""
    import re as _r
    toks = [t for t in (kw or "").split() if t]
    reg_toks = set(_r.findall(r"[가-힣]{2,}", region or ""))
    out = [t for t in toks if t not in reg_toks and not _r.search(r"(시|군|구|동|읍|면)$", t)]
    return " ".join(out).strip() or (toks[-1] if toks else "")


def _hook_gate(hook: str, keyword: str, biz_type: str, region: str) -> str:
    """오프닝 훅 게이트 — 위반 사유(통과 시 ''). ① 타깃 키워드 원형 통째 삽입 금지(비문·도배)
    ② 셀러·병행 가게는 훅에 지역명 금지(전국 탁송 손님 초장에 거르기 방지). 매장 전용은 지역 허용."""
    import re as _r
    h = _r.sub(r"[{}]", "", hook or "").strip()
    kw = (keyword or "").strip()
    if kw and kw.replace(" ", "") in h.replace(" ", ""):     # 키워드 원형 통째 → 차단
        # ★ 사유 문구에 '훅'을 명시한다(2026-08-01 실사고) — 호출부의 강등 경로가
        #   ("중복","미이행","과장","서식","인용","훅") 부분일치로 소프트 위반을 가려내는데,
        #   이 문구에 '훅'이 없어 하드 위반으로 분류돼 **영상 전체가 생성 중단**됐다.
        #   훅은 한 줄일 뿐이다 — 그 줄만 갈아끼우면 되지 영상을 포기할 이유가 없다.
        return f"훅 키워드 원형 삽입('{kw}')"
    if (biz_type or "local") in ("seller", "hybrid"):
        _regcores = set()
        for tok in _r.findall(r"[가-힣]{2,}", region or ""):
            core = _r.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|시|군|구|읍|면|동|도)$", "", tok)
            if len(core) >= 2:
                _regcores.add(core)
            if len(tok) >= 2:
                _regcores.add(tok)
        for core in _regcores:
            if core in h:                                    # 셀러·병행 훅에 지역명(어간) → 차단
                return f"훅 지역명 노출(셀러·병행, '{core}')"
    return ""


def _script_from_body(body: str, n: int, kw_nat: str, source: str, tone: str = "info",
                      biz_type: str = "local", region: str = "", title: str = "") -> list | None:
    """본문 전문 → 씬 N개 대본(1콜, Haiku 경로). tone='info'(네이버 정보형)|'reach'(쇼츠·릴스 도달형).
    구조: 핵심(본문 순서 유지) → 단점·정직 고지 → (클로징은 템플릿).
    대본 게이트·사실 게이트 실패 시 사유 피드백 재생성 1회 → 재실패 None(호출부 폴백)."""
    import logging as _lg
    log = _lg.getLogger("shopcast.video")
    from app import llm as _llm
    # 🎬 스토리 아크(2026-07-28 사장님 지시: '목차 낭독' 폐지) — 요약 나열이 아니라 하나의 이야기:
    #   질문 훅 → 사진이 증거가 되며 궁금증을 하나씩 풀되, 각 씬 끝이 다음 씬을 부르게 → 답 확정 → 행동.
    _struct = ("- 구조: [훅(핵심 숫자·반전을 맨 앞)] → 전개 2~3 → 마무리. 첫 줄은 스크롤 멈추는 강한 훅.\n"
               "- 톤: 도달형(짧고 리듬감 있는 구어체 허용). 단 과장·보장('짱짱·끝납니다·최고·완벽·무조건') 금지, 경쟁 저격 금지.\n"
               if tone == "reach" else
               "- 구조(스토리 아크 — 본문 순서를 그대로 따라가지 말 것): ①첫 씬=시청자가 진짜 궁금한 질문 하나 "
               "②중간 씬들=그 질문에 사진이 증거가 되도록 하나씩 답하되, 씬마다 '작은 확인→다음 궁금증'으로 "
               "연결(예: '실주행은 확인됐죠. 그럼 내부는?') ③끝에서 두 번째=단점·한계 정직 고지 1개 "
               "④마지막=처음 질문에 대한 한 줄 답. 전체가 질문 하나를 푸는 한 편의 이야기여야 한다.\n"
               "- 어미는 씬마다 변화(명사 종결·질문·청유 혼용, '~입니다' 연속 금지). 과장·보장 표현 금지.\n"
               "- 소제목·목차식 씬 금지(예: '외관부터 솔직하게 – 전면·후면' 같은 제목형 문장 금지) — "
               "모든 씬은 사람이 말하는 문장이어야 한다.\n")
    _allow_region = (biz_type or "local") not in ("seller", "hybrid")   # 매장 전용만 지역 허용
    _hook_rule = (
        "- 첫 줄(훅)은 검색자의 실제 궁금증으로 새로 써라. 소재는 "
        + ("지역·방문(예 '○○에서 썬팅, 어디에 맡길까요?')" if _allow_region
           else "매물·상품·가격·상태(차량이면 '9만km 모닝, 830만 원이면 어떤 상태일까요?', 제품이면 '수제 딸기잼, 왜 이틀이면 품절일까요?' 식 — 업종에 맞게). "
                "단 입력에 명시된 긍정 사실(무사고 등)을 의심·번복하는 훅 금지 — 밝혀진 사실은 확정으로 두고 강점으로 써라") + ".\n"
        + ("" if _allow_region else "- 훅과 모든 자막에 지역명(시·군·구·동 이름)을 넣지 마라 — 전국 손님이 대상이다.\n")
        + "- 타깃 키워드를 통째로 훅에 넣지 마라(비문·도배). 질문·반전으로 자연스럽게.\n"
        # ★ 대상 없는 껍데기 훅 금지(2026-08-01 사장님 지적) — 실측: '부산 기장 중고차 모르면 손해'는
        #   '무엇을' 모르면 손해인지가 없다. 겁만 주고 알맹이가 없는 문장이다.
        + "- 훅에는 '무엇에 대한 이야기인지'가 반드시 들어가야 한다. 대상이 빠진 문장 금지"
          "(나쁜 예: '○○ 모르면 손해', '○○ 이것만 알면 끝' — 무엇을 모르는지·무엇이 이것인지 없다).\n"
        + "- 본문에 있는 구체적 사실(차종·모델·수치·가격·상태 중 하나 이상)을 훅에 담아라. "
          "그 사실이 곧 대상이고, 손님이 클릭하는 이유다.\n"
        + (f"- ★ 이 글의 제목은 '{title}'이다. 훅은 이 제목과 같은 것을 말해야 한다"
           "(같은 대상·같은 약속). 제목은 무엇을 보여준다고 하는데 훅은 겁만 주는 식의 어긋남 금지. "
           "제목을 그대로 베끼지는 말고, 영상 첫 줄답게 짧게.\n" if title else ""))
    # 🗣 화자·청자(2026-08-01 사장님 지적) — 글에는 적용했는데 영상 대본이 빠져 있었다.
    #   실측 대본: "부산 기장 중고차 모르면 손해 / 사기 당할까 봐 걱정되셨죠?" — 소비자에게 겁을 주는
    #   정보성 블로거 말투다. 올린다 사용자는 물건을 팔아야 하는 셀러이고, 이 영상은 그 판매 도구다.
    _voice = ("- 말하는 사람은 '가게 사장'이다. 내 물건·서비스를 손님에게 보여주는 영상이다. "
              "말투는 끝까지 주인의 것(예: 제가 직접 확인했습니다 / 보여드릴게요 / 오시면 열어드립니다). "
              "손님이 쓴 사용기·후기처럼 쓰지 마라 — 우리는 파는 쪽이다.\n"
              "- " + fear_ban_line() + "\n"
              "- 손님의 고민을 부를 때만 손님 말을 쓴다(손님은 사는 쪽이다 — '판매 가격'이 아니라 '구매 가격').\n")
    base = (_voice
            + "아래 블로그 본문을 근거로, 세로 영상 자막 대본을 써라. 전체가 하나의 이야기가 되게.\n"
            f"- 자막 씬 {n}개, 한 줄씩 출력(번호·라벨 없이). 각 씬 12~20자(공백 포함, 절대 24자 초과 금지) — 한 호흡에 읽히게.\n"
            "- 한 문장이 길면 두 씬으로 쪼개되, 반드시 '문장 경계'에서만 쪼개라 — 각 씬은 그 자체로 완결"
            "(종결어미 다/요/죠/까 또는 문장부호로 끝). 문장 중간에서 끊긴 씬 절대 금지. 씬 하나에 두 메시지 금지.\n"
            + _struct +
            "- 예고를 했으면('단점부터 볼게요' 등) 바로 다음 씬이 그 내용이어야 한다. 예고만 하고 안 보여주기 금지.\n"
            "- 동일·유사 문장 반복 금지. 씬당 하나의 메시지, 핵심 숫자·단어를 문장 앞에.\n"
            "- 본문에 있는 사실만. 새 정보·수치·명사 추가 절대 금지. 완결된 문장만(어중간한 조각·조사 시작 금지).\n"
            "- 각 씬에서 가장 중요한 숫자·핵심명사(차종·메뉴·제품명 등) 하나만 {중괄호}로 감싸라(씬당 최대 1개). "
            "조사를 떼고 명사·숫자만 감싸라(예: {마스킹} O / {테이프로} X, {36,524km} O).\n"
            "- 출력은 자막 줄만. 머리말·설명·'대본입니다' 류 문장 절대 출력 금지.\n"
            + _hook_rule +
            f"- 타깃 키워드(참고용 — 훅·자막에 이 문구를 통째로 넣지 말고, 검색자의 실제 궁금증을 네 말로): {kw_nat}\n\n[본문]\n" + body[:3500])
    feedback = ""
    for attempt in (1, 2):
        try:
            raw = _llm.call_task("spoken", base + feedback, max_tokens=800)
        except Exception as e:
            log.warning("[script] 대본 생성 호출 실패: %r", repr(e)[:100])
            return None
        import re as _r
        lines = [_r.sub(r"^\s*\d+[.)]\s*", "", ln).strip().strip('"“”')
                 for ln in (raw or "").splitlines() if ln.strip()]
        lines = [_r.sub(r"\}+", "}", _r.sub(r"\{+", "{", ln)) for ln in lines]   # 중복 중괄호 정규화({{·}})
        lines = [ln for ln in lines
                 if not _r.search(r"(대본|자막 씬|씬 \d|아래는|다음은|다음과 같|출력)", ln)][:n]   # 머리말 제거
        if len(lines) < max(3, n - 1):
            feedback = f"\n\n[재작성] 씬 수가 {len(lines)}개였다 — 정확히 {n}줄로 다시."
            continue
        bad = next((f"{i + 1}번 씬 {_fact_guard(l, source)}" for i, l in enumerate(lines)
                    if _fact_guard(l, source)), "") or _script_gate(lines)
        if not bad and lines:                          # 첫 줄=훅 게이트(키워드 원형·셀러/병행 지역명)
            _hb = _hook_gate(lines[0], kw_nat, biz_type, region)
            if _hb:
                bad = f"훅 {_hb}"
        if not bad:
            _lim = 30 if tone == "reach" else 34      # 자막 2줄(줄당 ~10자·최대 3줄) 내 — 4줄 자막 실사고 방지
            _over = [l for l in lines if len(l.replace("{", "").replace("}", "")) > _lim]
            if _over:
                bad = f"씬 길이 초과({len(_over)}개, 각 {_lim}자 이내로): '{_over[0][:26]}…'"
        if not bad:
            # 씬 완결성 게이트(2026-07-28 실사고: '이 글이 도움 출고 직후' 절단 자막) —
            # 종결어미·문장부호로 끝나지 않는 씬 = 문장 중간 절단 → 재작성
            _frag = [l for l in lines
                     if not _r.search(r"([다요죠까네]|[.!?…]|\d|km|%)\s*[.!?…]?$",
                                      l.replace("{", "").replace("}", "").strip())]
            if _frag:
                bad = f"문장 중간 절단 씬({len(_frag)}개, 완결 문장으로): '{_frag[0][:26]}…'"
        if not bad:
            return lines
        log.warning("[script] 대본 게이트 차단(%d/2): %s", attempt, bad)
        feedback = f"\n\n[재작성 — 직전 대본이 검증에서 차단됨: {bad}] 위반을 고쳐 전체를 다시 써라."
    return None


# 씬 자막 유형 → 우선 매칭할 사진 묘사 키워드(vision 태그 우선순위 보정 — 정밀화 2-3)
_SCENE_PHOTO_HINT = [
    (("검수", "점검", "시동", "하체", "엔진", "누유", "냉각수", "성능점검"),
     ("엔진", "엔진룸", "보닛", "계기판", "하체", "하부", "언더", "리프트", "누유", "오일")),
    (("서류", "기록부", "성능기록", "보험이력", "등록증", "점검표", "명세"),
     ("서류", "기록부", "문서", "등록증", "점검표", "명세", "종이")),
    (("가격", "만원", "연식", "주행거리", "매물", "실매물", "스펙", "출고"),
     ("전면", "정면", "측면", "외관", "전측면", "대각", "전경", "차량")),
    (("실내", "시트", "옵션", "네비", "핸들", "대시"),
     ("실내", "시트", "대시", "센터", "핸들", "운전석", "내부")),
    # 업종 중립(2026-07-29 골든 실측 후): 음식·시술/시공·매장 — 전 업종 공통 계열
    (("메뉴", "맛", "신선", "재료", "주문", "포장", "예약"),
     ("음식", "메뉴", "빵", "케이크", "디저트", "완성", "진열", "제품")),
    (("시술", "시공", "작업", "과정", "마감", "제작"),
     ("작업", "시공", "과정", "도구", "손", "제작", "부착")),
    (("매장", "방문", "위치", "분위기", "찾아"),
     ("매장", "전경", "간판", "인테리어", "내부", "입구")),
]


def _hint_bonus(scene_text: str, desc_text: str) -> float:
    """씬 자막 유형과 사진 묘사가 같은 계열이면 가점(검수 자막↔엔진룸 사진 등)."""
    st, dt = scene_text or "", desc_text or ""
    for keys, photo_kw in _SCENE_PHOTO_HINT:
        if any(k in st for k in keys) and any(pk in dt for pk in photo_kw):
            return 0.5
    return 0.0


def _distinctive_objects(descs: dict) -> set:
    """vision 묘사에서 '변별력 있는 시각 대상 어휘' 추출 — 전 사진의 절반 이하에만 나오는 2자+ 명사류.
    (엔진룸·계기판·휠·등록증·실내 등). 어휘는 데이터(vision 출력) 유래 — 업종 하드코딩 0."""
    import re as _r
    from collections import Counter
    cnt = Counter()
    for raw in descs.values():
        toks = set(w for w in _r.findall(r"[가-힣]{2,}", raw or "")
                   if w not in ("사진", "차량", "모습", "보이", "있습니다", "있는", "촬영", "제품", "매장"))
        for w in toks:
            cnt[w] += 1
    n = max(1, len(descs))
    # 절반 이하 사진에만 등장 = 변별력(전체에 다 나오는 일반어 제외)
    return {w for w, c in cnt.items() if 1 <= c <= max(1, n // 2)}


_DOC_WORDS = ("서류", "등록증", "기록부", "문서", "증명서", "계약서", "성능점검")
_EXT_WORDS = ("외관", "전면", "후면", "측면", "전측면", "후측면", "차체", "전체 모습",
              "매장 전경", "전경", "제품 전체", "완성품", "진열")


def _photo_role(desc: str) -> str:
    """per-photo vision 묘사 → 역할 분류(데이터 유래): doc(서류) / ext(외관 대표컷) / etc."""
    d = desc or ""
    if any(w in d for w in _DOC_WORDS):
        return "doc"
    if any(w in d for w in _EXT_WORDS):
        return "ext"
    return "etc"


def _apply_video_grammar(lines: list, imgs: list, orig_imgs: list, gen_source: str,
                         log_tag: str = "") -> list:
    """영상 문법 가드(실측 결함 수정 — 서류 풀프레임 남발·자막 불일치·흐름 붕괴):
    ① 서류 사진은 '서류를 말하는 자막' 씬에만 + 영상 전체 1씬 상한(증거컷 역할만)
    ② 오프닝·클로징 씬은 외관 우선(비주얼 훅 — 단 그 자막이 서류 얘기면 유지)
    ③ 배정 없는·부적합 씬은 미사용 비서류 사진으로 대체(순차 끼워넣기 폐지)
    역할은 per-photo vision 묘사로 분류(데이터 유래). 분석 부재·실패 시 원본 유지(무해)."""
    try:
        import logging as _lg
        import re as _r
        descs = {}
        for m in _r.finditer(r"\[사진(\d+)\]\s*([^\n]+)", gen_source or ""):
            i = int(m.group(1)) - 1
            if 0 <= i < len(orig_imgs):
                descs[orig_imgs[i]] = m.group(2)
        if not descs or not lines:
            return imgs

        def role(p):
            return _photo_role(descs.get(p, ""))
        n = len(lines)
        out = list(imgs[:n]) + [None] * max(0, n - len(imgs))
        used = {x for x in out if x}
        pool = [p for p in orig_imgs if p not in used]

        def take(prefs):
            for want in prefs:
                for p in pool:
                    if role(p) == want:
                        pool.remove(p)
                        return p
            return pool.pop(0) if pool else None

        def is_doc_line(k):
            return any(w in (lines[k] or "") for w in ("서류", "등록증", "기록부", "점검", "증명", "계약"))
        doc_used = 0
        for k in range(n):
            p = out[k]
            if p is None:                               # 배정 없음 → 서류 자막이면 서류(상한 내), 아니면 외관
                if is_doc_line(k) and doc_used < 1:
                    c = take(("doc", "ext", "etc"))
                    if c and role(c) == "doc":
                        doc_used += 1
                    out[k] = c
                else:
                    out[k] = take(("ext", "etc"))
                continue
            if role(p) == "doc":
                if not is_doc_line(k) or doc_used >= 1:  # 서류 자막 아님 or 상한 초과 → 교체
                    repl = take(("ext", "etc"))
                    if repl:                             # 대체 불가면 유지(씬-사진 수 정합이 우선)
                        pool.append(p)
                        out[k] = repl
                else:
                    doc_used += 1
            elif is_doc_line(k) and doc_used < 1:        # 역방향 교정: 서류 자막인데 비서류 사진
                repl = take(("doc",))
                if repl:
                    pool.append(p)
                    out[k] = repl
                    doc_used += 1
        for k in ((0, n - 1) if n > 1 else (0,)):        # 오프닝·클로징 외관 우선(서류 자막 씬 제외)
            if out[k] is not None and role(out[k]) != "ext" and not is_doc_line(k):
                repl = next((p for p in pool if role(p) == "ext"), None)
                if repl:
                    pool.remove(repl)
                    pool.append(out[k])
                    out[k] = repl
        out = [x for x in out if x is not None]
        _lg.getLogger("shopcast.video").info("[grammar:%s] %d씬 roles=%s", log_tag, len(out),
                                             [role(p) for p in out])
        return out or imgs
    except Exception:
        return imgs


_re_sell = re.compile(r"^\s*(\d{1,2})\s*[.)]\s*(.+)$")


def _selling_lines(descs: list, drafts: list, facts: str, shop: str, kw: str,
                   gate=None, report: dict = None) -> list:
    """사진별 '파는 말' 한 줄씩(1콜, 2026-08-02 사장님 승인).

    왜: 지금까지 자막 재료가 vision 묘사였다 — '파노라마 선루프', '오렌지색 고전압'.
    정확하지만 손님을 사게 만드는 말이 아니다. 묘사를 아무리 다듬어도 묘사다.
    화자는 가게(파는 쪽)여야 한다는 원칙, '30초 보고 구매 의사가 생겨야 한다'는 기준을
    정제만으로는 만족할 수 없다.

    어떻게: 사진 순서를 먼저 고정하고(=화면-자막 일치는 구조로 보장), 사진마다 '이 사진에 대해
    할 말'을 쓰게 한다. 근거는 본문 사실과 그 사진 묘사뿐 — 없는 정보는 만들지 않는다.
    한 줄이라도 게이트에 걸리면 그 줄만 원래 묘사로 되돌린다(전체를 버리지 않는다).

    반환: drafts와 같은 길이의 리스트(실패 시 drafts 그대로).
    report를 주면 {swapped, kept, why:[...]}를 채운다 — 왜 그 줄이 묘사로 남았는지는
    로그에만 두면 화면에서 읽을 수 없다(조용한 실패 금지).
    """
    import logging as _lg
    log = _lg.getLogger("shopcast.video")
    if not drafts:
        return drafts
    n = len(drafts)
    from app import llm as _llm
    _scenes = "\n".join(
        f"{i + 1}. [사진] {(descs[i] if i < len(descs) else '')[:120]}"
        f"\n   (지금 자막: {drafts[i][:60]})" for i in range(n))
    prompt = (
        f"너는 '{shop}' 사장 본인이다. 아래 사진 {n}장 각각에 대해, 손님에게 직접 하는 말을 "
        "한 줄씩 써라. 영상 자막으로 쓴다.\n\n"
        "[반드시 지킬 것]\n"
        "1. 사진 번호 순서 그대로, 정확히 " + str(n) + "줄. 각 줄은 그 사진에 대한 말이어야 한다.\n"
        "2. 완결된 한 문장. 끊긴 조각·명사 나열 금지('블랙 그릴과 라디에이터 하이라이트, 스포크형' 같은 카탈로그 나열 금지).\n"
        "3. 한 줄 34자 이내(공백 포함). 넘으면 짧게 다시 써라.\n"
        "4. 파는 사람의 말로. 손님이 '이건 봐야겠다'고 느끼게. 촬영 각도·배경·조명 이야기 금지.\n"
        "5. 아래 [사실]과 사진 묘사에 있는 것만 써라. 없는 수치·가격·이력은 절대 지어내지 마라.\n"
        "6. 겁주기('호구', '사기당', '모르면 손해') 금지. 과장·보장('최고', '완벽', '무조건') 금지.\n"
        "7. 글을 가리키는 말('이 글', '본문에서') 금지 — 영상만 보는 사람에게는 앞뒤가 끊긴다.\n"
        "8. 어미를 씬마다 바꿔라('~입니다' 연속 금지).\n"
        f"9. 참고 키워드: {kw} (통째로 넣지 말 것)\n\n"
        f"[사실]\n{(facts or '')[:2500]}\n\n[사진]\n{_scenes}\n\n"
        f"출력: {n}줄, 각 줄 '번호. 문장' 형식. 설명·머리말 없이 줄만.")
    try:
        raw = _llm.call_task("spoken", prompt, max_tokens=700)
    except Exception as e:
        log.warning("[selling] 호출 실패 — 묘사 자막 유지: %r", repr(e)[:120])
        if report is not None:
            report.update({"swapped": 0, "kept": len(drafts),
                           "why": [f"호출 실패: {repr(e)[:60]}"]})
        return drafts
    got = {}
    for ln in (raw or "").splitlines():
        m = _re_sell.match(ln.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        txt = m.group(2).strip().strip('"“”')
        if 0 <= idx < n and txt:
            got[idx] = txt
    out, kept, swapped, why = [], 0, 0, []
    for i, d in enumerate(drafts):
        t = got.get(i, "")
        bad = ""
        if t:
            bad = (gate(t) if gate else "") or ""
            if len(t) > 40:
                bad = bad or "길이 초과"
        if t and not bad:
            out.append(t)
            swapped += 1
        else:
            out.append(d)                      # 그 줄만 되돌린다 — 전체를 버리지 않는다
            kept += 1
            if t and bad:
                log.warning("[selling] %d번 반려(%s): %s", i + 1, bad, t[:40])
                why.append(f"{i + 1}번 반려({bad}): {t[:34]}")
            elif not t:
                why.append(f"{i + 1}번 문장 없음 — 묘사 유지")
    log.warning("[selling] %d/%d줄 판매 문장으로 교체(묘사 유지 %d)", swapped, n, kept)
    if report is not None:
        report.update({"swapped": swapped, "kept": kept, "why": why[:6]})
    return out


def _lines_for_photos(imgs: list, gen_source: str, cand_lines: list, gate=None,
                      desc_map: dict = None) -> tuple:
    """📺 화면-자막 일치 보장(2026-08-01 사장님 불변 원칙 ①) — 방향을 뒤집는다.

    기존: 자막을 먼저 만들고 사진을 맞춘다 → 지시어 없는 자막은 '남은 사진 아무거나'가 배정돼
          차 후면 사진에 '고민 끝. 이 글이면 충분'이 붙었다(실측).
    변경: 사진을 먼저 놓고, 그 사진의 vision 묘사와 겹치는 본문 문장을 고른다. 겹치는 문장이
          없으면 묘사 자체를 짧게 다듬어 쓴다 — 어느 쪽이든 자막은 그 사진에 대한 말이 된다.

    gate(line) → 사유 문자열(통과 시 빈 값). 걸린 후보는 건너뛰고 다음 후보를 본다.
    desc_map을 주면 {사진경로: 원본 묘사}로 채워준다(자막을 '파는 말'로 다시 쓸 때 재료로 쓴다).
    반환 (사진들, 자막들) — 길이 동일, 순서 대응. 묘사가 없으면 ([], [])로 호출부가 기존 경로 유지.
    업종·지명 하드코딩 0(대조 재료는 그 세트의 사진 묘사와 본문뿐)."""
    import re as _r
    # ★ gen_source에는 같은 [사진N]이 여러 번 나온다(실측: 분석 배치가 이어붙어 번호가 겹침).
    #   나중 것으로 덮어쓰면 '한국 소상공인 마케팅 관점의 분석 결과입니다' 같은 안내문이 자막이 된다.
    #   → 번호당 '가장 묘사다운 줄'을 고른다: 안내·메타 문장 배제 후 가장 긴 것.
    _META = _r.compile(r"(분석 결과|분석입니다|관점의|다음과 같|촬영 팁|추천 활용|마케팅|사진 분석)")
    _cands: dict = {}
    for m in _r.finditer(r"\[사진(\d+)\]\s*([^\n]+)", gen_source or ""):
        i = int(m.group(1)) - 1
        if not (0 <= i < len(imgs)):
            continue
        _t = (m.group(2) or "").strip()
        if _META.search(_t):
            continue
        _cands.setdefault(i, []).append(_t)
    if not _cands:
        return [], []

    def _tok(t):
        return {w for w in _r.findall(r"[가-힣A-Za-z0-9]{2,}", t or "")}

    def _short(desc):
        """묘사 → 자막 한 줄. 실제 vision 출력 형식을 그대로 보고 만든다(2026-08-01 실측):
        '* 피사체/차종: …' 라벨, '[오버레이]' 단독 줄, 따옴표 인용, 쉼표 나열이 섞여 있다.
        규칙: ①내부 표기·라벨 제거 ②완결된 조각까지만(어절 경계 + 열린 인용 금지)
              ③조사·연결어미·관형형으로 끝나면 그 앞까지. 못 만들면 빈 문자열."""
        d = _r.sub(r"\[[^\]]{1,20}\]", " ", desc or "")            # [오버레이] 등 내부 표기
        # ★ 촬영 메타 제거(2026-08-02 실측 결함) — '45도 앵글, 스튜디오 배경', '클로즈업 샷'은
        #   사진을 설명하는 말이지 손님에게 파는 말이 아니다. 손님은 각도·배경을 사지 않는다.
        #   화자는 가게(파는 쪽)여야 한다는 원칙에도 어긋난다. 언어 규칙만 — 업종 무관.
        d = _r.sub(r"[^,，]*\b\d{1,3}\s?도\s?앵글[^,，]*", " ", d)
        d = _r.sub(r"[^,，]*(앵글|구도|배경|샷|프레이밍|클로즈업|촬영|조명|화각|정면 ?컷|측면 ?컷)[^,，]*",
                   " ", d)
        d = _r.sub(r"^\s*[,，]+|[,，]\s*(?=[,，])", " ", d)          # 지우고 남은 쉼표 정리
        d = _r.sub(r"^\s*[*\-•]+\s*", "", d)                       # 불릿
        d = _r.sub(r"^\s*[가-힣A-Za-z/·]{2,12}\s*[:：]\s*", "", d)   # '피사체/차종:' 류 라벨
        d = _r.sub(r"\s*\([^)]*\)", " ", d)                        # 괄호 주석
        d = _r.sub(r"\s+", " ", d).strip(" .,·—-")
        # ★ 쉼표 정리는 괄호·라벨을 다 걷어낸 '뒤'에 한다(2026-08-02 실측: 순서가 앞서 있어
        #   '다이얼(P/R/N/D 버튼), 듀얼' → '다이얼 , 듀얼'로 남았다).
        #   숫자 사이 쉼표는 천 단위 구분자라 건드리지 않는다('57,216km').
        d = _r.sub(r"(?<!\d)\s*[,，]\s*(?!\d)", ", ", d).strip(" ,")
        d = _r.sub(r"(입니다|이다|이에요|예요|모습입니다|모습으로)$", "", d).strip(" ,·—-")
        if not _r.search(r"[가-힣]{2,}", d):
            return ""
        # ★ 어절을 하나씩 붙이되, '그 자리에서 끝나도 말이 되는' 지점만 기억한다(불변 원칙 ②).
        #   26자에서 무조건 자르던 것이 '…흰색 SUV', '…회전' 같은 조각을 만든 근본 원인이다.
        #   끝맺을 수 있는 자리 = 조사·연결어미·관형형·수식어·열린 인용이 아닌 어절.
        _TAIL_BAD = _r.compile(
            r"("
            r"와|과|의|에|을|를|이|가|은|는|도|로|으로|랑|및|고|며|"          # 조사
            r"는데|은데|지만|면서|라서|어서|아서|으며|하며|거나|든지|려면|다면|으니|니까|"  # 연결어미
            r"들어간|적용된|열린|놓인|찍힌|각인된|전시된|보이는|켜진|달린|연|"            # 관형형
            r"흰색|검은색|은색|회색|남색|빨간색|파란색|회전|점등|상단|하단|우측|좌측|"     # 수식·위치어
            # ★ 뒤에 올 '이름'을 기다리는 말(2026-08-02 실측: '…오렌지색 고전압'에서 끊겨
            #   무엇이 고전압인지 없는 조각이 자막으로 구워졌다). 색·형·식·용·급으로 끝나는
            #   말은 그 자체로 사물이 아니다. 언어 규칙만 — 색깔·업종 목록을 늘리는 대신 어미로 잡는다.
            r"관련|관련된|포함|위주|중심|기반|전용|[가-힣]{2,}(?:색|형|식|용|급|압|형태|계열)"
            r")$")
        best, acc = "", ""
        for w in d.split(" "):
            nxt = (acc + " " + w).strip()
            # ★ 상한은 자막 규격에서 온다(_cap_lines: 3줄·가중치 30). 26자는 근거 없이 좁아
            #   '…스티어링', '…성능·상태'처럼 명사 뒤에서 끊겼다(실측).
            if len(nxt) > 38:
                break
            acc = nxt
            _t = acc.rstrip(" ,·—-")
            _tail = _t.rsplit(" ", 1)[-1] if " " in _t else _t
            _closed = (_t.count("'") % 2 == 0 and _t.count('"') % 2 == 0
                       and _t.count("(") == _t.count(")"))      # 인용·괄호가 열린 채 끝나지 않게
            _standalone = not (_TAIL_BAD.search(_tail)          # 조사·연결어미·관형형·수식어
                               or _r.fullmatch(r"[A-Z]{2,}", _tail)   # 영문 약어 단독
                               or len(_tail) <= 1)              # 한 글자 조각
            if _closed and _standalone:
                best = _t
        d = (best or "").rstrip(" ,·—-")
        for _ in range(6):                                       # 끝맺음 정리(불변 원칙 ②)
            _b = d
            if _r.search(r"(와|과|의|에|을|를|이|가|은|는|도|로|으로|랑|및|고|며)$", d) and " " in d:
                d = d[:d.rfind(" ")].rstrip(" ,·—-")
            if _r.search(r"(들어간|적용된|열린|놓인|찍힌|각인된|전시된|보이는|켜진|달린|점등된|"
                         r"부착된|장착된|표기된|기재된|촬영된|첨부된)$", d) and " " in d:
                d = d[:d.rfind(" ")].rstrip(" ,·—-")             # 관형형 = 꾸밈 대상이 뒤에 와야 함
            # ★ 마지막 어절이 '홀로 서지 못하는 조각'이면 잘라낸다(실측: '…흰색 SUV', '…회전').
            #   판정: 끝 어절이 수식어(색·형용)거나 영문 약어 단독이면 앞 어절까지만 남긴다.
            _last = d.rsplit(" ", 1)[-1] if " " in d else ""
            if _last and (_r.fullmatch(r"[A-Z]{2,}", _last)
                          or _r.search(r"(흰색|검은색|은색|회색|남색|빨간색|파란색|회전|점등)$", _last)):
                d = d[:d.rfind(" ")].rstrip(" ,·—-")
            if d.count("'") % 2 or d.count('"') % 2:
                _q = max(d.rfind("'"), d.rfind('"'))
                d = (d[:_q] if _q > 0 else d).rstrip(" ,·—-")
            if d == _b:
                break
        # 한글 내용어가 2어절 이상 남아야 자막이다(실측: 인용에서 끊겨 'Encar'만 남는 경우)
        _kor = _r.findall(r"[가-힣]{2,}", d)
        return d if (len(d) >= 8 and len(_kor) >= 2) else ""

    # 🛒 구매로 이어지는 순서(사장님 불변 원칙 ③) — 손님이 사기로 마음먹는 순서대로 보여준다.
    #   ①전체 모습(뭘 파는지) ②근거 서류·수치(믿을 만한가) ③속·부품 상태(꼼꼼한가) ④나머지.
    #   판정 근거는 그 세트의 vision 묘사뿐이다 — 업종·상품 하드코딩 0.
    #   '서류·수치'는 숫자나 문서형 낱말이 묘사에 있는가로, '전체 모습'은 부분 묘사가 아닌가로 본다.
    # ★ 같은 사진 번호에 묘사가 수십 줄 쌓인다(실측 21줄 — 분석 배치가 이어붙는다).
    #   가장 긴 줄은 요소를 잔뜩 나열해 26자에서 끊기기 쉽다 → _short가 실제로 만들어낸
    #   자막이 가장 긴(정보량 많은) 줄을 고른다. 자막을 못 만드는 줄은 후보에서 제외.
    raws = {}
    for _i, _vs in _cands.items():
        _best_raw, _best_len = "", 0
        for _v in _vs:
            _ln = _short(_v)
            if len(_ln) > _best_len:
                _best_raw, _best_len = _v, len(_ln)
        if _best_raw:
            raws[_i] = _best_raw
    if not raws:
        return [], []

    _WHOLE = ("외관", "전면", "후면", "측면", "전경", "전체", "정면", "외부")
    _DOCW = ("서류", "기록부", "증명", "등록증", "점검", "보증", "검인", "명세", "영수", "성적서")
    _PARTW = ("내부", "부품", "엔진", "실내", "시트", "타이어", "휠", "계기", "콘솔", "핸들",
              "스티어링", "도어", "트렁크", "배선", "하부", "패널", "필름", "시공", "마감", "표면")

    def _rank(desc: str) -> int:
        """실측 묘사 기준 3단계. 전체 모습이 먼저 나와야 '뭘 파는지'가 보인다(불변 원칙 ③).
        ★ 판정 순서가 중요하다 — 외관 묘사에도 수치가 섞여 있어(번호판 등) 수치를 먼저 보면
          외관이 '근거'로 분류돼 순서가 뭉개진다(실측)."""
        # ★ 앞부분만 본다(실측: 엔진룸 묘사 끝의 '전면 현대 로고 그릴' 때문에 전체 모습으로 잡혔다).
        #   vision 묘사는 '주 피사체'를 먼저 쓰고 뒤에 부속 요소를 나열한다.
        d = (desc or "").split(",")[0]
        if any(w in d for w in _WHOLE):
            return 0                                   # 전체 모습
        if any(w in d for w in _DOCW):
            return 1                                   # 근거(서류)
        if any(w in d for w in _PARTW) or _r.search(r"\d{2,}", d):
            return 2                                   # 상태(속·부품·수치)
        return 3                                       # 나머지

    _ordered = sorted(raws, key=lambda i: (_rank(raws[i]), i))
    out_imgs, out_lines, used = [], [], set()
    for i in _ordered:
        desc = raws[i]
        dt = _tok(desc)
        best, best_s = None, 0.0
        for li, ln in enumerate(cand_lines or []):
            if li in used:
                continue
            s = len(dt & _tok(ln)) / max(1, len(dt | _tok(ln)))
            if s > best_s:
                best, best_s = li, s
        line = ""
        if best is not None and best_s >= 0.12:
            _c = cand_lines[best]
            if not (gate and gate(_c)):
                line, _mark = _c, used.add(best)
        if not line:                                  # 겹치는 문장이 없거나 게이트 탈락 → 묘사에서 직접
            _d = _short(desc)
            if _d and len(_d) >= 6 and not (gate and gate(_d)):
                line = _d
        if line:
            out_imgs.append(imgs[i])
            out_lines.append(line)
            if desc_map is not None:               # 자막을 '파는 말'로 다시 쓸 때 쓰는 원본 묘사
                desc_map[imgs[i]] = desc
    return out_imgs, out_lines


def _match_photos(lines: list, imgs: list, gen_source: str, log_tag: str = "",
                  drops: "list | None" = None, axis_vocab: "set | None" = None,
                  subject_vocab: "set | None" = None) -> list:
    """대본 확정 후 씬 내용 ↔ 사진 매칭 (B: 지시어 강제 대조 추가).
    ① 자막의 '시각 대상 지시어'(vision 태그 변별어 유래·하드코딩 0)가 있으면, 배정 사진 vision 묘사에
       그 지시어가 실제 있어야 함 — 없으면 그 사진 배정 금지(등록증 자막→전면샷 차단).
    ② 지시어 일치 사진 없으면 order=None + drops에 인덱스 기록(호출부가 씬 삭제). 일치 사진 있으면 재배정.
    ③ 매칭 로그: [자막 / 지시어 / 배정 사진 / 판정]."""
    import re as _r
    import logging as _lg
    descs, raws = {}, {}
    for m in _r.finditer(r"\[사진(\d+)\]\s*([^\n]+)", gen_source or ""):
        i = int(m.group(1)) - 1
        if 0 <= i < len(imgs):
            descs[i] = _norm_line(m.group(2))
            raws[i] = m.group(2)
    if not descs:
        return imgs
    # 지시어 후보 = vision 묘사 변별어 ∪ 스키마 attribute_axes 토큰(둘 다 데이터 유래·하드코딩 0)
    obj_vocab = _distinctive_objects(raws) | {w for w in (axis_vocab or set()) if len(w) >= 2}
    # ★ 하드 지시어는 'vision 묘사에 실제 등장하는 것'만 — 부위(엔진룸·실내)는 확인 가능하지만
    #   모델명(그랜저 등 매물 정체성)은 vision이 안 적어 확인 불가 → 드롭 트리거에서 제외(과잉 삭제 방지).
    _desc_words = set()
    for _raw in raws.values():
        _desc_words |= set(_r.findall(r"[가-힣]{2,}", _raw or ""))
    # 영상 주제어(canonical subject, 예: 모델명 '그랜저')는 하드 지시어 제외 — 전 사진의 피사체이자
    # 주제 언급일 뿐 특정 사진을 지시하지 않음(vision이 일부 묘사에만 적어 오드롭 유발). 부위(엔진룸)는 유지.
    _subj = subject_vocab or set()
    used, order, _log = set(), [], []
    for li, ln in enumerate(lines):
        lt = _norm_line(ln)
        refs = [w for w in obj_vocab
                if w in _desc_words and w not in _subj
                and _r.search(r"(?<![가-힣])" + _r.escape(w), ln or "")]  # 자막의 지시어(vision 확인 가능·주제어 제외)
        best, best_s = None, 0.0
        for i, dt in descs.items():
            if i in used or not dt:
                continue
            # ★ 지시어 강제: 자막에 지시어가 있으면 그 사진 묘사에 지시어가 실제 있어야 후보
            if refs and not any(_r.search(r"(?<![가-힣])" + _r.escape(rf), raws.get(i, "") or "") for rf in refs):
                continue
            s = len(lt & dt) / max(1, len(lt | dt)) + _hint_bonus(ln, raws.get(i, ""))
            if s > best_s:
                best, best_s = i, s
        _jud = "매칭" if best is not None and best_s >= 0.08 else ("지시어불일치→삭제" if refs else "순차")
        if best is not None and best_s >= 0.08:
            order.append(best); used.add(best)
        elif refs and drops is not None:                      # 지시어 있는데 일치 사진 없음 → 씬 삭제(opt-in)
            order.append("DROP"); drops.append(li)
        else:
            order.append(None)                                # 지시어 없거나 삭제 미사용 → 순차 폴백(기존 동작)
        _log.append((ln[:14], "·".join(refs) or "-", (best + 1) if isinstance(best, int) else "-", _jud))
    if log_tag:
        _lg.getLogger("shopcast.video").warning("[%s] 씬-자막 매칭: %s", log_tag,
            " / ".join(f"'{t}'[지시:{r}]→#{p}({j})" for t, r, p, j in _log))
    remain = [i for i in range(len(imgs)) if i not in used]
    final = []
    for o in order:
        if o == "DROP":
            final.append(None)                                # 호출부가 씬 삭제(자막·사진 함께)
        elif isinstance(o, int):
            final.append(imgs[o])
        else:
            final.append(imgs[remain.pop(0)] if remain else (imgs[0] if imgs else None))
    final += [imgs[i] for i in remain if imgs[i] not in final]
    return final


# 화질 기준(R3) — 코드에 고정: 짧은 변 1080 이상 + 비트레이트 하한. 본체 블러·재스케일 금지.
MIN_SHORT_SIDE = 1080
MIN_BITRATE = 1_500_000     # 1.5Mbps — 실측 정상 산출물(쇼츠 2.5M·클립 3.3M) 대비 보수 하한


def _web_safe_encode(src: str, out: str) -> bool:
    """최종 정규화 재인코딩(재검 2차 — concat 조립 구조문제 원천 차단).
    ① yuvj420p→yuv420p(리미티드) ② CFR 30fps + 표준 타임스케일(15360→30000)
    ③ B프레임 제거(-bf 0)로 CTS 오프셋·비디오 edit list(elst media_time≠0) 소거 — 네이버 트랜스코더 비호환 주범
    ④ 오디오 aresample(async·first_pts=0)로 갭·프라이밍 정규화 ⑤ faststart·AAC 44100.
    실패 시 False(호출부가 원본 유지)."""
    cmd = ["ffmpeg", "-y", "-fflags", "+genpts", "-i", src,
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:in_range=full:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
           "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-preset", "veryfast", "-crf", "20",
           "-r", "30", "-fps_mode", "cfr", "-g", "60", "-keyint_min", "30", "-bf", "0",
           "-af", "aresample=async=1:first_pts=0", "-c:a", "aac", "-ar", "44100", "-b:a", "128k",
           "-video_track_timescale", "30000", "-movflags", "+faststart", out]
    return _run_ff(cmd, 300, "web-safe")


def _compat_check(path: str) -> tuple[bool, dict]:
    """웹 재생 호환 검사 — pix_fmt=yuv420p, 코덱 h264+aac, faststart, 짝수 해상도. (합격, 스펙)."""
    import json as _j
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,codec_name,pix_fmt,width,height", "-of", "json", path],
                           capture_output=True, timeout=30)
        d = _j.loads(r.stdout.decode("utf-8", "ignore") or "{}")
        vs = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        aus = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
        pix = vs.get("pix_fmt", ""); w = int(vs.get("width") or 0); h = int(vs.get("height") or 0)
        # faststart: moov가 파일 앞쪽(mdat 이전)
        head = open(path, "rb").read(1_000_000)
        _mv, _md = head.find(b"moov"), head.find(b"mdat")
        faststart = (0 <= _mv) and (_md < 0 or _mv < _md)
        spec = {"pix_fmt": pix, "vcodec": vs.get("codec_name"), "acodec": aus.get("codec_name"),
                "faststart": faststart, "even": (w % 2 == 0 and h % 2 == 0)}
        ok = (pix == "yuv420p" and vs.get("codec_name") == "h264"
              and (not aus or aus.get("codec_name") == "aac") and faststart and spec["even"])
        return ok, spec
    except Exception:
        return True, {}                                    # 검사 불가 시 통과(발행 흐름 유지)


def _probe_quality(path: str) -> tuple[bool, dict]:
    """렌더 산출물 화질 자동 검사 — (합격 여부, {width,height,bitrate}). 프로브 실패는 통과(발행 흐름 유지)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-show_entries", "format=bit_rate",
                            "-of", "json", path], capture_output=True, timeout=30)
        import json as _j
        d = _j.loads(r.stdout.decode("utf-8", "ignore") or "{}")
        st = (d.get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        br = int((d.get("format") or {}).get("bit_rate") or 0)
        spec = {"width": w, "height": h, "bitrate": br}
        if not (w and h):
            return True, spec
        return (min(w, h) >= MIN_SHORT_SIDE and (br == 0 or br >= MIN_BITRATE)), spec
    except Exception:
        return True, {}


def _run_ff(cmd: list, timeout: int, tag: str = "") -> bool:
    """ffmpeg 실행 + 실패 시 stderr 로깅(소실 방지, 영상강화 PHASE 6). 성공 True."""
    import logging
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.warning("[video] ffmpeg %s 타임아웃(%ds)", tag, timeout)
        return False
    except Exception as e:
        logging.warning("[video] ffmpeg %s 예외: %s", tag, e)
        return False
    if r.returncode != 0:
        logging.warning("[video] ffmpeg %s 실패 rc=%s: %s", tag, r.returncode,
                        r.stderr.decode("utf-8", "ignore")[-300:])
        return False
    return True


def _parse_dropped(note: str) -> int:
    """assemble note의 '씬탈락 N' → N (없으면 0)."""
    m = re.search(r"씬탈락 (\d+)", note or "")
    return int(m.group(1)) if m else 0


def _quality_gate(path: str, hook_first: bool, subs_burned: bool, dropped: int = 0,
                  subtitles: list | None = None) -> dict:
    """영상 품질 자동 점검(영상강화 PHASE 6) — 규격·길이·오디오·훅·자막·워터마크 부재.
    발행을 막지 않고 진단 결과를 payload에 남긴다(검수 화면·로그용)."""
    # (근본수정 4) 자막 텍스트 검사 — 오염 자막(내부 지시문·라벨)이 채점 입력에 없던 구멍 봉합
    if subtitles:
        _s = SceneScript(hook="", sentences=[t for t in subtitles if t], outro="", source="audit")
        _bad = _subtitle_gate(_s)
        if _bad:
            return {"pass": False, "score": 0, "error": f"자막 오염: {_bad}", "checks": {}}
    import json
    import logging
    gate = {"pass": False, "checks": {}, "dropped_scenes": dropped}
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_type,width,height:format=duration", "-of", "json", path],
                           capture_output=True, timeout=20)
        info = json.loads(r.stdout or b"{}")
        streams = info.get("streams", [])
        vs = next((s for s in streams if s.get("codec_type") == "video"), {})
        dur = float((info.get("format") or {}).get("duration") or 0)
        c = gate["checks"]
        c["spec_9x16"] = (vs.get("width") == W and vs.get("height") == H)      # 쇼츠/릴스 9:16 정규격
        c["duration_ok"] = 8 <= dur <= 62                                      # 쇼츠 30~45 목표, 허용 8~62
        c["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
        c["hook_first_frame"] = hook_first                                     # 첫 프레임 = 훅(인트로 없음)
        c["subtitles_burned"] = subs_burned                                    # 자막 합성 성공 여부
        c["no_watermark"] = True                                               # 로고 오버레이 제거됨(구조 보장)
        c["no_dropped_scenes"] = dropped == 0
        gate["duration"] = round(dur, 1)
        gate["pass"] = all(v for k, v in c.items() if k != "has_audio")        # 무음(TTS 무키)은 통과 허용
        if not gate["pass"]:
            logging.warning("[video] 품질 게이트 미통과: %s", {k: v for k, v in c.items() if not v})
    except Exception as e:
        gate["error"] = str(e)[:120]
    return gate


def _probe_dur(path: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path], capture_output=True, timeout=20)
        return float(r.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def _split_sentences(text: str) -> list[str]:
    """내레이션/본문을 문장 단위로 분할(씬 텍스트)."""
    text = re.sub(r"\[[^\]]*\]", " ", text or "")        # [사진N] 등 마커 제거
    text = re.sub(r"#\S+", " ", text)                    # 해시태그 제거
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    out = []
    for s in parts:
        s = s.strip(" -·•\t")
        if len(s) >= 4:
            out.append(s)
    return out


# ── 브랜드 테마(사업형태별) + ASS 카라오케 자막 + 로고 ──
_THEME = {"seller": (245, 179, 1), "local": (16, 185, 129), "hybrid": (99, 102, 241)}


def _theme_rgb(key: str):
    return _THEME.get(key or "local", _THEME["local"])


def _ass_color(rgb) -> str:
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600); sec -= h * 3600
    m = int(sec // 60); s = sec - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_ass(scenes, kws, theme_key, out, preset: dict | None = None) -> str:
    """본문 씬을 단어 단위 카라오케 자막(.ass)으로 — 말하는 단어가 차오르며 강조(프로 시그니처).
    영상강화 PHASE 2: ① 실측 타이밍(ElevenLabs with-timestamps) 있으면 글자수 근사 대신 사용
    ② 폰트 78 + 외곽선/그림자 강화(모바일 가독) ③ 하단 안전영역 밖(MarginV 380) ④ 키워드 색+굵기 강조
    ⑤ 조판 프리셋(industries.subtitle_preset — 업종별 색·강조·반투명 바) + 명시 강조({어절} → 1.3배)."""
    preset = preset or {}
    sung = _ass_color(preset.get("primary") or (255, 255, 255))
    unsung = "&H00B8B8B8"
    theme = _ass_color(preset.get("accent") or _theme_rgb(theme_key))
    _bold = "-1" if preset.get("bold", True) else "0"
    # 반투명 배경 바(밝은 사진 위 가독) — libass BorderStyle=4(줄 배경 박스)
    _bstyle, _outline = ("4", "10") if preset.get("bg_bar") else ("1", "7")
    kws_low = [k.lower() for k in (kws or []) if k and len(k) >= 2]
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # 폰트 78·외곽선 7·그림자 4 — 밝은 배경 사진 위에서도 대비 확보(작은 폰 화면 기준)
        f"Style: Cap,Pretendard,78,{sung},{unsung},&H00101014,&H96000000,{_bold},0,0,0,100,100,0,0,"
        f"{_bstyle},{_outline},4,2,80,80,380,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    def _cw(s):   # 글자 폭 가중치(한글/한자=1, 그 외=0.55) — 줄 길이 계산용
        return sum(1.0 if ("가" <= c <= "힣" or "一" <= c <= "鿿") else 0.55 for c in s)

    def _word_cs(words, dur, word_times):
        """단어별 강조 시간(센티초) — 실측 타이밍 우선, 없으면 글자수 근사."""
        if word_times and len(word_times) == len(words):
            cs_list = []
            for j, (_w, s, e) in enumerate(word_times):
                nxt = word_times[j + 1][1] if j + 1 < len(word_times) else dur   # 다음 단어 시작까지(간격 포함)
                try:
                    cs_list.append(max(8, int(round((float(nxt) - float(s)) * 100))))
                except Exception:
                    return None
            return cs_list
        return None

    LINE_BUDGET = 10.0     # 한 줄 최대(한글 10자 ≈ 폭 880px @ 폰트78) — 넘치면 다음 줄로
    lines = []
    for sc in scenes:
        start, dur, text = sc[0], sc[1], sc[2]
        word_times = sc[3] if len(sc) > 3 else []
        emph_words = [e for e in (sc[4] if len(sc) > 4 else []) if e]
        words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
        if not words:
            continue
        measured = _word_cs(words, dur, word_times)
        tot = sum(max(1, len(w)) for w in words)
        body = ""
        line_w = 0.0
        for wi, w in enumerate(words):
            ww = _cw(w)
            # 단음절 선행어('안 해요') 또는 다음이 의존명사('보는 건')면 다음 어절과 한 줄 보장
            _nxt_w = words[wi + 1] if wi + 1 < len(words) else ""
            _glue = (_cw(_nxt_w) + 0.55) if _nxt_w and (w in _WRAP_GLUE or _nxt_w in _TRAIL_GLUE) else 0.0
            if line_w > 0 and line_w + 0.55 + ww + _glue > LINE_BUDGET:   # 어절 단위 줄바꿈(띄어쓰기 보존)
                body = body.rstrip() + "\\N"
                line_w = 0.0
            cs = (measured[wi] if measured
                  else max(8, int(round(dur * 100 * len(w) / tot))))   # 실측 or 글자수 근사
            wl = w.lower()
            emph = any(e in w or w in e for e in emph_words)     # 명시 강조({어절}) — 씬당 1개
            hot = any((k in wl) or (wl in k) for k in kws_low) if kws_low else False
            if emph:  # 강조색 + 1.3배(카피 조판) — 사실 게이트 통과 텍스트 내 마킹만 가능
                body += ("{\\1c" + theme + "\\b1\\fscx130\\fscy130\\k" + str(cs) + "}" + w
                         + "{\\1c" + sung + "\\b0\\fscx100\\fscy100} ")
            elif hot:   # 키워드는 테마색 + 굵기·크기 강조
                body += ("{\\1c" + theme + "\\b1\\fscx106\\fscy106\\k" + str(cs) + "}" + w
                         + "{\\1c" + sung + "\\b0\\fscx100\\fscy100} ")
            else:
                body += "{\\k" + str(cs) + "}" + w + " "
            line_w += ww + 0.55
        lines.append("Dialogue: 0," + _ts(start) + "," + _ts(start + dur) + ",Cap,,0,0,0,," + body.strip())
    with open(out, "w") as f:
        f.write(head + "\n".join(lines) + "\n")
    return out


_LOSS_WORDS = ["손해", "모르면", "모르고", "놓치", "후회", "돈 버리", "낭비", "속지", "함정", "실수"]


def _pick_hook(cands: list[str], kws: list[str]) -> str:
    """훅 3~5안 → 최강 1개(영상강화 PHASE 1). 손실회피·숫자·키워드·적정길이 가점."""
    best, best_s = "", -1
    for c in cands:
        c = c.strip().strip('"').strip()
        if not (4 <= len(c) <= 26):
            continue
        s = 0
        if any(w in c for w in _LOSS_WORDS):
            s += 4                                     # 손실회피 = 검색 유입자 공감 최강
        if re.search(r"\d", c):
            s += 2
        if any(k and k[:4] in c for k in (kws or [])[:3]):
            s += 2                                     # 검색 키워드 포함(쇼츠 검색 노출)
        if c.endswith(("?", "요", "죠")):
            s += 1
        if 8 <= len(c) <= 16:
            s += 2                                     # 첫 프레임에서 한눈에 읽히는 길이
        if s > best_s:
            best, best_s = c, s
    return best or (cands[0].strip() if cands else "")


def _brand_logo_png(out, theme_key) -> str:
    """우상단 로고 워터마크(브랜드 일관성)."""
    from PIL import Image, ImageDraw
    rgb = _theme_rgb(theme_key)
    img = Image.new("RGBA", (340, 104), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 8, 340, 96], 44, fill=(10, 12, 20, 150))   # 어떤 배경에서도 보이게 다크 pill
    d.rounded_rectangle([18, 24, 82, 88], 16, fill=rgb + (255,))
    d.line([30, 72, 44, 52, 56, 62, 74, 38], fill="white", width=7, joint="curve")
    d.ellipse([68, 34, 80, 46], fill="white")
    f = _pil_font(46, "ExtraBold")
    d.text((100, 32), "올린다", font=f, fill=(255, 255, 255, 245))
    img.save(out)
    return out


class ShortVideoGenerator(Generator):
    kind = ContentKind.SHORT

    def __init__(self, model: str = MODEL):
        self.model = model

    def generate(self, tenant: Tenant, asset: Asset,
                 images: list[str] | None = None) -> ContentPiece:
        # 🎬 사용자가 고른 플랫폼만 만든다(2026-08-01 사장님 지적 — 네이버만 눌렀는데 쇼츠·릴스가
        #   같이 만들어졌다). want가 상태 이름표에만 쓰이고 렌더 단계엔 전달되지 않던 결함.
        _want = set(getattr(asset, "_want_platforms", None) or {"shorts", "reels", "naver"})

        def _stage(msg: str) -> None:      # 진행 단계 보고(없으면 조용히 무시 — 단건 경로 안전)
            try:
                cb = getattr(asset, "_stage_cb", None)
                if cb:
                    cb(msg)
            except Exception:
                pass
        _need_shorts = bool({"shorts", "reels"} & _want)
        imgs_all = [p for p in (images or [asset.path]) if p and os.path.exists(p)]
        imgs = imgs_all[:8]        # 씬 소스만 상한(씬 6개 + 여유) — payload에는 전체 기록(사진 제한 해제)
        vid_imgs = self._downscale_for_video(imgs)   # 대용량 원본(5712×4284) → zoompan 타임아웃 방지(백그라운드 스레드)
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        # ★ 키워드 = 공유 관문(전 생성기 공통) — SHORT가 자체 target_keywords로 정하다 캐스퍼에 납치된 계보 차단.
        _kw0s, kws = seo.resolve_target_keyword(
            industry=(getattr(tenant, "industry", "") or prof.name), region=tenant.region or "",
            note=asset.note or "", biz=(getattr(tenant, "biz_type", "local") or "local"),
            content_type=(getattr(asset, "content_type", "sell") or "sell"), brand=tenant.brand_name or "",
            keyword_axis=strat.keyword_axis, target_kw_override=(getattr(asset, "target_kw", "") or ""),
            tenant_id=tenant.id, prof_name=prof.name)
        buy = buy_block(tenant)
        cta_hint = (f"마지막 자막/내레이션은 구매 유도: {buy}" if strat.closing in ("buy", "both") and buy
                    else "마지막 자막/내레이션은 방문·예약 유도(지역/연락)")
        fmt = pick_format(strat.key, asset.note)   # 이미 터진 영상의 검증된 포맷 접목
        prompt = (
            f"[가게] {tenant.name} ({prof.name}, {tenant.region})\n"
            f"[사업형태] {strat.label} — {strat.goal}\n"
            f"[페르소나] {prof.persona}\n{industry_brief(prof)}[입력 정보] {asset.note}\n[사진 {len(imgs)}장]\n"
            f"[CTA] {strat.cta}\n{cta_hint}\n"
            f"{seo.speaker_frame(strat.key)}\n"
            f"{format_directive(fmt)}\n"
            f"{seo.keywords_line(kws)}\n\n"
            f"{seo.SHORT_DIRECTIVES_SELLER if strat.key == 'seller' else seo.SHORT_DIRECTIVES}\n"
            f"{seo.HOOK_RULE}\n{seo.VIDEO_SCRIPT_CRAFT}\n{seo.SUBTITLE_DENSITY}\n{seo.SAVE_SHARE_RULE}\n{seo.PLATFORM_YOUTUBE}\n{seo.PLATFORM_REEL}\n{seo.COPY_PSYCH}\n{seo.FACTS_RULE}\n"
            f"[검색 진입] 제목과 0~3초 첫 자막에 검색 키워드('{kws[0] if kws else prof.name}')를 자연스럽게 포함(쇼츠 검색 노출).\n"
            "[루프] 마지막 장면이 첫 장면과 자연스럽게 이어지게(끝→처음 루프 = 재생 반복 → 재노출). 길이 30~45초 목표.\n\n"
            "위 규칙으로 인스타 릴스/유튜브 쇼츠를 기획하라. 아래 형식 그대로(대괄호 머리표 유지):\n"
            "[제목]\n(후킹 제목)\n[길이]\n(예: 25초)\n[플랫폼]\n(인스타 릴스/유튜브 쇼츠)\n"
            "[훅 규칙 — 정직성] 따옴표 인용문은 위 입력(경험담·본문)에 원문이 있을 때만. 없는 발화를 "
            "지어내 인용하지 마라. 가격·견적 표현은 입력에 해당 서술이 있을 때만 — '비싸게만 받으셨나요' 류 "
            "경쟁·비교 저격 톤 금지(동네 동업자 저격은 훅으로도 부적격). 번호·라벨(①, 1., STEP)을 자막 문장에 "
            "넣지 마라. 훅은 평서·질문형 사실 기반(예: '신차 첫 썬팅, 뭘 봐야 할까요' / '모닝 신차패키지, 이렇게 마감했습니다').\n"
            "[훅후보]\n(첫 3초 훅 4안 — 한 줄씩. 검색 유입자가 공감할 문제제기·손실회피형 우선"
            "(예: '여름 앞유리 이거 모르면 손해'). 각 8~16자, 훅 공식(결과/손실회피/호기심갭/숫자) 서로 다르게)\n"
            "[내레이션]\n(한 문장씩 줄바꿈. 각 문장이 한 장면이 됨. 5~6문장, 구어체, 마지막은 CTA)\n"
            "[대본 규칙 — 한 편의 이야기] ① 첫 문장(훅)에 핵심 숫자·반전을 앞세워라. "
            "② 예고를 했으면('단점부터 볼게요' 등) 바로 다음 문장이 그 내용이어야 한다 — 예고만 하고 안 보여주기 금지. "
            "③ 같은·비슷한 문장 반복 금지(각 문장은 새 정보). ④ 전개는 입력 사실의 자연스러운 순서로.\n"
            "[장면]\n1) 0-3초 | 비주얼: .. | 자막: .. | 내레이션: ..\n2) .."
        )
        from app import llm as _llm
        raw = _llm.call_task("caption", prompt, 1500, default_model=self.model)   # 릴스 캡션·훅(이원화)
        _llm_route = dict(_llm.LAST_ROUTE.get("caption") or {})
        d = _parse_sections(raw, ["제목", "길이", "플랫폼", "훅후보", "훅", "내레이션", "장면"])
        scenes_meta = _parse_scenes(d.get("장면", ""))
        title = d.get("제목") or "shorts"          # (근본수정) note 폴백 제거
        # 첫 3초 훅(영상강화 PHASE 1) — 3~5안 중 손실회피·숫자·적정길이 점수로 최강 1개 선택
        hook_cands = [_strip_labels(h)
                      for h in (d.get("훅후보") or d.get("훅") or "").split("\n") if h.strip()]
        hook = (_pick_hook(hook_cands, kws)
                or (scenes_meta[0]["on_screen_text"] if scenes_meta else title[:18])).strip()
        narration = d.get("내레이션", "")

        # 씬 텍스트 = 캡션 생성기의 '시청자용 최종 출력'(내레이션→장면 자막)만.
        # (근본수정) asset.note 폴백 제거 — 내부 프롬프트·라벨이 자막에 노출되던 배선 차단.
        def _viewer_sentences(dd):
            s = _split_sentences(dd.get("내레이션", ""))
            if not s:
                s = [x["on_screen_text"] for x in _parse_scenes(dd.get("장면", "")) if x.get("on_screen_text")]
            return s
        sent = _viewer_sentences(d)
        if not sent:                                   # 스크립트 형식 미준수 → 캡션 1회 재생성
            raw = _llm.call_task("caption", prompt, 1500, default_model=self.model)
            d = _parse_sections(raw, ["제목", "길이", "플랫폼", "훅후보", "훅", "내레이션", "장면"])
            scenes_meta = _parse_scenes(d.get("장면", ""))
            sent = _viewer_sentences(d)
        sent = sent[:MAX_SCENES]

        if strat.closing in ("buy", "both") and buy:
            outro_cta = buy                                    # 구매 링크
        elif (getattr(tenant, "biz_type", "local") or "local") == "seller":
            outro_cta = "🔗 프로필 링크에서 구매하세요"
        else:
            outro_cta = (f"📍 네이버 '{tenant.name}' 검색\n방문·예약 환영" if tenant.name else "방문·예약 환영")
        outro_cta += "\n🔖 저장해두고 필요할 때 보세요"       # 저장 유도(정보성 포맷 = 저장 신호, PHASE 5)

        _evidence = (asset.note or "")
        _gen_src = ""                                   # 사진-자막 매칭 근거([사진N] vision 묘사)
        _blog_body = ""
        try:                                            # 본문(있으면)도 인용 근거에 포함
            from app import db as _dbe
            _bp = next((p for p in _dbe.get_set_pieces(asset.id) if p.kind.value == "blog"), None)
            if _bp:
                _blog_body = (_bp.payload or {}).get("body") or ""
                _evidence += "\n" + _blog_body
                _gen_src = (_bp.payload or {}).get("gen_source") or ""
        except Exception:
            pass
        # 씬 크기 대본(도달형) 우선 생성 — 본문 있으면 씬별 짧은 자막을 1콜로(캡션 산문 후분할 대신).
        # 실패 시 아래 캡션 내레이션(_viewer_sentences)로 폴백(사실 우선 — 영상은 나온다).
        if _blog_body:
            _kwn = seo._kw_shorten(kws[0]) if kws else prof.name
            _rsent = _script_from_body(_blog_body, min(8, max(4, len(imgs))), _kwn, _evidence, tone="reach",
                                       biz_type=(getattr(tenant, "biz_type", "local") or "local"),
                                       region=(getattr(tenant, "region", "") or ""))
            if _rsent and len(_rsent) >= 4:
                hook = _rsent[0]                      # reach 대본이 훅+씬 전부 소유(캡션 훅과 중복 방지)
                sent = _rsent[1:]
                _reach_hook = True
                __import__("logging").getLogger("shopcast.video").warning("[shorts] 씬 대본(reach) 훅+%d씬 채택", len(sent))
        hook = _strip_labels(hook)
        outro_cta = "\n".join(_strip_labels(l) or l for l in outro_cta.split("\n"))   # 아웃트로 불릿(▶) 세척
        sent = [_strip_labels(s) for s in sent if _strip_labels(s)]
        sent = _seam_dedup(hook, sent, outro_cta)      # 훅·아웃트로 이음매 중복 제거
        sent = _cap_lines(sent)                        # 씬당 3줄 초과 강제 분할(코드 강제)
        script = SceneScript(hook=hook, sentences=sent, outro=outro_cta, source="caption_llm", evidence=_evidence)
        _kw0 = (kws[0] if kws else "")
        _bizt = (getattr(tenant, "biz_type", "local") or "local")
        _regt = getattr(tenant, "region", "") or ""
        _gate_bad = (_subtitle_gate(script, _evidence, tenant.name, title=title)
                     or _script_gate([hook] + sent) or _hook_gate(hook, _kw0, _bizt, _regt)) if sent else "자막 소스 없음(스크립트 파싱 실패)"
        if _gate_bad:                                  # 자막+대본 게이트 — 오염/서사붕괴 시 1회 재생성 후 재검
            # 차단 사유를 프롬프트에 피드백 — 같은 위반(예: '830만원'→'800만원대' 반올림)이 재현되는 것 방지
            _retry_prompt = (prompt + f"\n\n[재작성 — 직전 출력이 검증에서 차단됨: {_gate_bad}] "
                             "위반을 고쳐 전체를 다시 써라. 숫자·금액은 입력에 있는 값 그대로만(반올림·'~대'·범위 금지), "
                             "예고한 내용은 다음 문장에서 반드시 보여주고, 문장 반복 없이.")
            raw = _llm.call_task("caption", _retry_prompt, 1500, default_model=self.model)
            d = _parse_sections(raw, ["제목", "길이", "플랫폼", "훅후보", "훅", "내레이션", "장면"])
            scenes_meta = _parse_scenes(d.get("장면", "")) or scenes_meta
            title = (d.get("제목") or title).strip() or title      # 제목이 차단 원인일 수도 — 함께 재선정
            _hc2 = [_strip_labels(h)
                    for h in (d.get("훅후보") or d.get("훅") or "").split("\n") if h.strip()]
            hook = _strip_labels(_pick_hook(_hc2, kws) or hook)
            sent = [_strip_labels(s) for s in _viewer_sentences(d)[:MAX_SCENES] if _strip_labels(s)]
            sent = _cap_lines(_seam_dedup(hook, sent, outro_cta))   # 재생성분도 이음매·3줄 캡 동일 적용
            narration = d.get("내레이션", narration)
            script = SceneScript(hook=hook, sentences=sent, outro=outro_cta, source="caption_llm", evidence=_evidence)
            _gate_bad = (_subtitle_gate(script, _evidence, tenant.name, title=title)
                         or _script_gate([hook] + sent) or _hook_gate(hook, _kw0, _bizt, _regt)) if sent else "자막 소스 없음(재생성 후에도)"
        # 강등 폴백(사실 우선 — 영상은 나온다): 소프트 위반(중복·미이행·과장·서식·인용)은 해당 씬만 제거해 재구성.
        # 하드 위반(수치 날조·업체명 불일치·내부 시그니처)이 남으면 강등 불가(오염 방치 금지) → 영상 생략.
        if _gate_bad and any(k in _gate_bad for k in ("중복", "미이행", "과장", "서식", "인용", "훅")):
            def _line_hard_bad(_ln):    # 개별 씬의 하드 위반만 True(수치·업체명·시그니처·명령형)
                _b = _subtitle_gate(SceneScript(hook="", sentences=[_ln], outro="",
                                                source="caption_llm", evidence=_evidence), _evidence, tenant.name)
                return bool(_b) and not any(k in _b for k in ("과장", "서식", "인용"))
            _keep = [s for s in _dedup_lines(sent) if _strip_labels(s) and not _line_hard_bad(s)
                     and not _subtitle_gate(SceneScript(hook="", sentences=[s], outro="", source="caption_llm", evidence=_evidence), _evidence, tenant.name)]
            _clean = _cap_lines(_keep)
            _hk = hook if not _line_hard_bad(hook) and "과장" not in (_subtitle_gate(
                SceneScript(hook=hook, sentences=["x"], outro="", source="caption_llm", evidence=_evidence), _evidence, tenant.name) or "") else (_clean[0] if _clean else hook)
            _sc2 = SceneScript(hook=_hk, sentences=_clean, outro=outro_cta, source="caption_llm", evidence=_evidence)
            _sub_bad = _subtitle_gate(_sc2, _evidence, tenant.name, title=title) if _clean else "정제 후 자막 없음"
            if not _sub_bad and len(_clean) >= 2:
                _nlogv = __import__("logging").getLogger("shopcast.video")
                _nlogv.warning("[shorts] 게이트(%s) → 위반 씬 제거 강등(%d→%d씬)", _gate_bad, len(sent), len(_clean))
                hook, sent, script, _gate_bad = _hk, _clean, _sc2, ""
        if _gate_bad:
            video_path, note, dur_sec, cover_path = None, f"자막 게이트 차단: {_gate_bad}", 0, None
            _scene_note, _scene_ok = note, False
        else:
            sent = _seam_dedup(hook, list(sent), outro_cta)   # 최종 이음매 중복 제거(강등·재생성 후 재보증)
            script = SceneScript(hook=hook, sentences=sent, outro=outro_cta, source="caption_llm", evidence=_evidence)
            if _gen_src and sent:                     # 씬 내용 ↔ 사진 vision 태그 매칭(서류 씬=서류 사진 등)
                _orig_v = list(vid_imgs)
                vid_imgs = _match_photos(list(sent), vid_imgs, _gen_src, "shorts")
                vid_imgs = _apply_video_grammar(list(sent), vid_imgs, _orig_v, _gen_src, "shorts")
            _stage("쇼츠 영상 만드는 중" if _need_shorts else "네이버 영상 준비 중")
            if not _need_shorts:                           # 네이버만 요청 → 쇼츠 렌더 생략(시간·중복 산출물 방지)
                video_path, note, dur_sec, cover_path = None, "쇼츠 미요청(건너뜀)", 0, None
            else:
                video_path, note, dur_sec, cover_path = self._build_scene_video(
                    vid_imgs, script, kws, tenant, strat, title)
            _scene_note = note                                # 씬 경로 결과/오류(진단용)
            _scene_ok = bool(video_path)
            # 폴백: 씬 파이프라인 실패 → 기존 슬라이드쇼 + 단일자막 + 오디오(게이트 통과 자막만 도달)
            if not video_path and _need_shorts:
                per = _per_image(len(vid_imgs))
                video_path, note = self._assemble_legacy(vid_imgs, hook, tenant.id, per)
                video_path, _t, _b, _ = self._add_audio(video_path, narration, tenant.id)
                dur_sec = round(max(len(imgs), 1) * per)
                cover_path = imgs[0] if imgs else asset.path
        # 다중 화면비(1:1·4:5) 변형 자동 생성 (#1)
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
        # video_path 확정: 중간파일(video.mp4)/작업폴더 경로면 out_dir로 복사(재생 404 원천차단 — 모든 경로 공통)
        if video_path and os.path.exists(video_path) and (
                "scenes_" in video_path or os.path.basename(video_path) in ("video.mp4", "video_fx.mp4")):
            _safe = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
            try:
                shutil.copy(video_path, _safe)
                video_path = _safe
            except Exception:
                pass
        variants = (self._aspect_variants(video_path, out_dir)
                    if (video_path and "reels" in _want) else {})
        # 네이버용 정보형 영상(추가 산출물) — 실패해도 릴스·글 흐름에 영향 없음(R1·R3)
        # 온디맨드: 사용자가 네이버를 선택 안 했으면 렌더 생략(_want_naver=False, ingest가 지정)
        naver_path, naver_meta = None, {}
        if getattr(asset, "_want_naver", True):
            _stage("네이버 영상 만드는 중 (대본→음성→사진)")
            try:
                naver_path, naver_meta = self._naver_video(tenant, asset, vid_imgs, kws, strat, out_dir)
            except Exception:
                import logging
                logging.getLogger("shopcast.video").exception("[naver-video] 생성 실패 t=%s", tenant.id)
        # 화질 자동 검사(R3) — 쇼츠도 동일 기준으로 계측(미달은 경고+기록, 발행 흐름은 유지)
        _vq_ok, _vq_spec = (True, {})
        if video_path and os.path.exists(video_path):
            _vq_ok, _vq_spec = _probe_quality(video_path)
            if not _vq_ok:
                import logging
                logging.getLogger("shopcast.video").warning(
                    "[quality] 쇼츠 화질 미달 %s t=%s", _vq_spec, tenant.id)
        for _vp in vid_imgs:                       # 영상용 다운스케일 임시파일 정리(디스크 누수 방지)
            if _vp not in imgs and _vp.endswith("_vid.jpg") and os.path.exists(_vp):
                try:
                    os.remove(_vp)
                except Exception:
                    pass

        return ContentPiece(
            id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
            channel=Channel.YOUTUBE, kind=self.kind,
            payload={
                "title": title, "video_title": title,
                "duration": d.get("길이", f"{dur_sec}초"),
                "target_platform": d.get("플랫폼", "인스타 릴스/유튜브 쇼츠"),
                "hook_strategy": hook, "subtitle": hook, "hook_candidates": hook_cands,
                "narration": narration, "scenes": scenes_meta, "script": raw,
                "scene_texts": sent, "outro_cta": outro_cta, "viral_format": fmt.name,
                "subtitles": [hook] + list(sent) + [outro_cta],   # 자막 전문 기록(사후 감사·채점 입력)
                "trending_sound_tip": "발행 시 인스타/유튜브 앱에서 '트렌딩 사운드'를 입히면 도달이 크게 늘어요(공식 API 미지원→앱에서 1탭).",
                "save_share_cta": {"youtube": seo.save_share_line("youtube"),
                                   "instagram": seo.save_share_line("instagram")},   # 설명란 삽입용(PHASE 5)
                "biz_type": strat.key, "target_keywords": kws,
                "video_path": video_path, "image_path": imgs[0] if imgs else asset.path,
                "image_paths": imgs_all, "duration_sec": dur_sec, "cover_path": cover_path,
                "video_variants": variants,    # {square, feed45} 다중 화면비
                "video_quality": {**_vq_spec, "pass": _vq_ok},   # 화질 게이트 계측(R3)
                "naver_video": naver_meta,     # 네이버용 정보형 영상(블로그 첨부·클립) — 없으면 {}
                "llm_route": _llm_route,       # 캡션·훅 라우팅(폴백 여부 — 원가 추적)
                "assemble_note": note, "_scene_note": _scene_note,
                # 품질 게이트(영상강화 PHASE 6) — 규격·길이·훅·자막·워터마크 부재 자동점검
                "quality_gate": (_quality_gate(video_path, hook_first=_scene_ok,
                                               subs_burned=_scene_ok, dropped=_parse_dropped(note),
                                               subtitles=[hook] + list(sent) + [outro_cta])
                                 if video_path and os.path.exists(video_path)
                                 else {"pass": False, "error": "no video"}),
            },
            status=ContentStatus.DRAFT)

    def _naver_video(self, tenant, asset, vid_imgs, kws, strat, out_dir):
        """네이버용 정보형 영상(블로그 첨부·클립 겸용) — 릴스와 별도 산출물.
        구성: [키워드 질문형 오프닝] → [핵심 답 3(글 소제목 축약)] → [사진 장면+본문 발췌 캡션]
              → [마무리: 가게명+지역+'자세한 내용은 본문에']. 감성 훅·밈 금지 — 검색어에 답하는 구조.
        정직성(R2): 자막은 게이트 통과한 글 본문·확정 사실에서 '그대로 발췌'만(LLM 재작성 없음 =
        날조 원천 차단). 실패 시 (None, {}) — 키트에서 블록만 생략, 글 발행 흐름 유지."""
        import re as _r
        import logging as _lg
        _nlog = _lg.getLogger("shopcast.video")
        _nlog.warning("[naver-video] 진입 asset=%s imgs=%d", getattr(asset, "id", "?"), len(vid_imgs or []))
        try:
            from app import db as _db
            blog = next((p for p in _db.get_set_pieces(asset.id) if p.kind.value == "blog"), None)
        except Exception:
            blog = None
        if not (blog and vid_imgs):
            _nlog.warning("[naver-video] 중단: blog=%s imgs=%d", bool(blog), len(vid_imgs or []))
            return None, {}
        pl = blog.payload or {}
        body = (pl.get("body") or "").strip()
        kw0 = ((pl.get("target_keywords") or [""])[0] or (kws[0] if kws else "")).strip()
        if not (body and kw0):
            _nlog.warning("[naver-video] 중단: body=%d kw0=%r", len(body), kw0)
            return None, {}
        kw_nat = seo._kw_shorten(kw0)
        # 핵심 답 3 = 글 소제목 축약(구조 섹션 제외 — 정보 소제목만)
        heads = [ln.lstrip("#").strip().strip('"“”') for ln in body.splitlines()
                 if ln.strip().startswith("##")]
        heads = [h for h in heads if not any(x in h for x in ("한눈 요약", "자주 묻", "가격", "영업 안내", "마무리"))][:3]
        if not heads:
            _nlog.warning("[naver-video] 중단: 소제목 0 (본문 구조 확인 필요)")
            return None, {}
        # 사진 캡션 = 본문 문단 첫 문장 발췌(사진 수만큼)
        paras = [p.strip() for p in body.split("\n") if len(p.strip()) >= 20
                 and not p.strip().startswith(("#", "|", "[", "!"))]
        region_short = seo._kw_shorten(getattr(tenant, "region", "") or "")
        _nm_flat = (tenant.name or "").replace(" ", "")
        caps = []
        for p in paras:
            s = _r.split(r"(?<=[.!?])\s", p)[0].strip()      # 문장부호 기준(중간 절단 방지)
            # 소개 문단(가게명+서술형) 제외 — 마무리 씬(가게명·지역)이 그 역할, 통째 자막화 금지
            if _nm_flat and _nm_flat in s.replace(" ", "") and s.endswith("입니다."):
                continue
            if 10 <= len(s) <= 60:
                caps.append(s)
            if len(caps) >= max(1, len(vid_imgs) - 1):
                break
        # 구조 라벨('핵심 N.') 없이 내용만 — 씬 순서가 목차 역할. 발췌 → 구어화 → 사실 보존 검사.
        _fact_src = "\n".join([body, tenant.name or "", region_short, kw_nat])   # 근거 = 본문+확정 프로필
        _biz = (getattr(tenant, "biz_type", "local") or "local")
        _reg = getattr(tenant, "region", "") or ""
        # 대본 단위 생성(구조 전환) — 대본 첫 줄이 훅(고정 템플릿 폐기), 훅 게이트(키워드 원형·지역) 경유.
        # 실패 시 기존 씬별 발췌+구어화 폴백(영상 흐름 불차단).
        # 30초+ 하한(상위노출 v2 1-4): 정보 씬 8~9개(씬당 ~4초 + 훅·아웃트로 ≈ 30~40초). 허사 아닌 본문 내용으로.
        _photo_locked = False                     # 사진↔자막 짝이 확정되면 뒤의 재매칭을 건너뛴다
        _sell_rep: dict = {}                      # 판매 문장 교체 결과(진단 — 조용한 실패 금지)
        _n_scenes = min(9, max(7, len(vid_imgs)))
        _rs = _script_from_body(body, _n_scenes, kw_nat, _fact_src, tone="info", biz_type=_biz,
                                region=_reg, title=(pl.get("title") or ""))
        _script_mode = bool(_rs and len(_rs) >= 4)
        if _script_mode:
            opening = _rs[0]                           # 대본이 쓴 훅(검색자 궁금증) — 고정 조립 폐기
            sent = _rs[1:]
        else:
            _nlog.warning("[naver-video] 대본 생성 실패 — 씬별 발췌 폴백")
            # 폴백 훅: 키워드 원형 조립 회피. 셀러·병행=지역 제외, 매장 전용=지역 유지하되 말미 업종어(업체·전문점) 제거(원형 회피).
            import re as _rh
            if _biz in ("seller", "hybrid"):
                _hk_kw = _kw_shorten_nolocal(kw_nat, _reg)
            else:
                _hk_kw = _rh.sub(r"\s*(업체|전문점|전문|추천|가격|후기)\s*$", "", kw_nat).strip()
            # ★ 폴백 훅도 '손님이 실제로 하는 질문'이어야 한다(2026-08-01 사장님 지적).
            #   실측 사고: '중고차판매, 궁금하셨죠?' — 사업자등록증 업종명을 그대로 박았고,
            #   손님은 파는 게 아니라 사는 쪽이라 주어가 뒤집혔다.
            #   업종명은 손님 검색어로 정규화(searcher_term)하고, 손님 행동어로 질문을 만든다.
            _ind_raw = ((getattr(tenant, "industry", "") or "").split(",")[0]).strip()
            try:
                _ind_kw = seo.searcher_term(_ind_raw) or _ind_raw
            except Exception:
                _ind_kw = _ind_raw
            _hk_kw = _hk_kw or _ind_kw
            # 업종 무관 질문 틀 — '어디서/어떻게' 고를지가 손님의 실제 고민이다.
            opening = (f"{_hk_kw}, 어디서 고르면 좋을까요?" if _hk_kw else "지금 확인해 보세요")
            if _hook_gate(opening, kw_nat, _biz, _reg):        # 게이트 위반이면 업종어만으로 재조립
                opening = (f"{_ind_kw}, 어디서 고르면 좋을까요?" if _ind_kw else "지금 확인해 보세요")
            if _hook_gate(opening, kw_nat, _biz, _reg):        # 그래도 걸리면 지역·업종 없는 일반형
                opening = "고르기 전에 이것부터 보세요"
            # 목차 낭독 방지(2026-07-28): 폴백도 소제목(제목형 문장)보다 본문 발췌(사람 말) 우선 —
            # 발췌가 3개 미만일 때만 소제목으로 보충
            if len(caps) >= 3:
                sent = (caps + [_cut_word(h, 30) for h in heads])[:6]
            else:
                sent = ([_cut_word(h, 30) for h in heads] + caps)[:6]
            sent = _to_spoken(sent, _fact_src)
            sent = _dedup_lines(sent)                 # 폴백도 서사 정제 — 내용없는 예고('단점부터 말씀드릴게요')·중복 제거
            # ★ 폴백 발췌도 자막 게이트를 통과해야 한다(2026-08-01 실사고).
            #   대본 경로에만 게이트가 걸려 있어, 본문에서 그대로 퍼온 "호구 될까 불안하다면"이
            #   검사 없이 영상에 구워졌다. 본문에는 문장이 많으니 걸린 줄은 버리고 다음 줄을 쓴다.
            #   전 업종 공통 — 걸러내는 기준은 업종 어휘가 아니라 화법(겁주기·저격)이다.
            _pool = list(dict.fromkeys(sent + [c for c in caps if c not in sent]))
            _clean_sent, _dropped_fb = [], []
            for _ln in _pool:
                if len(_clean_sent) >= max(4, len(sent)):
                    break
                _bad_ln = _subtitle_gate(SceneScript(hook="", sentences=[_ln], outro="",
                                                     source="body_excerpt", evidence=_fact_src),
                                         _fact_src, getattr(tenant, "name", "") or "")
                if not _bad_ln and _SELFREF.search(_ln):
                    _bad_ln = "영상에서 '글'을 가리킴"
                if _bad_ln:
                    _dropped_fb.append((_ln[:28], _bad_ln[:40]))
                else:
                    _clean_sent.append(_ln)
            if _dropped_fb:
                _nlog.warning("[naver-video] 폴백 자막 %d줄 게이트 탈락: %s", len(_dropped_fb), _dropped_fb[:3])
            # ★ 한 말은 끝까지 맺어야 한다(2026-08-01 사장님 불변 원칙).
            #   실측: '"중고차는 사진이랑 실물이 다르다"고들 하시는데' — 뒷말 없이 끊겼다.
            #   연결어미로 끝나는 줄은 다음 말이 있어야 하는 문장이므로 자막으로 쓰지 않는다.
            #   언어 규칙만 사용(업종 무관).
            _UNFIN = _re.compile(r"(는데|은데|지만|면서|라서|어서|아서|으며|하며|고들|거나|든지|"
                                 r"려면|다면|으니|니까|는지|은지|ㄹ지|고요|구요|고,|며,|"
                                 r"와|과|및|의|에|으로|로)$")
            _fin = [x for x in _clean_sent if not _UNFIN.search(x.rstrip(" .…"))]
            if len(_fin) >= 3:
                _drop_un = [x for x in _clean_sent if x not in _fin]
                if _drop_un:
                    _nlog.warning("[naver-video] 미완결 자막 %d줄 제외: %s", len(_drop_un), _drop_un[:2])
                _clean_sent = _fin
            if len(_clean_sent) >= 3:                 # 남은 줄이 너무 적으면 원본 유지(영상 불차단)
                sent = _clean_sent
            # ★ 화면-자막 일치(사장님 불변 원칙 ①) — 사진을 먼저 놓고 그 사진에 대한 말을 고른다.
            #   지시어 없는 자막에 '남은 사진 아무거나'가 배정되던 구조를 뒤집는다.
            def _fb_gate(_ln: str) -> str:
                if _SELFREF.search(_ln):
                    return "자기참조"
                if _UNFIN.search(_ln.rstrip(" .…")):
                    return "미완결"
                return _subtitle_gate(SceneScript(hook="", sentences=[_ln], outro="",
                                                  source="body_excerpt", evidence=_fact_src),
                                      _fact_src, getattr(tenant, "name", "") or "") or ""
            _desc_of: dict = {}
            _pairs_i, _pairs_l = _lines_for_photos(vid_imgs, pl.get("gen_source") or "",
                                                   list(sent) + list(caps), gate=_fb_gate,
                                                   desc_map=_desc_of)
            if len(_pairs_l) >= 3:
                _nlog.warning("[naver-video] 화면-자막 일치 재구성: %d씬(사진 기준)", len(_pairs_l))
                # 🗣 사진별 '파는 말'로 바꾼다(2026-08-02 사장님 승인). 사진 순서는 이미 고정됐으므로
                #   화면-자막 일치는 구조로 보장되고, 여기서는 '묘사 → 가게가 손님에게 하는 말'만 바꾼다.
                #   실패하거나 게이트에 걸린 줄은 원래 묘사로 남는다(전체를 버리지 않는다).
                _descs = [_desc_of.get(_ix, "") for _ix in _pairs_i[:9]]   # 사진별 원본 묘사
                _pairs_l = _selling_lines(_descs, _pairs_l[:9], _fact_src,
                                          getattr(tenant, "name", "") or "", kw_nat,
                                          gate=_fb_gate, report=_sell_rep)
                # ★ 분할이 일어나도 짝을 유지한다(2026-08-02 실측: 사진 9장인데 자막 12줄이 되어
                #   뒤 3씬이 사진 없이 남았다). imgs를 함께 넘겨 조각마다 원본 사진을 물린다.
                sent, vid_imgs = _cap_lines(_pairs_l[:9], imgs=_pairs_i[:9])
                _photo_locked = True
        # 클로징 다양화 — 고정 템플릿 대신 글 CTA '사실' 기반 선택(본문에 근거 있는 패턴만, 없으면 현행 유지)
        if any(k in body for k in ("성능점검", "서류", "점검기록부")):
            _cta_line = "서류까지 본문에서 확인하세요"          # 매물형 — 본문이 서류 확인을 다룰 때만
        elif any(k in body for k in ("예약", "방문", "오시면")):
            _cta_line = "실차 확인은 예약 한 번이면 됩니다" if "중고" in (tenant.industry or "") else "방문 예약은 본문에서"
        else:
            _cta_line = "자세한 내용은 본문에"                  # 공통형(현행)
        outro = f"{tenant.name} · {region_short}\n{_cta_line}"
        # 서식 세척 + 3줄 초과 강제 분할(캡 후 최종 sent로 1회만 매칭).
        #   ★ 사진이 잠긴 경로(_photo_locked)에서는 여기서도 짝을 유지해야 한다 — 안 그러면
        #     위에서 맞춰둔 1:1이 이 한 줄에서 다시 깨진다.
        if _photo_locked:
            sent, vid_imgs = _cap_lines([_strip_labels(s) for s in sent], imgs=vid_imgs)
        else:
            sent = _cap_lines([_strip_labels(s) for s in sent])
        _gen_src2 = pl.get("gen_source") or ""
        if _gen_src2 and sent and not _photo_locked:
            _drops = []
            _axv = set()
            try:                                              # 스키마 attribute_axes 토큰 → 지시어 소스(데이터 유래)
                from app.services import indschema as _isc2
                _sch2 = _isc2.get_schema(getattr(tenant, "industry", "") or "", _biz)
                for _a in (_sch2.get("attribute_axes") or []):
                    for _t in (_a.get("tokens") or []):
                        if isinstance(_t, str):
                            _axv.add(_t)
            except Exception:
                pass
            import re as _rsub                                 # 영상 주제어(모델명 등) → 하드 지시어 제외
            _subjv = {w for w in _rsub.findall(r"[가-힣]{2,}", (kw0 or "") + " " + (kw_nat or ""))
                      if w not in ("중고", "구매", "판매", "추천", "가격", "후기", "정보")}
            _orig_nv = list(vid_imgs)
            _matched = _match_photos(list(sent), vid_imgs, _gen_src2, "naver-video",
                                     drops=_drops, axis_vocab=_axv, subject_vocab=_subjv)
            _perline = _matched[:len(sent)]                   # 앞부분=자막별 배정(뒤는 미사용 잉여 사진)
            _dropset = set(_drops)
            # B: 지시어 불일치 씬 '삭제'(기본). 단 하한(3씬) 아래로 떨어지면 삭제 보류(그땐 순차 폴백 유지)
            if _drops and (len(sent) - len(_drops)) >= 3:
                _keep = [k for k in range(len(sent)) if k not in _dropset and _perline[k] is not None]
                sent = [sent[k] for k in _keep]
                vid_imgs = [_perline[k] for k in _keep]
                _nlog.warning("[naver-video] 지시어 불일치 %d씬 삭제 → %d씬 남김", len(_drops), len(sent))
            else:
                # 순차 끼워넣기 폐지(실측: 자막-사진 불일치 원인) — 정렬 유지, 빈 씬은 문법 가드가 안전 사진으로
                vid_imgs = list(_perline)
                if _drops:
                    _nlog.warning("[naver-video] 지시어 불일치 %d씬 — 문법 가드가 안전 사진으로 대체", len(_drops))
            vid_imgs = _apply_video_grammar(list(sent), vid_imgs, _orig_nv, _gen_src2, "naver-video")
            _nlog.warning("[naver-video] 사진 재배정 %d씬↔%d장", len(sent), len(vid_imgs))
        path, note, dur, _cover = self._build_scene_video(
            vid_imgs, SceneScript(hook=opening, sentences=sent, outro=outro, source="body_excerpt", evidence=body),
            kws, tenant, strat, f"{kw0} 정리")
        _nlog.warning("[naver-video] _build_scene_video 결과 path=%s dur=%s note=%r", bool(path), dur, (note or "")[:200])
        if not path:
            return None, {"_build_note": (note or "")[:300]}     # 실패 사유 표면화(진단)
        # 30초 하한 가드(v2 1-4): 정보형 영상은 30초+가 체류·D.I.A.+ 가점 유리. 대본이 짧으면 씬 확장 1회.
        if path and dur and dur < 30 and _script_mode and len(sent) < 9:
            _nlog.warning("[naver-video] %s초 < 30 — 대본 씬 확장 재생성", dur)
            _rs2 = _script_from_body(body, min(9, len(sent) + 2), kw_nat, _fact_src, tone="info",
                                     title=(pl.get("title") or ""),
                                     biz_type=_biz, region=_reg)
            if _rs2 and len(_rs2) > len(sent):
                opening2 = _rs2[0]; sent2 = _cap_lines([_strip_labels(x) for x in _rs2[1:]])
                _gs2 = pl.get("gen_source") or ""
                _vi2 = _match_photos(list(sent2), vid_imgs, _gs2, "naver-video") if _gs2 else vid_imgs
                path2b, note2b, dur2b, _c2b = self._build_scene_video(
                    _vi2, SceneScript(hook=opening2, sentences=sent2, outro=outro, source="body_excerpt", evidence=body),
                    kws, tenant, strat, f"{kw0} 정리")
                if path2b and os.path.exists(path2b) and (dur2b or 0) > dur:
                    path, note, dur, _cover, opening, sent = path2b, note2b, dur2b, _c2b, opening2, sent2
                    # ★ 사진 목록도 함께 채택한다(2026-08-02 실측 결함). 자막만 sent2로 바꾸고
                    #   vid_imgs를 옛 목록으로 두면, 뒤의 화질 재빌드가 '새 자막 + 옛 사진'으로
                    #   다시 굽는다 — 화면과 자막이 어긋난 채 발행된다(사장님 불변 원칙 위반).
                    vid_imgs = _vi2
        # 30초 하한 가드(폴백 발췌 경로) — ★ 기준을 대본 경로와 맞춘다(2026-08-01 실측 교정).
        #   기존 15초는 폴백만 낮게 잡혀 있어, 21초짜리가 어느 확장에도 안 걸렸다.
        #   '정보형은 30초+가 체류·D.I.A.+에 유리'라는 같은 근거를 두 경로에 동일 적용(전 업종 공통).
        if path and dur and dur < 30 and not _script_mode and len(caps) > len(sent) - len(heads):
            _nlog.warning("[naver-video] %s초 < 30 — 캡션 확장 재빌드(폴백)", dur)
            sent2 = _to_spoken(([_cut_word(h, 30) for h in heads] + caps)[:MAX_SCENES + 2], _fact_src)
            path2, note2, dur2, _cover2 = self._build_scene_video(
                vid_imgs, SceneScript(hook=opening, sentences=sent2, outro=outro, source="body_excerpt", evidence=body),
                kws, tenant, strat, f"{kw0} 정리")
            if path2 and os.path.exists(path2):
                path, note, dur, _cover = path2, note2, dur2, _cover2
            else:                                  # 재빌드 실패 → 1차 성공본 유지(15초 미만이라도 영상은 살린다)
                _nlog.warning("[naver-video] 확장 재빌드 실패(%s) — 1차 결과(%s초) 유지", note2, dur)
        if not (path and os.path.exists(path)):
            _nlog.warning("[naver-video] 중단: 씬 빌드 실패 — path=%r exists=%s dur=%r note=%s",
                          path, bool(path and os.path.exists(path)), dur, note)
            return None, {}
        # SEO 파일명으로 out_dir 확정 복사(이미지 SEO와 동일 규칙)
        ind0 = ((getattr(tenant, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
        core = " ".join(kw_nat.replace(region_short, "").split()) or ind0
        _toks = list(dict.fromkeys([x for p in (region_short, ind0, core) for x in p.split() if x]))
        # 부분 포함 dedupe(2-2): '썬팅'⊂'썬팅업체'처럼 앞 토큰이 다른 토큰에 포함되면 제거
        _parts = [t for t in _toks if not any(t != o and t in o for o in _toks)] + ["영상"]
        fname = _r.sub(r"[^가-힣A-Za-z0-9\-]", "", "-".join(_parts)) + ".mp4"
        final = os.path.join(out_dir, f"naver_{uuid.uuid4().hex}.mp4")
        try:
            shutil.copy(path, final)
        except Exception:
            _nlog.warning("[naver-video] 중단: 파일 복사 실패 %s", final)
            return None, {}
        # 화질 게이트(R3): 9:16 원본 그대로 제공 — 블러 패딩·리스케일 파일 생성 금지.
        # 기준 미달(1080 미만 또는 저비트레이트)이면 재빌드 1회 — 저품질이 조용히 발행되는 구조 금지.
        _q_ok, _spec = _probe_quality(final)
        _cp_ok, _cp_spec = _compat_check(final)        # 웹 재생 호환(pix_fmt·faststart·코덱·짝수)
        if not _cp_ok:                                 # 비호환 → 웹 안전 재인코딩 즉시 교정(재빌드 불필요)
            _fix = os.path.join(out_dir, f"naver_{uuid.uuid4().hex}.mp4")
            if _web_safe_encode(final, _fix) and os.path.exists(_fix):
                _nlog.warning("[naver-video] 비호환 %s → 웹 안전 재인코딩", _cp_spec)
                try:
                    os.remove(final)
                except Exception:
                    pass
                final = _fix
                _cp_ok, _cp_spec = _compat_check(final)
            if not _cp_ok:
                _nlog.warning("[naver-video] 재인코딩 후에도 비호환 %s(발행은 유지)", _cp_spec)
        if not _q_ok:
            _nlog.warning("[naver-video] 화질 미달 %s — 재빌드 1회", _spec)
            path2, note2, dur2, _c2 = self._build_scene_video(
                vid_imgs, SceneScript(hook=opening, sentences=sent, outro=outro, source="body_excerpt", evidence=body),
                kws, tenant, strat, f"{kw0} 정리")
            if path2 and os.path.exists(path2):
                _q2, _spec2 = _probe_quality(path2)
                if _q2:
                    try:
                        shutil.copy(path2, final)
                        dur = dur2 or dur
                        _spec = _spec2
                    except Exception:
                        pass
                else:
                    _nlog.warning("[naver-video] 재빌드도 미달 %s — 원본 유지(사유 기록)", _spec2)
        blog_title = (pl.get("title") or "").strip()
        vtitle = f"{kw0} 핵심만 정리했어요"                       # 글 제목과 중복되지 않는 변형
        if vtitle == blog_title:
            vtitle = f"{kw0} — 영상으로 보는 핵심"
        desc = (f"{kw_nat} 관련 내용을 영상으로 정리했어요.\n"
                f"{tenant.name} · {region_short}\n"
                "자세한 과정과 안내는 블로그 본문에 있어요.")
        try:
            from app import storage as _st
            _st.mirror_to_r2(final)                    # 로컬 정리 후에도 키트·다운로드 유지(R2 폴백)
        except Exception:
            pass
        _nlog.warning("[naver-video] 성공 path=%s dur=%s size=%s", final, dur,
                       os.path.getsize(final) if os.path.exists(final) else 0)
        # 클립용 해시태그(3-2): 키워드·지역·업종 기반 3~5개, 중복 제거·도배 금지
        _tag_seed = [kw_nat.replace(" ", ""), (region_short + " " + ind0).replace(" ", ""),
                     ind0, (region_short.split()[0] if region_short.split() else "") + ind0]
        hashtags = []
        for t_ in _tag_seed:
            t_ = _r.sub(r"[^가-힣A-Za-z0-9]", "", t_)
            if t_ and len(t_) >= 2 and f"#{t_}" not in hashtags:
                hashtags.append(f"#{t_}")
        hashtags = hashtags[:5]
        desc = desc + "\n" + " ".join(hashtags)       # 설명 복사에 포함(클립 업로드용)
        meta = {"path": final, "title": vtitle, "desc": desc, "filename": fname,
                "hashtags": hashtags, "quality": _spec,
                "duration_sec": dur, "opening": opening, "scene_texts": [opening] + sent + [outro],
                # 🎬 화면-자막 짝을 기록한다(2026-08-02). 자막만 남기면 '일치했는가'를 영상을 눈으로
                #   봐야만 확인할 수 있다 — 불변 원칙이라면 검증 가능해야 한다.
                "scene_pairs": [{"img": os.path.basename(vid_imgs[_i]) if _i < len(vid_imgs) else "",
                                 "line": _s} for _i, _s in enumerate(sent)],
                "photo_locked": bool(_photo_locked),
                "selling": _sell_rep}          # 몇 줄을 파는 말로 바꿨는지·왜 못 바꿨는지
        # 🎬 클립 전용 파생본(2026-08-01 사장님 승인) — 통합검색 '네이버 클립' 블록 진입용.
        #   실측 배경: 지역+업종 통합검색 첫 화면에 블로그 지면이 0인 판이 많고, 클립 블록은 열려 있다.
        #   블로그 첨부용(20~45초 정보형)과 클립용(15~25초 훅형)은 성격이 다르다 → 같은 소스에서
        #   앞부분만 잘라 파생(재생성·LLM·AI 0, ffmpeg 재인코딩만 = 원가 증가 0).
        try:
            if "clip" not in getattr(asset, "_want_platforms", {"clip"}):
                raise RuntimeError("clip_not_requested")  # ★ 사용자가 클립을 요청했을 때만 만든다
            if not self._clip_allowed(tenant):
                raise RuntimeError("plan_no_clip")       # 아래 except가 조용히 흡수(본편 유지)
            clip_path, clip_dur = self._clip_cut(final, out_dir, kw_nat)
            if clip_path:
                meta["clip"] = {"path": clip_path, "duration_sec": clip_dur,
                                "filename": (os.path.splitext(fname)[0] + "_클립.mp4"),
                                "title": vtitle, "desc": desc, "hashtags": hashtags}
        except Exception:
            _nlog.exception("[naver-video] 클립 파생 실패(본편은 유지)")
        return final, meta

    def _clip_allowed(self, tenant) -> bool:
        """클립 파생 제공 여부 — ①네이버 영상을 요청한 세트에서만 호출됨(온디맨드) ②플랜 허용 필요.
        운영자·대행 tenant(연결 사용자 없음)는 허용(내부 운영). 조회 실패 시 보수적으로 미제공."""
        try:
            from app import config as _cfg
            from app import db as _dbp
            u = _dbp.get_user_by_tenant(getattr(tenant, "id", "") or "")
            if not u:
                return True                              # 운영자·대행 tenant
            return _cfg.plan_limit(u.get("plan") or "free", "clip_video") != 0
        except Exception:
            return False

    def _clip_cut(self, src: str, out_dir: str, kw_nat: str = "") -> tuple:
        """완성 영상 → 클립용 15~22초 파생본. 소리 없이 보는 지면이라 첫 화면 훅 자막을 크게 얹는다.
        전 업종 공통(문구는 키워드에서 파생 — 하드코딩 0). 실패 시 (None, 0)."""
        if not (src and os.path.exists(src) and shutil.which("ffmpeg")):
            return None, 0
        _src_dur = _probe_dur(src)
        if _src_dur < 6:
            return None, 0
        target = min(22.0, max(15.0, _src_dur * 0.55))     # 15~22초(클립 알고리즘 안전대)
        out = os.path.join(out_dir, f"clip_{uuid.uuid4().hex}.mp4")
        # 첫 1.6초 훅 자막(큰 글씨) — 자막은 이미 본편에 구워져 있으므로 상단에만 얹는다
        hook = (kw_nat or "").strip()
        vf = f"scale={W}:{H},setsar=1,fps={FPS}"
        if hook:
            _fp = _font_path("Bold")
            if _fp:
                _txt = hook.replace("'", "").replace(":", "")[:14]
                vf += (f",drawtext=fontfile='{_fp}':text='{_txt}':fontcolor=white:fontsize=92:"
                       f"borderw=8:bordercolor=black@0.85:x=(w-text_w)/2:y=180:"
                       f"enable='between(t,0,1.6)'")
        cmd = ["ffmpeg", "-y", "-t", f"{target:.2f}", "-i", src, "-vf", vf,
               "-t", f"{target:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
               "-c:v", "libx264", "-preset", "medium", "-crf", "22",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]
        if not (_run_ff(cmd, 180, "clipcut") and os.path.exists(out)):
            return None, 0
        return out, round(_probe_dur(out))

    def _downscale_for_video(self, imgs):
        """영상용 사진 다운스케일 — 대용량 원본(예: 5712×4284)은 zoompan/scale이 느려
        백그라운드 스레드(CPU 적음)에서 ffmpeg 타임아웃 → 씬 실패 → 레거시(짧고 자막없음) 유발.
        긴 변 1600px로 줄여 처리 속도↑ (원본은 payload/블로그용으로 그대로 유지)."""
        from PIL import Image as _I, ImageOps as _IO
        out = []
        for p in imgs:
            try:
                im = _I.open(p)
                orient = (im.getexif() or {}).get(0x0112, 1)   # EXIF orientation 태그
                im = _IO.exif_transpose(im)                    # 세로 사진 눕는 문제 방지(V1)
                if max(im.size) <= 1600 and orient in (1, 0):  # 회전 불필요 + 소형 → 원본 유지
                    out.append(p)
                    continue
                im = im.convert("RGB")
                im.thumbnail((1600, 1600))
                dp = os.path.splitext(p)[0] + "_vid.jpg"
                im.save(dp, "JPEG", quality=88)
                out.append(dp if os.path.exists(dp) else p)
            except Exception:
                out.append(p)
        return out or imgs

    # ───────────────────── 씬 기반 빌드 (핵심) ─────────────────────
    def _build_scene_video(self, imgs, script, kws, tenant, strat, title):
        """글→씬 변환 영상 — 자막 소스는 SceneScript 계약 타입만 받는다(근본수정: 임의 문자열 차단).
        렌더 직전 자막 게이트를 한 번 더 강제. 성공 시 (path,note,dur,cover)."""
        if not isinstance(script, SceneScript):
            return None, "자막 소스 계약 위반(SceneScript 아님)", 0, None
        _bad = _subtitle_gate(script, script.evidence, getattr(tenant, "name", "") or "")
        if _bad:
            return None, f"자막 게이트 차단: {_bad}", 0, None
        hook, sentences, outro_cta = script.hook, list(script.sentences), script.outro
        if not shutil.which("ffmpeg"):
            return None, "ffmpeg 미설치", 0, None
        try:
            from PIL import Image  # noqa: F401
        except Exception:
            return None, "Pillow 미설치", 0, None
        # ★ PHASE 1: 디스크 하한 게이트 — 만차에서 렌더하면 무한 502·SQLite I/O. 여유 미달이면 렌더 보류(안내).
        _free = _disk_free_mb(os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage")))
        if _free is not None and _free < _RENDER_FLOOR_MB:
            logging.warning("[video] 디스크 여유 %dMB < 하한 %dMB — 렌더 보류", _free, _RENDER_FLOOR_MB)
            return (None, f"디스크 여유 부족({_free}MB) — 영상 렌더를 잠시 보류했어요. 공간 확보 후 자동 재시도됩니다.",
                    0, None)
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
        os.makedirs(out_dir, exist_ok=True)
        # 임시 작업은 /tmp(컨테이너 디스크)에서 — 작은 /data 볼륨(434MB) 디스크풀 방지(근본책)
        import tempfile
        work = os.path.join(tempfile.gettempdir(), f"omc_scenes_{uuid.uuid4().hex}")
        os.makedirs(work, exist_ok=True)
        try:
            visuals = self._visuals_for(imgs, sentences, kws, work, strat.key)
            if not visuals:
                return None, "사용 가능한 이미지 없음", 0, None
            vclips: list[str] = []     # 영상(무음) 클립
            awavs: list[str] = []      # 씬별 오디오(PCM, 정확히 dur초)
            ass_scenes = []            # (start, dur, text, word_times) — 본문 자막 타이밍
            t = 0.0
            dropped = 0                # 씬 탈락 카운트(영상강화 PHASE 6 — 품질 진단)
            # 0) 첫 3초 훅(영상강화 PHASE 1) — 실사진 배경 + 큰 문제제기 텍스트.
            #    그라데이션 카드 대신 실사진(오리지널 신호) + 첫 프레임부터 즉시 노출(페이드인 없음).
            hook_png = os.path.join(work, "hook.png")
            real_bg = next((p for p in visuals if not os.path.basename(p).startswith("cardbg")), None)
            if real_bg:
                ok_hook = self._hook_photo_png(hook_png, big=hook or title, small=tenant.name,
                                               img_path=real_bg, accent=strat.key)
                if not ok_hook:
                    self._card_png(hook_png, big=hook or title, small=tenant.name,
                                   accent=strat.key, kind="hook")
            else:
                self._card_png(hook_png, big=hook or title, small=tenant.name,
                               accent=strat.key, kind="hook")
            hook_tts = tts_lib.synthesize(hook, work) if hook else None
            ht = _probe_dur(hook_tts) if hook_tts else 0
            hdur = self._clamp((ht + 0.5) if ht > 0.3 else (len(hook or "") * 0.14 + 1.4))
            v = self._scene_card_video(hook_png, hdur, os.path.join(work, "v_hook.mp4"),
                                       punch=True, fade_in=False, tail=XFADE)
            aw = self._audio_segment(hook_tts, hdur, os.path.join(work, "a_hook.wav"))
            durs: list[float] = []                     # xfade 오프셋 계산용(체감 씬 길이)
            if v and aw:
                vclips.append(v); awavs.append(aw); durs.append(hdur); t += hdur
            # 문법 2: 데이터 카드 준비 — 세트 실값 수치(스키마 축 우선) + visual_preset 디자인 토큰
            try:
                from app.services import indschema as _isc
                _biz = getattr(tenant, "biz_type", "local") or "local"
                _sch = _isc.get_schema(getattr(tenant, "industry", "") or "", _biz)
                _vtok = self._visual_tokens(_sch.get("visual_preset") or "basic")
                _factsrc = " ".join(sentences) + "\n" + (getattr(script, "evidence", "") or "")
                _sp0 = _resolve_sale_price(getattr(script, "evidence", "") or "", _factsrc)
                _dcards = self._extract_data_points(_factsrc, _factsrc, _sch, _biz, sale_price=_sp0)
            except Exception:
                _vtok, _dcards = self._visual_tokens("basic"), []
            _dc_pos = {len(sentences) // 2} if (sentences and _dcards) else set()   # 중간 1장 삽입(색감 리셋)
            _dc_i = 0
            # 1) 본문 씬들 — 자막은 ASS 카라오케로 별도(여기선 영상+켄번스+색보정만)
            #    ElevenLabs with-timestamps 실측 단어 타이밍(있으면) → 카라오케 싱크 정확(영상강화 PHASE 2)
            from app.media import ai_clip as _aic
            _clip_budget = _aic.ClipBudget()             # 편당 신규 생성 상한(비용) — 캐시는 무제한
            for i, text in enumerate(sentences):
                img = visuals[i % len(visuals)]
                text, _emph = _parse_emphasis(text)          # TTS·카드엔 마킹 없는 원문(음성-화면 일치)
                seg_tts, word_times = tts_lib.synthesize_timed(text, work)
                td = _probe_dur(seg_tts) if seg_tts else 0
                # 음성이 있으면 씬 길이 = 음성 길이(+여유). 9초로 자르지 않음 → 긴 문장 나레이션 끊김·자막불일치 방지
                sdur = min(15.0, max(MIN_SCENE, td + 0.4)) if td > 0.3 else self._clamp(len(text) * 0.13 + 1.2)
                _ac = _clip_budget.get(img)                  # AI 카메라워크(QC 통과분만) — 없으면 켄번스
                v = self._scene_video(img, sdur, i, os.path.join(work, f"v{i}.mp4"), tail=XFADE,
                                      ai_clip=_ac)
                aw = self._audio_segment(seg_tts, sdur, os.path.join(work, f"a{i}.wav"))
                if v and aw:
                    ass_scenes.append((t, sdur, text, word_times, _emph))
                    vclips.append(v); awavs.append(aw); durs.append(sdur); t += sdur
                else:
                    dropped += 1
                # 문법 2: 중간 데이터 카드 삽입(무음, 색감 리셋 겸 수치 강조)
                if i in _dc_pos and _dc_i < len(_dcards):
                    _val, _lab = _dcards[_dc_i]; _dc_i += 1
                    _dc_png = os.path.join(work, f"dc{i}.png")
                    try:
                        self._data_card_png(_dc_png, _val, _lab, _vtok)
                        _ddur = 1.8
                        _dv = self._scene_card_video(_dc_png, _ddur, os.path.join(work, f"v_dc{i}.mp4"), tail=XFADE)
                        _da = self._audio_segment(None, _ddur, os.path.join(work, f"a_dc{i}.wav"))
                        if _dv and _da:
                            vclips.append(_dv); awavs.append(_da); durs.append(_ddur); t += _ddur
                            __import__("logging").getLogger("shopcast.video").info("[card] 데이터 카드 삽입: %s / %s", _lab, _val)
                    except Exception:
                        __import__("logging").getLogger("shopcast.video").exception("[card] 데이터 카드 실패")
            # 2) 아웃트로 CTA 카드(무음) — 셀러는 판매 QR(추적링크) 삽입 → 스캔 시 성과 집계
            qr_url = ""
            if strat.key == "seller":
                dest = getattr(tenant, "buy_url", "") or getattr(tenant, "map_url", "")
                if dest:
                    try:
                        from app import db as _db
                        _base = os.environ.get("SHOPCAST_BASE", "https://ollinda.kr").rstrip("/")
                        _tl = _db.ensure_track_link(tenant.id, dest, "스토어")
                        qr_url = (_base + "/r/" + _tl["code"]) if _tl else dest
                    except Exception:
                        qr_url = dest
            outro_png = os.path.join(work, "outro.png")
            # 루프 연결(영상강화 PHASE 4): 아웃트로도 훅과 같은 실사진 배경 → 끝→처음이 자연스럽게
            # 이어져 반복재생 유도. 셀러 QR은 가독성 위해 기존 카드 유지.
            if real_bg and not qr_url:
                ok_outro = self._hook_photo_png(outro_png, big=outro_cta, small=tenant.name,
                                                img_path=real_bg, accent=strat.key)
                if not ok_outro:
                    self._card_png(outro_png, big=outro_cta, small=tenant.name,
                                   accent=strat.key, kind="outro", qr_url=qr_url)
            else:
                self._card_png(outro_png, big=outro_cta, small=tenant.name,
                               accent=strat.key, kind="outro", qr_url=qr_url)
            odur = 2.8
            v = self._scene_card_video(outro_png, odur, os.path.join(work, "v_outro.mp4"),
                                       fade_in=False, fade_out=False)   # 끝 페이드 없음(루프 연결)
            aw = self._audio_segment(None, odur, os.path.join(work, "a_outro.wav"))
            if v and aw:
                vclips.append(v); awavs.append(aw); durs.append(odur); t += odur
            if not vclips:
                return None, "씬 클립 생성 실패", 0, None
            total = t
            # 3) 영상 xfade 크로스페이드 연결(검은 플래시 제거, PHASE 4) + 오디오 concat(PCM — 드리프트 없음)
            #    각 클립의 tail(XFADE초)이 전환에 소모돼 총 길이 = sum(durs) = 오디오 길이 → 싱크 유지
            video_only = self._concat_xfade(vclips, durs, os.path.join(work, "video.mp4"))
            full_wav = self._concat(awavs, os.path.join(work, "audio.wav"))
            if not (video_only and full_wav):
                return None, "concat 실패", 0, None
            # 4) ASS 단어자막 + 진행바 오버레이 — 로고 워터마크 제거(워터마크=노출 감소, PHASE 4)
            from app.industries import subtitle_preset as _sp
            ass = _build_ass(ass_scenes, kws, strat.key, os.path.join(work, "cap.ass"),
                             preset=_sp(getattr(tenant, "industry", "") or ""))
            fx = self._post_overlay(video_only, ass, total, strat.key)
            # 5) 영상+연속오디오 mux (+BGM: 업종 분위기 선택) — 길이 동일 → 정확히 싱크
            final = self._mux(fx, full_wav, out_dir, mood=bgm_lib.mood_for(tenant.industry))
            # 웹·네이버 호환 재인코딩(3채널 공통 단일 빌더) — yuvj420p→yuv420p 등 안전 프로파일 강제.
            # 실패해도 원본 유지(발행 흐름 불차단). 산출은 항상 out_dir(작업폴더 밖 — rmtree 404 방지).
            if final and os.path.exists(final):
                _ws = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
                if _web_safe_encode(final, _ws) and os.path.exists(_ws):
                    if out_dir in final:               # 정규화 전 조립본이 out_dir에 있으면 즉시 삭제(디스크 배증 방지)
                        try:
                            os.remove(final)
                        except Exception:
                            pass
                    final = _ws
                elif work in final:                    # 재인코딩 실패 → 최소한 작업폴더 밖으로 복사(소실 방지)
                    try:
                        shutil.copy(final, _ws); final = _ws
                    except Exception as ce:
                        import logging
                        logging.warning("[video] 안전복사 실패: %r", ce)
            # 6) 커버(썸네일) = 훅 카드
            cover = os.path.join(out_dir, f"cover_{uuid.uuid4().hex}.png")
            try:
                shutil.copy(hook_png, cover)
            except Exception:
                cover = None
            try:
                shutil.rmtree(work, ignore_errors=True)   # 씬 작업폴더(wav·중간mp4·ass) 정리 — 디스크 누수 차단
            except Exception:
                pass
            note = (f"씬 {len(sentences)}개 · 실사진 훅 · 단어자막(ASS{'·실측싱크' if any(len(s) > 3 and s[3] for s in ass_scenes) else ''}) · "
                    f"xfade 전환 · 켄번스+색보정 · 진행바(워터마크 없음) · "
                    f"{'TTS싱크' if tts_lib.configured() else '무음'}"
                    f"{' · AI이미지' if len(visuals) > len(imgs) else ''}"
                    f"{f' · 씬탈락 {dropped}' if dropped else ''}"
                    f"{(' · AI무빙 ' + str(_clip_budget.stats())) if _clip_budget.used or _clip_budget.generated else ''}")
            return final, note, round(total), cover
        except Exception as e:
            try:
                shutil.rmtree(work, ignore_errors=True)   # 실패해도 작업폴더 정리
            except Exception:
                pass
            return None, f"씬 빌드 오류: {str(e)[:120]}", 0, None

    # ───────────────── 콘티→렌더 어댑터 (2-C) — 결정권만 콘티로 ─────────────────
    def render_storyboard(self, sb: dict, img_by_id: dict, kws, tenant, strat, title="",
                          evidence="", sale_price="", mileage=""):
        """AI 디렉터 콘티(render_v1) → mp4. ★ 새 렌더 로직 발명 없음 — _build_scene_video 자산
        (_scene_video/_scene_card_video/_data_card_png/_audio_segment/_concat*/_post_overlay/_mux)
        을 그대로 호출하고, 씬 순서·사진·crop·길이·카드 '결정'만 콘티가 내린다.
        img_by_id = {photo_id: 실경로}(호출부가 _restore_media로 R2 포함 복원해 넘김).
        sale_price = 명시 판매가(VG3 검증 기준). 반환: (final, note, dur, cover, compare_log)."""
        if not (isinstance(sb, dict) and isinstance(sb.get("scenes"), list) and sb["scenes"]):
            return None, "콘티 없음 — 기존 경로", 0, None, []
        if not shutil.which("ffmpeg"):
            return None, "ffmpeg 미설치", 0, None, []
        # ★ 디스크 하한 게이트(_build_scene_video와 동일) — 만차 렌더 보류
        _free = _disk_free_mb(os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage")))
        if _free is not None and _free < _RENDER_FLOOR_MB:
            return (None, f"디스크 여유 부족({_free}MB) — 렌더 보류", 0, None, [])
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id)
        os.makedirs(out_dir, exist_ok=True)
        import tempfile
        work = os.path.join(tempfile.gettempdir(), f"omc_sb_{uuid.uuid4().hex}")
        os.makedirs(work, exist_ok=True)
        try:
            # 데이터 카드 디자인 토큰(업종 중립 — 스키마 visual_preset)
            try:
                from app.services import indschema as _isc
                _biz = getattr(tenant, "biz_type", "local") or "local"
                _sch = _isc.get_schema(getattr(tenant, "industry", "") or "", _biz)
                _vtok = self._visual_tokens(_sch.get("visual_preset") or "basic")
            except Exception:
                _vtok = self._visual_tokens("basic")
            scenes = sb["scenes"]
            vclips, awavs, durs, ass_scenes, compare = [], [], [], [], []
            t, dropped = 0.0, 0
            from app.media import ai_clip as _aic
            _clip_budget = _aic.ClipBudget()             # 편당 신규 생성 상한(비용) — 캐시는 무제한
            for i, s in enumerate(scenes):
                role = s.get("role", "")
                line = (s.get("line") or "").strip()
                # ★ 주행거리 단일화: 자막·발화 모두 canonical 하나로(오판독값 제거). 전 표면 동일 수치.
                if mileage:
                    line = _normalize_mileage(line, mileage)
                sh = s.get("shot") or {}
                spec_line = {"role": role, "line": line[:40],
                             "weight": round(float(s.get("duration_weight", 1) or 1), 2)}
                # ★ VG3(가격 날조): 자막에 판매가와 불일치하는 '라벨 없는 가격'이 실리면 씬 탈락
                _pv = _price_semantics_violation(line, sale_price)
                if _pv:
                    dropped += 1
                    compare.append({**spec_line, "shot": (sh.get("card") or sh.get("photo_id")),
                                    "실행": f"탈락(VG3 가격: {_pv})", "dur": 0})
                    continue
                # ★ TTS 발화 정규화: 자막(line) 원문 불변, 발화 텍스트만 숫자→한국어 수사. 게이트로 미변환 검출.
                _speak = _speechify(line)
                _left = _speech_number_left(_speak)
                if _left:
                    dropped += 1
                    compare.append({**spec_line, "실행": f"탈락(발화 미변환 숫자: {_left})", "dur": 0})
                    continue
                # line → TTS(발화 정규화본) + ASS 자막(원문 line). 발화≠자막이면 단어싱크 비활성(카라오케 드리프트 방지).
                seg_tts, word_times = tts_lib.synthesize_timed(_speak, work) if line else (None, [])
                if _speak != line:
                    word_times = []
                td = _probe_dur(seg_tts) if seg_tts else 0
                # ★ 예산 정합: 씬 길이 = TTS 길이(+여유)만. duration_weight로 부풀리지 않는다(예산 초과 방지).
                #   나레이션은 안 자름(끊김 방지) — 부풀림만 제거. 추정식(estimate_duration)과 일치.
                sdur = min(15.0, max(MIN_SCENE, td + 0.4)) if td > 0.3 else \
                    self._clamp(len(line) * 0.14 + 1.0)
                if "photo_id" in sh:                             # photo_id → 사진(_restore_media 경유 경로)
                    pid = sh.get("photo_id")
                    crop = sh.get("crop", "full")
                    # ★ VG4(크롭 후 증거 소실): 자막이 수치·기록·일치 등 '읽어야 할 증거'를 지시하면
                    #   과확대 클로즈업 금지 — 전체샷으로 폴백해 증거(계기판 숫자 등)가 프레임에 보이게.
                    if crop == "closeup" and _EVIDENCE_REF.search(line):
                        crop = "full"
                        spec_line["vg4"] = "closeup→full(증거 가시성)"
                    img = img_by_id.get(pid)
                    spec_line["shot"] = f"photo#{pid}/{crop}"
                    if not (img and os.path.exists(img)):        # 사진 소실 → 씬 탈락(픽셀 생성 금지)
                        dropped += 1
                        compare.append({**spec_line, "실행": "탈락(사진 없음)", "dur": 0})
                        continue
                    _ac = _clip_budget.get(img)          # AI 카메라워크(QC 통과분만) — 없으면 켄번스
                    v = self._scene_video(img, sdur, i, os.path.join(work, f"v{i}.mp4"),
                                          tail=XFADE, crop=crop, ai_clip=_ac)
                    exec_desc = f"사진 렌더 crop={crop}" + ("+AI무빙" if _ac else "")
                elif "card" in sh:                               # data_card → 기존 타이포 카드 렌더러
                    cv = str((sh["card"] or {}).get("value", "")).strip()
                    cl = str((sh["card"] or {}).get("label", "")).strip()
                    if mileage:                                  # 카드 수치도 주행거리 단일화
                        cv = _normalize_mileage(cv, mileage)
                    spec_line["shot"] = f"card:{cv}({cl})"
                    if not cv:
                        dropped += 1
                        compare.append({**spec_line, "실행": "탈락(카드값 없음)", "dur": 0})
                        continue
                    # ★ VG3: 가격류 카드는 명시 판매가와 일치하거나 항목 라벨이 있어야 — 아니면 탈락
                    if _PRICE_RE.search(cv):
                        _cpv = _price_semantics_violation(f"{cl} {cv}", sale_price)
                        if _cpv:
                            dropped += 1
                            compare.append({**spec_line, "실행": f"탈락(VG3 가격카드: {_cpv})", "dur": 0})
                            continue
                    _cp = os.path.join(work, f"c{i}.png")
                    self._data_card_png(_cp, cv, cl, _vtok)
                    v = self._scene_card_video(_cp, sdur, os.path.join(work, f"v{i}.mp4"),
                                               punch=True, fade_in=(i == 0), tail=XFADE)
                    exec_desc = "타이포 카드 렌더"
                else:
                    dropped += 1
                    compare.append({**spec_line, "shot": "?", "실행": "탈락(shot 없음)", "dur": 0})
                    continue
                aw = self._audio_segment(seg_tts, sdur, os.path.join(work, f"a{i}.wav"))
                if v and aw:
                    _text, _emph = _parse_emphasis(line)
                    ass_scenes.append((t, sdur, _text, word_times, _emph))
                    vclips.append(v); awavs.append(aw); durs.append(sdur); t += sdur
                    compare.append({**spec_line, "실행": exec_desc, "dur": round(sdur, 1),
                                    "tts": round(td, 1), "자막": line, "발화": _speak})
                else:
                    dropped += 1
                    compare.append({**spec_line, "실행": "탈락(클립 생성 실패)", "dur": 0})
            if not vclips:
                return None, "씬 클립 생성 실패", 0, None, compare
            total = t
            video_only = self._concat_xfade(vclips, durs, os.path.join(work, "video.mp4"))
            full_wav = self._concat(awavs, os.path.join(work, "audio.wav"))
            if not (video_only and full_wav):
                return None, "concat 실패", 0, None, compare
            from app.industries import subtitle_preset as _sp
            ass = _build_ass(ass_scenes, kws, strat.key, os.path.join(work, "cap.ass"),
                             preset=_sp(getattr(tenant, "industry", "") or ""))
            fx = self._post_overlay(video_only, ass, total, strat.key)
            final = self._mux(fx, full_wav, out_dir, mood=bgm_lib.mood_for(tenant.industry))
            if final and os.path.exists(final):
                _ws = os.path.join(out_dir, f"sbshort_{uuid.uuid4().hex}.mp4")
                if _web_safe_encode(final, _ws) and os.path.exists(_ws):
                    if out_dir in final:
                        try:
                            os.remove(final)
                        except Exception:
                            pass
                    final = _ws
                elif work in final:
                    try:
                        shutil.copy(final, _ws); final = _ws
                    except Exception:
                        pass
            cover = os.path.join(out_dir, f"sbcover_{uuid.uuid4().hex}.png")   # 첫 프레임 = 커버
            try:
                _run_ff(["ffmpeg", "-y", "-i", final or video_only, "-vframes", "1",
                         "-q:v", "3", cover], 40, "sbcover")
                cover = cover if os.path.exists(cover) else None
            except Exception:
                cover = None
            try:
                shutil.rmtree(work, ignore_errors=True)
            except Exception:
                pass
            note = (f"디렉터판(콘티 render_v1) · 씬 {len(vclips)}개 · xfade · 켄번스+색보정 · "
                    f"단어자막(ASS) · {'TTS싱크' if tts_lib.configured() else '무음'}"
                    f"{f' · 씬탈락 {dropped}' if dropped else ''}"
                    f"{(' · AI무빙 ' + str(_clip_budget.stats())) if _clip_budget.used or _clip_budget.generated else ''}")
            return final, note, round(total), cover, compare
        except Exception as e:
            try:
                shutil.rmtree(work, ignore_errors=True)
            except Exception:
                pass
            return None, f"콘티 렌더 오류: {str(e)[:120]}", 0, None, []

    def _clamp(self, v: float) -> float:
        return max(MIN_SCENE, min(MAX_SCENE, v or MIN_SCENE))

    def _visuals_for(self, imgs, sentences, kws, work, theme_key="local") -> list[str]:
        """씬 수에 맞춰 비주얼 확보. 사진 부족→AI 이미지(최대 MAX_AI_FILL),
        사진 0장→그라데이션 텍스트카드 배경(정보카드형 영상 #4)."""
        vis = list(imgs)
        need = min(len(sentences), MAX_SCENES)
        if len(vis) < need and len(vis) < 3:
            base_kw = ", ".join(kws[:3]) or "제품, 매장"
            for j in range(min(MAX_AI_FILL, need - len(vis))):
                prompt = (f"고품질 세로형 사진, {base_kw}, 한국 소상공인/제품 마케팅용, "
                          f"밝고 선명, 텍스트 없음, 광고 감성 #{j+1}")
                p = ai_image.generate(prompt, work)
                if p and os.path.exists(p):
                    vis.append(p)
        if not vis:   # 사진이 아예 없으면 → 텍스트카드 배경으로 영상 구성
            for j in range(max(1, need)):
                cp = os.path.join(work, f"cardbg{j}.png")
                self._gradient_bg(cp, j, theme_key)
                vis.append(cp)
        return vis

    def _gradient_bg(self, out, idx, theme_key="local") -> None:
        """텍스트카드형 배경(사진 없을 때) — 테마색 그라데이션."""
        from PIL import Image, ImageDraw
        rgb = _theme_rgb(theme_key)
        dark = (12, 14, 22)
        c2 = tuple(int(rgb[k] * 0.45 + dark[k] * 0.55) for k in range(3))
        top = ((28, 24, 46), c2) if idx % 2 == 0 else (c2, (16, 16, 26))
        img = Image.new("RGB", (W, H), top[0]); ov = Image.new("RGB", (W, H), top[1])
        m = Image.new("L", (W, H)); md = ImageDraw.Draw(m)
        for y in range(H):
            md.line([(0, y), (W, y)], fill=int(255 * y / H))
        img.paste(ov, (0, 0), m)
        img.save(out)

    def _aspect_variants(self, video, out_dir) -> dict:
        """9:16 최종본 → 1:1(피드)·4:5(피드) 자동 리사이즈(블러 배경). #1 다중 화면비."""
        out = {}
        if not (video and os.path.exists(video) and shutil.which("ffmpeg")):
            return out
        os.makedirs(out_dir, exist_ok=True)
        for key, (tw, th) in {"square": (1080, 1080), "feed45": (1080, 1350)}.items():
            dst = os.path.join(out_dir, f"{key}_{uuid.uuid4().hex}.mp4")
            # ★ 흐린 배경 대신 어두운 단색 여백(2026-08-01 사장님 지시 — 사진 씬·AI클립과 동일 원칙).
            fc = (f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                  f"color=c=0x0a0d14:s={tw}x{th}[bg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v]")
            cmd = ["ffmpeg", "-y", "-i", video, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "1", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-movflags", "+faststart", dst]   # 최종 규격 파생본은 화질↑(PHASE 12)
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode == 0 and os.path.exists(dst):
                out[key] = dst
        return out

    # ───────────────────── PIL 렌더 ─────────────────────
    def _caption_png(self, out: str, text: str, kws: list[str]) -> None:
        """하단 자막 PNG(투명 1080x1920). 키워드는 강조색. 둥근 반투명 박스."""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        font = _pil_font(62, "Bold")
        accent = (255, 224, 77)        # 키워드 강조(노랑)
        lines = self._wrap_lines(d, text, font, W - 150)[:2]   # 쇼츠 정석 1~2줄(4줄 자막 실사고 방지)
        lh = 84
        block_h = lh * len(lines)
        y0 = H - 470 - block_h
        # 반투명 박스
        pad = 34
        d.rounded_rectangle([60, y0 - pad, W - 60, y0 + block_h + pad - 10], 28,
                            fill=(10, 12, 20, 165))
        kw_low = [k.lower() for k in kws if k]
        for li, line in enumerate(lines):
            self._draw_highlighted(d, line, font, y0 + li * lh, kw_low, accent)
        img.save(out)

    def _draw_highlighted(self, d, line, font, y, kw_low, accent):
        """한 줄을 가운데 정렬해 그리되, 키워드 토큰만 강조색."""
        toks = self._tokenize(line, kw_low)
        total = sum(d.textlength(t[0], font=font) for t in toks)
        x = (W - total) / 2
        for txt, hot in toks:
            col = accent if hot else (255, 255, 255)
            # 외곽선(가독성)
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                d.text((x + dx, y + dy), txt, font=font, fill=(0, 0, 0, 220))
            d.text((x, y), txt, font=font, fill=col)
            x += d.textlength(txt, font=font)

    def _tokenize(self, line: str, kw_low: list[str]):
        """라인을 (텍스트, 강조여부) 런으로 분할 — 키워드 부분만 True."""
        if not kw_low:
            return [(line, False)]
        low = line.lower()
        marks = [False] * len(line)
        for kw in kw_low:
            start = 0
            while kw and (idx := low.find(kw, start)) != -1:
                for i in range(idx, idx + len(kw)):
                    marks[i] = True
                start = idx + len(kw)
        runs, cur, curm = [], "", None
        for ch, m in zip(line, marks):
            if curm is None or m == curm:
                cur += ch; curm = m
            else:
                runs.append((cur, curm)); cur, curm = ch, m
        if cur:
            runs.append((cur, curm))
        return runs

    # ───────────── 문법 2: 타이포 카드 시스템(디자인 토큰 = visual_preset 유래) ─────────────
    @staticmethod
    def _visual_tokens(vpreset: str) -> dict:
        """visual_preset(basic|soft|fresh|auto) → 카드·자막 공용 디자인 토큰(배경 그라데·포인트색·태그).
        전 카드·전 자막이 같은 토큰 → 영상 전체가 한 브랜드로 보이게. 업종 중립(스키마 유래)."""
        T = {
            "auto":  {"bg": ((16, 18, 30), (40, 48, 92)), "accent": (90, 170, 255), "tag": "체크"},
            "soft":  {"bg": ((28, 20, 34), (96, 60, 96)),  "accent": (255, 170, 190), "tag": "오늘"},
            "fresh": {"bg": ((14, 26, 22), (30, 92, 70)),  "accent": (120, 230, 170), "tag": "신선"},
            "basic": {"bg": ((16, 18, 26), (54, 40, 96)),  "accent": (255, 224, 77),  "tag": "확인"},
        }
        return T.get(vpreset or "basic", T["basic"])

    @staticmethod
    def _extract_data_points(body: str, gen_source: str, schema: dict, biz: str = "local",
                             sale_price: str = "") -> list:
        """데이터 카드 수치 추출 — 세트 실값(본문·사진분석)만, 스키마 attribute_axes 축 우선순위로.
        업종 하드코딩 0: 코드는 '수치+단위' 패턴만 인식하고, 어떤 축을 앞세울지는 스키마가 정한다.
        ★ 가격(VG3): 본문에서 임의로 긁지 않는다 — 명시 판매가(sale_price)만 '판매가' 라벨로 올린다.
        판매가 미명시면 가격 카드 없음(서류 출고가·취득가가 판매가로 승격되는 날조 차단). 반환 [(value,label)] 최대 3."""
        import re as _r
        src = (gen_source or "") + "\n" + (body or "")
        axes = [a.get("axis", "") for a in (schema.get("attribute_axes") or [])]
        # 범용 수치 유형(단위 기반) — 가격은 여기서 제외(명시 판매가만 별도 처리)
        UNIT = [("주행거리", r"([\d,]{2,})\s*(?:km|㎞|키로|만km)"),
                ("연식", r"((?:19|20)\d{2})\s*년?식"),
                ("용량", r"([\d,]{2,})\s*(?:ml|mL|㎖|g|kg|리터|L)"),
                ("소요시간", r"([\d,]{1,})\s*(?:분|시간)"),
                ("횟수", r"([\d,]{1,})\s*회")]
        found, seen = [], set()

        def _axis_rank(label):
            for i, ax in enumerate(axes):
                if label[:2] in ax or ax[:2] in label:
                    return i
            return 99
        for label, pat in sorted(UNIT, key=lambda u: _axis_rank(u[0])):
            m = _r.search(pat, src)
            if m and m.group(0) not in seen:
                seen.add(m.group(0))
                found.append((m.group(0).strip(), label))
            if len(found) >= 3:
                break
        # 가격: 명시 판매가만(맨 앞). 없으면 가격 카드 자체를 만들지 않음.
        if sale_price:
            found = [(sale_price, "판매가")] + found
        return found[:3]

    def _data_card_png(self, out: str, value: str, label: str, tokens: dict) -> None:
        """데이터 카드(풀스크린) — 라벨 + 큰 수치. 사진 없음·그라데 배경(디자인 토큰). 수치가 주인공."""
        from PIL import Image, ImageDraw
        c1, c2 = tokens["bg"]; accent = tokens["accent"]
        img = Image.new("RGB", (W, H), c1); top = Image.new("RGB", (W, H), c2)
        m = Image.new("L", (W, H)); md = ImageDraw.Draw(m)
        for y in range(H):
            md.line([(0, y), (W, y)], fill=int(255 * y / H))
        img.paste(top, (0, 0), m)
        d = ImageDraw.Draw(img)
        fl = _pil_font(52, "Bold")
        d.text(((W - d.textlength(label, font=fl)) / 2, H // 2 - 300), label, font=fl, fill=(210, 214, 235))
        # 큰 수치 — 2줄 넘지 않게 폰트 자동
        fv = _pil_font(150, "ExtraBold")
        for fs in (170, 150, 130, 110, 92):
            fv = _pil_font(fs, "ExtraBold")
            if d.textlength(value, font=fv) <= W - 120:
                break
        d.text(((W - d.textlength(value, font=fv)) / 2, H // 2 - 130), value, font=fv, fill=accent)
        # 하단 라인 장식
        d.rectangle([W // 2 - 120, H // 2 + 130, W // 2 + 120, H // 2 + 138], fill=accent)
        img.save(out)

    def _hook_photo_png(self, out: str, big: str, small: str, img_path: str, accent: str) -> bool:
        """첫 3초 훅 — 실사진 배경(cover crop) + 어둡게 + 큰 문제제기 텍스트(영상강화 PHASE 1).
        그라데이션 카드보다 '진짜 현장' 느낌 = 오리지널·주제 일관성 신호. 성공 True."""
        try:
            from PIL import Image, ImageDraw, ImageOps, ImageFilter
            im = Image.open(img_path)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, (W, H), method=Image.LANCZOS, centering=(0.5, 0.42))
            # ★ 사진은 선명하게 둔다(2026-08-01 사장님 지시) — 손님이 보고 싶은 건 실물이다.
            #   기존: 사진 전체 블러 + 위 67%·아래 47% 농도의 검은 그라데이션 → 차가 뿌옇게 보였다.
            #   변경: 블러 제거, 어둡게 하는 것은 '글자가 놓이는 띠'로만 한정(전 업종 공통).
            _band_top, _band_bot = int(H * 0.10), int(H * 0.40)   # 훅 텍스트가 놓이는 구간만
            ov = Image.new("L", (W, H), 0)
            od = ImageDraw.Draw(ov)
            for y in range(_band_top, _band_bot):
                _e = min((y - _band_top) / 60.0, 1.0, (_band_bot - y) / 60.0)   # 위아래 60px 페이드
                od.line([(0, y), (W, y)], fill=int(150 * max(0.0, _e)))
            im.paste(Image.new("RGB", (W, H), (8, 10, 16)), (0, 0), ov)
            d = ImageDraw.Draw(im)
            # 훅 텍스트 — 크게, 화면 상단 1/3(첫 프레임부터 한눈에)
            big_lines, fb = None, None
            for fs in (120, 108, 98, 88, 76):   # 오프닝 씬 중앙 큰 타이포(조판 리디자인 — 크기 상향)
                fb = _pil_font(fs, "ExtraBold")
                ls = self._wrap_lines(d, big, fb, W - 140)
                if len(ls) <= 2:
                    big_lines = ls
                    break
            if big_lines is None:
                fb = _pil_font(62, "ExtraBold")
                big_lines = self._wrap_lines(d, big, fb, W - 140)[:3]
            lh = int(getattr(fb, "size", 96) * 1.24)
            y = int(H * 0.16)
            for ln in big_lines:
                x = (W - d.textlength(ln, font=fb)) / 2
                for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (2, 2), (-2, 2)):  # 외곽선(가독)
                    d.text((x + dx, y + dy), ln, font=fb, fill=(0, 0, 0, 255))
                d.text((x, y), ln, font=fb, fill=(255, 255, 255))
                y += lh
            if small:
                fs2 = _pil_font(44, "SemiBold")
                x2 = (W - d.textlength(small, font=fs2)) / 2
                d.text((x2, y + 26), small, font=fs2, fill=(225, 228, 238))
            im.save(out)
            return os.path.exists(out)
        except Exception:
            return False

    def _card_png(self, out: str, big: str, small: str, accent: str, kind: str, qr_url: str = "") -> None:
        """훅/아웃트로 풀스크린 카드(그라데이션 + 큰 문구 + 셀러 판매 QR)."""
        from PIL import Image, ImageDraw
        c1, c2 = ((18, 18, 30), (60, 30, 110)) if kind == "hook" else ((60, 30, 110), (12, 14, 22))
        if accent == "seller":
            c2 = (140, 90, 10) if kind == "hook" else (12, 14, 22)
        img = Image.new("RGB", (W, H), c1)
        top = Image.new("RGB", (W, H), c2)
        mask = Image.new("L", (W, H))
        md = ImageDraw.Draw(mask)
        for y in range(H):
            md.line([(0, y), (W, y)], fill=int(255 * y / H))
        img.paste(top, (0, 0), mask)
        d = ImageDraw.Draw(img)
        tag = "잠깐!" if kind == "hook" else "지금"
        ft = _pil_font(48, "ExtraBold")
        d.text(((W - d.textlength(tag, font=ft)) / 2, H // 2 - 360), tag,
               font=ft, fill=(255, 224, 77))
        # 훅 문구 — 2줄 안에 들어가는 최대 폰트 자동 선택(단어 하나 고아로 떨어지는 것 방지)
        big_lines, fb = None, _pil_font(92, "ExtraBold")
        for fs in (92, 84, 76, 68, 60):
            fb = _pil_font(fs, "ExtraBold")
            ls = self._wrap_lines(d, big, fb, W - 160)
            if len(ls) <= 2:
                big_lines = ls
                break
        if big_lines is None:
            fb = _pil_font(58, "ExtraBold")
            big_lines = self._wrap_lines(d, big, fb, W - 160)[:3]
        lh = int(getattr(fb, "size", 92) * 1.28)
        y = H // 2 - 180
        for ln in big_lines:
            d.text(((W - d.textlength(ln, font=fb)) / 2, y), ln, font=fb, fill="white")
            y += lh
        if small:
            fs = _pil_font(50, "SemiBold")
            d.text(((W - d.textlength(small, font=fs)) / 2, y + 40), small,
                   font=fs, fill=(200, 205, 230))
            y += 100
        # 셀러 판매 QR — 영상 끝에서 손님이 폰으로 스캔 → 바로 스토어
        if qr_url and kind == "outro":
            try:
                import qrcode
                qsz = 340
                qr = qrcode.make(qr_url).convert("RGB").resize((qsz, qsz))
                pad = Image.new("RGB", (qsz + 44, qsz + 44), "white")
                pad.paste(qr, (22, 22))
                qx, qy = (W - qsz - 44) // 2, y + 120
                img.paste(pad, (qx, qy))
                fq = _pil_font(46, "ExtraBold")
                cap = "스캔하면 바로 구매 →"
                d.text(((W - d.textlength(cap, font=fq)) / 2, qy + qsz + 70), cap,
                       font=fq, fill=(255, 224, 77))
            except Exception:
                pass
        img.save(out)

    def _wrap_lines(self, d, text, font, maxw):
        """단어(띄어쓰기) 단위 줄바꿈 — 한글이 단어 중간에서 안 잘리게. 긴 단어만 예외적으로 글자 분할."""
        out = []
        for para in (text or "").split("\n"):
            cur = ""
            _ws = [x for x in para.split(" ") if x]
            for _i, w in enumerate(_ws):
                cand = (cur + " " + w) if cur else w
                _nw = _ws[_i + 1] if _i + 1 < len(_ws) else ""
                _nxt = (" " + _nw) if _nw and (w in _WRAP_GLUE or _nw in _TRAIL_GLUE) else ""
                if d.textlength(cand + _nxt, font=font) <= maxw or (not cur and not _nxt):
                    cur = cand
                    continue
                if cur:
                    out.append(cur)
                if d.textlength(w, font=font) > maxw:      # 단어 하나가 폭 초과 → 글자 단위(예외)
                    piece = ""
                    for ch in w:
                        if d.textlength(piece + ch, font=font) <= maxw:
                            piece += ch
                        else:
                            if piece:
                                out.append(piece)
                            piece = ch
                    cur = piece
                else:
                    cur = w
            if cur:
                out.append(cur)
        return out

    # ───────────────────── ffmpeg: 영상(무음) + 오디오(연속) 분리 ─────────────────────
    def _fade(self, dur: float) -> str:
        """씬 전환용 페이드 인/아웃(딥) — 클립 길이 불변이라 오디오 싱크 영향 없음."""
        if dur < 0.9:
            return ""
        return f",fade=t=in:st=0:d=0.22,fade=t=out:st={max(0.0, dur - 0.25):.2f}:d=0.22"

    def _scene_video(self, img, dur, idx, out, tail: float = 0.0, crop: str = "full",
                     ai_clip: str | None = None) -> str | None:
        """이미지 → 켄번스 + 색보정(통일감), 정확히 dur(+tail)초 무음 영상. 자막은 ASS로 별도.
        tail>0 = xfade 전환용 여유 꼬리(전환에 소모돼 체감 길이는 dur) — 페이드 없음(검은 플래시 제거).
        crop='closeup' = 콘티 지정 클로즈업 — 기존 zoompan을 더 타이트하게 파라미터화(새 렌더 로직 아님).
        ai_clip = QC 통과한 AI 카메라워크 클립(mp4) — 켄번스 대신 실사 무빙, 실패 시 켄번스 폴백."""
        total_t = dur + max(0.0, tail)
        if ai_clip and os.path.exists(ai_clip):
            r = self._scene_video_from_clip(ai_clip, total_t, dur, tail, out, idx)
            if r:
                return r                              # AI 클립 렌더 실패 시 아래 켄번스로 폴백
        frames = max(1, int(total_t * FPS))
        # ★ 가로 사진 판별(2026-07-27 실사고): 가로 차 사진을 9:16 커버 크롭하면 가운데 세로 띠만 남아
        #   차가 잘려나감(트렁크만 보이는 정체불명 화면). 가로는 '통째로 + 블러 패드'로 렌더.
        _iw = _ih = 0
        try:
            from PIL import Image as _Im
            with _Im.open(img) as _im:
                _iw, _ih = _im.size
        except Exception:
            pass
        _landscape = bool(_iw and _ih and _iw > _ih * 1.05)
        if _landscape and crop == "closeup":
            crop = "full"                              # 가로 사진 매크로 크롭 금지(정체불명 화면 방지)
        if crop == "closeup":                              # 콘티 crop 힌트 → zoompan 시작 배율 상향(부위 강조)
            zdir = ("if(eq(on,1),1.22,min(zoom+0.0012,1.38))" if idx % 2 == 0
                    else "if(eq(on,1),1.38,max(zoom-0.0012,1.22))")
        else:
            zdir = "min(zoom+0.0012,1.12)" if idx % 2 == 0 else "if(eq(on,1),1.12,max(zoom-0.0012,1.0))"
        _zp = (f"zoompan=z='{zdir}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
               f"d={frames}:s={W}x{H}:fps={FPS}")
        _eq = "eq=contrast=1.06:saturation=1.12:brightness=0.02"
        if _landscape:
            # ★ 여백 없이 꽉 채운다(2026-08-01 사장님 지적 — 검은 여백이 화면 절반을 먹었다).
            #   흐린 배경(기존)도, 검은 여백(직전 시도)도 답이 아니었다. 사진을 세로 화면에 채우되
            #   위아래 여백이 과하지 않도록 '높이 기준 커버 + 중앙 크롭'으로 간다.
            #   가로 사진의 피사체(차량·시공물·제품)는 중앙에 오는 것이 일반적이라 좌우 크롭이 안전하고,
            #   흐릿함도 여백도 없다. 전 업종 공통(사진 비율은 업종과 무관하다).
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                  f"{_eq},{_zp}" + ("" if tail > 0 else self._fade(dur)))
            cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{total_t:.2f}", "-i", img, "-vf", vf,
                   "-map", "0:v", "-t", f"{total_t:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
                   "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
        else:                                  # 세로·정사각 사진도 같은 방식(커버 크롭)
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                  f"{_eq},{_zp}" + ("" if tail > 0 else self._fade(dur)))
            cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{total_t:.2f}", "-i", img, "-vf", vf,
                   "-map", "0:v", "-t", f"{total_t:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
                   "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
        r = _run_ff(cmd, 120, f"scene{idx}")
        return out if (r and os.path.exists(out)) else None

    def _scene_video_from_clip(self, clip, total_t, dur, tail, out, idx) -> str | None:
        """AI 카메라워크 클립(가로 720p 등) → 세로 캔버스 무음 씬. 켄번스 문법과 동일한
        색보정·블러패드·페이드를 적용해 다른 씬과 톤이 이어지게 한다.
        씬이 클립보다 길면 슬로모(setpts)로 늘림 — 프레임 정지보다 자연스러움(실사 무빙 유지)."""
        try:
            src_dur = _probe_dur(clip)
            if src_dur < 1.0:
                return None
            ratio = max(1.0, total_t / src_dur)        # 빠르게 줄이진 않음(움직임 과속 방지)
            cw = ch = 0
            pr = subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                                 "-show_entries", "stream=width,height", "-of", "csv=p=0", clip],
                                capture_output=True, text=True, timeout=20)
            try:
                cw, ch = (int(x) for x in (pr.stdout or "").strip().split(",")[:2])
            except ValueError:
                pass
            _eq = "eq=contrast=1.06:saturation=1.12:brightness=0.02"
            _fade = "" if tail > 0 else self._fade(dur)
            if cw and ch and cw > ch:                  # 가로 클립 → 선명한 원본 + 어두운 여백
                # ★ 사진 씬과 같은 원칙(2026-08-01 사장님 지시): 흐린 배경 대신 단색 여백.
                vf = (f"[0:v]setpts={ratio:.4f}*PTS,scale={W}:-2[fg];"
                      f"color=c=0x0a0d14:s={W}x{H}:r={FPS}[bg];"
                      f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,"
                      f"{_eq},fps={FPS}{_fade}[v]")
            else:                                      # 세로 클립 → 커버 스케일
                vf = (f"[0:v]setpts={ratio:.4f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H},setsar=1,{_eq},fps={FPS}{_fade}[v]")
            cmd = ["ffmpeg", "-y", "-i", clip, "-filter_complex", vf, "-map", "[v]",
                   "-t", f"{total_t:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
                   "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
            r = _run_ff(cmd, 120, f"aiclip{idx}")
            return out if (r and os.path.exists(out)) else None
        except Exception:
            __import__("logging").getLogger("shopcast.video").exception("[aiclip] 씬 렌더 실패 — 켄번스 폴백")
            return None

    def _scene_card_video(self, png, dur, out, punch=False, fade_in=True, tail: float = 0.0,
                          fade_out=True) -> str | None:
        """카드(훅/아웃트로) → 정확히 dur(+tail)초 무음 영상. punch=True면 천천히 줌인.
        fade_in=False = 첫 프레임부터 즉시 노출(첫 3초 훅). tail>0 = xfade 여유 꼬리(페이드 없음).
        fade_out=False = 끝 페이드 없음(마지막 씬 루프 연결)."""
        total_t = dur + max(0.0, tail)
        frames = max(1, int(total_t * FPS))
        if punch:
            vf = (f"scale={W}:{H},setsar=1,zoompan=z='min(zoom+0.0018,1.10)':"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}")
        else:
            vf = f"scale={W}:{H},setsar=1,fps={FPS}"
        if tail > 0 or not (fade_in or fade_out):
            pass                                       # xfade/루프 모드: 페이드 없음
        elif fade_in and fade_out:
            vf += self._fade(dur)
        elif fade_out and dur >= 0.9:                 # 훅: 페이드아웃만(다음 씬 전환용), 인은 즉시
            vf += f",fade=t=out:st={max(0.0, dur - 0.25):.2f}:d=0.22"
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{total_t:.2f}", "-i", png, "-vf", vf,
               "-t", f"{total_t:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
        r = _run_ff(cmd, 120, "card")
        return out if (r and os.path.exists(out)) else None

    def _concat_xfade(self, clips, durs, out) -> str | None:
        """씬 클립들을 xfade 크로스페이드로 연결(검은 플래시 제거, 영상강화 PHASE 4).
        클립 k(마지막 제외)는 durs[k]+XFADE 길이(tail) → 전환이 tail을 소모해
        출력 총 길이 = sum(durs) = 오디오 길이(싱크 보존). 실패 시 tail 트림 후 concat 폴백."""
        if not clips:
            return None
        if len(clips) == 1:
            return self._concat(clips, out)
        if len(durs) == len(clips):
            cmd = ["ffmpeg", "-y"]
            for c in clips:
                cmd += ["-i", c]
            # ★ 전환 버퍼링 근본수정: xfade 입력마다 타임베이스·fps·픽셀포맷·SAR을 통일한다.
            #   카드(정지PNG 생성)와 사진(zoompan) 클립의 인코딩 프로파일이 달라 join 지점에서
            #   정지→재생 스터터가 났음 — 정규화로 프레임 타이밍을 일치시켜 제거(단일 필터그래프 패스).
            fc = ""
            for k in range(len(clips)):
                fc += f"[{k}:v]settb=AVTB,fps={FPS},format=yuv420p,setsar=1[n{k}];"
            prev, off = "[n0]", 0.0
            for k in range(1, len(clips)):
                off += durs[k - 1]
                lab = f"[x{k}]" if k < len(clips) - 1 else "[v]"
                # 전환 다양화(디자인 레이어 2026-07-28): 단조로운 fade 반복 → 절제된 3종 순환(말끔한 리듬)
                _tr = ("fade", "smoothleft", "smoothup")[k % 3]
                fc += f"{prev}[n{k}]xfade=transition={_tr}:duration={XFADE}:offset={off:.2f}{lab};"
                prev = lab
            cmd += ["-filter_complex", fc.rstrip(";"), "-map", "[v]", "-r", str(FPS),
                    "-fps_mode", "cfr", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    "-preset", "ultrafast", "-video_track_timescale", "90000",
                    "-threads", "1", "-an", out]
            if _run_ff(cmd, 420, "xfade") and os.path.exists(out):
                return out
        # 폴백: tail을 잘라 정확 길이로 재인코딩 → copy concat(싱크 보존, 전환은 컷)
        trimmed = []
        for k, c in enumerate(clips):
            if k < len(clips) - 1 and len(durs) == len(clips):
                tp = c.replace(".mp4", "_trim.mp4")
                if _run_ff(["ffmpeg", "-y", "-i", c, "-t", f"{durs[k]:.2f}", "-r", str(FPS),
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                            "-threads", "1", "-an", tp], 120, "trim") and os.path.exists(tp):
                    trimmed.append(tp)
                    continue
            trimmed.append(c)
        return self._concat(trimmed, out)

    def _post_overlay(self, video, ass, total, theme_key) -> str:
        """ASS 단어자막 + 상단 진행바 합성 — 최종 화질 패스(veryfast -crf 20, PHASE 4).
        로고 워터마크는 넣지 않는다(워터마크 = 교차게시 노출 감소). 단계적 폴백(자막 우선 보존)."""
        rgb = _theme_rgb(theme_key)
        hexcol = "0x%02X%02X%02X" % rgb
        out = os.path.join(os.path.dirname(video), "video_fx.mp4")
        assp = ass.replace("\\", "/")
        fontsdir = _FONT_DIR.replace("\\", "/")
        subs = f"subtitles=filename='{assp}':fontsdir='{fontsdir}'"
        bar = f"drawbox=x=0:y=0:w='iw*t/{total:.2f}':h=12:color={hexcol}@0.92:t=fill"
        attempts = [
            f"{subs},{bar}",     # 자막+진행바
            f"{subs}",           # 자막만
        ]
        for vf in attempts:
            cmd = ["ffmpeg", "-y", "-i", video, "-vf", vf, "-t", f"{total:.2f}", "-r", str(FPS),
                   "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-threads", "1", out]
            if _run_ff(cmd, 300, "post_overlay") and os.path.exists(out) and _probe_dur(out) > total * 0.8:
                return out
        return video   # 전부 실패 시 원본(자막 없이) 반환

    def _audio_segment(self, tts, dur, out_wav) -> str | None:
        """그 씬 오디오를 정확히 dur초 PCM으로. TTS 있으면 사용, 없거나 실패하면 무음으로 폴백
        (절대 None으로 두지 않아 씬이 드롭되지 않음 → TTS 장애에도 풀길이 보장)."""
        if tts and os.path.exists(tts) and os.path.getsize(tts) > 200:
            cmd = ["ffmpeg", "-y", "-i", tts, "-af", "apad", "-t", f"{dur:.2f}",
                   "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", out_wav]
            r = subprocess.run(cmd, capture_output=True, timeout=60)
            if r.returncode == 0 and os.path.exists(out_wav):
                return out_wav
        # 폴백: 무음
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-t", f"{dur:.2f}",
               "-i", "anullsrc=r=44100:cl=stereo", "-c:a", "pcm_s16le", out_wav]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return out_wav if (r.returncode == 0 and os.path.exists(out_wav)) else None

    def _concat(self, files, out) -> str | None:
        """동일 규격 파일들을 concat. PCM/동일코덱이라 copy로 무손실·무드리프트."""
        listf = out + ".list.txt"
        with open(listf, "w") as f:
            for c in files:
                f.write(f"file '{os.path.abspath(c)}'\n")
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                            "-c", "copy", out], capture_output=True, timeout=240)
        if (r.returncode != 0 or not os.path.exists(out)) and out.endswith(".mp4"):
            r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-pix_fmt", "yuv420p", "-an", out],
                               capture_output=True, timeout=300)
        return out if os.path.exists(out) else None

    def _mux(self, video, full_wav, out_dir, mood: str = "") -> str:
        """무음영상 + 연속오디오(+BGM) → 최종. 둘 길이가 같아 정확히 싱크.
        BGM은 업종 분위기(mood)로 선택(영상강화 PHASE 3). 목소리 loudnorm -14 LUFS,
        BGM 0.30 + 사이드체인 더킹(threshold 0.03 → 무음 구간 펌핑 방지)."""
        bgm = bgm_lib.pick(mood)
        out = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
        if bgm:
            # 목소리 full + BGM 사이드체인 더킹(내레이션 구간 BGM 자동 감쇄 → 명료도↑, 무음 구간 펌핑 방지, PHASE 11)
            # → loudnorm -14 LUFS(소셜 표준). sidechaincompress 실패 시 _add_audio/폴백이 무음이라도 확정 저장
            fc = ("[1:a]volume=1.0,asplit=2[v][vkey];[2:a]volume=0.30[b];"
                  "[b][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[bd];"
                  "[v][bd]amix=inputs=2:duration=first:normalize=0[m];"
                  "[m]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
            cmd = ["ffmpeg", "-y", "-i", video, "-i", full_wav, "-stream_loop", "-1", "-i", bgm,
                   "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
                   "-movflags", "+faststart", "-shortest", out]
        else:
            cmd = ["ffmpeg", "-y", "-i", video, "-i", full_wav,
                   "-filter_complex", "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]",
                   "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100",
                   "-movflags", "+faststart", "-shortest", out]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode == 0 and os.path.exists(out):
            return out
        import logging   # ffmpeg 실패 원인 로깅(현재 소실되던 stderr, PHASE 12)
        logging.warning("[video] mux 실패 rc=%s: %s", r.returncode, r.stderr.decode("utf-8", "ignore")[-500:])
        # mux 실패 → 무음이라도 out_dir에 확정 저장(작업폴더 경로 반환 금지: rmtree로 삭제돼 재생 404)
        try:
            shutil.copy(video, out)
            return out
        except Exception as ce:
            logging.warning("[video] mux 폴백 copy 실패: %r", ce)
            return video

    # ───────────────────── 레거시 폴백 ─────────────────────
    def _add_audio(self, video_path, narration, tenant_id):
        if not (video_path and os.path.exists(video_path)):
            return video_path, None, None, "영상 없음"
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant_id)
        tts_path = tts_lib.synthesize(narration, out_dir)
        bgm_path = bgm_lib.pick()
        if not tts_path and not bgm_path:
            return video_path, None, None, "무음"
        out = os.path.join(out_dir, f"shortav_{uuid.uuid4().hex}.mp4")
        cmd = ["ffmpeg", "-y", "-i", video_path]
        if tts_path:
            cmd += ["-i", tts_path]
        if bgm_path:
            cmd += ["-stream_loop", "-1", "-i", bgm_path]
        if tts_path and bgm_path:
            fc, amap = ("[1:a]volume=1.0[v];[2:a]volume=0.22[bg];[v][bg]amix=inputs=2:duration=first:normalize=0[m];"
                        "[m]loudnorm=I=-14:TP=-1.5:LRA=11[a]", "[a]")
        elif tts_path:
            fc, amap = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]", "[a]"
        else:
            fc, amap = "[1:a]volume=0.5,loudnorm=I=-16:TP=-1.5:LRA=11[a]", "[a]"
        if fc:
            cmd += ["-filter_complex", fc]
        cmd += ["-map", "0:v", "-map", amap, "-c:v", "copy", "-c:a", "aac", "-shortest", out]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(out):
                return video_path, tts_path, bgm_path, "오디오 합성 실패→무음"
            return out, tts_path, bgm_path, "오디오 합성됨"
        except Exception as e:
            return video_path, tts_path, bgm_path, f"오디오 오류: {str(e)[:60]}"

    def _assemble_legacy(self, images, subtitle, tenant_id, per=PER_IMAGE_SECONDS):
        if not shutil.which("ffmpeg"):
            return None, "ffmpeg 미설치"
        imgs = [p for p in images if p and os.path.exists(p)]
        if not imgs:
            return None, "원본 이미지 없음"
        out_dir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant_id)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
        cmd = ["ffmpeg", "-y"]
        for p in imgs:
            cmd += ["-loop", "1", "-t", f"{per:.2f}", "-i", p]
        parts, labels = [], ""
        for i in range(len(imgs)):
            parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v{i}]")
            labels += f"[v{i}]"
        parts.append(f"{labels}concat=n={len(imgs)}:v=1:a=0[cat]")
        # 폴백 영상에도 자막 굽기 — 이스케이프 이슈 회피 위해 textfile 사용(V3)
        vf_out, sub_file = "[cat]", None
        sub = (subtitle or "").strip().replace("\n", " ")
        if sub:
            sub_file = os.path.join(out_dir, f"sub_{uuid.uuid4().hex}.txt")
            with open(sub_file, "w", encoding="utf-8") as _f:
                _f.write(sub[:120])
            font = _font_path()
            fontclause = f":fontfile='{font}'" if font else ""
            parts.append(
                f"[cat]drawtext=textfile='{sub_file}'{fontclause}:fontcolor=white:fontsize=54:"
                f"box=1:boxcolor=black@0.5:boxborderw=20:x=(w-text_w)/2:y=h-text_h-180[out]")
            vf_out = "[out]"
        cmd += ["-filter_complex", ";".join(parts), "-map", vf_out,
                "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                "-threads", "1", "-movflags", "+faststart", out]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode != 0 or not os.path.exists(out):
                return None, "ffmpeg 실패: " + r.stderr.decode()[-120:]
            return out, f"{len(imgs)}장 슬라이드쇼(폴백)"
        except Exception as e:
            return None, f"영상 조립 오류: {str(e)[:100]}"
        finally:
            if sub_file and os.path.exists(sub_file):
                try:
                    os.remove(sub_file)
                except Exception:
                    pass


def _parse_scenes(block: str) -> list[dict]:
    scenes = []
    for line in block.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        time_range = parts[0].split(")", 1)[-1].strip() if ")" in parts[0] else parts[0]
        sc = {"time_range": time_range, "visual_description": "", "camera_movement": "",
              "on_screen_text": "", "narration_segment": ""}
        for p in parts[1:]:
            if p.startswith("비주얼:"):
                sc["visual_description"] = p[4:].strip()
            elif p.startswith("카메라:"):
                sc["camera_movement"] = p[4:].strip()
            elif p.startswith("자막:"):
                sc["on_screen_text"] = p[3:].strip()
            elif p.startswith("내레이션:"):
                sc["narration_segment"] = p[5:].strip()
        scenes.append(sc)
    return scenes
