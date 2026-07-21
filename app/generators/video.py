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
# 동시 렌더 상한 — ffmpeg 폭주(업로드 N건=프로세스 N개) 방지(성장 PHASE 12)
RENDER_SEM = _threading.BoundedSemaphore(int(os.environ.get("SHOPCAST_RENDER_CONCURRENCY", "2")))

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
    return t.strip()


# 경쟁·가격 저격(정직성·상도의) — 훅/자막 전면 금지 패턴
_RIVAL_JAB = __import__("re").compile(
    r"(비싸게|바가지|덤터기|호구 ?잡|딴 데|다른 (업체|가게|집)|타 ?업체|(남들|다들)[^.]{0,12}(비싼|비싸))")


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


def _parse_emphasis(text: str) -> tuple[str, list]:
    """자막 강조 마킹 {어절} 파싱 → (마킹 제거 텍스트, 강조 어절 목록[최대 1 — 남발 금지]).
    마킹은 기존 어절을 감싸는 표시일 뿐 — 텍스트 자체는 사실 게이트를 통과한 그대로."""
    import re as _r
    emph = _r.findall(r"\{([^{}]{1,20})\}", text or "")[:1]
    clean = _r.sub(r"[{}]", "", text or "")
    return clean, [e.strip() for e in emph if e.strip()]


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
        r"없이|있게|없게|같이|처럼|만큼|토록|도록|채로|대로|듯이|듯)$")
    src_toks = set(_rg.findall(r"[가-힣]{2,}", source or ""))
    for tok in _rg.findall(r"[가-힣]{3,}", line):   # 2자 토큰은 조사 결합('차라') 오탐이 커 제외(수치는 별도 검사)
        if tok in _SPOKEN_FUNC or _PRED.search(tok):
            continue                                  # 기능어·서술어(활용형)는 통과
        if any((tok.startswith(s) or s.startswith(tok[:max(2, len(tok) - 2)])) for s in src_toks if len(s) >= 2):
            continue                                  # 본문과 어간 공유(양방향)
        if any(tok[:n] in source for n in range(len(tok), 1, -1)):
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
              "- 각 문장에서 가장 중요한 숫자·차종·핵심명사 어절 하나만 중괄호로 감싸라(예: {830만 원}). 문장당 최대 1개, 없으면 안 감싸도 된다. 중괄호 안 어절은 원문에 있는 그대로만.\n"
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


def _cap_lines(sentences: list, max_lines: int = 3, budget: float = 9.0) -> list:
    """씬당 3줄 초과 강제 분할(코드 강제) — 긴 문장을 절 경계로 나눠 각 조각이 3줄 이내가 되게.
    분할 조각은 같은 사진을 쓰게 되므로(순서 보존) 사진 정합 유지. 강조 마킹 {} 균형 보존."""
    import re as _r
    cap = max_lines * budget                          # 3줄 ≈ 가중치 30
    def _w(s):
        s = s.replace("{", "").replace("}", "")
        return sum(1.0 if ("가" <= c <= "힣" or "一" <= c <= "鿿") else 0.55 for c in s)
    out = []
    for s in sentences:
        s = (s or "").strip()
        if not s:
            continue
        if _w(s) <= cap:
            out.append(s)
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
                        break
                    acc = (acc + " " + w).strip()
                out.append(acc.strip(" ,"))
                rest = " ".join(ws[len(acc.split(" ")):]).strip()
            if rest:
                out.append(rest.strip(" ,"))
    # 고립 말미 조각(예: '시운전해 보시고') 병합 — 앞 줄과 합쳐 어중간한 조각 방지(3줄 소폭 초과 허용)
    merged = []
    for s in out:
        if merged and _w(s) < 8 and _w(merged[-1]) + _w(s) <= cap + 8:
            merged[-1] = (merged[-1].rstrip(" ,") + " " + s).strip()
        else:
            merged.append(s)
    # 중괄호 균형 복구(분할로 한쪽만 남으면 제거)
    fixed = []
    for s in merged:
        if s.count("{") != s.count("}"):
            s = s.replace("{", "").replace("}", "")
        fixed.append(s)
    return fixed


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
        return f"키워드 원형 삽입('{kw}')"
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
                return f"셀러·병행 훅 지역명 노출('{core}')"
    return ""


