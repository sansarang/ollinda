"""관리 섹션 어휘 단일 관문 — 같은 뼈대가 매번 같은 말로 반복되는 것을 막는다.

왜 생겼나 (2026-08-16 사장님 지적: "AI가 쓴다는 느낌이 안 들어야 한다"):
  실물 6편 확인 결과 '## 한눈 요약'·'## 자주 묻는 질문'이 **6/6**로 글자 하나 안 틀리고 같았다.
  한 블로그에 수십 편 쌓이면 사람 눈에도 기계 티가 나고, 유사문서 판정 위험도 커진다.

왜 관문이 필요한가:
  이 문구를 **읽는 코드가 여러 곳**이다 — seo(GEO 점검·경고), qualitycheck(존재 검사),
  text_claude(사진 배정 금지 구역), video(영상 씬 제외). 문구만 바꾸면 그 전부가 조용히 깨진다.
  헌법: 같은 재료를 읽는 소비자가 둘 이상이면 파서를 하나로 만든다.

변형은 **글마다 결정적으로** 고른다(같은 글은 언제 다시 봐도 같은 문구).
무작위로 고르면 재생성 때마다 달라져 검증이 불가능해진다.
"""
from __future__ import annotations

#: 핵심 요약 섹션 — 첫 번째가 기준형(기존 문구, 하위호환)
SUMMARY = ("한눈 요약", "요약하면", "핵심만 정리", "짧게 정리")
#: 질문 섹션
FAQ = ("자주 묻는 질문", "많이들 물어보시는 것", "이건 꼭 물어보세요", "자주 받는 질문")
#: 관련 글 섹션
RELATED = ("함께 보면 좋은 글", "이 글과 같이 보면 좋아요", "관련해서 쓴 글")

#: 사진 배정 금지·영상 씬 제외 등에서 '관리 섹션'을 알아보기 위한 전체 목록
ALL = SUMMARY + FAQ + RELATED


def _pick(pool: tuple, seed: str) -> str:
    """글 단위 결정적 선택 — 같은 글은 항상 같은 문구."""
    s = (seed or "").strip()
    if not s:
        return pool[0]
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return pool[h % len(pool)]


def summary_head(seed: str = "") -> str:
    return _pick(SUMMARY, seed)


def faq_head(seed: str = "") -> str:
    return _pick(FAQ, seed)


def related_head(seed: str = "") -> str:
    return _pick(RELATED, seed)


def has_summary(text: str) -> bool:
    """요약 섹션이 있는가 — 어떤 변형이든 인정한다."""
    t = text or ""
    return any(v in t for v in SUMMARY) or "한 눈 요약" in t


def has_faq(text: str) -> bool:
    """질문 섹션이 있는가 — 변형 + 기존 판정어(Q&A·Q.)까지."""
    t = text or ""
    return any(v in t for v in FAQ) or any(s in t for s in ("자주묻는", "Q&A", "Q.", "Q1"))


def is_admin_head(head: str) -> bool:
    """이 소제목이 '관리 섹션'인가(본문 소제목과 구분)."""
    h = (head or "").strip()
    return any(v in h for v in ALL)


def prompt_names(seed: str = "") -> dict:
    """생성 프롬프트에 넣을 이번 글의 섹션 이름."""
    return {"summary": summary_head(seed), "faq": faq_head(seed),
            "related": related_head(seed)}
