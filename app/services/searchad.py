"""
네이버 검색광고(SearchAd) API — 키워드 도구(연관키워드 + 월간 검색량).
상위노출 롱테일(검색량 500~5,000 = 경쟁↓·전환↑)을 '실측'으로 골라준다.
env: NAVER_SEARCHAD_API_KEY(액세스라이선스), NAVER_SEARCHAD_SECRET(비밀키), NAVER_SEARCHAD_CUSTOMER(고객ID).
키 없으면 [] 반환 → graceful(기존 규칙 기반 키워드로 동작).
docs: https://naver.github.io/searchad-apidoc/
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import requests

BASE = "https://api.searchad.naver.com"


def configured() -> bool:
    return bool(os.environ.get("NAVER_SEARCHAD_API_KEY")
               and os.environ.get("NAVER_SEARCHAD_SECRET")
               and os.environ.get("NAVER_SEARCHAD_CUSTOMER"))


def _sign(ts: str, method: str, path: str, secret: str) -> str:
    msg = f"{ts}.{method}.{path}"
    dig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(dig).decode("utf-8")


def _to_int(v) -> int:
    if isinstance(v, int):
        return v
    s = str(v).replace("<", "").replace(",", "").strip()   # "< 10" 같은 값 처리
    try:
        return int(float(s))
    except Exception:
        return 0


def keyword_volumes(hints: list[str], limit: int = 40) -> list[dict]:
    """힌트 키워드 → [{keyword, pc, mobile, total, comp}] (월간 검색량). 무키/실패 []."""
    hints = [h.replace(" ", "") for h in (hints or []) if h and h.strip()]
    if not (configured() and hints):
        return []
    key = os.environ["NAVER_SEARCHAD_API_KEY"]
    sec = os.environ["NAVER_SEARCHAD_SECRET"]
    cid = os.environ["NAVER_SEARCHAD_CUSTOMER"]
    path = "/keywordstool"
    ts = str(int(time.time() * 1000))
    headers = {"X-Timestamp": ts, "X-API-KEY": key, "X-Customer": str(cid),
               "X-Signature": _sign(ts, "GET", path, sec)}
    try:
        r = requests.get(BASE + path, params={"hintKeywords": ",".join(hints[:5]), "showDetail": "1"},
                         headers=headers, timeout=8)
        if r.status_code != 200:
            return []
        out = []
        for it in r.json().get("keywordList", [])[:limit]:
            pc = _to_int(it.get("monthlyPcQcCnt", 0))
            mo = _to_int(it.get("monthlyMobileQcCnt", 0))
            out.append({"keyword": (it.get("relKeyword") or "").strip(),
                        "pc": pc, "mobile": mo, "total": pc + mo,
                        "comp": it.get("compIdx", "")})
        return out
    except Exception:
        return []


def _relevant(kw: str, hints: list[str]) -> bool:
    """힌트와 2글자 이상 겹치는 키워드만 = 무관한 연관어(직업전문학교 등) 노이즈 제거."""
    kw = kw.replace(" ", "")
    for h in hints:
        h = h.replace(" ", "")
        for i in range(len(h) - 1):
            if h[i:i + 2] in kw:
                return True
    return False


def sweet_spot_keywords(hints: list[str], lo: int = 500, hi: int = 5000, limit: int = 8) -> list[str]:
    """검색량 500~5,000 롱테일 우선(경쟁↓·전환↑) → 그 밖은 후순위. 힌트와 무관한 연관어는 제외."""
    vols = [v for v in keyword_volumes(hints, limit=80) if _relevant(v["keyword"], hints)]
    if not vols:
        return []
    inzone = sorted([v for v in vols if lo <= v["total"] <= hi], key=lambda v: -v["total"])
    high = sorted([v for v in vols if v["total"] > hi], key=lambda v: v["total"])      # 너무 큰 건(경쟁↑) 뒤로
    low = sorted([v for v in vols if 0 < v["total"] < lo], key=lambda v: -v["total"])
    ordered = inzone + high + low
    seen, out = set(), []
    for v in ordered:
        k = v["keyword"]
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:limit]
