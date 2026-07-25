"""
gowatch 소비 클라이언트 — 본체가 gowatch(관측-적응 워커)를 HTTP로만 소비(경계 격리).

원칙: 본체는 gowatch DB를 직접 만지지 않는다(PG 드라이버 없음). readview와 대칭으로,
gowatch가 노출한 API만 쓴다 — GET /adaptations(큐 조회)·POST /adaptations/{id}/status(상태전이
요청, 쓰기는 gowatch가 자기 테이블에)·GET /observations(D3)·GET /health(생존체크).
gowatch 미배포/불통이면 조용히 빈 결과(대시보드는 배너로 안내) — 본체 파이프라인 무영향.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT = 8


def _base() -> str:
    return (os.environ.get("GOWATCH_URL") or "").rstrip("/")


def _token() -> str:
    return os.environ.get("GOWATCH_TOKEN") or ""


def configured() -> bool:
    return bool(_base() and _token())


def _get(path: str) -> "dict | None":
    if not configured():
        return None
    req = urllib.request.Request(_base() + path, headers={"Authorization": "Bearer " + _token()})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def health() -> "dict | None":
    """gowatch /health(공개) — 워커 생존·수집 stale·큐 상태. 불통이면 None(D2 배너 경로)."""
    base = _base()
    if not base:
        return None
    try:
        with urllib.request.urlopen(base + "/health", timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def list_adaptations(status: str = "queued", limit: int = 50) -> list[dict]:
    """소비할 적응 큐 조회(우선순위순). 실패/미배포면 []."""
    d = _get(f"/adaptations?status={urllib.parse.quote(status)}&limit={int(limit)}")
    if not d or not d.get("ok"):
        return []
    return d.get("adaptations") or []


def list_observations(tenant: str = "", limit: int = 200) -> list[dict]:
    """발행 글별 최신 관측(D3 관측 현황). 실패/미배포면 []."""
    q = f"/observations?limit={int(limit)}"
    if tenant:
        q += "&tenant=" + urllib.parse.quote(tenant)
    d = _get(q)
    if not d or not d.get("ok"):
        return []
    return d.get("observations") or []


def set_status(adaptation_id: str, status: str) -> bool:
    """상태전이 요청(consumed/proposed/skipped) — gowatch가 자기 테이블에 쓴다. 성공 여부."""
    if not configured():
        return False
    body = json.dumps({"status": status}).encode("utf-8")
    req = urllib.request.Request(
        _base() + "/adaptations/" + urllib.parse.quote(adaptation_id) + "/status",
        data=body, method="POST",
        headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
            return bool(d.get("ok"))
    except Exception:
        return False


import urllib.parse  # noqa: E402  (quote/quote 사용 — 하단 배치로 순환 없음)
