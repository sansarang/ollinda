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

# 🚫 **바깥으로 나가는 자격증명을 지우고 시작한다**(2026-08-19 실사고).
#   여기엔 원래 "외부 키는 모두 미설정"이라는 **주석만** 있었다. 코드가 아니라 가정이었다.
#   골든을 프로덕션 env가 실린 셸에서 돌리자(생성 실측 때문에 railway env를 source했다)
#   `test_경보가_터져도_문의는_저장된다`가 진짜 Resend로 메일을 보냈고,
#   사장님 받은편지함에 **'[올린다 문의] 경보죽음테스트'가 실제로 도착했다** — 두 번.
#   그 골든은 watchtower만 대역으로 바꾸고 mailer는 진짜를 그대로 썼다.
#   ★ 테스트가 실제 세상에 닿는 경로는 케이스마다 막을 것이 아니라 **입구에서 끊는다.**
for _k in ("RESEND_API_KEY", "MAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
           "SMTP_PASS", "SMTP_FROM", "ALERT_EMAIL", "SHOPCAST_OWNER_EMAILS",
           "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SLACK_WEBHOOK_URL"):
    os.environ.pop(_k, None)
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
