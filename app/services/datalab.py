"""
📈 네이버 데이터랩 검색어트렌드 — 키워드 '상승 추세' 신호(2026-08-01 사장님 승인 ②).

상위 블로거 루틴: "조회수가 오르는 중인 키워드"를 선점 — 글이 자리 잡을 때쯤 수요가 도착한다.
최근 12주 주간 시계열에서 (최근 4주 평균 ÷ 이전 8주 평균 - 1) = 성장률.

공식 API(openapi.naver.com/v1/datalab/search) — place.py와 같은 NAVER_CLIENT_ID/SECRET.
※ 네이버 개발자센터 앱에 '데이터랩(검색어트렌드)' 사용 설정이 있어야 함 — 미설정이면
  401/403이 오고 조용히 중립(None) 반환(선정 로직은 기존 그대로 동작).
쿼터 1,000콜/일 — 요청당 키워드 5개 묶음 + 24h 캐시로 보호. 실패는 전부 중립.
"""
from __future__ import annotations

import logging
import os
import time

import requests

_log = logging.getLogger("shopcast.datalab")

_CACHE: dict = {}          # kw → (ts, growth|None)
_DISABLED_UNTIL = [0.0]    # 401/403(미설정) 시 1시간 침묵 — 헛콜 방지


def configured() -> bool:
    return bool(os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET"))


def _fetch_batch(keywords: list[str]) -> dict:
    """키워드 최대 5개 → {kw: growth(비율, 0.2=+20%)|None}. 실패 전원 None."""
    from datetime import date, timedelta
    end = date.today() - timedelta(days=1)
    start = end - timedelta(weeks=12)
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "week",
            "keywordGroups": [{"groupName": k, "keywords": [k]} for k in keywords[:5]]}
    try:
        r = requests.post("https://openapi.naver.com/v1/datalab/search", json=body,
                          headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                                   "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"],
                                   "Content-Type": "application/json"}, timeout=10)
        if r.status_code in (401, 403, 404):
            _DISABLED_UNTIL[0] = time.time() + 3600
            _log.warning("[datalab] API 미설정/권한 없음(%s) — 1시간 침묵. "
                         "네이버 개발자센터 앱에 '데이터랩(검색어트렌드)' 추가 필요", r.status_code)
            return {k: None for k in keywords}
        if r.status_code != 200:
            return {k: None for k in keywords}
        out = {}
        for g in r.json().get("results", []):
            pts = [p.get("ratio") or 0 for p in (g.get("data") or [])]
            if len(pts) >= 8:
                recent = sum(pts[-4:]) / 4
                base = sum(pts[:-4]) / max(1, len(pts) - 4)
                out[g.get("title")] = round((recent / base) - 1, 3) if base > 0 else None
            else:
                out[g.get("title")] = None             # 데이터 빈약(신조어 등) = 중립
        return {k: out.get(k) for k in keywords}
    except Exception:
        return {k: None for k in keywords}


def growth(keywords: list[str]) -> dict:
    """{kw: 성장률|None} — 24h 캐시, 5개 단위 배치. 미설정·실패는 None(중립)."""
    now = time.time()
    if not configured() or now < _DISABLED_UNTIL[0]:
        return {k: None for k in keywords}
    out, todo = {}, []
    for k in keywords:
        hit = _CACHE.get(k)
        if hit and now - hit[0] < 86400:
            out[k] = hit[1]
        else:
            todo.append(k)
    for i in range(0, len(todo), 5):
        batch = _fetch_batch(todo[i:i + 5])
        for k, v in batch.items():
            if k:
                out[k] = v
                _CACHE[k] = (now, v)
    if len(_CACHE) > 3000:
        _CACHE.pop(next(iter(_CACHE)), None)
    return out
