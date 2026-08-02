"""
생성 파이프라인 결함 박제(2026-08-02 전수 스윕).

어제 실생성 14세트를 전 표면 스캔해 확정한 결함만 여기 못 박는다.
스캔에서 나온 오탐(썬팅업의 '시공', 수정 배포 이전 세트)은 결함이 아니므로 제외했다.

박제 대상:
  A. 겁주기 표현이 본문에 남은 채 88점 통과 — 채점기가 겁주기 목록을 안 봤다.
     프롬프트(HUMAN_TOUCH)에는 '호구 낚시 훅' 금지가 있었는데 검사 규칙과 어긋나 있었다.
     (영상 자막에서 이미 겪은 것과 같은 사고 — 생성 규칙과 검사 규칙의 이원화)
  B. 지역 토큰 중복 — 광역과 기초지역이 같은 값이면 '부산 부산'이 그대로 표면에 나갔다.
"""
from __future__ import annotations

from app import seo


def _audit(body: str, title: str = "부산 기장 중고차 가격 공개", kw: str = "부산 기장 중고차"):
    pl = {"body": body, "title": title, "target_keywords": [kw]}
    return seo.quality_audit("naver_blog", "blog", pl, source=body)


def test_fear_marketing_is_penalized():
    """A. 겁주기 표현은 반드시 감점으로 잡힌다(88점 통과 재발 방지).
    실측 문장: '호구 잡힐까 불안한 분들, 여기부터 보세요.'"""
    body = ("## 먼저 서류부터 깝니다\n"
            "호구 잡힐까 불안한 분들, 여기부터 보세요. "
            "성능점검기록부에 사고이력 없음으로 체크돼 있습니다.")
    au = _audit(body)
    hits = [w for w in (au.get("warnings") or []) if "겁주기" in w]
    assert hits, f"겁주기 표현이 감점되지 않음: {au.get('warnings')}"


def test_normal_body_not_flagged_as_fear():
    """A-역: 정상 문장은 겁주기로 오인되면 안 된다(과잉 차단 방지)."""
    body = ("## 먼저 서류부터 깝니다\n"
            "성능점검기록부 원본을 그대로 보여드립니다. "
            "실주행 57,216km, 사고이력 없음으로 체크돼 있어요.")
    au = _audit(body)
    hits = [w for w in (au.get("warnings") or []) if "겁주기" in w]
    assert not hits, f"정상 문장을 겁주기로 오인: {hits}"


def test_region_tokens_deduped():
    """B. 같은 지역 토큰이 반복되면 하나로 합친다('부산 부산' 재발 방지)."""
    assert seo.canonical_region("부산 부산", "local", "중고차판매", verify_volume=False) == "부산"
    assert seo.canonical_region("부산 부산 기장", "local", "중고차판매",
                                verify_volume=False) == "부산 기장"
    # 정상 입력은 그대로 — 중복 제거가 정보를 깎지 않는다
    assert seo.canonical_region("부산 기장", "local", "중고차판매",
                                verify_volume=False) == "부산 기장"
    assert seo.canonical_region("부산", "local", "중고차판매", verify_volume=False) == "부산"


def test_fear_list_is_single_source():
    """A-구조: 겁주기 목록은 영상 자막과 같은 뿌리를 써야 한다.
    두 곳에 따로 두면 반드시 어긋난다(실사고 2회: 영상 → 본문)."""
    pats = seo._fear_patterns()
    assert pats, "겁주기 목록이 비어 있음"
    try:
        from app.generators.video import FEAR_PATTERNS
        assert tuple(pats) == tuple(FEAR_PATTERNS), "본문·영상 겁주기 목록이 갈라짐"
    except ImportError:                        # 영상 모듈 미가용 환경은 폴백 목록 허용
        assert any("호구" in p for p in pats)
