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
    assert "THUMB_PX, 72" in dsrc, "썸네일 규격으로 축소하지 않는다"
    assert "THUMB_DIR" in dsrc, "파생본 폴더 규칙이 없다"
    # 원본이 없어도 목록이 죽지 않아야 한다(폴백)
    assert "status_code=404" in src or "RedirectResponse" in src


def test_thumb_generation_restores_from_r2():
    """B3. 로컬은 캐시고 원본은 R2다 — 컨테이너 재시작이면 로컬만 빈다.
    실측(2026-08-03): 디스크 0장·R2 17장인 세트에서 썸네일 생성이 실패했고,
    그 세트만 계속 원본을 직접 로드해 느렸다. 없으면 복원하고 만든다."""
    import inspect
    from app.services import derived as dv
    # 복원은 파생본 공통 준비 단계(_ensure_original)로 모았다 — 썸네일·웹용 둘 다 탄다
    esrc = inspect.getsource(dv._ensure_original)
    assert "_restore_media" in esrc, "R2 복원 없이 '원본 없음'으로 끝낸다"
    rsrc = inspect.getsource(dv._resize)
    i, j = rsrc.find("_ensure_original"), rsrc.find("Image.open")
    assert 0 <= i < j, "복원이 생성보다 뒤에 있다"
    assert "return False" in rsrc, "실패를 조용히 채운다"


def test_detail_view_uses_web_derivative():
    """B4. '보기'(상세)가 원본을 로드하면 실패한다 — 20장이면 약 93MB다(2026-08-03 체감 반려).
    화면은 파생본만, 원본은 저장·ZIP에서만."""
    from app.services import derived as dv
    src = inspect.getsource(m)
    assert "src='/dl/" not in src, "화면 어딘가가 아직 원본을 직접 로드한다"
    assert "/web/{asset_id}/" in src, "상세가 웹용 파생본을 안 쓴다"
    assert dv.WEB_PX <= 1600, f"웹용이 너무 크다({dv.WEB_PX}px)"
    assert dv.web_path("T", "a.jpg").endswith(f"T/{dv.WEB_DIR}/a.jpg")


def test_new_tenants_get_derivatives_automatically():
    """B5. 가게마다 손으로 데우는 건 지금뿐이다 — 가입자가 늘면 자동 경로가 유일한 보장선이다.
    ① 업로드 시점 생성(1차) ② 야간 전 가게 데우기(그물)."""
    import inspect
    from app.services import ingest as ing
    src = inspect.getsource(ing.ingest_upload)
    assert "make_thumb" in src and "make_web" in src, "업로드가 파생본을 안 만든다"
    i, j = src.find("make_thumb"), src.find("create_asset")
    assert 0 < i < j, "파생본 생성이 세트 생성보다 뒤에 있다"
    assert "파생본 생성 실패" in src, "실패를 조용히 넘긴다"

    from app import scheduler as sc
    ssrc = inspect.getsource(sc.start)
    assert 'id="derive_warm_daily"' in ssrc, "야간 데우기가 스케줄에 없다"
    wsrc = inspect.getsource(sc._derive_warm)
    assert "list_tenants()" in wsrc, "일부 가게만 돈다(신규 가입자 누락)"
    assert "has_thumb" in wsrc and "has_web" in wsrc, "이미 있는 것도 다시 만든다"


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


def test_목록_썸네일이_지연로드된다():
    """★ 2026-08-18 사장님: "내 콘텐츠 보기가 안 열린다."
    서버 렌더는 0.119초로 빨랐다(실측: list_sets 0.004 · 카드쿼리 36건 0.083 · 상세 0.021).
    병목은 브라우저였다 — 세트가 36개인데 썸네일에 지연로드가 없어 화면 밖 카드까지
    전부 한꺼번에 받았다(브라우저 동시연결 6개 → 대기 줄).
    """
    import inspect

    from app import main
    src = inspect.getsource(main)
    i = src.find("thumb_html = ")
    assert i > 0
    seg = src[i:i + 400]
    assert "loading='lazy'" in seg, "목록 썸네일이 지연로드되지 않는다(36개를 한꺼번에 받는다)"
    assert "width=" in seg and "height=" in seg, "치수가 없어 레이아웃이 흔들린다(CLS)"


def test_상세_대표이미지가_원본이_아니다():
    """derived.py가 'WEB_PX=1400 — 원본(4~5MB)을 화면에 쓰지 않는다'고 못 박아놨는데
    대표 이미지만 /dl(원본)을 쓰고 있었다. 인스타·X 미리보기가 전부 이걸 쓴다."""
    import inspect

    from app import main
    src = inspect.getsource(main._result_html)
    i = src.find("first_img = ")
    assert i > 0
    seg = src[i:i + 200]
    assert "/web/" in seg, "대표 이미지가 원본(/dl)이다 — 폰 사진은 4~5MB다"
    assert "/dl/" not in seg.split("\n")[0]
