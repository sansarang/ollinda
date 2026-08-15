"""🤖 크롤러 방문 기록 — 글 밖 신호 중 우리가 직접 잴 수 있는 유일한 것.

★ 한계를 먼저 못 박는다: 네이버 블로그(주안모터스·루마)에 오는 Yeti 로그는 **네이버 서버에 있다.
  우리는 못 본다.** 우리가 보는 것은 자체 도메인(ollinda.kr)에 오는 방문뿐이고,
  거기로 Yeti가 오는 경로는 블로그 본문에 심은 링크(/r/*)를 따라오는 것 정도다.

★ UA 위조 주의(R2): UA가 'Yeti'라고 주장해도 위조 가능하다.
  검증은 네이버 공식 방식을 따른다 — ①공식 IP 목록 대조 ②역방향 DNS가 .naver.com으로
  끝나는가 ③그 호스트명을 정방향 조회해 원래 IP와 일치하는가(FCrDNS).

★ 2026-08-16 사고: 대역을 125.209.192.0/18 **하나만** 적어두고 그 밖은 전부 '가짜'로 찍었다.
  실제 네이버 공식 목록은 36개 대역이다. 그래서 8일간 온 Yeti 17건 중 16건을 위조로 판정했고,
  나는 그 숫자를 보고 "네이버가 우리 사이트를 거의 안 온다"고 사장님께 보고했다.
  역방향 DNS를 한 번만 조회해봤으면 5분 만에 알았을 일이다(전부 crawl.*.web.naver.com).
  → 계측기를 먼저 검증한다(docs/DISCIPLINE.md 4번). 자를 못 믿으면 잰 것도 못 믿는다.

★ DNS는 요청 경로에서 돌리지 않는다: record()는 모든 요청마다 도는 미들웨어 안에 있다.
  거기서 DNS를 기다리면 사장님 페이지가 같이 멈춘다. 기록은 빠른 판정(메모리 목록)만 하고,
  느린 확인(DNS)은 분석 시점에 한다 — 원본을 그대로 남겨두므로 소급 검증이 가능하다.
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

# 공식 목록을 못 받았을 때의 최소 안전망 — 실측으로 확인된 대역만(2026-08 로그).
#   이것만으로 판정하지 않는다. 공식 목록이 있으면 그쪽이 우선이다.
_FALLBACK_NETS = [ipaddress.ip_network(x) for x in (
    "125.209.192.0/18", "114.111.32.0/24", "211.249.46.0/24", "110.93.150.0/24")]
NAVERBOT_JSON = "https://searchadvisor.naver.com/doc/naverbot.json"
_NETS_CACHE: dict = {"at": 0.0, "nets": None}      # 하루 1회 갱신
_NETS_TTL = 86400
_HOSTOK = re.compile(r"\.naver\.com$|\.naverbot\.com$", re.I)


def official_nets(force: bool = False) -> list:
    """네이버 공식 봇 IP 목록(캐시). 실패하면 최소 안전망으로 — 조용히 빈손이 되지 않는다."""
    now = time.time()
    if not force and _NETS_CACHE["nets"] is not None and now - _NETS_CACHE["at"] < _NETS_TTL:
        return _NETS_CACHE["nets"]
    nets = list(_FALLBACK_NETS)
    try:
        import requests
        d = requests.get(NAVERBOT_JSON, timeout=8).json()
        got = []
        for p in d.get("prefixes") or []:
            v = p.get("ipv4Prefix") or p.get("ipv6Prefix")
            if v:
                try:
                    got.append(ipaddress.ip_network(v))
                except Exception:
                    pass
        if got:
            nets = got
    except Exception:
        pass
    _NETS_CACHE.update({"at": now, "nets": nets})
    return nets


def in_official_range(ip: str) -> bool:
    """공식 IP 목록 대조 — 네트워크 호출 없음(캐시된 목록만). 요청 경로에서 쓰는 빠른 판정."""
    try:
        a = ipaddress.ip_address(ip)
    except Exception:
        return False
    nets = _NETS_CACHE["nets"]
    if nets is None:                    # 아직 안 받았으면 안전망으로 판정(요청을 붙잡지 않는다)
        nets = _FALLBACK_NETS
    return any(a in n for n in nets)


def fcrdns_ok(ip: str, timeout: float = 3.0) -> "bool | None":
    """역방향 DNS → .naver.com 확인 → 정방향 재조회로 IP 일치 확인(FCrDNS).

    ★ 느리다. 요청 경로에서 부르지 말 것 — 분석·배치에서만 쓴다.
    반환: True/False, 조회 자체가 안 되면 None(모름 — False로 단정하지 않는다).
    """
    import socket
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        try:
            host = socket.gethostbyaddr(ip)[0]
        except Exception:
            return None                 # 역방향 자체 실패 = 모름
        if not _HOSTOK.search(host or ""):
            return False                # 네이버 도메인이 아니다 = 가짜
        try:
            infos = socket.getaddrinfo(host, None)   # A 레코드가 여러 개일 수 있다
        except Exception:
            return None
        return any(i[4][0] == ip for i in infos)
    finally:
        try:
            socket.setdefaulttimeout(old)
        except Exception:
            pass
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


def verify_yeti(ip: str, deep: bool = False) -> "bool | None":
    """진짜 Yeti인가 — 네이버 공식 방식.

    deep=False(기본): 공식 IP 목록만 본다. 빠르다 — 요청 경로에서 안전.
    deep=True: 목록에 없으면 FCrDNS까지 확인한다(느림 — 분석 시점 전용).
    반환: True/False, 판정 불가면 None(모름을 False로 만들지 않는다).
    """
    if in_official_range(ip):
        return True
    if not deep:
        return False
    return fcrdns_ok(ip)


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
