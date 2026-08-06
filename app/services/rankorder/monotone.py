"""📈 단조성 분석 — 인자가 순위와 한 방향으로 움직이는가.

★ Spearman 순위상관을 쓴다(값의 크기가 아니라 순서만 본다 — 이상치에 안 흔들린다).
★ 업종 교차 필수: 한 업종의 상관은 그 업종 특성일 수 있다(오늘 tables 함정).
  여러 업종에서 **같은 방향**으로 나온 것만 후보로 올린다.
★ 상관≠인과 — '1위가 사진 많다'는 상관이다. 후보까지만 말한다(R5).
"""
from __future__ import annotations

import math

MIN_N = 6          # 한 업종에서 이만큼은 있어야 상관을 말한다
ALPHA = 0.05
MIN_INDUSTRIES = 2  # 교차 최소 업종 수

# ★ 2026-08-06 실측: at(수집 시각)이 rho=1.0으로 잡혔다.
#   1위부터 순서대로 수집했으니 수집 시각이 곧 순위다 — 인자가 아니라 **우리 절차의 흔적**이다.
#   이런 값을 그냥 두면 완벽한 순환 논리가 '가장 강한 인자'로 보고된다.
EXCLUDE_KEYS = ("at", "rank", "saved_at", "post", "at_utc")


def _ranks(xs: list) -> list:
    """동점은 평균 순위(Spearman 표준)."""
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            out[idx[k]] = avg
        i = j + 1
    return out


def spearman(a: list, b: list) -> tuple:
    """반환 (rho, p). 표본이 적으면 p는 보수적으로."""
    n = len(a)
    if n < 3 or n != len(b):
        return 0.0, 1.0
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    if da == 0 or db == 0:
        return 0.0, 1.0
    rho = num / (da * db)
    if abs(rho) >= 1.0:
        return rho, 0.0
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    p = math.erfc(abs(t) / math.sqrt(2))
    if n < 30:
        p = min(1.0, p * (1 + 6.0 / max(1.0, n - 2)))
    return rho, p


def analyze(rows: list, keys: list = None) -> dict:
    """업종별 순위-인자 상관 → 교차 공통 단조 인자."""
    rows = [r for r in (rows or []) if isinstance(r.get("rank"), int)]
    if not rows:
        return {"industries": {}, "candidates": [], "note": "표본 없음"}
    keys = keys or sorted({k for r in rows for k, v in r.items()
                           if isinstance(v, (int, float)) and k not in EXCLUDE_KEYS
                           and not isinstance(v, bool)})
    per = {}
    for ind in sorted({r.get("industry") or "?" for r in rows}):
        sub = [r for r in rows if (r.get("industry") or "?") == ind]
        if len(sub) < MIN_N:
            per[ind] = {"n": len(sub), "note": "표본 부족(미확정)", "factors": {}}
            continue
        fac = {}
        for k in keys:
            pairs = [(r["rank"], float(r[k])) for r in sub
                     if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
            if len(pairs) < MIN_N:
                continue
            rho, p = spearman([x for x, _ in pairs], [y for _, y in pairs])
            fac[k] = {"rho": round(rho, 3), "p": round(p, 4), "n": len(pairs),
                      "sig": p < ALPHA}
        per[ind] = {"n": len(sub), "factors": fac}
    # 교차 — 여러 업종에서 같은 방향으로 유의해야 후보다
    cand = []
    for k in keys:
        hits = [(i, d["factors"][k]) for i, d in per.items()
                if k in (d.get("factors") or {}) and d["factors"][k]["sig"]]
        if len(hits) < MIN_INDUSTRIES:
            continue
        signs = {1 if h[1]["rho"] > 0 else -1 for h in hits}
        if len(signs) != 1:
            continue                     # 방향이 엇갈리면 인자가 아니다
        cand.append({"factor": k, "industries": [h[0] for h in hits],
                     "rho_mean": round(sum(h[1]["rho"] for h in hits) / len(hits), 3),
                     "direction": ("순위 낮을수록 큼(1위가 큼)" if list(signs)[0] < 0
                                   else "순위 높을수록 큼(1위가 작음)"),
                     "verdict": "서열 인자 후보"})
    return {"industries": per, "candidates": cand, "n_candidates": len(cand),
            "note": ("여러 업종에서 같은 방향으로 유의한 것만 후보다. "
                     "상관이지 인과가 아니다. 표본 부족 업종은 미확정.")}
