"""
네이버 블로그 어댑터.
- 기본: 반자동(초안 export → 사람이 발행). 공식 API 없음.
- 옵션: Playwright 자동 포스팅 (NAVER_ID/NAVER_PW + playwright 설치 시).
  ⚠️ 브라우저 자동화는 네이버 약관 위반 소지 + 저품질/캡차/2차인증 리스크.
     자기 책임으로만 사용. 기본은 반자동 권장.
env: NAVER_ID, NAVER_PW, (선택) NAVER_AUTOPOST=1
"""
from __future__ import annotations

import importlib.util
import os

from app.adapters.base import Publisher
from app.domain.models import ChannelAccount, ContentPiece, PublishResult


def _playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def autopost_configured() -> bool:
    return bool(os.environ.get("NAVER_ID") and os.environ.get("NAVER_PW")
                and os.environ.get("NAVER_AUTOPOST") == "1" and _playwright_available())


class NaverBlogPublisher(Publisher):
    # 자동포스팅 설정 시에만 auto, 아니면 반자동(사람 발행)
    @property
    def supports_auto_publish(self) -> bool:  # type: ignore[override]
        return autopost_configured()

    def validate(self, content: ContentPiece) -> list[str]:
        errors: list[str] = []
        if not content.payload.get("title"):
            errors.append("제목 없음")
        if not content.payload.get("body"):
            errors.append("본문 없음")
        return errors

    def export_draft(self, content: ContentPiece) -> dict:
        return {
            "title": content.payload.get("title", ""),
            "body": content.payload.get("body", ""),       # [사진N] 마커 포함
            "tags": content.payload.get("tags", []),
            "images": content.payload.get("image_paths", []),
            "photo_markers": content.payload.get("photo_markers", []),
            "guide": "본문을 붙여넣고, [사진N] 위치에 아래 번호의 사진을 그 순서대로 넣으세요.",
        }

    def publish(self, account: ChannelAccount, content: ContentPiece) -> PublishResult:
        # supports_auto_publish=True일 때만 호출됨 → Playwright 자동 포스팅 시도
        return self._playwright_post(content)

    def _playwright_post(self, content: ContentPiece) -> PublishResult:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return PublishResult(ok=False, error="playwright 미설치")
        nid, npw = os.environ.get("NAVER_ID"), os.environ.get("NAVER_PW")
        title = content.payload.get("title", "")
        body = content.payload.get("body", "")
        try:
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                pg = b.new_page()
                # 1) 로그인 (⚠️ 캡차/2차인증 시 실패 가능)
                pg.goto("https://nid.naver.com/nidlogin.login", timeout=30000)
                pg.fill("#id", nid); pg.fill("#pw", npw)
                pg.click(".btn_login")
                pg.wait_for_timeout(3000)
                # 2) 글쓰기 (에디터 DOM은 수시 변경 → 셀렉터 유지보수 필요)
                pg.goto("https://blog.naver.com/GoBlogWrite.naver", timeout=30000)
                # TODO: 에디터 iframe 진입 → 제목/본문 입력 → 발행 버튼.
                #       네이버 에디터 구조가 자주 바뀌어 셀렉터 확정은 실계정 테스트 필요.
                b.close()
            return PublishResult(ok=False,
                                 error="Playwright 골격만 구현 — 에디터 셀렉터는 실계정 검증 필요")
        except Exception as e:
            return PublishResult(ok=False, error=f"네이버 자동포스팅 실패: {str(e)[:120]}")
