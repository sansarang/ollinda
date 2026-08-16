"""요청한 것만 만든다 — 인스타 캡션·X 글 온디맨드 골든 (2026-08-16 사장님 지시).

사장님 지시: "인스타 캡션은 사용자의 동의가 있을 때만 생성한다. 네이버 글만 생성을 하면 되고.
             미리보기에 인스타 및 다른 컨텐츠 만들기 버튼을 추가해라."

헌법에도 "사용자가 고른 것만 만든다 — 요청하지 않은 산출물을 만들지 않는다"가 있는데,
지금까지 매 생성마다 캡션·X를 같이 만들어 비용을 쓰고 있었다(영상은 이미 온디맨드였다).
"""
import os

from app.domain.models import ContentKind

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_default_generation_is_blog_only():
    """기본 생성은 네이버 글 하나뿐이다."""
    from app.services.ingest import CORE_KINDS
    assert tuple(CORE_KINDS) == (ContentKind.BLOG,), \
        f"요청하지 않은 산출물이 기본으로 만들어진다: {CORE_KINDS}"


def test_ondemand_kinds_cover_the_requested_ones():
    from app.services.ingest import ONDEMAND_KINDS
    assert ONDEMAND_KINDS["caption"] == ContentKind.CAPTION
    assert ONDEMAND_KINDS["x"] == ContentKind.X_POST


def test_ondemand_reuses_the_shared_generation_path():
    """새 생성 경로를 만들면 한쪽만 고쳐진다(헌법: 생성 경로는 하나)."""
    src = _src("app/services/ingest.py")
    i = src.find("def request_text_bundle")
    assert i > 0, "온디맨드 텍스트 진입점이 없다"
    seg = src[i:i + 2200]
    assert "generate_for(" in seg, "공용 생성 경로를 안 쓴다"
    assert "credit_out()" in seg, "크레딧 소진 확인이 없다(헛 생성 방지)"


def test_ondemand_refuses_when_already_made():
    """이미 있는 것을 또 만들면 비용만 든다."""
    src = _src("app/services/ingest.py")
    i = src.find("def request_text_bundle")
    seg = src[i:i + 2200]
    assert "이미 만들어 둔 것이에요" in seg


def test_ondemand_verifies_ownership():
    """남의 콘텐츠로 생성을 걸 수 있으면 취약점이다."""
    src = _src("app/main.py")
    i = src.find('@app.post("/me/text/make")')
    assert i > 0, "온디맨드 엔드포인트가 없다"
    seg = src[i:i + 1200]
    assert "내 콘텐츠가 아니에요" in seg, "소유 검증이 없다"
    assert "로그인이 필요해요" in seg, "인증 확인이 없다"


def test_preview_has_the_make_buttons():
    """사장님이 고를 자리가 화면에 있어야 온디맨드가 성립한다."""
    src = _src("app/main.py")
    assert "def _text_row(" in src, "미리보기에 텍스트 만들기 행이 없다"
    assert "인스타 캡션" in src and "X 글" in src, "고를 항목이 없다"
    assert "function tdMake(" in src, "만들기 버튼 동작이 없다"
    i = src.find("_vrow, _ = _video_row(")
    assert "_text_row(" in src[i:i + 200], "만든 행이 카드에 붙지 않았다(죽은 코드)"


def test_made_items_are_marked_done_not_offered_again():
    """이미 만든 것은 다시 고르게 하면 안 된다(죽은 자리·중복 비용)."""
    src = _src("app/main.py")
    i = src.find("def _text_row(")
    seg = src[i:i + 1800]
    assert "✓" in seg, "이미 만든 것 표시가 없다"
