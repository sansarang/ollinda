#!/usr/bin/env python3
"""
데모 자산 재생성(성장 PHASE 14) — 개선된 파이프라인(EXIF·3초훅·faststart·D.I.A.+ 블로그)으로
app/static/demo/ 의 데모 영상·블로그를 '실제 생성물'로 교체한다.

⚠️ 정직성: 이 스크립트는 실제 파이프라인을 돌려 진짜 결과물을 만든다(합성/과장 아님).
필요 조건(없으면 자산 교체 금지 — 가짜 데모 방지):
  - ANTHROPIC_API_KEY (글·씬 스크립트)   - ffmpeg (영상 렌더)   - (선택) ELEVENLABS_API_KEY (TTS)
사용: SHOPCAST_SECRET=... ANTHROPIC_API_KEY=... python scripts/regen_demo.py
"""
import os
import shutil
import sys

DEMO_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "static", "demo")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY 없음 → 진짜 생성 불가. 가짜 데모를 만들지 않기 위해 중단.")
        return 1
    if not shutil.which("ffmpeg"):
        print("ffmpeg 없음 → 영상 렌더 불가. 중단.")
        return 1
    from app import db
    from app.domain.models import ContentKind
    from app.services.ingest import ingest_upload
    db.init_db()
    t = db.create_tenant(name="초량 루마썬팅", industry="자동차 썬팅", region="부산 동구")
    src = os.path.join(DEMO_DIR, "photo.jpg")
    if not os.path.exists(src):
        print(f"데모 원본 사진 없음: {src}")
        return 1
    with open(src, "rb") as f:
        data = f.read()
    pieces = ingest_upload(t, [(data, "photo.jpg")] * 5, "열차단 썬팅 시공 · 매장 방문 유도")
    short = next((p for p in pieces if p.kind == ContentKind.SHORT and p.payload.get("video_path")), None)
    if short:
        shutil.copy(short.payload["video_path"], os.path.join(DEMO_DIR, "local_short.mp4"))
        print("✅ local_short.mp4 교체(개선 파이프라인 실물)")
    else:
        print("⚠️ 영상 생성 실패 — 자산 교체 안 함(가짜 데모 방지)")
    print("완료. git diff로 교체된 데모 자산 확인 후 커밋하세요.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    raise SystemExit(main())
