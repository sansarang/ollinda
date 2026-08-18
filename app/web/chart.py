"""📈 그래프 — 인라인 SVG만. 외부 라이브러리 0.

2026-08-18 사장님 지시:
  "가게를 누르고 발행글을 눌렀을때 조사한 항목들이 그래프로 나와야 한다."

왜 SVG 직접 그리는가:
  차트 라이브러리는 CDN 스크립트를 부른다 — 첫 화면이 그만큼 늦어지고,
  사장님이 이미 "내 콘텐츠가 안 열린다"고 지적한 화면이다(2026-08-18).
  순위 점 몇 개를 그리자고 200KB를 받을 이유가 없다.

★ 순위는 **낮을수록 좋다** — y축을 뒤집지 않으면 그래프가 진실과 정반대로 읽힌다.
  1위가 바닥에 깔리고 30위가 꼭대기에 뜨면, 떨어지는 그래프가 '성장'으로 보인다.
  이건 측정 허위와 같은 계열이라 `rank_line()`은 항상 반전해서 그린다.

★ 값이 없으면 그리지 않는다(침묵 폴백 금지).
  점이 0개면 빈 문자열을 돌려주고, 부르는 쪽이 '아직 자료가 없다'고 말하게 한다.
  없는 자료를 0으로 채워 평평한 선을 그리는 것은 날조다.
"""
from __future__ import annotations

from html import escape as _esc

#: 네이버 검색 결과 한 페이지 = 30위권. 그 밖은 '순위 밖'이라 선에 올리지 않는다.
RANK_FLOOR = 30


def _pts(vals: list[float], w: int, h: int, pad: int,
         vmin: float, vmax: float, invert: bool) -> list[tuple[float, float]]:
    """값 목록 → 좌표. invert=True면 작은 값이 위로 간다(순위용)."""
    n = len(vals)
    span = (vmax - vmin) or 1.0
    iw, ih = w - pad * 2, h - pad * 2
    out = []
    for i, v in enumerate(vals):
        x = pad + (iw * i / (n - 1) if n > 1 else iw / 2)
        frac = (v - vmin) / span
        y = pad + (frac * ih if invert else (1 - frac) * ih)
        out.append((round(x, 1), round(y, 1)))
    return out


def rank_line(history: list[dict], keyword: str = "", w: int = 320, h: int = 110) -> str:
    """순위 추이 선그래프. history = db.rank_history() 결과(오래된→최신).

    자료가 없거나 순위가 한 번도 안 잡혔으면 **빈 문자열**을 돌려준다.
    """
    pts_src = [(h_.get("rank"), (h_.get("checked_at") or "")[:10])
               for h_ in (history or []) if isinstance(h_.get("rank"), int)]
    if not pts_src:
        return ""
    vals = [min(r, RANK_FLOOR) for r, _ in pts_src]
    dates = [d for _, d in pts_src]
    pad = 14
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:                      # 변화가 없으면 선이 화면 가운데 오도록 여유를 준다
        vmin, vmax = max(1, vmin - 2), vmax + 2
    pts = _pts([float(v) for v in vals], w, h, pad, float(vmin), float(vmax), invert=True)
    poly = " ".join(f"{x},{y}" for x, y in pts)
    cur, first = vals[-1], vals[0]
    # 순위는 작아지는 게 좋아진 것 — 색도 그 방향으로 읽는다.
    up = cur < first
    color = "#059669" if up else ("#64748b" if cur == first else "#e11d48")
    dots = "".join(f"<circle cx='{x}' cy='{y}' r='2.5' fill='{color}'/>" for x, y in pts)
    last_x, last_y = pts[-1]
    area = (f"<polygon points='{poly} {last_x},{h - pad} {pts[0][0]},{h - pad}' "
            f"fill='{color}' opacity='0.07'/>")
    cap = f"{keyword} · " if keyword else ""
    delta = ("" if cur == first else
             f" <tspan fill='{color}'>({first}위 → {cur}위)</tspan>")
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}' role='img' "
            f"aria-label='{_esc(cap)}순위 추이' style='overflow:visible'>"
            f"{area}<polyline points='{poly}' fill='none' stroke='{color}' "
            f"stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>{dots}"
            f"<text x='{last_x}' y='{max(10, last_y - 8)}' font-size='11' font-weight='700' "
            f"fill='{color}' text-anchor='end'>{cur}위</text>"
            f"<text x='{pad}' y='{h - 2}' font-size='9' fill='#94a3b8'>{_esc(dates[0])}</text>"
            f"<text x='{w - pad}' y='{h - 2}' font-size='9' fill='#94a3b8' "
            f"text-anchor='end'>{_esc(dates[-1])}</text></svg>"
            f"<div class='text-xs text-slate-500 mt-1'>{_esc(cap)}"
            f"<b class='text-slate-800'>{cur}위</b>{delta}</div>")


def bars(rows: list[tuple], unit: str = "", w: int = 320) -> str:
    """가로 막대 — [(라벨, 값), ...]. 값이 전부 0이거나 비면 빈 문자열."""
    rows = [(str(a), b) for a, b in (rows or []) if isinstance(b, (int, float))]
    if not rows or not any(v for _, v in rows):
        return ""
    top = max(v for _, v in rows) or 1
    out = ""
    for lab, v in rows:
        pct = max(2.0, round(100.0 * v / top, 1))
        out += ("<div class='flex items-center gap-2 py-1'>"
                f"<span class='text-xs text-slate-500 w-24 truncate flex-shrink-0'>{_esc(lab)}</span>"
                "<span class='flex-1 h-2 bg-slate-100 rounded-full overflow-hidden'>"
                f"<span class='block h-full bg-indigo-500 rounded-full' style='width:{pct}%'></span></span>"
                f"<span class='text-xs font-bold text-slate-700 w-16 text-right flex-shrink-0'>"
                f"{v:,}{_esc(unit)}</span></div>")
    return out


def empty(reason: str) -> str:
    """자료가 없을 때 — **왜 없는지**를 적는다(빈칸 + 명시 사유, 날조 금지)."""
    return (f"<div class='text-xs text-slate-400 bg-slate-50 rounded-xl px-3 py-4 text-center'>"
            f"{_esc(reason)}</div>")
