"""
blurworker 클라이언트 — 사진 속 번호판·얼굴 탐지를 YOLO 워커(별도 서비스)로 위임.

Claude vision 대체: 크레딧 독립(고갈로 인한 침묵 실패 0)·비용 0·엔카 로고 등 브랜드 텍스트 오폭 없음
(번호판만 탐지). 문서 번호(등록번호·VIN)는 본체 OCR이 계속 담당 — 여긴 번호판·얼굴만.
워커 미배포/불통이면 None 반환 → 호출부가 기존 vision으로 폴백(무중단).
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

_TIMEOUT = 20


def _url() -> str:
    return (os.environ.get("BLUR_WORKER_URL") or "").rstrip("/")


def configured() -> bool:
    return bool(_url())


def _multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = "----blur" + uuid.uuid4().hex
    pre = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
           f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    body = pre + data + f"\r\n--{boundary}--\r\n".encode()
    return body, boundary


def _multipart_file_field(file_field: str, filename: str, data: bytes,
                          text_field: str, text_val: str) -> tuple[bytes, str]:
    """파일 1개 + 텍스트 필드 1개 멀티파트(/, inpaint용 — 이미지 + boxes JSON)."""
    boundary = "----blur" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{text_field}\"\r\n\r\n"
        f"{text_val}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, boundary


def inpaint(image_path: str, boxes: "list[dict]", timeout: int = 60) -> "bytes | None":
    """이미지 + 정규화 박스 → LaMa로 복원한 PNG 바이트. 워커 미구성/불통/미가용이면 None(호출부 telea 폴백).
    boxes: [{x0,y0,x1,y1}] (정규화). telea 얼룩 대신 자연 복원 — 워터마크·오버레이 제거 품질↑."""
    if not (configured() and image_path and os.path.exists(image_path) and boxes):
        return None
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        payload = json.dumps([{k: float(b.get(k, 0)) for k in ("x0", "y0", "x1", "y1")} for b in boxes])
        body, boundary = _multipart_file_field("photo", os.path.basename(image_path), data,
                                               "boxes", payload)
        req = urllib.request.Request(
            _url() + "/inpaint", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.headers.get("Content-Type", "")
            out = r.read()
        if "image" not in ct:            # 503/500 등 → JSON 에러 → None(폴백)
            return None
        return out
    except Exception:
        return None       # 불통 → None → telea 폴백


def detect(image_path: str) -> "list[dict] | None":
    """이미지 → 번호판·얼굴 마스킹 박스(정규화). 워커 미구성/불통이면 None(호출부 폴백).
    반환 박스 conf는 마스킹 통과하도록 높게 설정(워커가 이미 자체 임계로 필터)."""
    if not (configured() and image_path and os.path.exists(image_path)):
        return None
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        body, boundary = _multipart("photo", os.path.basename(image_path), data)
        req = urllib.request.Request(
            _url() + "/detect", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            return None
        out = []
        for b in d.get("boxes") or []:
            try:
                out.append({"type": "plate" if b.get("type") == "plate" else "face",
                            "x0": float(b["x0"]), "y0": float(b["y0"]),
                            "x1": float(b["x1"]), "y1": float(b["y1"]),
                            "conf": 0.99})                 # 워커 자체 임계 통과분 → 본체 게이트도 통과시켜 마스킹
            except Exception:
                continue
        return out
    except Exception:
        return None       # 불통 → None → vision 폴백
