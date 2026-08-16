"""사장님 말투 페르소나(A안) — **사장님이 직접 쓴 문장에서만** 목소리를 뽑는다.

왜 A안만 안전한가 (2026-08-16 검토):
  · A. 사장님 실제 말투 학습 → 실제 사장님 말이라 정직 게이트를 통과한다.
  · B. 가상 작가 페르소나("20년차 기사") → **인칭 위조**. 헌법 금지선.
  · C. 상위 블로거 문체 모방 → **내용 복제 변주**에 가깝다. 금지선.

★ 재료는 `owner_experience`(사장님이 직접 답한 문장)뿐이다.
  과거 발행 글은 쓰면 안 된다 — 그건 우리가 AI로 쓴 글이라, 학습하면
  **우리 AI 목소리를 우리가 다시 배우는 순환**이 된다. 사장님 말투가 아니다.

★ 표본이 얇으면 아무것도 하지 않는다. 빈약한 페르소나는 없는 것보다 나쁘다
  (근거 없는 말투를 지어내면 그 자체가 날조다).

업종 중립: 뽑는 것은 어미·호칭·자주 쓰는 표현이라는 **언어 특징**이지 업종어가 아니다.
"""
from __future__ import annotations

import re
from collections import Counter

MIN_SAMPLES = 2          # 답변 2건 미만이면 말투라고 부를 수 없다
MIN_CHARS = 180          # 총 글자가 이보다 적으면 표본이 아니다
TOP_ENDINGS = 3
TOP_HABITS = 3

#: 어미 추출 — 문장 끝 종결형. 언어 규칙만.
_END = re.compile(r"([가-힣]{2,5})[.!?]\s*$")
#: 습관어 후보에서 뺄 일반어(어느 글에나 나오는 말은 그 사람의 특징이 아니다)
_HABIT_STOP = {"그리고", "그래서", "하지만", "그런데", "이렇게", "저렇게", "있습니다", "합니다"}


def samples(tenant_id: str, limit: int = 40) -> list:
    """사장님이 직접 쓴 답변만. 실패하면 빈 목록(생성을 막지 않는다)."""
    from app import db
    try:
        with db._conn() as c:
            rows = c.execute(
                "SELECT answer FROM owner_experience WHERE tenant_id=? "
                "ORDER BY created_at DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [(r["answer"] or "").strip() for r in rows if (r["answer"] or "").strip()]
    except Exception:
        return []


def profile(tenant_id: str) -> dict:
    """말투 프로필 — {"n","chars","endings","habits","enough"}."""
    ss = samples(tenant_id)
    text = "\n".join(ss)
    chars = len(re.sub(r"\s", "", text))
    out = {"n": len(ss), "chars": chars, "endings": [], "habits": [],
           "enough": len(ss) >= MIN_SAMPLES and chars >= MIN_CHARS}
    if not out["enough"]:
        return out
    # ① 어미 — 이 사장님이 문장을 어떻게 끝내는가
    ends = Counter()
    for s in ss:
        for line in re.split(r"(?<=[.!?])\s+", s):
            m = _END.search(line.strip())
            if m:
                ends[m.group(1)] += 1
    out["endings"] = [w for w, _ in ends.most_common(TOP_ENDINGS)]
    # ② 습관어 — 2~3어절이 여러 답변에 걸쳐 반복되면 그 사람의 말버릇이다
    grams = Counter()
    for s in ss:
        ws = [w for w in re.split(r"\s+", s) if w]
        for n in (2, 3):
            for i in range(len(ws) - n + 1):
                g = " ".join(ws[i:i + n])
                if 4 <= len(g) <= 18 and not any(t in g for t in _HABIT_STOP):
                    grams[g] += 1
    # ★ 겹치는 조각 제거 — n-gram은 같은 구절을 여러 토막으로 낸다
    #   ('말씀을 제일' / '제일 많이' / '많이 하십니다'는 한 말버릇이다).
    #   긴 것을 먼저 잡고, 이미 잡은 것과 어절이 겹치면 버린다.
    picked: list = []
    for g, c in sorted(((g, c) for g, c in grams.items() if c >= 2),
                       key=lambda x: (-len(x[0].split()), -x[1], x[0])):
        gw = set(g.split())
        if any(gw & set(p.split()) for p in picked):
            continue
        picked.append(g)
        if len(picked) >= TOP_HABITS:
            break
    out["habits"] = picked
    return out


def directive(tenant_id: str) -> str:
    """생성 프롬프트에 넣을 말투 지시. 표본이 얇으면 빈 문자열(아무 말도 지어내지 않는다)."""
    p = profile(tenant_id)
    if not p["enough"]:
        return ""
    bits = []
    if p["endings"]:
        bits.append("문장을 이렇게 끝내신다: " + ", ".join(f"'…{e}'" for e in p["endings"]))
    if p["habits"]:
        bits.append("자주 쓰시는 말: " + ", ".join(f"'{h}'" for h in p["habits"]))
    if not bits:
        return ""
    return ("[사장님 말투 — 이 목소리로 써라]\n"
            "아래는 이 가게 사장님이 **직접 쓰신 문장**에서 뽑은 말투다. 이 목소리를 유지해라.\n"
            + "".join(f"· {b}\n" for b in bits)
            + "· 없는 말투를 지어내지 말고, 위 특징을 자연스럽게 살려라. "
              "억지로 매 문장에 끼워 넣으면 오히려 어색해진다.\n")
