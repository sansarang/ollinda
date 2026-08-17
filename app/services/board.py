"""판 스캔표 — 글을 쓰기 전에 **이길 수 있는 자리**부터 고른다.

왜 만들었나(2026-08-17 사장님 지시):
  지금은 사진을 받으면 키워드를 하나 정하고 글을 쓴다. 그런데 오늘 실측이 말한다 —
  **판마다 승산이 다르고, 그 차이가 글 품질보다 크다.**
    · 상위글 나이 중간값이 검색어마다 7일 ~ 3,599일(약 10년)로 갈렸다(kw_anatomy 29개)
    · 통합검색 첫 화면에 블로그 지면이 아예 없는 검색어가 있다('부산 썬팅', 2026-08-01 실측)
  10년 된 글이 지키는 자리에 새 글을 넣는 것은 잘 쓰고 못 쓰고의 문제가 아니다.

  대행사는 키워드를 감으로 고른다. 우리는 재서 고른다 — 그게 파는 이유가 된다.

★ 판정은 **이미 있는 실측 부품만** 쓴다. 새 추정을 만들지 않는다.
    bloganatomy.cached  → 상위글 나이·글자수·문서수(크롤 안 함, 3일 TTL 캐시)
    blogreach.blocks_for → 그 검색어에 블로그 지면이 있는가(저장된 판정)
    blogrank.blog_rank   → 지금 우리가 몇 위인가
★ 재보지 않은 것은 '모름'으로 둔다. 빈칸을 추측으로 채우지 않는다(정직 게이트).
"""
from __future__ import annotations

import logging

_log = logging.getLogger("shopcast.board")

#: 판정 기준 — 전부 실측값에서 나온 경계다.
YOUNG_DAYS = 60      # 상위글 나이 중간값이 이 아래면 새 글이 들어갈 자리가 있다
OLD_DAYS = 730       # 2년 넘게 버티는 판은 새 글로 못 뚫는다(실측 최대 3,599일)
TOP_PAGE = 10        # 1페이지 기준
#: live=1일 때 순위를 실조회할 최대 검색어 수. 조회 1건당 네트워크 1회라 상한이 없으면
#: 요청 전체가 타임아웃된다(2026-08-17 실측: 12개에서 502). 넘는 것은 '모름'으로 남는다.
LIVE_MAX = 6

# 판정 코드 — 화면·리포트가 이 값만 쓴다(문구가 두 곳에 살면 그 자체가 결함)
WIN = "확보"          # 이미 1페이지 안 — 지킨다
OPEN = "뚫림"         # 새 글이 들어갈 자리가 있다 — 지금 공략
HARD = "버팀"         # 오래된 글이 지킨다 — 지금은 피한다
NO_SURFACE = "지면없음"  # 그 검색어에 블로그 자리가 없다 — 글로 못 뚫는다
UNKNOWN = "모름"      # 아직 안 재봤다

ORDER = (WIN, OPEN, HARD, NO_SURFACE, UNKNOWN)


def judge(rank, age_days, has_surface) -> tuple:
    """(판정, 이유) — 단일 관문. 순서가 곧 규칙이다.

    ★ 지면 없음을 가장 먼저 본다. 지면이 없으면 순위도 나이도 의미가 없다
      (블로그탭 6위인데 손님 눈엔 0이었던 실측).
    """
    if has_surface is False:
        return NO_SURFACE, "통합검색 첫 화면에 블로그 자리가 없음"
    if isinstance(rank, int) and 1 <= rank <= TOP_PAGE:
        return WIN, f"이미 {rank}위"
    if age_days is None:
        return UNKNOWN, "상위글 나이를 아직 안 재봄"
    if age_days <= YOUNG_DAYS:
        return OPEN, f"상위글 나이 중간값 {age_days}일 — 새 글이 들어갈 자리"
    if age_days >= OLD_DAYS:
        return HARD, f"상위글이 {age_days}일({age_days // 365}년) 버팀"
    return OPEN if (rank is None or rank > TOP_PAGE) else WIN, f"상위글 나이 {age_days}일"


