"""배포 사고 방지용 최소 스모크 테스트 공통 픽스처.
앱 임포트 전에 필수 환경변수를 설정(SHOPCAST_SECRET는 fail-closed라 없으면 임포트 자체가 실패)."""
import os
import tempfile

# 앱 임포트보다 먼저 — 모듈 로드 시점에 읽는 값들
_tmp = tempfile.mkdtemp(prefix="shopcast-test-")
os.environ.setdefault("SHOPCAST_SECRET", "test-secret-32bytes-long-enough-xxxx")
os.environ["SHOPCAST_DB"] = os.path.join(_tmp, "test.sqlite")
os.environ["SHOPCAST_STORAGE"] = os.path.join(_tmp, "storage")
os.environ.setdefault("SHOPCAST_ADMIN_USER", "admin")
os.environ.setdefault("SHOPCAST_ADMIN_PASS", "test-admin-pass")
# 외부 키는 모두 미설정 → 생성기·발행은 graceful 폴백(더미/시뮬)으로 동작

import pytest  # noqa: E402
from app import db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    db.init_db()
    yield


@pytest.fixture()
def tiny_png_bytes():
    """PIL로 만든 최소 유효 이미지(외부 파일 의존 없이 업로드 플로우 검증)."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 140, 160)).save(buf, "PNG")
    return buf.getvalue()
