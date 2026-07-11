"""
Paddle Billing 결제(구독) — 해외 결제대행(Merchant of Record). 세금·카드 자동 처리.
토스보다 쉬움: 사업자 PG 심사 불필요, 패들 대시보드에서 상품 만들고 키만 넣으면 됨.

env:
  PADDLE_CLIENT_TOKEN   — 프론트 체크아웃 토큰 (Paddle > Developer tools > Authentication)
  PADDLE_PRICE_SELF     — 셀프 플랜 가격 ID (pri_...)
  PADDLE_PRICE_AGENCY   — (선택) 대행 플랜 가격 ID
  PADDLE_WEBHOOK_SECRET — 웹훅 서명 시크릿 (Notifications > 웹훅 대상)
  PADDLE_ENV            — 'sandbox'(테스트) 또는 'production'(실결제). 기본 production
docs: https://developer.paddle.com/build/checkout/build-overlay-checkout
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time


def env() -> str:
    return os.environ.get("PADDLE_ENV", "production").strip().lower()


def configured() -> bool:
    return bool(os.environ.get("PADDLE_CLIENT_TOKEN") and os.environ.get("PADDLE_PRICE_SELF"))


def client_token() -> str:
    return os.environ.get("PADDLE_CLIENT_TOKEN", "")


def price_id(plan: str) -> str:
    return os.environ.get("PADDLE_PRICE_" + (plan or "self").upper(), "") or os.environ.get("PADDLE_PRICE_SELF", "")


def plan_for_price_id(pid: str) -> str | None:
    """웹훅의 price id를 서버에 설정된 PADDLE_PRICE_* 와 역매칭해 플랜명 반환(없으면 None).
    클라이언트가 보낸 custom_data.plan을 신뢰하지 않기 위한 서버측 검증(B4)."""
    if not pid:
        return None
    for k, v in os.environ.items():
        if k.startswith("PADDLE_PRICE_") and v and hmac.compare_digest(v, pid):
            return k[len("PADDLE_PRICE_"):].lower()
    return None


def plan_from_event(data: dict) -> str | None:
    """웹훅 data.items[].price.id 들을 순회하며 서버 검증된 플랜명을 찾는다."""
    for it in (data.get("items") or []):
        plan = plan_for_price_id(str((it.get("price") or {}).get("id") or ""))
        if plan:
            return plan
    return None


def verify_webhook(sig_header: str, raw_body: str) -> bool:
    """Paddle-Signature(ts=..;h1=..) HMAC-SHA256 검증. 재생공격 방지(5분)."""
    secret = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
    if not (secret and sig_header and raw_body):
        return False
    try:
        parts = {}
        for p in sig_header.split(";"):
            if "=" in p:
                k, v = p.split("=", 1)
                parts[k.strip()] = v.strip()
        ts, h1 = parts.get("ts"), parts.get("h1")
        if not (ts and h1):
            return False
        if abs(int(time.time()) - int(ts)) > 300:
            return False
        signed = f"{ts}:{raw_body}"
        computed = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, h1)
    except Exception:
        return False
