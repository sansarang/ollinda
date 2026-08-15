"""지면(kind) 단일 관문 — 순위 스냅샷의 지면 정의·라벨·판독을 여기서만 한다.

왜 생겼나 (2026-08-16 실측으로 드러난 계측 결함 3종):
  ① **중복 기록** — `rank_snapshots.kind='blog'`는 이름과 달리 `place.rank()`(지역검색) 값이었다.
     `save_place_rank`가 같은 값을 `kind='place'`로 또 썼다. 한 값을 두 번 기록하고 있었다.
  ② **지면 혼합 비교** — `improving_keywords`·`stagnant_keywords`가 kind를 안 걸고 한 키워드의
     모든 지면을 한 시계열로 묶어 '순위 상승'을 계산했다. 플레이스 3위와 블로그탭 1위를
     비교한 셈이고, 그 값은 학습 루프(생성 브리프)로 **역주입**된다.
     헌법: "지면 부재와 순위 밖은 다른 진단이다."
  ③ **라벨 3중 복사** — 한글 라벨 사전이 main.py 2곳·weekly_report.py 1곳에 각각 살았다.

헌법 근거: "같은 재료를 읽는 소비자가 둘 이상이면 파서를 하나로 만든다."
따라서 지면을 읽고 쓰는 모든 코드는 이 모듈만 거친다.

순위 값 판독 규약(`place.py:245` 실측):
  · `None` = 조회 실패(기록하지 않는다)
  · `0`    = 상위 N위 **밖**(미노출) — 순위가 아니다. 정수라 순위처럼 읽히는 함정.
  · `>=1`  = 실제 순위
"""
from __future__ import annotations

BLOG_SEARCH = "blog_search"   # 블로그탭 — 자사 blog_id 정확 매칭(자사 식별자 판정)
POST = "post"                 # 포스트 URL 단위(생존 신고 race.py) — 블로그 단위 오탐 없음
PLACE = "place"               # 지역검색·플레이스
SHOP = "shop"                 # 쇼핑검색(셀러)
REGION_LEGACY = "blog"        # ⚠ 옛 라벨. 내용은 지역검색이다. **읽기 전용 — 새로 쓰지 않는다.**

_LABEL = {
    BLOG_SEARCH: "블로그탭",
    POST: "내 글",
    PLACE: "플레이스",
    SHOP: "쇼핑검색",
    REGION_LEGACY: "지역검색",
}

#: 새로 기록해도 되는 지면. REGION_LEGACY는 제외한다(중복 기록의 원인).
WRITABLE = (BLOG_SEARCH, POST, PLACE, SHOP)

#: 한 키워드에 여러 지면 기록이 있을 때 읽는 순서.
#: 자사 식별자 정확 매칭(blog_search·post)이 가장 신뢰도가 높고, 옛 라벨이 가장 낮다.
PRIORITY = (BLOG_SEARCH, POST, PLACE, SHOP, REGION_LEGACY)

MISS = 31                     # 미노출을 비교용 숫자로 바꿀 때 쓰는 최하위 값


def label(kind: str) -> str:
    """지면 한글 라벨. 모르는 값은 그대로 돌려준다(빈칸으로 삼키지 않는다)."""
    k = (kind or "").strip()
    return _LABEL.get(k, k)


def writable(kind: str) -> bool:
    """새로 기록해도 되는 지면인가. REGION_LEGACY 쓰기를 막는 관문."""
    return (kind or "").strip() in WRITABLE


def is_exposed(rank) -> bool:
    """실제로 노출된 순위인가. `0`(밖)과 `None`(조회 실패)은 노출이 아니다."""
    return isinstance(rank, int) and rank >= 1


def out_of_range(rank) -> bool:
    """조회는 됐으나 상위 N위 밖(미노출)인가 — `0`의 유일한 의미."""
    return rank == 0


def rank_for_compare(rank, miss: int = MISS) -> int:
    """순위 비교용 숫자. 미노출·조회실패는 최하위로 눕힌다.

    ★ 비교 전용이다. 이 값을 화면에 '순위'로 표시하면 없는 순위를 지어내는 것이 된다.
    """
    return rank if is_exposed(rank) else miss


def best_kind(kinds) -> str:
    """주어진 지면들 중 가장 신뢰도 높은 하나. 없으면 ''."""
    have = {(k or "").strip() for k in (kinds or [])}
    return next((k for k in PRIORITY if k in have), "")
