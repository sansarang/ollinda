"""본문 사진 수 상한 — 노출과 체류시간에 맞춰 자른다(2026-08-17 사장님 지시).

왜 필요한가 — 오늘 같은 재료로 세 번 만들어 실측했다:

    글자    문단   사진   문단당사진   뭉침
    3,547   22     9      0.41        0곳
    3,186   25    16      0.64        1곳
    3,502   19    25      1.32        9곳   ← 사진을 다 넣으면 이렇게 된다

  **문단당 사진이 1장을 넘으면 마커가 붙어 나온다.** 붙으면 그 사이에 본문이 없다는 뜻이고,
  읽는 사람은 사진만 넘기게 된다 — 체류시간이 늘지 않고 오히려 줄어든다.

기준 둘:
  ① 문단 수 — 사진 사이에는 읽을 것이 있어야 한다(뭉침 0의 조건).
  ② 상위글 실측 — kw_anatomy 29개 키워드에서 사진 중간값 21장(범위 3~52).
     Yeti는 이미지를 20초에 1장 가져간다(자체 로그 실측) — 22장이면 이미지 수집에만 7분.
     그래서 절대 상한을 22로 둔다. 더 넣어도 수집이 늦어질 뿐 노출에 보태지 않는다.

★ 남는 사진을 버리는 게 아니다. 본문 마커에서만 빼고, 영상·캡션 소재로는 그대로 쓴다.
"""
from __future__ import annotations

#: 절대 상한 — 상위글 사진 중간값 21장 + Yeti 수집 속도(20초/장) 실측 근거
HARD_MAX = 22
#: 문단당 사진 상한 — 이 값을 넘기면 마커가 붙는다(실측: 1.32에서 9곳 뭉침)
PER_PARA = 0.7
#: 글자당 사진 — 문단 수를 아직 모를 때(생성 전) 쓰는 대용 기준.
#: 어제 발행글이 199자당 1장(뭉침 1곳), 상위글 중간값은 84자당 1장이지만 그 글들은 1,757자로 짧다.
CHARS_PER_PHOTO = 200
#: 최소 — 사진이 너무 적으면 체류가 안 는다(상위글 최소 3장)
MIN_PHOTOS = 3


def cap_for(n_uploaded: int, target_chars: int = 0, n_paragraphs: int = 0,
            tenant_id: str = "") -> int:
    """본문에 넣을 사진 수. 업로드 수를 넘지 않는다.

    문단 수를 알면(생성 후 재배치) 그것을 쓰고, 모르면(생성 전) 목표 글자수로 어림한다.

    ★ 2026-08-17 — PER_PARA는 이제 **학습 에이전트가 정한다**(agents/params).
      이 값을 내가 손으로 세 번 고쳤고 세 번 다 틀렸다(0.7 → 뭉침 5곳 → 사진 3장).
      가게마다 글 길이·문단 리듬이 달라 하나의 상수로 맞을 수가 없다.
      저장소가 비었거나 죽었으면 아래 코드 기본값으로 그대로 돈다(자율 계층이 생성을 막지 않는다).
    """
    n = max(0, int(n_uploaded or 0))
    if n <= MIN_PHOTOS:
        return n                       # 적을 땐 그대로 — 자를 게 없다
    per = PER_PARA
    if tenant_id:
        try:
            from app.agents import params as _pm
            per = float(_pm.get(f"photo:{tenant_id}", "per_para", PER_PARA))
        except Exception:
            per = PER_PARA
    limits = [n, HARD_MAX]
    if n_paragraphs:
        limits.append(max(MIN_PHOTOS, int(n_paragraphs * per)))
    elif target_chars:
        limits.append(max(MIN_PHOTOS, target_chars // CHARS_PER_PHOTO))
    return max(MIN_PHOTOS, min(limits))


def placement_audit(body: str, note: str, n: int) -> dict:
    """배치 검증 — 각 [사진N]이 **그 사진 내용과 관련 있는 문단** 옆에 갔는가.

    왜 필요한가(2026-08-17 사장님 지적: "글 내용과 정반대로 가면 안 된다"):
      의미 배치(_semantic_photo_placement)는 토큰 겹침으로 자리를 정하는데,
      **겹치는 토큰이 0이어도 남은 자리에 그냥 넣는다.** 실측에서 9장 중 2장이 어긋났다 —
      센터 콘솔 '가죽' 코팅 사진 옆에 '세차·자외선·도장' 문장이 붙었다.
      배치는 확률이고 검증이 보장이다(게이트 없는 표면은 만들지 않는다).

    판정: 그 사진 묘사의 핵심어가 마커 앞뒤 문맥에 하나도 없으면 '어긋남'.
    """
    import re

    from app.services import photodesc as _pd

    def _toks(s: str) -> set:
        return {t for t in re.split(r"[^가-힣A-Za-z0-9]+", s or "") if len(t) >= 2}

    rows, miss = [], []
    for m in re.finditer(r"\[사진(\d+)\]", body or ""):
        i = int(m.group(1))
        desc = _pd.best_line(note or "", i) or ""
        if not desc:
            rows.append({"n": i, "hit": None, "why": "묘사 없음"})
            continue
        j = m.start()
        ctx = (body[max(0, j - 260):j] + body[m.end():m.end() + 120])
        overlap = _toks(desc) & _toks(ctx)
        ok = len(overlap) >= 1
        rows.append({"n": i, "hit": len(overlap), "words": sorted(overlap)[:4], "ok": ok})
        if not ok:
            miss.append(i)
    total = len([r for r in rows if r.get("hit") is not None])
    return {"rows": rows, "n_checked": total, "n_miss": len(miss), "miss": miss,
            "ok": not miss, "rate": (round(100 * (total - len(miss)) / total) if total else None)}


def reason(n_uploaded: int, capped: int) -> str:
    """왜 줄였는지 — 로그·payload에 남긴다(조용히 버리지 않는다)."""
    if capped >= n_uploaded:
        return ""
    return (f"본문 사진 {n_uploaded}장 → {capped}장으로 제한 "
            f"(문단당 1장 넘기면 마커가 붙어 체류시간이 오히려 줄어든다 · 실측)")
