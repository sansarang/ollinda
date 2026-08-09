"""
AI 카메라워크 클립 — 사진 1장 → 미세 전진(push-in) 5초 내외 실사 클립 (Veo 3.1 fast).

원칙(사장님 확정 2026-07-30, 루마 실사진 실측):
- 우리 사진이 진짜, 카메라 워크만 AI. 자유 카메라는 금지 — 실측에서 문이 닫히고
  없던 승용차가 생겼다(일반·lite 등급 포함). 유일한 안전 지대 = '사진 안쪽으로 미세 전진만'.
- 생성 후 원본 대조 QC(새 물체·글자 깨짐·피사체 변형) 불통과 → 폐기, 켄번스 폴백.
- 같은 사진 재생성 금지(내용 해시 캐시, 미디어 폴더에 영속) — 재렌더 비용 0.
- 편당 신규 생성 상한(VEO_CLIPS_PER_VIDEO, 기본 2) — 비용 통제. 캐시 히트는 무제한.
- 모든 실패는 조용히 None — 영상 파이프라인을 절대 막지 않는다.

끄기: VEO_CLIP=0. 키: GEMINI_API_KEY(선불 크레딧 소진 시 429 → 자동 켄번스 폴백).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time

log = logging.getLogger("shopcast.aiclip")

MODEL = os.environ.get("VEO_CLIP_MODEL", "veo-3.1-fast-generate-preview")
_API = "https://generativelanguage.googleapis.com/v1beta"
DUR_SEC = 3                      # 생성 길이(초) — 초당 과금이라 최소로, 씬이 더 길면 호출부가 슬로모로 늘림
                                 # (4→3초, 2026-08-09 비용 절감 승인 — 화질 손실 없음: 슬로모 확장 로직 기존)
POLL_CAP = 180                   # 생성 대기 상한(초) — 초과 시 포기(파이프라인 보호)


def _text_risk(img: str) -> bool:
    """사진에 또렷한 글자·숫자가 있으면 True → Veo 생략(켄번스 직행).

    실측(2026-08-09): QC 탈락 전건이 글자 계열이었다 — 레터링·엠블럼 뭉개짐, 시계 숫자 변형,
    번호판 모자이크 침범. 실패할 사진에 시도한 비용이 그날 지출의 대부분이었다.
    로컬 tesseract(무료·문서 PII OCR과 동일 도구)로 사전 감지한다. 판정: conf≥70이고
    이미지 높이 1.2% 이상인 단어가 2개 이상. tesseract 없으면 False(필터 없음 — 파이프라인 불변).
    끄기: VEO_TEXT_FILTER=0. 차단 마커는 남기지 않는다(필터 완화·모델 개선 여지)."""
    if os.environ.get("VEO_TEXT_FILTER", "1") == "0":
        return False
    import shutil as _sh
    if not _sh.which("tesseract"):
        return False
    try:
        import subprocess as _sp
        import tempfile as _tf

        from PIL import Image
        with _tf.TemporaryDirectory(prefix="veotf_") as td:
            small = os.path.join(td, "s.png")
            im = Image.open(img)
            w, h = im.size
            if w > 1600:
                im = im.resize((1600, int(h * 1600 / w)))
            im.convert("RGB").save(small)
            W, H = im.size
            out = os.path.join(td, "o")
            _sp.run(["tesseract", small, out, "-l", "kor+eng", "--psm", "11", "tsv"],
                    capture_output=True, timeout=30)
            if not os.path.exists(out + ".tsv"):
                return False
            strong = 0
            with open(out + ".tsv", encoding="utf-8") as f:
                rows = [r.split("\t") for r in f.read().splitlines()[1:]]
            for r in rows:
                if len(r) < 12:
                    continue
                try:
                    conf, wh, txt = float(r[10]), int(r[9]), (r[11] or "").strip()
                except ValueError:
                    continue
                if conf >= 70 and wh >= H * 0.012 and len(txt) >= 2:
                    strong += 1
                    if strong >= 2:
                        return True
            return False
    except Exception:
        return False                 # 필터 실패는 조용히 무필터 — 영상 파이프라인을 막지 않는다

# 보수 프롬프트 고정 — 실측 통과 조건 그대로(2026-07-30). 임의 완화 금지.
_PROMPT = (
    "Extremely subtle, slow cinematic camera push-in (dolly in) toward the main subject "
    "of the photo. Camera moves forward only a small amount. Stay strictly within the "
    "framing of the original photo - do NOT reveal any area outside the photo, do NOT "
    "rotate or orbit the camera. Everything in the scene stays exactly as photographed: "
    "same objects, same reflections, same lighting. No new objects, no people, no text."
)
_NEG = ("text, letters, captions, watermark, people, hands, new objects, new vehicles, "
        "camera orbit, camera pull back, revealing unseen areas, distortion, warping")


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")) and os.environ.get("VEO_CLIP", "1") != "0"


def _content_hash(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    h.update(MODEL.encode())
    return h.hexdigest()[:16]


def _prep_image(img: str) -> tuple[str, str] | None:
    """전송용 축소(긴 변 1280) → (b64, aspect). 실패 시 None."""
    try:
        from PIL import Image, ImageOps
        with Image.open(img) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            aspect = "9:16" if im.height > im.width * 1.05 else "16:9"
            im.thumbnail((1280, 1280))
            fd, tp = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            im.save(tp, "JPEG", quality=88)
        with open(tp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        os.remove(tp)
        return b64, aspect
    except Exception:
        log.exception("[aiclip] 이미지 준비 실패: %s", img)
        return None


def _generate(img: str, out: str) -> str | None:
    """Veo 생성 → out에 mp4 저장. 실패/타임아웃 None(마커 없음 — 일시 장애는 다음에 재시도)."""
    import requests
    key = os.environ.get("GEMINI_API_KEY")
    prep = _prep_image(img)
    if not (key and prep):
        return None
    b64, aspect = prep
    try:
        r = requests.post(
            f"{_API}/models/{MODEL}:predictLongRunning",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={"instances": [{"prompt": _PROMPT,
                                 "image": {"bytesBase64Encoded": b64, "mimeType": "image/jpeg"}}],
                  "parameters": {"aspectRatio": aspect, "durationSeconds": DUR_SEC,
                                 "resolution": "720p", "negativePrompt": _NEG}},
            timeout=60)
        if r.status_code != 200:
            log.warning("[aiclip] 생성 요청 실패 %s: %s", r.status_code, r.text[:200])
            return None
        op = r.json().get("name")
        if not op:
            return None
        t0 = time.time()
        while time.time() - t0 < POLL_CAP:
            time.sleep(10)
            g = requests.get(f"{_API}/{op}", headers={"x-goog-api-key": key}, timeout=30)
            d = g.json() if g.status_code == 200 else {}
            if d.get("done"):
                if "error" in d:
                    log.warning("[aiclip] 생성 오류: %s", str(d["error"])[:200])
                    return None
                try:
                    uri = d["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"]
                except (KeyError, IndexError):
                    return None
                v = requests.get(uri, headers={"x-goog-api-key": key}, timeout=120)
                if v.status_code != 200 or len(v.content) < 50_000:
                    return None
                with open(out, "wb") as f:
                    f.write(v.content)
                return out
        log.warning("[aiclip] 생성 대기 초과(%ss): %s", POLL_CAP, os.path.basename(img))
        return None
    except Exception:
        log.exception("[aiclip] 생성 실패")
        return None


def _qc(clip: str, orig_img: str) -> "bool | None":
    """원본 대조 QC — True=통과, False=불량(새 물체·글자 깨짐·피사체 변형), None=검사 불가.
    ★ '불량'과 '검사 못 함'을 구분한다(2026-08-01 사장님 지적으로 발견).
      기존엔 둘 다 False로 뭉개져, 비전 API가 잠깐 죽으면(크레딧 소진·타임아웃) 이미 돈을 쓴
      Veo 클립을 버리고 그 사진에 영구 차단 마커까지 찍었다 — 다시는 AI 무빙을 못 받는다.
      검사 불가는 이번 회차만 미사용(켄번스 폴백)하고, 마커는 남기지 않아 다음에 재시도된다."""
    try:
        frames = []
        with tempfile.TemporaryDirectory() as td:
            for i, ss in enumerate(("0", "2", "3.7")):
                fp = os.path.join(td, f"f{i}.jpg")
                subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", clip, "-vframes", "1",
                                "-vf", "scale=540:-2", "-q:v", "5", fp],
                               capture_output=True, timeout=30)
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        frames.append(("image/jpeg", base64.b64encode(f.read()).decode()))
            if len(frames) < 2:
                return None                       # 프레임 추출 실패 = 판정 불가(불량 아님)
            op = os.path.join(td, "orig.jpg")
            subprocess.run(["ffmpeg", "-y", "-i", orig_img, "-vf", "scale=540:-2", "-q:v", "5", op],
                           capture_output=True, timeout=30)
            if not os.path.exists(op):
                return None                       # 원본 축소 실패 = 판정 불가
            with open(op, "rb") as f:
                orig_b64 = ("image/jpeg", base64.b64encode(f.read()).decode())
        from app import llm
        raw = llm.call_task(
            "vision",
            f"첫 번째 이미지는 실제 촬영 원본 사진, 나머지 {len(frames)}장은 그 사진으로 AI가 만든 "
            "영상의 프레임이다. 원본과 대조해 JSON 한 줄로만 답하라.\n"
            "① new_object: 원본에 없던 물체·차량·사람·구조물이 프레임에 생겼으면 true "
            "(줌인으로 원본 일부가 프레임 밖으로 잘려나간 것은 정상=false)\n"
            "② text_broken: 원본에 있던 글자·로고가 프레임에서 뭉개지거나 다른 글자로 변했으면 true "
            "(글자가 프레임 밖으로 벗어나 안 보이는 것은 정상=false)\n"
            "③ subject_changed: 핵심 피사체(차량·제품·시공물)의 형태·색·구조가 원본과 달라졌으면 true\n"
            '형식: {"new_object":false,"text_broken":false,"subject_changed":false,"note":"한 줄"}',
            300, images=[orig_b64] + frames)
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return None                           # 응답 파싱 실패 = 판정 불가
        d = json.loads(m.group(0))
        bad = bool(d.get("new_object") or d.get("text_broken") or d.get("subject_changed"))
        if bad:
            log.warning("[aiclip] QC 탈락: %s (%s)", os.path.basename(clip), str(d.get("note"))[:100])
        return not bad
    except Exception:
        log.warning("[aiclip] QC 검사 불가(비전 호출 실패) — 이번 회차만 미사용, 차단 마커는 남기지 않음")
        return None


class ClipBudget:
    """영상 1편 렌더 동안의 신규 생성 상한 관리 + 통계. 캐시 히트는 상한 미차감."""

    def __init__(self, max_new: int | None = None):
        try:
            self.left = int(os.environ.get("VEO_CLIPS_PER_VIDEO", "2")) if max_new is None else max_new
        except ValueError:
            self.left = 2
        self.used = 0          # 씬에 실제 투입된 클립 수(캐시 포함)
        self.generated = 0     # 이번 렌더에서 새로 생성(과금)된 수
        self.qc_fail = 0
        self.qc_skip = 0                           # 검사 불가(비전 호출 실패) — 불량과 구분
        self.skipped = 0                           # 글자 감지 사전 생략(과금 0) — 2026-08-09

    def stats(self) -> dict:
        return {"used": self.used, "generated": self.generated,
                "qc_fail": self.qc_fail, "qc_skip": self.qc_skip, "skipped": self.skipped}

    def get(self, img: str) -> str | None:
        """img의 AI 클립 경로 또는 None(켄번스 폴백). 캐시 → 생성+QC 순."""
        if not (enabled() and img and os.path.exists(img)):
            return None
        cdir = os.path.dirname(img)
        # 캐시 키 = 파일명 스템(업로드별 uuid) — 재보정으로 픽셀이 바뀌어도 캐시가 산다
        # (2026-08-09 비용 절감 승인: 내용 해시 키는 재보정 시 캐시 미스 → 같은 사진 재과금).
        stem = os.path.splitext(os.path.basename(img))[0]
        cache = os.path.join(cdir, f"{stem}.veoclip.mp4")
        bad = os.path.join(cdir, f"{stem}.veoclip.bad")
        try:                                        # 구 해시 키 캐시 이관(재과금 방지)
            h = _content_hash(img)
            for old, new in ((f"{h}.veoclip.mp4", cache), (f"{h}.veoclip.bad", bad)):
                op = os.path.join(cdir, old)
                if os.path.exists(op) and not os.path.exists(new):
                    os.replace(op, new)
        except OSError:
            pass
        if os.path.exists(cache):
            self.used += 1
            return cache
        if os.path.exists(bad) or self.left <= 0:
            return None
        if _text_risk(img):                         # 글자 사진 = Veo 실패 예정 → 시도 자체를 안 한다
            self.skipped += 1
            log.info("[aiclip] 글자 감지 → Veo 생략(켄번스): %s", os.path.basename(img))
            return None
        # QC 탈락 시 1회 재추첨(2026-08-09 사장님 '클립 수준' 지시) — Veo는 비결정적이라
        # 같은 사진도 추첨마다 결과가 다르다. 실측: 한 세트 시도 2건 전부 탈락 → 무빙 0(슬라이드쇼).
        # 비용 통제 불변: 재추첨도 편당 예산(self.left)을 소모하며, 최종 탈락 후에만 영구 차단.
        retries = max(0, int(os.environ.get("VEO_QC_RETRY", "1")))
        for attempt in range(1 + retries):
            if self.left <= 0:
                return None
            self.left -= 1
            tmp = cache + ".part"
            if not _generate(img, tmp):
                return None                        # 생성 실패(쿼터 등) — 재추첨 무의미, 켄번스 폴백
            self.generated += 1
            _verdict = _qc(tmp, img)
            if _verdict is False:                  # 실제 불량 → 폐기, 마지막 시도까지 탈락 시 영구 차단
                self.qc_fail += 1
                try:
                    os.replace(tmp, bad + f".{attempt}.mp4")   # 진단용 보존
                except OSError:
                    pass
                if attempt < retries:
                    log.info("[aiclip] QC 탈락 → 재추첨 %d/%d: %s",
                             attempt + 1, retries, os.path.basename(img))
                    continue
                try:
                    open(bad, "w").close()         # 재생성 금지 마커(재과금 방지)
                except OSError:
                    pass
                return None
            if _verdict is None:                   # 검사 불가 → 클립은 보관, 마커 없음(다음에 재검사)
                self.qc_skip += 1
                try:
                    os.replace(tmp, cache + ".unverified")
                except OSError:
                    pass
                return None
            os.replace(tmp, cache)
            self.used += 1
            log.info("[aiclip] 생성+QC 통과: %s", os.path.basename(cache))
            return cache
        return None
