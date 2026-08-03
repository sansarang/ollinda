"""
목록 화면 성능 계약 박제(2026-08-03 실사고).

사고: '내 콘텐츠' 목록이 현저히 느렸다. 원인 둘 —
  ① 56px 썸네일 자리에 원본(장당 ~3MB)을 보냈다. 세트 19개면 약 57MB.
  ② 세트마다 조각 쿼리를 돌렸다(N+1).

교훈: 목록은 '가벼운 표'다. 원본은 상세에서만 만진다.
"""
from __future__ import annotations

import inspect
import uuid

from app import db
from app import main as m


def test_list_uses_thumbnails_not_originals():
    """A. 목록 카드가 원본 경로(/dl)를 쓰면 실패한다 — 이게 57MB의 원인이었다."""
    src = inspect.getsource(m.my_dashboard)
    i = src.find("thumb = next(")
    assert i > 0, "목록 썸네일 생성부를 못 찾음"
    seg = src[i:i + 200]
    assert "/thumb/" in seg, "목록이 원본을 쓴다"
    assert "/dl/" not in seg, "목록에 원본 경로가 남아 있다"


def test_thumb_route_caches_and_shrinks():
    """B. 썸네일은 작게 만들고 캐시한다 — 매 요청 리사이즈면 서버가 대신 느려진다.
    ★ 2026-08-03: 경로·생성은 단일 함수(services/derived.py)로 옮겼다 — 라우트는 그걸 쓴다."""
    from app.services import derived as dv
    src = inspect.getsource(m.thumb_media)
    assert "derived" in src and "make_thumb" in src, "라우트가 단일 함수를 안 쓴다"
    assert "Cache-Control" in src, "브라우저 캐시를 안 준다"
    assert dv.THUMB_PX <= 480, f"썸네일이 너무 크다({dv.THUMB_PX}px)"
    dsrc = inspect.getsource(dv)
    assert "thumbnail((THUMB_PX, THUMB_PX))" in dsrc, "축소하지 않는다"
    assert "THUMB_DIR" in dsrc, "파생본 폴더 규칙이 없다"
    # 원본이 없어도 목록이 죽지 않아야 한다(폴백)
    assert "status_code=404" in src or "RedirectResponse" in src


def test_thumb_generation_restores_from_r2():
    """B3. 로컬은 캐시고 원본은 R2다 — 컨테이너 재시작이면 로컬만 빈다.
    실측(2026-08-03): 디스크 0장·R2 17장인 세트에서 썸네일 생성이 실패했고,
    그 세트만 계속 원본을 직접 로드해 느렸다. 없으면 복원하고 만든다."""
    import inspect
    from app.services import derived as dv
    src = inspect.getsource(dv.make_thumb)
    assert "_restore_media" in src, "R2 복원 없이 '원본 없음'으로 끝낸다"
    i, j = src.find("_restore_media"), src.find("Image.open")
    assert 0 < i < j, "복원이 생성보다 뒤에 있다"
    # 복원도 실패하면 조용히 채우지 않고 False(침묵 폴백 금지)
    assert "return False" in src


def test_thumb_path_rule_lives_in_one_place():
    """B2. 생성 경로와 참조 경로는 같은 함수 하나를 쓴다(2026-08-03 조항).
    규칙이 두 곳에 살면 '만들었는데 화면은 못 찾는' 어긋남이 난다 — 이번 반려의 원인 계열."""
    from app.services import derived as dv
    src = inspect.getsource(m)
    assert '".thumbs"' not in src, "화면 쪽에 경로 규칙이 따로 살아 있다"
    assert dv.thumb_path("T", "a.jpg").endswith(f"T/{dv.THUMB_DIR}/a.jpg")
    assert dv.has_thumb("T_NOPE", "x.jpg") is False


def test_list_does_not_touch_disk_or_r2():
    """C. 목록 렌더에서 파일 존재 확인·R2 왕복 금지 — 세트 수만큼 I/O가 늘어난다.
    깨진 참조는 표시(onerror 폴백)로 처리한다."""
    src = inspect.getsource(m.my_dashboard)
    for banned in ("os.path.exists", "r2_media_url", "mirror_to_r2"):
        assert banned not in src, f"목록이 파일/R2를 만진다: {banned}"


def test_list_fetches_pieces_in_one_query():
    """D. N+1 제거 — 세트마다 쿼리를 돌리면 세트 수만큼 왕복한다."""
    src = inspect.getsource(m.my_dashboard)
    assert "get_pieces_for_assets" in src, "일괄 조회를 안 쓴다"
    i = src.find("for s in sets:")
    seg = src[i:i + 400]
    assert "db.get_set_pieces(" not in seg, "루프 안에서 세트별 쿼리를 돈다"


def test_bulk_query_returns_same_shape():
    """D2. 일괄 조회가 개별 조회와 같은 결과를 줘야 한다 — 빠르지만 틀리면 소용없다."""
    from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus
    tid, aid = "T_PERF_" + uuid.uuid4().hex[:6], str(uuid.uuid4())
    try:
        for k in (ContentKind.BLOG, ContentKind.CAPTION):
            db.save_piece(ContentPiece(id=str(uuid.uuid4()), tenant_id=tid, asset_id=aid,
                                       channel=Channel.NAVER_BLOG, kind=k,
                                       payload={"body": "b"}, status=ContentStatus.DRAFT))
        one = db.get_set_pieces(aid)
        bulk = db.get_pieces_for_assets([aid]).get(aid, [])
        assert len(one) == len(bulk) == 2
        assert {p.id for p in one} == {p.id for p in bulk}
        assert db.get_pieces_for_assets([]) == {}, "빈 입력에 쿼리를 돌린다"
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM content_pieces WHERE tenant_id=?", (tid,))
