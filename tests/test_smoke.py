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

    # ingest → generate (키 없음 → 더미 생성기). 크래시 없이 초안 생성돼야 함
    pieces = ingest_upload(tenant, [(tiny_png_bytes, "photo.png")], "신메뉴 라떼 출시")
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


def test_paddle_webhook_priceid_blocks_bypass(monkeypatch):
    secret = "whsec_test"
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("PADDLE_PRICE_PRO", "pri_realpro")   # pri_realpro → plan 'pro'

    import app.main as main
    client = TestClient(main.app)

    u = db.create_user(email="pay@t.com", pw_hash="h", salt="s")
    uid = u["id"]

    # (a) 우회 시도: custom_data.plan='agency' 인데 실제 결제 price는 pro → pro로만 승격돼야
    body_a = json.dumps({
        "event_type": "subscription.activated",
        "data": {"custom_data": {"user_id": uid, "plan": "agency"},
                 "items": [{"price": {"id": "pri_realpro"}}]},
    })
    r = client.post("/webhook/paddle", content=body_a,
                    headers={"Paddle-Signature": _paddle_sig(secret, body_a)})
    assert r.status_code == 200
    assert db.get_user(uid)["plan"] == "pro", "custom_data.plan로 agency 승격되면 안 됨(B4)"

    # (b) 매칭 price 없음: 플랜 변경 보류(우회 완전 차단)
    db.set_user_plan(uid, "free")
    body_b = json.dumps({
        "event_type": "subscription.activated",
        "data": {"custom_data": {"user_id": uid, "plan": "pro"},
                 "items": [{"price": {"id": "pri_unknown"}}]},
    })
    r = client.post("/webhook/paddle", content=body_b,
                    headers={"Paddle-Signature": _paddle_sig(secret, body_b)})
    assert r.status_code == 200
    assert db.get_user(uid)["plan"] == "free", "미매칭 price인데 플랜이 바뀌면 안 됨(B4)"

    # (c) 서명 위조 → 401
    r = client.post("/webhook/paddle", content=body_b, headers={"Paddle-Signature": "ts=1;h1=bad"})
    assert r.status_code == 401


# ── 3) SHOPCAST_SECRET 미설정 시 기동 실패 (B1, fail-closed) ──
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
