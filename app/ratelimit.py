"""
경량 인메모리 레이트리밋 + TTL 캐시 — 무거운 의존성 없이(표준 라이브러리만).
인스턴스 1개(1 Replica) 전제라 프로세스 로컬 메모리로 충분. 스레드 안전.
용도: /api/rank-check 남용 방지(네이버 API 쿼터·과금 보호).
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_hits: dict[str, list[float]] = {}          # ip -> [요청 timestamp...]
_cache: dict[str, tuple[float, dict]] = {}   # key -> (저장시각, 결과)
_MAX_KEYS = 5000                             # 메모리 상한(초과 시 만료분 정리)


def allow(ip: str, per_min: int, per_hour: int) -> bool:
    """슬라이딩 윈도우 — 분/시간 한도 내면 True(요청 기록), 초과면 False."""
    now = time.time()
    with _lock:
        ts = [t for t in _hits.get(ip, []) if now - t < 3600]   # 1시간 밖은 폐기
        recent_min = sum(1 for t in ts if now - t < 60)
        if recent_min >= per_min or len(ts) >= per_hour:
            _hits[ip] = ts
            return False
        ts.append(now)
        _hits[ip] = ts
        if len(_hits) > _MAX_KEYS:                              # 오래된 IP 정리(메모리 누수 방지)
            for k in [k for k, v in _hits.items() if not any(now - t < 3600 for t in v)]:
                _hits.pop(k, None)
        return True


def cache_get(key: str, ttl: int):
    """TTL 내면 캐시 결과, 아니면 None."""
    now = time.time()
    with _lock:
        ent = _cache.get(key)
        if ent and now - ent[0] < ttl:
            return ent[1]
    return None


def cache_set(key: str, value: dict) -> None:
    now = time.time()
    with _lock:
        _cache[key] = (now, value)
        if len(_cache) > _MAX_KEYS:                             # 오래된(1h+) 항목 정리
            for k in [k for k, (t, _) in _cache.items() if now - t > 3600]:
                _cache.pop(k, None)
