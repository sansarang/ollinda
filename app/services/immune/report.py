"""📊 면역계 보고 — 배우고 있다는 증거만 남긴다.

★ 지표에는 분모가 필수다(R5). '사용자 발견 버그 N건'은 아무 뜻이 없다.
  **변경 커밋 100건당** 사장님이 먼저 발견한 버그 수 — 이 수치의 감소만이 증거다.
★ 기준선이 추측 위에 서지 않게, 확정할 수 없는 분자는 '추정(미확정)'으로 표기한다.
"""
from __future__ import annotations

import subprocess
import time

from app.services.immune import ledger as _led


def _count_commits(since: str, until: str) -> int:
    try:
        out = subprocess.run(["git", "rev-list", "--count", f"--since={since}",
                              f"--until={until}", "HEAD"],
                             capture_output=True, text=True, timeout=60).stdout
        return int((out or "0").strip() or 0)
    except Exception:
        return 0


def monthly(rows: list = None, months: int = 3) -> list:
    """월별 [변경 커밋 수 / 사용자 발견 / 시스템 발견 / 100커밋당 사용자 발견]."""
    rows = rows if rows is not None else _led.read()
    now = time.gmtime()
    out = []
    y, m = now.tm_year, now.tm_mon
    for _ in range(months):
        s = f"{y:04d}-{m:02d}-01"
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        e = f"{ny:04d}-{nm:02d}-01"
        n_commit = _count_commits(s, e)
        lo = time.mktime((y, m, 1, 0, 0, 0, 0, 0, 0))
        hi = time.mktime((ny, nm, 1, 0, 0, 0, 0, 0, 0))
        inb = [r for r in rows if lo <= (r.get("at") or 0) < hi]
        u = sum(1 for r in inb if r.get("found_by") == "사용자")
        sy = sum(1 for r in inb if r.get("found_by") == "시스템")
        un = sum(1 for r in inb if r.get("found_by") == "미상")
        out.append({"month": f"{y:04d}-{m:02d}", "commits": n_commit,
                    "user_found": u, "system_found": sy, "unknown": un,
                    "per100": round(u * 100.0 / n_commit, 2) if n_commit else None,
                    "note": ("발견 주체 미상 %d건 — 이 수치는 하한이다" % un) if un else ""})
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


# 착수 시점 기준선 — 추정임을 못 박는다(사장님 착수 결정)
BASELINE_NOTE = {
    "at": "2026-08-05",
    "numerator_estimate": 4,
    "items": ["캡션 품질(실물 판정)", "관련글 링크 클릭 불가", "완성 후 결과 화면 이동 실패",
              "주안모터스 62점"],
    "confidence": "추정(미확정)",
    "why": "커밋 트레일러 규약 이전이라 발견 주체를 사후에 확정할 수 없다. "
           "기준선은 추측 위에 서지 않게 이렇게 표기하고, 확정 집계는 트레일러 시행 이후부터다.",
}
