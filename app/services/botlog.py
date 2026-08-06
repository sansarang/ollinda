"""🤖 크롤러 방문 기록 — 글 밖 신호 중 우리가 직접 잴 수 있는 유일한 것.

★ 한계를 먼저 못 박는다: 네이버 블로그(주안모터스·루마)에 오는 Yeti 로그는 **네이버 서버에 있다.
  우리는 못 본다.** 우리가 보는 것은 자체 도메인(ollinda.kr)에 오는 방문뿐이고,
  거기로 Yeti가 오는 경로는 블로그 본문에 심은 링크(/r/*)를 따라오는 것 정도다.

★ UA 위조 주의(R2): UA가 'Yeti'라고 주장해도 위조 가능하다.
  진짜 Yeti는 IP 125.209.192.0/18 대역이다. 대역 밖이면 '자칭 Yeti(미검증)'로 분리한다.
★ 봇 종류 구분(R3): 텍스트 수집용과 크롬 렌더링용 UA를 나눠 센다 —
  단순 색인인지 JS 렌더까지 하는지가 갈린다.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time

from app.services.immune import data_root as _dr

LOG_PATH = os.environ.get("SHOPCAST_BOTLOG", "") or os.path.join(_dr(), "botlog.jsonl")

# 진짜 Yeti IP 대역(네이버 공식 고지). 이 밖은 자칭이다.
YETI_NETS = [ipaddress.ip_network("125.209.192.0/18")]
_YETI = re.compile(r"Yeti", re.I)
_CHROME_RENDER = re.compile(r"Chrome/\d+", re.I)      # 렌더용 변종은 크롬 토큰을 달고 온다
_IMG = re.compile(r"image|img", re.I)
KNOWN_BOTS = ("Yeti", "Googlebot", "bingbot", "Daum", "facebookexternalhit",
              "Twitterbot", "AhrefsBot", "SemrushBot", "GPTBot", "ClaudeBot",
              "PerplexityBot", "Bytespider", "Applebot")
_BOT = re.compile("|".join(KNOWN_BOTS), re.I)


def client_ip(request) -> str:
    """프록시 뒤라 X-Forwarded-For의 첫 값이 실제 클라이언트다(Railway·CDN 공통)."""
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        return xff
    return getattr(getattr(request, "client", None), "host", "") or ""


def verify_yeti(ip: str) -> bool:
    """진짜 Yeti인가 — IP 대역으로만 판정한다(UA는 위조 가능)."""
    try:
        a = ipaddress.ip_address(ip)
    except Exception:
        return False
    return any(a in n for n in YETI_NETS)


def ua_kind(ua: str) -> str:
    """UA 종류 — 텍스트 수집용/렌더용/이미지용을 나눈다(R3)."""
    if _IMG.search(ua or ""):
        return "image"
    if _CHROME_RENDER.search(ua or ""):
        return "render"
    return "text"


def record(request, status: int) -> None:
    """봇 방문 1건 기록. 사람 방문은 남기지 않는다(개인정보·용량).

    ★ 원본 라인을 그대로 남긴다(R4) — 집계는 나중에 다시 할 수 있어야 한다.
    """
    ua = request.headers.get("user-agent") or ""
    if not _BOT.search(ua):
        return
    ip = client_ip(request)
    is_yeti = bool(_YETI.search(ua))
    row = {
        "at": int(time.time()), "ip": ip, "ua": ua[:300],
        "path": str(request.url.path)[:200], "status": status,
        "bot": (_BOT.search(ua).group(0) if _BOT.search(ua) else ""),
        "is_yeti_ua": is_yeti,
        # ★ UA만 믿지 않는다 — 대역 검증 결과를 함께 남긴다
        "yeti_verified": (verify_yeti(ip) if is_yeti else None),
        "ua_kind": ua_kind(ua),
    }
    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass                                    # 기록 실패가 서비스를 막지 않는다


def load(limit: int = 5000) -> list:
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()][-limit:]
    except Exception:
        return []


def summary(rows: list = None) -> dict:
    """집계 — 진짜/자칭 분리, UA 종류별, URL별, 응답 코드별."""
    rows = rows if rows is not None else load()
    yeti = [r for r in rows if r.get("is_yeti_ua")]
    real = [r for r in yeti if r.get("yeti_verified")]
    fake = [r for r in yeti if not r.get("yeti_verified")]

    def _cnt(xs, k):
        d = {}
        for x in xs:
            d[str(x.get(k))] = d.get(str(x.get(k)), 0) + 1
        return dict(sorted(d.items(), key=lambda i: -i[1])[:20])

    days = {}
    for r in real:
        d = time.strftime("%Y-%m-%d", time.gmtime(r.get("at", 0) + 9 * 3600))
        days[d] = days.get(d, 0) + 1
    return {
        "total_bot_hits": len(rows),
        "yeti_ua": len(yeti), "yeti_verified": len(real), "yeti_unverified": len(fake),
        "by_bot": _cnt(rows, "bot"),
        "yeti_by_kind": _cnt(real, "ua_kind"),
        "yeti_by_path": _cnt(real, "path"),
        "yeti_by_status": _cnt(real, "status"),
        "yeti_daily": dict(sorted(days.items())),
        "robots_txt_hits": sum(1 for r in rows if r.get("path") == "/robots.txt"),
        "note": ("네이버 블로그에 오는 Yeti 로그는 네이버 서버에 있어 우리가 못 본다. "
                 "여기 잡히는 것은 자체 도메인(ollinda.kr) 방문뿐이다."),
    }