def _script_from_body(body: str, n: int, kw_nat: str, source: str, tone: str = "info",
                      biz_type: str = "local", region: str = "") -> list | None:
    """본문 전문 → 씬 N개 대본(1콜, Haiku 경로). tone='info'(네이버 정보형)|'reach'(쇼츠·릴스 도달형).
    구조: 핵심(본문 순서 유지) → 단점·정직 고지 → (클로징은 템플릿).
    대본 게이트·사실 게이트 실패 시 사유 피드백 재생성 1회 → 재실패 None(호출부 폴백)."""
    import logging as _lg
    log = _lg.getLogger("shopcast.video")
    from app import llm as _llm
    _struct = ("- 구조: [훅(핵심 숫자·반전을 맨 앞)] → 전개 2~3 → 마무리. 첫 줄은 스크롤 멈추는 강한 훅.\n"
               "- 톤: 도달형(짧고 리듬감 있는 구어체 허용). 단 과장·보장('짱짱·끝납니다·최고·완벽·무조건') 금지, 경쟁 저격 금지.\n"
               if tone == "reach" else
               "- 구조: 핵심 내용(본문 등장 순서 유지) → 뒤쪽에 단점·한계 등 정직한 고지 1개.\n"
               "- 어미는 씬마다 변화(명사 종결·질문·청유 혼용, '~입니다' 연속 금지). 과장·보장 표현 금지.\n")
    _allow_region = (biz_type or "local") not in ("seller", "hybrid")   # 매장 전용만 지역 허용
    _hook_rule = (
        "- 첫 줄(훅)은 검색자의 실제 궁금증으로 새로 써라. 소재는 "
        + ("지역·방문(예 '○○에서 썬팅, 어디에 맡길까요?')" if _allow_region
           else "매물·불안·가격·상태(예 '9만km 모닝, 830만 원이면 어떤 상태일까요?' / '무사고, 그 말 그대로 믿으세요?')") + ".\n"
        + ("" if _allow_region else "- 훅과 모든 자막에 지역명(시·군·구·동 이름)을 넣지 마라 — 전국 손님이 대상이다.\n")
        + "- 타깃 키워드를 통째로 훅에 넣지 마라(비문·도배). 질문·반전으로 자연스럽게.\n")
    base = ("아래 블로그 본문을 근거로, 세로 영상 자막 대본을 써라. 전체가 하나의 이야기가 되게.\n"
            f"- 자막 씬 {n}개, 한 줄씩 출력(번호·라벨 없이). 각 씬 12~20자(공백 포함, 절대 24자 초과 금지) — 한 호흡에 읽히게.\n"
            "- 한 문장이 길면 두 씬으로 쪼개라(내용을 더 많은 짧은 씬에 나눠 담기). 씬 하나에 두 메시지 금지.\n"
            + _struct +
            "- 예고를 했으면('단점부터 볼게요' 등) 바로 다음 씬이 그 내용이어야 한다. 예고만 하고 안 보여주기 금지.\n"
            "- 동일·유사 문장 반복 금지. 씬당 하나의 메시지, 핵심 숫자·단어를 문장 앞에.\n"
            "- 본문에 있는 사실만. 새 정보·수치·명사 추가 절대 금지. 완결된 문장만(어중간한 조각·조사 시작 금지).\n"
            "- 각 씬에서 가장 중요한 숫자·차종·핵심명사 어절 하나만 {중괄호}로 감싸라(씬당 최대 1개, 원문 어절 그대로).\n"
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
            _lim = 30 if tone == "reach" else 46      # reach는 짧은 씬 강제(과분할 방지), info는 하류 캡에 위임
            _over = [l for l in lines if len(l.replace("{", "").replace("}", "")) > _lim]
            if _over:
                bad = f"씬 길이 초과({len(_over)}개, 각 {_lim}자 이내로): '{_over[0][:26]}…'"
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
]


