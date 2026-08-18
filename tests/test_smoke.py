"""프로덕션 배포 전 최소 스모크 테스트 — 배포 사고 막을 핵심 4개.
외부 API(Claude/네이버/발행)는 키 미설정으로 graceful 폴백(더미/시뮬)을 탄다.
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.domain.models import ContentKind, ContentStatus

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 1) ingest → generate → publish 핵심 플로우 (외부 API mock/폴백) ──
def test_ingest_generate_publish_flow(tiny_png_bytes, monkeypatch):
    from app.services import ingest as ingest_mod
    from app.services.ingest import ingest_upload
    from app.services.publish import publish_and_record

    # 영상 번들(백그라운드 스레드+ffmpeg)은 이 스모크 범위 밖 → no-op로 격리
    monkeypatch.setattr(ingest_mod, "_spawn_video_bundle", lambda *a, **k: None)

    tenant = db.create_tenant(name="스모크가게", industry="카페", region="부산 동구")
    # 🚧 2026-08-03부터 테스트 tenant는 발행 경로에 못 들어간다(오배송 사건).
    #   이 테스트가 보는 건 '발행 흐름'이지 격리 정책이 아니므로, 이 가게만 실계정으로 등록해 통과시킨다.
    #   격리 정책 자체는 tests/test_tenant_isolation.py가 검사한다.
    from app import config as _cfg
    monkeypatch.setattr(_cfg, "PRODUCTION_TENANTS", tuple(_cfg.PRODUCTION_TENANTS) + (tenant.id,))

    # ingest → generate (키 없음 → 더미 생성기). 크래시 없이 초안 생성돼야 함
    # ★ 2026-08-16부터 기본 생성은 **네이버 글 하나뿐**이다(사장님 지시: 인스타는 동의 시에만).
    #   이 테스트가 보는 것은 '발행 흐름'이므로 캡션을 **명시적으로 요청**해서 만든다 —
    #   그게 새 계약(고른 것만 만든다)에서 캡션이 생기는 유일한 길이다.
    pieces = ingest_upload(tenant, [(tiny_png_bytes, "photo.png")], "신메뉴 라떼 출시",
                           kinds=[ContentKind.BLOG, ContentKind.CAPTION])
    assert pieces, "생성된 콘텐츠 초안이 없음"
    assert all(p.payload for p in pieces), "payload 비어있는 초안 존재"

    # publish: 캡션(인스타)을 승인 → 토큰 없으면 시뮬 발행(SIM-...)
    cap = next(p for p in pieces if p.kind == ContentKind.CAPTION)
    db.set_piece_status(cap.id, ContentStatus.APPROVED)
    cap.status = ContentStatus.APPROVED

    result = publish_and_record(cap)
    assert result.ok, f"발행 실패: {result.error}"
    assert str(result.external_id).startswith("SIM-"), "토큰 없으면 시뮬 발행이어야 함"

    # DB에 발행 기록 + 상태 PUBLISHED
    refreshed = db.get_piece(cap.id)
    assert refreshed.status == ContentStatus.PUBLISHED


# ── 2) Paddle 웹훅 priceId 검증 (B4: custom_data.plan 우회 차단) ──
def _paddle_sig(secret: str, raw: str) -> str:
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{ts}:{raw}".encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={mac}"


def test_결제는_사라졌다():
    """★ 2026-08-18 사장님: "대행이다. 사용자의 가입은 원하지 않는다."

    여기 있던 것은 Paddle 웹훅의 가격 우회 방지 검사였다(custom_data.plan으로
    상위 플랜 승격 금지). 결제 자체가 사라졌으니 지킬 대상이 없다 —
    대행에서 계약은 사장님이 직접 하고, 우리 시스템은 돈을 받지 않는다.

    지금 지켜야 할 것은 반대다: **결제 문이 다시 열리지 않는 것.**
    """
    import app.main as main
    paths = {getattr(r, "path", "") for r in main.app.routes}
    for dead in ("/billing", "/billing/success", "/billing/fail",
                 "/webhook/paddle", "/admin/billing/charge-due"):
        assert dead not in paths, f"결제 경로가 되살아났다: {dead}"
def test_missing_secret_fails_closed():
    env = {k: v for k, v in os.environ.items() if k != "SHOPCAST_SECRET"}
    env.pop("SHOPCAST_SECRET", None)
    proc = subprocess.run([sys.executable, "-c", "import app.auth"],
                          cwd=REPO, env=env, capture_output=True, text=True)
    assert proc.returncode != 0, "SHOPCAST_SECRET 없이 임포트가 성공하면 안 됨(B1)"
    assert "SHOPCAST_SECRET" in proc.stderr


# ── 4) admin 라우트 인증 게이트 (B2) ──
def test_admin_requires_auth(monkeypatch):
    import app.main as main
    client = TestClient(main.app)

    # 비밀번호 설정 상태: 인증 없이 접근 → 401
    monkeypatch.setenv("SHOPCAST_ADMIN_PASS", "secret-pass")
    assert client.get("/admin").status_code == 401
    assert client.get("/admin/cleanup").status_code == 401

    # 비밀번호 미설정 상태: fail-closed로 전면 차단 → 503 (무인증 개방 아님)
    monkeypatch.delenv("SHOPCAST_ADMIN_PASS", raising=False)
    assert client.get("/admin").status_code == 503
    assert client.get("/admin/cleanup").status_code == 503


def test_target_banner_only_with_param():
    """타겟 배너는 ?target_kw 진입 시에만 — plain /me·made 복귀엔 없음(C1 회귀 가드)."""
    import app.main as main
    from app import auth
    client = TestClient(main.app)
    u = db.create_user(email="banner@t.t")
    t = db.create_tenant("이어폰샵", "이어폰", "", biz_type="seller")
    db.update_tenant_classification(t.id, "seller", "coupang", "", "블루투스 이어폰", "")
    db.set_user_tenant(u["id"], t.id)
    client.cookies.set(auth.COOKIE, auth.make_session(u["id"]))
    # (auto) 키워드 미노출 원칙: 배너 문구는 '글감은 AI가 정해뒀어요' — 파라미터 진입 시에만 표시
    assert "글감은 AI가 정해뒀어요" not in client.get("/me").text
    assert "글감은 AI가 정해뒀어요" not in client.get("/me?made=x").text
    assert "글감은 AI가 정해뒀어요" in client.get("/me?target_kw=블루투스 이어폰").text
