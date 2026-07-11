"""
토스페이먼츠 정기결제(빌링) 연동.
env: TOSS_CLIENT_KEY(프론트), TOSS_SECRET_KEY(서버). 키 없으면 configured()=False → 결제 UI 'graceful'.
흐름: 프론트 requestBillingAuth → success(authKey,customerKey) → issue_billing_key → charge → 구독 활성.
docs: https://docs.tosspayments.com/guides/v2/billing/integration
"""
from __future__ import annotations

import base64
import os

import requests

API = "https://api.tosspayments.com"

from app.config import PLANS  # 가격·플랜은 app/config.py 단일 소스(성장 개선 규칙3)

# 연 결제 플랜(월가×12×0.7) — Paddle/Toss priceId는 env 매핑
from app import config as _cfg
for _k in ("basic", "pro"):
    PLANS[f"{_k}_yearly"] = {"name": PLANS[_k]["name"] + "(연)",
                             "price": _cfg.yearly_price(PLANS[_k]["price"]),
                             "monthly": PLANS[_k]["monthly"], "yearly": True}


def configured() -> bool:
    return bool(os.environ.get("TOSS_SECRET_KEY") and os.environ.get("TOSS_CLIENT_KEY"))


def client_key() -> str:
    return os.environ.get("TOSS_CLIENT_KEY", "")


def _auth() -> dict:
    sk = os.environ.get("TOSS_SECRET_KEY", "")
    tok = base64.b64encode((sk + ":").encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json"}


def issue_billing_key(auth_key: str, customer_key: str) -> dict:
    """카드 등록 authKey → billingKey 발급. 성공 시 {'billingKey':...}, 실패 시 {'error':...}."""
    try:
        r = requests.post(f"{API}/v1/billing/authorizations/issue", headers=_auth(),
                          json={"authKey": auth_key, "customerKey": customer_key}, timeout=15)
        if r.status_code == 200:
            return r.json()
        return {"error": (r.json().get("message") if r.headers.get("content-type", "").startswith("application/json") else r.text[:120])}
    except Exception as e:
        return {"error": str(e)[:120]}


def charge(billing_key: str, customer_key: str, amount: int, order_id: str, order_name: str) -> dict:
    """billingKey로 amount 청구. 성공 시 결제 json, 실패 시 {'error':...}."""
    try:
        r = requests.post(f"{API}/v1/billing/{billing_key}", headers=_auth(),
                          json={"customerKey": customer_key, "amount": amount,
                                "orderId": order_id, "orderName": order_name}, timeout=20)
        if r.status_code == 200:
            return r.json()
        return {"error": (r.json().get("message") if r.headers.get("content-type", "").startswith("application/json") else r.text[:120])}
    except Exception as e:
        return {"error": str(e)[:120]}
