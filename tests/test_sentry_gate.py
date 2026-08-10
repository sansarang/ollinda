"""Sentry 게이트 골든 — 2026-08-10 런타임 에러 트래킹 도입 박제.

계약: ① DSN 없으면 no-op(graceful — 로컬·테스트 환경 무영향) ② DSN 있으면 활성
③ 진단 라우트는 의도된 예외를 던진다(배선 실발화 검증용, admin Basic 인증 뒤).
"""
import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.main import app


def test_no_dsn_is_noop(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert m._init_sentry() is False


def test_dsn_activates(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN",
                       "https://abc123@o000000.ingest.de.sentry.io/0000000")
    assert m._init_sentry() is True
    import sentry_sdk
    assert sentry_sdk.get_client().is_active(), "DSN 설정에도 클라이언트 비활성"
    sentry_sdk.get_client().close()            # 테스트 잔류 방지


def test_sentry_test_route_raises(monkeypatch):
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/admin/sentry-test", auth=("admin", "test-admin-pass"))
    assert r.status_code == 500, "진단 라우트는 의도된 500이어야 한다"