def scan(tenant_id: str, keywords: list, blog_id: str = "", live: bool = False) -> dict:
    """검색어들을 판정해 표로 돌려준다.

    live=False(기본)면 **저장된 실측만** 읽는다 — 네트워크를 치지 않아 화면에서 즉시 뜬다.
    live=True면 순위만 조회한다(나이·지면은 캐시 유지 — 크롤은 여기서 하지 않는다).
    """
    from app.services import bloganatomy as _ana
    from app.services import blogreach as _reach

    rows = []
    for kw in [" ".join((k or "").split()) for k in keywords if (k or "").strip()]:
        age = docs = None
        a = _ana.cached(kw) or {}
        if a:
            age = a.get("age_days_median")
            docs = a.get("n")
        blk = _reach.blocks_for(tenant_id, kw) or {}
        surface = blk.get("blog_surface")
        has_surface = None if surface is None else bool(surface)

        rank = None
        if live and blog_id and len(rows) < LIVE_MAX:
            # ★ 순위 조회는 검색어당 네트워크 1회다. 상한 없이 돌면 요청이 통째로 타임아웃되고
            #   (2026-08-17 실측: 12개 조회에 502) 화면이 빈손으로 돌아온다.
            #   여기서 못 잰 것은 '모름'으로 남는다 — 빈칸이 거짓보다 낫다.
            try:
                from app.services import blogrank as _br
                r = _br.blog_rank(kw, blog_id) or {}
                rk = r.get("rank")
                rank = rk if isinstance(rk, int) and rk >= 1 else None
            except Exception as e:
                _log.warning("[board] 순위 조회 실패 kw=%s: %s", kw, repr(e)[:100])

        verdict, why = judge(rank, age, has_surface)
        rows.append({"keyword": kw, "verdict": verdict, "why": why,
                     "rank": rank, "age_days": age, "top_n": docs,
                     "has_surface": has_surface})

    rows.sort(key=lambda r: (ORDER.index(r["verdict"]), r["age_days"] if r["age_days"] is not None else 10**6))
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ORDER}
    return {"rows": rows, "counts": counts,
            "attack": [r["keyword"] for r in rows if r["verdict"] == OPEN],
            "avoid": [r["keyword"] for r in rows if r["verdict"] in (HARD, NO_SURFACE)],
            "unmeasured": counts[UNKNOWN]}


def next_target(tenant_id: str, keywords: list, blog_id: str = "") -> str:
    """다음에 쓸 글이 노릴 검색어 하나 — 뚫리는 판 중 상위글이 가장 어린 곳.

    없으면 빈 문자열을 돌려준다. **아무 키워드나 고르지 않는다** —
    뚫리는 자리가 없다는 사실 자체가 보고할 내용이다(침묵 폴백 금지).
    """
    b = scan(tenant_id, keywords, blog_id)
    return b["attack"][0] if b["attack"] else ""


def summary_line(board: dict) -> str:
    """사장님 화면·리포트용 한 줄. 주방 용어(검색량·문서수) 쓰지 않는다."""
    c = board.get("counts") or {}
    n_open, n_win = c.get(OPEN, 0), c.get(WIN, 0)
    if n_win and n_open:
        return f"이미 첫 페이지에 있는 검색어 {n_win}개, 지금 노려볼 만한 곳 {n_open}개를 찾았어요."
    if n_win:
        return f"첫 페이지에 있는 검색어가 {n_win}개예요. 지킬 차례입니다."
    if n_open:
        return f"지금 노려볼 만한 검색어를 {n_open}개 찾았어요."
    if c.get(UNKNOWN):
        return "아직 재보지 않은 검색어가 많아요. 며칠 지켜보면 자리가 보입니다."
    return "지금은 뚫고 들어갈 자리가 잘 안 보입니다. 다른 각도를 찾아볼게요."
