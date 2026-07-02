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

PLANS = {
    "self":   {"name": "셀프 플랜", "price": 39900, "monthly": 60},   # 월 60건
    "agency": {"name": "대행 플랜", "price": 299000, "monthly": 0},   # 0=무제한(운영자 관리)
}


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
