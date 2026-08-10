"""피스 미디어(/asset·/video) 소유 검증 골든 — 2026-08-11 모바일 전수검사에서 발견한
무인증 노출 구멍의 박제. pid만 알면 남의 사진·영상을 볼 수 있으면 실패해야 한다.

허용 3경로: ① 소유자 세션 ② 운영자 Basic ③ 서명 URL(외부 발행 fetch — 인스타).
③이 깨지면 인스타 자동발행이 죽는다 — 서명 검증도 여기서 같이 산다.
"""
import base64
import time
import uuid

from fastapi.testclient import TestClient

from app import auth, db
from app.domain.models import Channel, ContentKind, ContentPiece


def _mk_piece():
    t = db.create_tenant(name="미디어가드가게", industry="카페", region="부산 동구")
    p = ContentPiece(id=str(uuid.uuid4()), tenant_id=t.id, asset_id=str(uuid.uuid4()),
                     channel=Channel.INSTAGRAM, kind=ContentKind.CAPTION,
                     payload={"image_path": "/nonexistent.jpg"})
    db.save_piece(p)
    return t, p


def _client():
    from app.main import app
    return TestClient(app)


def test_anonymous_gets_challenged_not_media():
    _, p = _mk_piece()
    c = _client()
    for url in (f"/asset/{p.id}", f"/asset/{p.id}/0", f"/video/{p.id}"):
        r = c.get(url)
        assert r.status_code == 401, f"{url}: 무인증인데 {r.status_code} — 남의 미디어 노출"


def test_other_users_session_gets_404():
    _, p = _mk_piece()
    other = db.create_user("other-" + uuid.uuid4().hex[:8] + "@t.kr", "pw123456")
    c = _client()
    c.cookies.set(auth.COOKIE, auth.make_session(other["id"]))
    r = c.get(f"/asset/{p.id}")
    assert r.status_code == 404, f"남의 세션인데 {r.status_code} — 존재를 숨겨야 한다"


def test_admin_basic_passes_guard():
    _, p = _mk_piece()
    c = _client()
    cred = base64.b64encode(b"admin:test-admin-pass").decode()
    r = c.get(f"/video/{p.id}", headers={"Authorization": f"Basic {cred}"})
    # 가드는 통과해야 한다 — 파일이 없으니 404가 정상이지만 401(거부)이면 검수화면이 죽는다
    assert r.status_code != 401, "운영자 Basic이 거부됨 — /admin/review 미디어가 끊긴다"


def test_signed_url_passes_and_expires():
    _, p = _mk_piece()
    c = _client()
    exp = int(time.time()) + 600
    sig = auth.media_sig(p.id, exp)
    r = c.get(f"/asset/{p.id}?exp={exp}&sig={sig}")
    assert r.status_code != 401, "유효 서명이 거부됨 — 인스타 발행 fetch가 죽는다"
    r2 = c.get(f"/asset/{p.id}?exp={int(time.time()) - 10}&sig={auth.media_sig(p.id, int(time.time()) - 10)}")
    assert r2.status_code == 401, "만료 서명이 통과됨 — 시한부가 아니다"
    r3 = c.get(f"/asset/{p.id}?exp={exp}&sig=deadbeef")
    assert r3.status_code == 401, "위조 서명이 통과됨"


def test_preview_videos_never_eager_autoplay():
    """미리보기 영상은 autoplay 속성 금지(전량 즉시 다운로드 유발, 개당 30MB 실측) —
    data-autoplay + IntersectionObserver(보일 때만 재생)로만 자동재생한다."""
    src = open("app/main.py", encoding="utf-8").read()
    # 허용 1곳: 랜딩 게스트 데모 — 방문자가 생성을 기다렸다 보는 그 한 편(작은 클립)의 공개 연출.
    # 그 외(세트 미리보기 등)에 autoplay가 늘어나면 회귀다.
    assert src.count("controls autoplay") <= 1, "video autoplay 속성 회귀 — 모바일이 수십 MB를 즉시 받는다"
    assert "video[data-autoplay]" in src, "IO 재생 선택자 유실 — 릴스식 자동재생이 죽는다"
    assert src.count("data-autoplay") >= 3, "미리보기 영상의 data-autoplay 마킹 유실(2곳 + JS 선택자)"
