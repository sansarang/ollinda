"""업로드 시점 파생본 선생성 계약(2026-08-11) — 저장하면 썸네일·웹본이 따라와야 한다.
이게 깨지면 새 사진의 첫 열람이 다시 즉석 변환(장당 ~105ms 실측)으로 느려진다.
"""
import os
import time

from app import db, storage
from app.services import derived


def test_save_upload_pregenerates_derived(tiny_png_bytes):
    t = db.create_tenant(name="파생선생성가게", industry="카페", region="부산 동구")
    path = storage.save_upload(tiny_png_bytes, "photo.png", t.id)
    fname = os.path.basename(path)
    # 데몬 스레드 완료 대기(작은 이미지라 수십 ms면 끝난다)
    deadline = time.time() + 5
    while time.time() < deadline:
        if os.path.exists(derived.thumb_path(t.id, fname)) and os.path.exists(derived.web_path(t.id, fname)):
            break
        time.sleep(0.05)
    assert os.path.exists(derived.thumb_path(t.id, fname)), "업로드 후 썸네일 미생성 — 첫 조회가 다시 느려진다"
    assert os.path.exists(derived.web_path(t.id, fname)), "업로드 후 웹본 미생성 — 상세 첫 열람이 다시 느려진다"


def test_non_image_upload_skips_derived():
    t = db.create_tenant(name="파생스킵가게", industry="카페", region="부산 동구")
    path = storage.save_upload(b"\x00\x01fake-mp4", "clip.mp4", t.id)
    time.sleep(0.2)
    fname = os.path.basename(path)
    assert not os.path.exists(derived.thumb_path(t.id, fname)), "영상에 이미지 파생본을 만들려 함"