def _hint_bonus(scene_text: str, desc_text: str) -> float:
    """씬 자막 유형과 사진 묘사가 같은 계열이면 가점(검수 자막↔엔진룸 사진 등)."""
    st, dt = scene_text or "", desc_text or ""
    for keys, photo_kw in _SCENE_PHOTO_HINT:
        if any(k in st for k in keys) and any(pk in dt for pk in photo_kw):
            return 0.5
    return 0.0


def _match_photos(lines: list, imgs: list, gen_source: str, log_tag: str = "") -> list:
    """대본 확정 후 씬 내용 ↔ 사진 매칭 — gen_source의 [사진N] 묘사 토큰 겹침 + 유형 힌트 가점(2-3).
    검수 자막→엔진룸/계기판, 서류 자막→문서, 가격·스펙→전면/측면 우선. 근거 없으면 원 순서.
    매칭 스코어 로그 기록(검증용)."""
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
    used, order, _scores = set(), [], []
    for ln in lines:
        lt = _norm_line(ln)
        best, best_s = None, 0.0
        for i, dt in descs.items():
            if i in used or not dt:
                continue
            s = len(lt & dt) / max(1, len(lt | dt)) + _hint_bonus(ln, raws.get(i, ""))
            if s > best_s:
                best, best_s = i, s
        if best is not None and best_s >= 0.08:
            order.append(best)
            used.add(best)
            _scores.append((ln[:16], best + 1, round(best_s, 2)))
        else:
            order.append(None)
            _scores.append((ln[:16], None, 0.0))
    if log_tag:
        _lg.getLogger("shopcast.video").warning("[%s] 씬-사진 매칭: %s", log_tag,
            " / ".join(f"'{t}'→#{p}({s})" if p else f"'{t}'→순차" for t, p, s in _scores))
    remain = [i for i in range(len(imgs)) if i not in used]
    final = []
    for o in order:
        final.append(imgs[o] if o is not None else imgs[remain.pop(0)] if remain else imgs[0])
    final += [imgs[i] for i in remain]
    return final


