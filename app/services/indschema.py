"""
업종 스키마 + 추론 엔진(전 업종 동적 적응) — 상위노출 특화 잔재 제거.

설계: 업종 지식을 코드에 '저장'하지 않고 '추론(LLM)+실측 검증(searchad)+캐시'로.
- 스키마(틀)는 전 업종 고정 — 코드가 읽는 필드가 동일해 하드코딩이 구조적으로 불가.
- 값은 가게 등록/수정 시 Haiku 1콜로 추론, DB 캐시(industry_schema_cache).
- industries.py 유래 시드(썬팅·중고차)는 'seed' 소스 캐시로 강등(추론 스킵=회귀 0).
- 추론된 search_grammar 키워드는 반드시 검색량 관문(select_target_keyword) 경유 → 비실재 조합 자동 탈락.
- 3층(성과 피드백)·4층(집단 학습)은 범위 밖 — perf 필드 자리만 예약.
"""
from __future__ import annotations

import json
import logging
import re

from app import db

_log = logging.getLogger("shopcast.indschema")

# ── 전 업종 고정 스키마 틀 (필드 = PHASE 1 감사에서 도출) ────────────
SCHEMA_FIELDS = ("attribute_axes", "search_grammar", "trade_area", "content_angles",
                 "visual_preset", "privacy_patterns", "honesty_hooks", "general_tags", "perf")

# 범용 기본값 — 추론 실패·미지원 시 폴백(파이프라인 중단 금지). 업종 어휘 없음.
GENERIC_SCHEMA = {
    "attribute_axes": [{"axis": "핵심상품", "tokens": []}],   # 가게가 파는 것(예시 토큰은 추론이 채움)
    "search_grammar": ["{속성} {의도}", "{지역} {업종}", "{업종} 추천"],
    "trade_area": "local",              # local(동네 방문)|regional(광역)|national(전국 배송)
    "allow_region_hook": True,          # 훅에 지역명 허용 여부(방문형=True, 전국셀러=False)
    "content_angles": ["review", "howto", "price"],
    "visual_preset": "basic",           # basic|auto|soft|fresh (video subtitle_preset 키)
    "privacy_patterns": ["신분증", "계약서", "명함", "카드"],
    "honesty_hooks": ["장단점을 함께"],
    "general_tags": [],
    "perf": {},                          # 성과 피드백 자리 예약(3층·범위 밖)
}

# 시드(캐시 히트) — industries.py 유래를 '검증된 캐시'로 강등. 회귀 0 목표.
_SEED = {
    "썬팅": {
        "attribute_axes": [{"axis": "필름·시공", "tokens": ["루마버텍스700", "신차패키지", "유리막코팅", "생활보호PPF", "PPF", "썬팅지"]},
                           {"axis": "차종", "tokens": ["모닝", "그랜저", "아반떼", "쏘나타", "경차", "SUV"]}],
        "search_grammar": ["{지역} {업종}", "{업종} 추천", "신차 {업종}", "{차종} {업종}"],
        "trade_area": "local", "allow_region_hook": True,
        "content_angles": ["review", "howto", "price"], "visual_preset": "auto",
        "privacy_patterns": ["번호판", "차대번호", "등록증", "계약서"],
        "honesty_hooks": ["시공 시간·비용은 차종·상태에 따라"], "general_tags": ["썬팅", "자동차썬팅", "신차썬팅"],
        "perf": {},
    },
    "중고차": {
        "attribute_axes": [{"axis": "차종", "tokens": ["모닝", "레이", "스파크", "캐스퍼", "아반떼", "쏘나타", "그랜저",
                                                    "K3", "K5", "K7", "K8", "코나", "티볼리", "셀토스", "투싼", "쏘렌토",
                                                    "싼타페", "카니발", "스포티지", "포터", "봉고", "제네시스", "GV70", "GV80", "팰리세이드"]},
                           {"axis": "차급", "tokens": ["경차", "소형", "준중형", "중형", "준대형", "대형", "SUV", "승합", "화물", "수입"]},
                           {"axis": "연식", "tokens": []}],
        "search_grammar": ["{차종} 중고", "{차종} 중고차", "{연식} {차종} 중고", "{차급} 중고", "{지역} {차종} 중고"],
        "trade_area": "hybrid", "allow_region_hook": False,
        "content_angles": ["review", "howto", "price"], "visual_preset": "auto",
        "privacy_patterns": ["번호판", "차대번호", "등록증", "성능점검", "계약서", "기록부"],
        "honesty_hooks": ["신차 같은 컨디션은 아닙니다", "흠집·사고이력 투명 공개"],
        "general_tags": ["중고차", "중고차추천", "실매물"], "perf": {},
    },
}


