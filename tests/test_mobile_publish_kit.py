"""모바일 발행 편의 5종 골든 — 2026-08-09 사장님 전체 승인 구현 박제.

계약: ① 발행 위저드(순차 복사·진행 기억) ② 사진 일괄 저장(Web Share, 번호 파일명)
③ 본문 미리보기 마커 썸네일(복사 원문 불변) ④ 발행 확인 원탭(클립보드 → 폼)
⑤ PWA(매니페스트·share_target → /share-publish). 자동 발행 금지 원칙은 그대로 —
전부 복붙 과정을 매끄럽게 만들 뿐 발행 버튼은 사장님 몫이다.
"""
import os
import uuid
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import auth, db, landing
from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus
from app.main import app

client = TestClient(app)


@pytest.fixture()
def owner_kit():
    """로그인 유저 + 소유 tenant + [사진1][사진2] 마커가 든 블로그 세트."""
    u = db.create_user(email=f"kit_{uuid.uuid4().hex[:8]}@test.local", name="키트")
    t = db.create_tenant(f"키트검증-{uuid.uuid4().hex[:6]}", "카페", region="부산 전포")
    db.set_user_tenant(u["id"], t.id)
    db.link_store(u["id"], t.id)
    aid = str(uuid.uuid4())
    p = ContentPiece(
        id=str(uuid.uuid4()), tenant_id=t.id, asset_id=aid,
        channel=Channel.NAVER_BLOG, kind=ContentKind.BLOG,
        payload={"title": "부산 전포 카페 브런치 후기", "body": "인사말\n\n[사진1]\n\n본문 단락\n\n[사진2]\n\n마무리",
                 "target_keywords": ["부산 전포 카페"], "target_kw": "부산 전포 카페",
                 "image_paths": ["/tmp/kit-a.jpg", "/tmp/kit-b.jpg"]},
        status=ContentStatus.DRAFT)
    db.save_piece(p)
    client.cookies.set("shop_session", auth.make_session(u["id"]))
    yield {"user": u, "tenant": t, "asset_id": aid, "piece": p}
    client.cookies.delete("shop_session")
    with db._conn() as c:
        c.execute("DELETE FROM content_pieces WHERE tenant_id=?", (t.id,))
        c.execute("DELETE FROM blog_publishes WHERE tenant_id=?", (t.id,))
        c.execute("DELETE FROM tenants WHERE id=?", (t.id,))
        c.execute("DELETE FROM users WHERE id=?", (u["id"],))


def test_kit_wizard_and_sequential_copy(owner_kit):
    h = client.get(f"/kit/{owner_kit['asset_id']}/naver").text
    assert "nvWiz" in h and "발행 4단계" in h, "발행 위저드 누락"
    assert "nvSeqBtn" in h and "제목 복사부터 시작" in h, "순차 복사 버튼 누락"
    assert "localStorage" in h and "nvwiz_" in h, "진행 상태 기억(localStorage) 누락"


def test_kit_share_all_photos(owner_kit):
    h = client.get(f"/kit/{owner_kit['asset_id']}/naver").text
    assert "nvShareAll" in h and "nvShareData" in h, "사진 일괄 저장 누락"
    assert "-01.jpg" in h and "-02.jpg" in h, "번호 파일명([사진N] 대응) 누락"
    assert "navigator.canShare" in h, "미지원 브라우저 게이트 누락"


def test_kit_marker_thumbnails_preview_only(owner_kit):
    aid = owner_kit["asset_id"]
    h = client.get(f"/kit/{aid}/naver").text
    assert f"/web/{aid}/kit-a.jpg" in h, "마커 썸네일 누락"
    assert "📷 사진1 위치</b></span>" in h, "마커 칩 누락"
    # 복사 원문(평문 textarea)에는 마커 텍스트가 그대로 — 썸네일이 복사물을 오염시키면 안 된다
    import re
    plain = re.search(r"<textarea id='nvPlain'[^>]*>(.*?)</textarea>", h, re.S)
    assert plain and "📷 사진1 위치" in plain.group(1) and "<img" not in plain.group(1), \
        "복사 원문이 미리보기 장식에 오염됨"


def test_kit_one_tap_publish_confirm(owner_kit):
    h = client.get(f"/kit/{owner_kit['asset_id']}/naver").text
    assert "nvPasteUrl" in h and "복사해둔 글 주소로 바로 등록" in h, "원탭 발행 확인 누락"
    assert "clipboard.readText" in h and 'id=\'nvFb\'' in h.replace('"', "'"), "클립보드→폼 배선 누락"


def test_pwa_manifest_and_icons():
    r = client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    m = r.json()
    assert m["name"].startswith("올린다") and m["display"] == "standalone"
    assert m["share_target"]["action"] == "/share-publish", "share_target 누락 — 공유→발행확인이 끊긴다"
    for icon in m["icons"]:
        assert client.get(icon["src"]).status_code == 200, f"아이콘 실물 없음: {icon['src']}"
    assert "/static/manifest.webmanifest" in landing.render(), "head 매니페스트 링크 누락"


def test_share_publish_requires_login_and_url():
    client.cookies.delete("shop_session")
    r = client.get("/share-publish?url=https://blog.naver.com/x/1", follow_redirects=False)
    assert r.status_code in (302, 303, 307) and "/login" in r.headers["location"]


def test_share_publish_no_url_is_honest(owner_kit):
    r = client.get("/share-publish?text=아무 주소 없는 공유", follow_redirects=False)
    assert "주소를 찾지 못했어요" in unquote(r.headers["location"])


def test_share_publish_confirms_pending_piece(owner_kit, monkeypatch):
    from app.services import pipesync
    monkeypatch.setattr(pipesync, "_rss_meta_for_url", lambda t, url: {})   # 실네트워크 차단
    aid, piece = owner_kit["asset_id"], owner_kit["piece"]
    url = "https://blog.naver.com/kittest/223000000001"
    r = client.get(f"/share-publish?url={url}", follow_redirects=False)
    assert f"/kit/{aid}/naver" in unquote(r.headers["location"]), "발행 확인 후 키트로 복귀해야"
    pub = db.get_blog_publish(piece.id)
    assert pub and pub.get("published_url", "").startswith("https://blog.naver.com/kittest/")
    assert (pub.get("target_kw") or "") == "부산 전포 카페", "발행 기록 키워드 박제(2026-08-09 계약) 회귀"
