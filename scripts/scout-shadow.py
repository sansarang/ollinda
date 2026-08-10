"""섀도 정찰 — Railway(데이터센터 IP)에서 네이버 지면 스캔이 맥(주거 IP)과 같은 결과를
주는지 검증하는 워커 (2026-08-10 사장님 '전부 구현' 승인).

★ 섀도 원칙(지면 지도 오염 금지): blocks-ingest에 POST하지 않는다 — 결과는 로그로만.
  맥 결과(kw_blocks)와 대조해 [차단 없음 · 블록 구성 일치]가 실측되기 전까지 전환 금지.
  데이터센터 IP는 네이버가 다른 결과·캡차를 줄 수 있다 — 검증 없는 전환은 지도 왜곡이다.
★ 수집 규율은 본체와 동일(scout.session 하나): 로그인 0 · 공개 결과만 · 사람 속도 ·
  캡차 신호 시 즉시 중단(재시도 금지).

env: OLLINDA_URL(기본 https://ollinda.kr), OLLINDA_ADMIN(admin:pass),
     SHADOW_KW_CAP(총 키워드 상한, 기본 6)
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SERVER = os.environ.get("OLLINDA_URL", "https://ollinda.kr").rstrip("/")
AUTH = os.environ.get("OLLINDA_ADMIN", "")
CAP = int(os.environ.get("SHADOW_KW_CAP", "6"))


def _get(path: str) -> dict:
    req = urllib.request.Request(SERVER + path)
    req.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def main() -> None:
    if not AUTH:
        print(json.dumps({"shadow": "abort", "reason": "OLLINDA_ADMIN 없음"}))
        return
    plan = _get("/admin/scout-plan?limit=3")
    shops = plan.get("shops") or []
    if not shops:
        print(json.dumps({"shadow": "empty", "reason": "정찰 계획 없음"}))
        return
    from app.services.scout import blocks
    total, blocked, no_blocks = 0, 0, 0
    for s in shops:
        if total >= CAP:
            break
        kws = (s.get("keywords") or [])[: max(1, CAP - total)]
        if not kws:
            continue
        rows = blocks.scan(kws, my_blog=(s.get("blog_id") or ""))
        for r in rows:
            total += 1
            if r.get("error"):
                blocked += 1
            elif not r.get("blocks"):
                no_blocks += 1
            # 한 줄 JSON 로그 — Railway 로그에서 맥 결과와 키워드 단위 대조용
            print(json.dumps({"shadow": "row", "tenant": s.get("tenant"),
                              "keyword": r.get("keyword"),
                              "blocks": list(r.get("blocks") or [])[:12],
                              "blog_blocks": r.get("blog_blocks"),
                              "my_visible": r.get("my_visible"),
                              "error": r.get("error")}, ensure_ascii=False), flush=True)
    verdict = "suspect_blocked" if (total and blocked + no_blocks >= total) else \
              ("partial" if blocked else "ok")
    print(json.dumps({"shadow": "summary", "total": total, "errors": blocked,
                      "empty": no_blocks, "verdict": verdict,
                      "note": "verdict=ok가 수회 반복 + 맥 kw_blocks와 블록 일치 확인 전까지 전환 금지"},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