def _norm_key(industry: str) -> str:
    return (industry or "").replace("/", ",").split(",")[0].strip().lower()


def _match_seed(industry: str) -> "dict | None":
    k = industry or ""
    for seed_kw, sch in _SEED.items():
        if seed_kw in k:
            return dict(sch)
    return None


def get_schema(industry: str, biz_type: str = "local", desc: str = "", infer: bool = True) -> dict:
    """업종 스키마 — 캐시(seed 우선) → DB 캐시 → 추론 → 범용 폴백. 항상 dict 반환(중단 금지)."""
    key = _norm_key(industry)
    if not key:
        return dict(GENERIC_SCHEMA)
    # 1) DB 캐시
    cached = db.get_industry_schema(key)
    if cached:
        return _fill(cached)
    # 2) 시드(코드 유래 → 캐시로 저장 후 반환)
    seed = _match_seed(industry)
    if seed:
        db.save_industry_schema(key, seed, source="seed")
        return _fill(seed)
    # 3) 추론(Haiku)
    if infer:
        inf = _infer(industry, biz_type, desc)
        if inf:
            db.save_industry_schema(key, inf, source="inferred")
            return _fill(inf)
    # 4) 범용 폴백
    return _fill(dict(GENERIC_SCHEMA))


def _fill(sch: dict) -> dict:
    """누락 필드를 범용 기본값으로 보강 — 코드가 항상 전 필드를 안전하게 읽게."""
    out = dict(GENERIC_SCHEMA)
    for f in SCHEMA_FIELDS + ("allow_region_hook",):
        if f in sch and sch[f] not in (None, "", []):
            out[f] = sch[f]
    return out


def _infer(industry: str, biz_type: str, desc: str) -> "dict | None":
    """Haiku 1콜 스키마 추론 — 실패 시 None(폴백)."""
    from app import llm as _llm
    prompt = (
        "너는 한국 소상공인 업종 분석가다. 아래 가게의 '검색 마케팅 스키마'를 JSON으로만 출력하라(설명 금지).\n"
        f"[업종] {industry}\n[사업형태] {biz_type}\n[설명] {desc[:200]}\n\n"
        "다음 키를 채워라:\n"
        "- attribute_axes: 이 업종이 검색될 때 핵심이 되는 '속성 축' 1~3개. "
        "각 {\"axis\":\"축이름\",\"tokens\":[실제 이 업종에서 흔한 구체 값 5~8개]}. "
        "예) 카페=메뉴·시그니처(아메리카노·디저트…), 미용=시술(펌·염색…), 헬스=프로그램(PT·필라테스…), 꽃집=용도·꽃종류(생일·장미…).\n"
        "- search_grammar: 검색 키워드 조합 문법 3~5개. 플레이스홀더 {지역}{업종}{속성}{의도} 사용. 예: \"{속성} 추천\", \"{지역} {업종}\".\n"
        "- trade_area: local(동네 방문)|regional(광역)|national(전국 배송) 중 하나.\n"
        "- allow_region_hook: 훅(첫 문장)에 지역명 넣어도 되는지 true/false(방문 필수 업종=true, 전국 배송 셀러=false).\n"
        "- content_angles: review·howto·price 중 이 업종에 맞는 것들.\n"
        "- visual_preset: basic|auto|soft|fresh 중(자동차=auto, 카페·뷰티=soft, 식당·식품=fresh, 기타=basic).\n"
        "- privacy_patterns: 이 업종 사진에 흔한 개인정보 위험 요소 3~6개(예: 신분증·처방전·계약서·차량번호판·카드메시지).\n"
        "- honesty_hooks: 이 업종에서 정직하게 밝힐 단점·한계 소재 1~3개(짧은 구).\n"
        "- general_tags: 이 업종 일반 태그 2~3개(붙여쓰기).\n"
        "JSON만 출력."
    )
    try:
        raw = _llm.call_task("spoken", prompt, max_tokens=700)   # spoken=Haiku 라우팅
    except Exception as e:
        _log.warning("[indschema] 추론 호출 실패: %r", repr(e)[:120])
        return None
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        _log.warning("[indschema] JSON 파싱 실패: %r", (raw or "")[:120])
        return None
    if not isinstance(data.get("attribute_axes"), list):
        return None
    return data


def attribute_tokens(schema: dict) -> list:
    """스키마의 모든 속성 토큰(차종·메뉴·시술 등) 평탄화 — 키워드 인식·추출 공용."""
    out = []
    for ax in (schema.get("attribute_axes") or []):
        out += [t for t in (ax.get("tokens") or []) if t]
    return list(dict.fromkeys(out))