# 화질 기준(R3) — 코드에 고정: 짧은 변 1080 이상 + 비트레이트 하한. 본체 블러·재스케일 금지.
MIN_SHORT_SIDE = 1080
MIN_BITRATE = 1_500_000     # 1.5Mbps — 실측 정상 산출물(쇼츠 2.5M·클립 3.3M) 대비 보수 하한


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
        imgs_all = [p for p in (images or [asset.path]) if p and os.path.exists(p)]
        imgs = imgs_all[:8]        # 씬 소스만 상한(씬 6개 + 여유) — payload에는 전체 기록(사진 제한 해제)
        vid_imgs = self._downscale_for_video(imgs)   # 대용량 원본(5712×4284) → zoompan 타임아웃 방지(백그라운드 스레드)
        prof = resolve_industry(tenant.industry)
        strat = resolve_strategy(tenant)
        kws = seo.target_keywords(prof.name, tenant.region, asset.note,
                                  axis=strat.keyword_axis, brand=tenant.brand_name)
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
                vid_imgs = _match_photos(list(sent), vid_imgs, _gen_src, "shorts")
            video_path, note, dur_sec, cover_path = self._build_scene_video(
                vid_imgs, script, kws, tenant, strat, title)
            _scene_note = note                                # 씬 경로 결과/오류(진단용)
            _scene_ok = bool(video_path)
            # 폴백: 씬 파이프라인 실패 → 기존 슬라이드쇼 + 단일자막 + 오디오(게이트 통과 자막만 도달)
            if not video_path:
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
        variants = self._aspect_variants(video_path, out_dir) if video_path else {}
        # 네이버용 정보형 영상(추가 산출물) — 실패해도 릴스·글 흐름에 영향 없음(R1·R3)
        naver_path, naver_meta = None, {}
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
        _n_scenes = min(7, max(4, len(vid_imgs)))
        _rs = _script_from_body(body, _n_scenes, kw_nat, _fact_src, tone="info", biz_type=_biz, region=_reg)
        _script_mode = bool(_rs and len(_rs) >= 4)
        if _script_mode:
            opening = _rs[0]                           # 대본이 쓴 훅(검색자 궁금증) — 고정 조립 폐기
            sent = _rs[1:]
        else:
            _nlog.warning("[naver-video] 대본 생성 실패 — 씬별 발췌 폴백")
            # 폴백 훅: 키워드 원형 조립 대신 업종 기반 질문(셀러·병행은 지역 제외)
            _hk_kw = _kw_shorten_nolocal(kw_nat, _reg) if _biz in ("seller", "hybrid") else kw_nat
            opening = f"{_hk_kw}, 궁금하셨죠?" if _hk_kw else "지금 확인해 보세요"
            sent = ([_cut_word(h, 30) for h in heads] + caps)[:6]
            sent = _to_spoken(sent, _fact_src)
            sent = _dedup_lines(sent)                 # 폴백도 서사 정제 — 내용없는 예고('단점부터 말씀드릴게요')·중복 제거
        # 클로징 다양화 — 고정 템플릿 대신 글 CTA '사실' 기반 선택(본문에 근거 있는 패턴만, 없으면 현행 유지)
        if any(k in body for k in ("성능점검", "서류", "점검기록부")):
            _cta_line = "서류까지 본문에서 확인하세요"          # 매물형 — 본문이 서류 확인을 다룰 때만
        elif any(k in body for k in ("예약", "방문", "오시면")):
            _cta_line = "실차 확인은 예약 한 번이면 됩니다" if "중고" in (tenant.industry or "") else "방문 예약은 본문에서"
        else:
            _cta_line = "자세한 내용은 본문에"                  # 공통형(현행)
        outro = f"{tenant.name} · {region_short}\n{_cta_line}"
        sent = _cap_lines([_strip_labels(s) for s in sent])   # 서식 세척 + 3줄 초과 강제 분할(캡 후 최종 sent로 1회만 매칭)
        _gen_src2 = pl.get("gen_source") or ""
        if _gen_src2 and sent:
            vid_imgs = _match_photos(list(sent), vid_imgs, _gen_src2, "naver-video")   # 씬↔사진 매칭(유형 힌트+로그)
            _nlog.warning("[naver-video] 사진 재배정 %d씬↔%d장", len(sent), len(vid_imgs))
        path, note, dur, _cover = self._build_scene_video(
            vid_imgs, SceneScript(hook=opening, sentences=sent, outro=outro, source="body_excerpt", evidence=body),
            kws, tenant, strat, f"{kw0} 정리")
        # 15초 하한 가드(3-4): D.I.A.+ 동영상 가점 기준 미달이면 본문 발췌 캡션을 늘려 1회 재빌드
        if path and dur and dur < 15 and not _script_mode and len(caps) > len(sent) - len(heads):
            _nlog.warning("[naver-video] %s초 < 15 — 캡션 확장 재빌드", dur)
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
                "duration_sec": dur, "opening": opening, "scene_texts": [opening] + sent + [outro]}
        return final, meta

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
            # 1) 본문 씬들 — 자막은 ASS 카라오케로 별도(여기선 영상+켄번스+색보정만)
            #    ElevenLabs with-timestamps 실측 단어 타이밍(있으면) → 카라오케 싱크 정확(영상강화 PHASE 2)
            for i, text in enumerate(sentences):
                img = visuals[i % len(visuals)]
                text, _emph = _parse_emphasis(text)          # TTS·카드엔 마킹 없는 원문(음성-화면 일치)
                seg_tts, word_times = tts_lib.synthesize_timed(text, work)
                td = _probe_dur(seg_tts) if seg_tts else 0
                # 음성이 있으면 씬 길이 = 음성 길이(+여유). 9초로 자르지 않음 → 긴 문장 나레이션 끊김·자막불일치 방지
                sdur = min(15.0, max(MIN_SCENE, td + 0.4)) if td > 0.3 else self._clamp(len(text) * 0.13 + 1.2)
                v = self._scene_video(img, sdur, i, os.path.join(work, f"v{i}.mp4"), tail=XFADE)
                aw = self._audio_segment(seg_tts, sdur, os.path.join(work, f"a{i}.wav"))
                if v and aw:
                    ass_scenes.append((t, sdur, text, word_times, _emph))
                    vclips.append(v); awavs.append(aw); durs.append(sdur); t += sdur
                else:
                    dropped += 1
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
            # 안전장치: 최종본이 작업폴더 안이면 out_dir로 복사(rmtree 삭제 방지 → 재생 404 원천 차단)
            if final and (work in final):
                safe = os.path.join(out_dir, f"short_{uuid.uuid4().hex}.mp4")
                try:
                    shutil.copy(final, safe)
                    final = safe
                except Exception as ce:
                    import logging
                    logging.warning("[video] 안전복사 실패(작업폴더 경로 유지 → 정리로 소실 위험): %r", ce)
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
                    f"{f' · 씬탈락 {dropped}' if dropped else ''}")
            return final, note, round(total), cover
        except Exception as e:
            try:
                shutil.rmtree(work, ignore_errors=True)   # 실패해도 작업폴더 정리
            except Exception:
                pass
            return None, f"씬 빌드 오류: {str(e)[:120]}", 0, None

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
            fc = (f"[0:v]split=2[a][b];"
                  f"[b]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},boxblur=22:2[bg];"
                  f"[a]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                  f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
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
        lines = self._wrap_lines(d, text, font, W - 150)[:4]
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

    def _hook_photo_png(self, out: str, big: str, small: str, img_path: str, accent: str) -> bool:
        """첫 3초 훅 — 실사진 배경(cover crop) + 어둡게 + 큰 문제제기 텍스트(영상강화 PHASE 1).
        그라데이션 카드보다 '진짜 현장' 느낌 = 오리지널·주제 일관성 신호. 성공 True."""
        try:
            from PIL import Image, ImageDraw, ImageOps, ImageFilter
            im = Image.open(img_path)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, (W, H), method=Image.LANCZOS, centering=(0.5, 0.42))
            im = im.filter(ImageFilter.GaussianBlur(1))          # 미세 블러 → 텍스트 대비↑(사진은 살림)
            # 상하 어두운 그라데이션 오버레이(텍스트 가독) — 중앙 사진은 보이게
            ov = Image.new("L", (W, H), 0)
            od = ImageDraw.Draw(ov)
            for y in range(H):
                if y < H * 0.55:
                    a = int(170 * (1 - y / (H * 0.55)) ** 1.3)   # 위쪽 어둡게(텍스트 영역)
                else:
                    a = int(120 * ((y - H * 0.55) / (H * 0.45)) ** 1.6)
                od.line([(0, y), (W, y)], fill=a)
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

    def _scene_video(self, img, dur, idx, out, tail: float = 0.0) -> str | None:
        """이미지 → 켄번스 + 색보정(통일감), 정확히 dur(+tail)초 무음 영상. 자막은 ASS로 별도.
        tail>0 = xfade 전환용 여유 꼬리(전환에 소모돼 체감 길이는 dur) — 페이드 없음(검은 플래시 제거)."""
        total_t = dur + max(0.0, tail)
        frames = max(1, int(total_t * FPS))
        zdir = "min(zoom+0.0012,1.12)" if idx % 2 == 0 else "if(eq(on,1),1.12,max(zoom-0.0012,1.0))"
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
              f"eq=contrast=1.06:saturation=1.12:brightness=0.02,"
              f"zoompan=z='{zdir}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
              f"d={frames}:s={W}x{H}:fps={FPS}" + ("" if tail > 0 else self._fade(dur)))
        cmd = ["ffmpeg", "-y", "-loop", "1", "-t", f"{total_t:.2f}", "-i", img, "-vf", vf,
               "-map", "0:v", "-t", f"{total_t:.2f}", "-r", str(FPS), "-pix_fmt", "yuv420p",
               "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1", "-an", out]
        r = _run_ff(cmd, 120, f"scene{idx}")
        return out if (r and os.path.exists(out)) else None

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
            fc, prev, off = "", "[0:v]", 0.0
            for k in range(1, len(clips)):
                off += durs[k - 1]
                lab = f"[x{k}]" if k < len(clips) - 1 else "[v]"
                fc += f"{prev}[{k}:v]xfade=transition=fade:duration={XFADE}:offset={off:.2f}{lab};"
                prev = lab
            cmd += ["-filter_complex", fc.rstrip(";"), "-map", "[v]", "-r", str(FPS),
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
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
