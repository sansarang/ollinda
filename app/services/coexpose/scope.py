"""🚫 테스트 범위 — 사장님 실운영 업종은 영구 제외(2026-08-06 지시).

자동차 썬팅·틴팅·PPF·유리막코팅, 중고차·자동차 판매는 **모든** 수집·대조에서 뺀다.
실운영 업종이라 편향·특수성이 끼어 업종 중립을 깨뜨린다.
이미 수집된 썬팅 결과는 삭제하지 않고 '업종중립 아님, 미확정'으로 동결한다 — 인자 판정에 쓰지 않는다.
"""
from __future__ import annotations

import re

# 제외 신호(업종·질의·채널 어디에서든). 언어 규칙만.
EXCLUDED = ("썬팅", "선팅", "틴팅", "PPF", "ppf", "유리막코팅", "중고차", "자동차판매",
            "신차패키지", "자동차시공", "카센터", "자동차")
_EX = re.compile("|".join(re.escape(x) for x in EXCLUDED))


def is_excluded(*fields) -> bool:
    """질의·업종·제목 어디에 걸려도 제외."""
    return bool(_EX.search(" ".join(str(f or "") for f in fields)))


def filter_queries(queries: list) -> tuple:
    """수집 전 걸러낸다. 반환 (통과, 제외됨)."""
    ok, dropped = [], []
    for q in (queries or []):
        s = " ".join(str(v or "") for v in (q.values() if isinstance(q, dict) else [q]))
        (dropped if is_excluded(s) else ok).append(q)
    return ok, dropped


FROZEN_NOTE = ("썬팅·중고차 표본은 업종중립이 아니라 동결됐다(미확정). "
               "보존은 하되 인자 판정에 쓰지 않는다.")
