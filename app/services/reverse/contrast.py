"""📊 대조 분석 — 상관을 인자 후보로, 인과는 말하지 않는다(R5).

★ "상위 글에 표가 많다"는 상관이다. "표 때문에 뽑혔다"는 아직 아니다.
  대조군과 비교해 유의차가 난 것만 '인자 후보'로 채택하고, 아니면 '유의차 없음'으로 적는다.
★ 표본이 적으면 '표본 부족(미확정)'이다. 적은 표본에서 나온 차이를 인자라고 부르면
  그게 날조다 — 오늘 9.1%를 전체로 늘리지 않은 것과 같은 규율이다.
★ 정보성 질의와 상업성 질의를 섞지 않는다. 지면마다 승리 공식이 다를 수 있다.
"""
from __future__ import annotations

import math

MIN_N = 5                  # 군당 최소 표본 — 이 아래는 미확정
ALPHA = 0.05               # 유의수준(양측)


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _welch(a: list, b: list) -> tuple:
    """Welch t — 분산이 다른 두 군을 비교한다. 반환 (t, df)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0, 0.0
    va, vb = _var(a), _var(b)
    se2 = va / na + vb / nb
    if se2 <= 0:
        return 0.0, 0.0
    t = (_mean(a) - _mean(b)) / math.sqrt(se2)
    df_num = se2 ** 2
    df_den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    return t, (df_num / df_den if df_den > 0 else 0.0)


def _p_two_sided(t: float, df: float) -> float:
    """t분포 양측 p — 정규 근사(df가 작을 때는 보수적으로 읽는다)."""
    if df <= 0:
        return 1.0
    x = abs(t)
    # 표준정규 생존함수 근사(Abramowitz-Stegun)
    p = math.erfc(x / math.sqrt(2))
    if df < 30:                       # 자유도가 작으면 p를 키워 보수적으로
        p = min(1.0, p * (1 + 6.0 / max(1.0, df)))
    return max(0.0, min(1.0, p))


def compare(hi: list, lo: list, keys: list = None) -> list:
    """상위군 vs 하위군의 인자별 대조. 유의차가 난 것만 후보로 표시한다."""
    if not hi or not lo:
        return []
    keys = keys or sorted({k for r in (hi + lo) for k, v in r.items()
                           if isinstance(v, (int, float, bool))})
    out = []
    small = len(hi) < MIN_N or len(lo) < MIN_N
    for k in keys:
        a = [float(r[k]) for r in hi if isinstance(r.get(k), (int, float, bool))]
        b = [float(r[k]) for r in lo if isinstance(r.get(k), (int, float, bool))]
        if len(a) < 2 or len(b) < 2:
            continue
        t, df = _welch(a, b)
        p = _p_two_sided(t, df)
        ma, mb = _mean(a), _mean(b)
        sig = (p < ALPHA) and not small
        out.append({
            "factor": k, "hi_mean": round(ma, 2), "lo_mean": round(mb, 2),
            "diff": round(ma - mb, 2), "n_hi": len(a), "n_lo": len(b),
            "p": round(p, 4),
            # ★ 표본이 적으면 유의여도 '미확정'이다. 확신 없는 것을 확정처럼 쓰지 않는다.
            "verdict": ("인자 후보" if sig else
                        ("표본 부족(미확정)" if small else "유의차 없음")),
        })
    out.sort(key=lambda r: (r["verdict"] != "인자 후보", r["p"]))
    return out


def split_by(rows: list, key: str, hi_pred) -> tuple:
    """조건으로 두 군을 가른다. 라벨 정의는 부르는 쪽이 명시한다(R6: 라벨을 섞지 않는다)."""
    hi = [r for r in rows if hi_pred(r)]
    lo = [r for r in rows if not hi_pred(r)]
    return hi, lo


def summarize(rows: list) -> dict:
    """대조 결과 한 장 — 미확정은 미확정으로 남긴다."""
    cand = [r for r in rows if r["verdict"] == "인자 후보"]
    return {"factors": rows, "candidates": cand,
            "n_candidates": len(cand),
            "note": ("표본이 군당 %d개 미만이면 유의여도 미확정으로 둔다" % MIN_N)}
