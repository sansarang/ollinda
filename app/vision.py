"""
사진 분석(비전) — Claude 멀티모달로 업로드 사진을 실제로 '보고' 분석.
업로드당 1회 호출 → 결과를 글/영상 생성 프롬프트에 넣어 '사진과 일치'하게.
키 없으면 "" 반환(graceful, 메모만으로 생성).

모델: Sonnet 기본(Opus는 멀티이미지에서 30~50s+ → 프론트/인프라 타임아웃으로 '분석 안 됨'.
Sonnet은 사진 분석·오버레이 탐지 품질 충분하면서 ~3배 빠름). env LLM_VISION로 오버라이드 가능.
"""
from __future__ import annotations

import base64
import os

MODEL = os.environ.get("SHOPCAST_VISION_MODEL", "claude-sonnet-5")
_CATALOG_LAST_RAW = ""            # 진단: build_catalog 첫 청크 원시 응답
_CATALOG_CREDIT_EXHAUSTED = False  # vision 콜 전부 빈반환 = 크레딧 고갈 의심(엔드포인트가 사용자 안내)


def configured() -> bool:
    """비전 사용 가능 여부 — 라우팅이 gemini면 GEMINI 키로도 동작(이원화)."""
    from app import llm
    if llm.route("vision")[0] == "gemini" and os.environ.get("GEMINI_API_KEY"):
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _b64_for_vision(image_path: str, max_px: int = 1568) -> tuple[str, str]:
    """전송용 (media_type, b64) — 긴 변 max_px·JPEG 재인코딩.
    원본 대용량(스마트폰 4~8MB)은 Anthropic 이미지 제한(5MB/장)에 걸려 폴백 vision이
    침묵 실패(주안 캡션 재분석 청크 1·2 실증). gemini도 작은 페이로드가 안전·저비용.
    ★ max_px 상향(문서 PII 검출) — 표 안 작은 식별번호가 1568px에선 판독 불가(등록번호·VIN 누락 주범)."""
    try:
        import io
        from PIL import Image, ImageOps
        im = Image.open(image_path)
        im = ImageOps.exif_transpose(im).convert("RGB")
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        # 고해상 문서는 q80로 5MB 이내 유지(작은 글씨 가독은 해상도가 좌우)
        q = 80 if max_px > 1568 else 85
        im.save(buf, "JPEG", quality=q)
        data = buf.getvalue()
        if len(data) > 4_900_000 and max_px > 1568:      # 5MB 제한 방어 — 초과 시 1568로 재축소
            return _b64_for_vision(image_path, 1568)
        return "image/jpeg", base64.standard_b64encode(data).decode()
    except Exception:
        with open(image_path, "rb") as f:
            return _media_type(image_path), base64.standard_b64encode(f.read()).decode()


def _resized_dims(w: int, h: int, max_px: int) -> tuple[float, float]:
    """_b64_for_vision가 긴 변을 max_px로 축소한 뒤의 (w,h) — vision이 픽셀좌표를 반환할 때 정규화 기준."""
    long = max(w, h)
    if long <= max_px:
        return float(w), float(h)
    s = max_px / long
    return w * s, h * s


def _norm_box(b: dict, rw: float, rh: float) -> dict | None:
    """vision 박스를 0~1 정규화로 통일. 모델이 0~1 또는 (리사이즈)픽셀좌표를 섞어 반환 →
    좌표 중 하나라도 1.5 초과면 픽셀로 보고 리사이즈 dims로 나눈다. 좌표계 혼용 버그 방어."""
    try:
        x0, y0, x1, y1 = float(b["x0"]), float(b["y0"]), float(b["x1"]), float(b["y1"])
    except Exception:
        return None
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) > 1.5:      # 픽셀좌표 → 정규화
        x0, y0, x1, y1 = x0 / rw, y0 / rh, x1 / rw, y1 / rh
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return {"x0": max(0.0, min(1.0, x0)), "y0": max(0.0, min(1.0, y0)),
            "x1": max(0.0, min(1.0, x1)), "y1": max(0.0, min(1.0, y1))}


def _media_type(path: str) -> str:
    p = path.lower()
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".webp"):
        return "image/webp"
    if p.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _context_block(context: str) -> str:
    """가게 맥락 주입(의도 오분류 해결) — '무엇'(객관)과 별개로 '이 가게 관점의 해석'을 요구.
    맥락은 해석에만 쓰고 사진에 없는 것을 지어내지 않게 명시. 맥락 없으면 해석 보류."""
    head = (f"[가게 맥락] {context}\n" if (context or "").strip()
            else "[가게 맥락] 없음(업종 미상) — 해석을 단정하지 말고 확신도 low로.\n")
    return (
        head
        + "※ 맥락은 아래 '[해석]'에만 사용하라. 사진에 보이지 않는 사물·상태를 맥락 때문에 있다고 말하지 마라.\n"
        "출력 마지막에 다음 3줄을 반드시 추가하라:\n"
        "[해석] 이 가게 관점에서 이 사진(들)이 무엇에 관한 것인지 한 줄"
        "(예: '썬팅 시공 대상 차량으로 보여요' / 맥락 없으면 '업종을 알려주시면 더 정확해져요')\n"
        "[확신도] high 또는 low 한 단어 — 맥락과 사진이 자연스럽게 맞으면 high, "
        "맥락이 없거나 사진의 의도가 갈리면(예: 차량=시공 대상일 수도 판매 매물일 수도) low\n"
        "[선택지] 확신도 low면 그럴듯한 의도 2~3개를 '|'로 구분해 사장님 말로 짧게"
        "(예: 시공 이야기|판매 매물 / 재배·수확 이야기|매장 판매 상품). high면 '없음'\n"
    )


def analyze(image_path: str, industry_name: str = "", context: str | None = None) -> str:
    """사진 → 마케팅 관점 분석 텍스트. 미설정/실패 시 ""(빈 문자열)."""
    if not (configured() and image_path and os.path.exists(image_path)):
        return ""
    try:
        mt, data = _b64_for_vision(image_path)
        prompt = (
            f"이 사진을 한국 소상공인 마케팅 관점에서 분석하라. 업종: {industry_name or '일반'}.\n"
            "다음을 한국어로 간결히(각 1줄):\n"
            "1) 무엇이 보이는가(피사체/메뉴/제품/차종 등 구체적으로)\n"
            "2) 분위기·색감·구도\n"
            "3) 사진 속 글자(간판/가격표/메뉴판 등 보이면 그대로, 없으면 '없음')\n"
            "4) 마케팅에서 강조하면 좋을 포인트\n"
            "※ 사진에 실제로 보이는 것만. 추측·과장 금지."
            + ("\n" + _context_block(context) if context is not None else "")
        )
        from app import llm
        return llm.call_task("vision", prompt, 500, default_model=MODEL,
                             images=[(mt, data)]).strip()
    except Exception:
        return ""


def analyze_all(image_paths: list[str], industry_name: str = "", max_imgs: int = 30,
                context: str | None = None, progress_cb=None) -> str:
    """여러 사진 분석 — 사진 제한 해제(안전 상한 30). 6장 초과는 청크(6장) 병렬.
    progress_cb(done,total): 진행률 표시용(청크 완료마다 누적 장수 콜백)."""
    paths = [p for p in (image_paths or []) if p and os.path.exists(p)][:max_imgs]
    if not (configured() and paths):
        return ""
    if len(paths) == 1:
        _o = analyze(paths[0], industry_name, context)
        if progress_cb:
            try:
                progress_cb(1, 1)
            except Exception:
                pass
        return _o
    if len(paths) > 6:                                   # 배치 처리 — ★청크 병렬(순차 대기 제거)
        import re as _r
        import threading as _th
        _idxs = list(range(0, len(paths), 6))
        _done = [0]
        _lock = _th.Lock()

        def _do(ci):
            chunk = paths[ci:ci + 6]
            part = analyze_all(chunk, industry_name, max_imgs=6,
                               context=(context if ci + 6 >= len(paths) else None))  # 해석·[전체]는 마지막 청크만
            if progress_cb:                              # 청크 완료 → 누적 장수 보고(정직한 진행)
                with _lock:
                    _done[0] += len(chunk)
                    _d = _done[0]
                try:
                    progress_cb(_d, len(paths))
                except Exception:
                    pass
            return _r.sub(r"\[사진(\d+)\]", lambda m: f"[사진{int(m.group(1)) + ci}]", part or "")

        from concurrent.futures import ThreadPoolExecutor
        _cc = max(1, int(os.environ.get("SHOPCAST_VISION_CONCURRENCY", "4")))
        with ThreadPoolExecutor(max_workers=min(_cc, len(_idxs))) as _ex:
            parts = list(_ex.map(_do, _idxs))               # 입력 순서 보존
        return "\n".join(p for p in parts if p).strip()
    try:
        imgs64 = []
        for i, p in enumerate(paths):
            imgs64.append(_b64_for_vision(p))
        prompt_all = (
            f"위 사진 {len(paths)}장을 한국 소상공인 마케팅 관점에서 분석하라. 업종: {industry_name or '일반'}.\n"
            "각 사진마다 '[사진N]'으로 구분해서 무엇이 보이는지 구체적으로(피사체·제품·차종·전후 변화·사진 속 글자 그대로).\n"
            "마지막에 '[전체]'로, 사진들이 이어지는 하나의 이야기를 한 줄로(예: 시공 전→과정→완성, 제품→사용→결과).\n"
            "촬영 피사체가 아니라 사진 위에 '덧씌워진' 오버레이 그래픽(반투명 로고·문자 스탬프·프레임 밴드 등, 특정 업체·플랫폼명 불문)이 있으면 해당 [사진N] 줄에 '[오버레이]'라고만 덧붙여라. 단, 피사체 자체에 부착·부착물(가림막·스티커 등)은 오버레이가 아니다.\n"
            "※ 사진에 실제로 보이는 것만. 추측·과장 금지. 각 항목 간결히."
            + ("\n" + _context_block(context) if context is not None else ""))
        # 사진 순서 표기는 프롬프트에 명시(각 이미지가 순서대로 [사진N]) — 어댑터는 이미지 나열 후 텍스트
        prompt_all = "이미지들은 순서대로 [사진1]..[사진N]이다.\n" + prompt_all
        from app import llm
        # ★ 재시도 1회 — 빈 반환(과부하·레이트리밋) 시 조용히 진행하면 note에 매물 앵커가 없어 유령 키워드에
        #   납치된다(캐스퍼 사건 1차 원인). 빈/실패면 1회 재시도 후에도 비면 "" 반환(호출부가 앵커부재 처리).
        for _try in (1, 2):
            try:
                _out = (llm.call_task("vision", prompt_all, 1000, default_model=MODEL, images=imgs64) or "").strip()
            except Exception:
                _out = ""
            if _out:
                return _out
            if _try == 1:
                import logging as _lgv
                _lgv.getLogger("shopcast.vision").warning("[vision] analyze_all 빈 반환 — 1회 재시도")
                import time as _tv
                _tv.sleep(2)
        import logging as _lgv2
        _lgv2.getLogger("shopcast.vision").error("[vision] analyze_all 재시도 후에도 빈 반환 (%d장) — note 앵커 부재 위험", len(paths))
        return ""
    except Exception:
        return ""


def build_catalog(image_paths: list[str], industry_name: str = "", max_imgs: int = 30) -> list[dict]:
    """PHASE 2-A: 디렉터의 눈 — 사진별 구조화 카탈로그. 반환 [{id, subject, part, text, shot, flags}].
    part(촬영 부위)는 vision이 '본 대로' 자유 명명(업종 하드코딩 0 — 엔진룸·실내·서류·계기판·외관·휠 등
    사진에 실제 보이는 부위). shot=전체|클로즈업, flags=[흐림|표식|저해상]. 부실/실패 시 []."""
    paths = [p for p in (image_paths or []) if p and os.path.exists(p)][:max_imgs]
    if not (configured() and paths):
        return []
    import json as _j
    import re as _r
    import hashlib as _h
    import time as _tq
    import logging as _lgc
    from app import llm as _llm
    from app import db as _db
    global _CATALOG_LAST_RAW, _CATALOG_CREDIT_EXHAUSTED
    _CATALOG_LAST_RAW = ""
    _CATALOG_CREDIT_EXHAUSTED = False

    def _phash(p):
        try:
            with open(p, "rb") as _f:
                return _h.sha256(_f.read()).hexdigest()[:24]
        except Exception:
            return ""

    def _norm(e):
        return {"subject": str(e.get("subject", ""))[:80], "part": str(e.get("part", ""))[:40],
                "text": str(e.get("text", ""))[:120],
                "shot": ("클로즈업" if "클로즈" in str(e.get("shot", "")) else "전체"),
                "flags": [f for f in (e.get("flags") or []) if f in ("흐림", "표식", "저해상")]}

    # ① 사진 해시 캐시 조회 — 동일 사진 재분석 콜 0(크레딧 절약, regen 재분석 주범 차단)
    entries, uncached = {}, []
    for p in paths:
        _hh = _phash(p)
        _c = _db.get_catalog_cache(_hh) if _hh else None
        if _c:
            entries[p] = _c
        else:
            uncached.append((p, _hh))
    # ② 미캐시만 vision(작은 청크 + 큐) — 큰 청크가 결정적 빈반환 유발(6장 실증), 3장이 안정적. 캐시로 누적.
    _CH = int(os.environ.get("SHOPCAST_CATALOG_CHUNK", "3"))
    _calls, _oks = 0, 0

    def _do_chunk(chunk, model=MODEL):
        """청크 1개 vision 분석 → {path:entry}, calls, ok, raw, to_cache. 스레드 안전(공유상태 미변경·DB 미접근).
        model: 1차 Haiku(빠름/저렴), 서류·텍스트 사진 재판독은 Sonnet(정확)."""
        try:
            imgs64 = [_b64_for_vision(p) for p, _ in chunk]
            prompt = (
                f"이미지들은 순서대로 [사진1]..[사진{len(chunk)}]이다. 업종: {industry_name or '일반'}.\n"
                "각 사진을 마케팅 영상 편집자 관점에서 구조화 분석하라. 사진에 실제 보이는 것만(추측 금지).\n"
                "JSON 배열만 출력(설명·코드블록 없이). 각 원소:\n"
                '{"id":번호,"subject":"주요 피사체 한 줄","part":"촬영 부위(사진에 보이는 그대로 — 예: 외관 전면, '
                '엔진룸, 실내 대시보드, 휠, 계기판, 서류/성능점검부, 트렁크 등. 특정 업종 어휘 강요 말고 실제 보이는 부위명)",'
                '"text":"사진 속 글자 그대로(서류 항목·수치 등, 없으면 빈칸)","shot":"전체|클로즈업",'
                '"flags":["흐림"|"표식"|"저해상" 중 해당되는 것만, 없으면 빈 배열]}\n'
                "part는 '이 사진이 무엇을 보여주는 컷인지'다 — 서류 사진은 '서류', 엔진룸 사진은 '엔진룸'으로 정확히. "
                "★ 서류·계기판·표지판 등 글자가 있으면 text에 숫자·항목까지 정확히 옮겨라.")
            resp, calls = "", 0
            for _try in (1, 2):
                resp = (_llm.call_task("vision", prompt, 1400, default_model=model, images=imgs64) or "").strip()
                calls += 1
                if resp:
                    break
                _tq.sleep(1)
            m = _r.search(r"\[.*\]", resp, _r.S)
            arr = []
            if m:
                try:
                    arr = _j.loads(m.group(0))
                except Exception:
                    for mm in _r.finditer(r"\{[^{}]*\}", m.group(0)):
                        try:
                            arr.append(_j.loads(mm.group(0)))
                        except Exception:
                            pass
            res, to_cache = {}, []
            for i, e in enumerate(arr):
                if isinstance(e, dict) and i < len(chunk):
                    p, _hh = chunk[i]
                    ent = _norm(e)
                    res[p] = ent
                    if _hh:
                        to_cache.append((_hh, ent))
            return {"entries": res, "calls": calls, "ok": 1 if arr else 0, "raw": resp[:600], "cache": to_cache}
        except Exception:
            return {"entries": {}, "calls": 0, "ok": 0, "raw": "", "cache": []}

    def _needs_sonnet(ent):
        """서류·계기판·텍스트/수치 사진 = 정확 판독 필요 → Sonnet 재판독 대상. 나머지는 Haiku로 확정."""
        part = ent.get("part") or ""
        text = ent.get("text") or ""
        if any(k in part for k in ("서류", "점검", "등록", "문서", "계기", "번호", "명세", "증", "표지", "라벨", "스티커")):
            return True
        if text and _r.search(r"\d{2,}", text):              # 의미있는 수치(주행거리·연식 등) → 정확 재판독
            return True
        return False

    # ★ 하이브리드: 1차 Haiku(빠름·저렴) 병렬 → 서류/텍스트 사진만 Sonnet 재판독(정확). 캐시는 스레드 밖 저장.
    _MODEL1 = _llm.HAIKU if os.environ.get("SHOPCAST_CATALOG_HYBRID", "1") != "0" else MODEL
    from concurrent.futures import ThreadPoolExecutor
    _cc = max(1, int(os.environ.get("SHOPCAST_VISION_CONCURRENCY", "4")))

    def _run(chs, model):
        if not chs:
            return []
        if len(chs) == 1:
            return [_do_chunk(chs[0], model)]
        with ThreadPoolExecutor(max_workers=min(_cc, len(chs))) as _ex:
            return list(_ex.map(lambda c: _do_chunk(c, model), chs))

    _chunks = [uncached[ci:ci + _CH] for ci in range(0, len(uncached), _CH)]
    for _r2 in _run(_chunks, _MODEL1):                       # 1차 Haiku
        entries.update(_r2["entries"])
        _calls += _r2["calls"]
        _oks += _r2["ok"]
        if not _CATALOG_LAST_RAW and _r2["raw"]:
            _CATALOG_LAST_RAW = _r2["raw"]
        for _hh, _ent in _r2["cache"]:
            _db.save_catalog_cache(_hh, _ent)
    if _MODEL1 != MODEL:                                     # 2차 Sonnet — 서류/텍스트 사진만 재판독
        _doc = [(p, _hh) for (p, _hh) in uncached if p in entries and _needs_sonnet(entries[p])]
        _dchunks = [_doc[ci:ci + _CH] for ci in range(0, len(_doc), _CH)]
        for _r3 in _run(_dchunks, MODEL):
            entries.update(_r3["entries"])                   # Sonnet 판독으로 교체
            _calls += _r3["calls"]
            _oks += _r3["ok"]
            for _hh, _ent in _r3["cache"]:
                _db.save_catalog_cache(_hh, _ent)            # 캐시도 Sonnet 판독으로 덮음
        if _doc:
            _lgc.getLogger("shopcast.vision").info("[catalog] 하이브리드: Haiku %d장 + Sonnet 재판독 %d장",
                                                   len(uncached), len(_doc))
    # ③ 크레딧 고갈 감지 — 미캐시가 있었는데 vision 콜이 전부 빈반환 = 크레딧 고갈 의심(조용한 실패 금지)
    if uncached and _calls > 0 and _oks == 0:
        _CATALOG_CREDIT_EXHAUSTED = True
        _lgc.getLogger("shopcast.vision").error(
            "[catalog] vision %d콜 전부 빈반환 — 크레딧 고갈 의심(레이트 아님). 사용자 안내 필요.", _calls)
    # ④ paths 순서로 id 할당
    out = []
    for idx, p in enumerate(paths, 1):
        if p in entries:
            _e = dict(entries[p])
            _e["id"] = idx
            out.append(_e)
    return out


# 문서 식별번호 정규식(업종 중립) — OCR 텍스트에서 매칭. 날짜·주행거리·금액은 형식이 달라 자동 제외.
# 문서 식별번호 정규식(업종 중립·strict). 번호판은 한글 중간자 필수 → 주행거리·날짜·제원 과매칭 0.
#   (서버에 tessdata_best kor 탑재로 한글 '다' 판독 보장 — Dockerfile.)
_DOC_ID_PATTERNS = [
    ("plate",   r"\d{2,3}[가-힣]\d{4}"),               # 차량 등록번호 370다4358 (한글 필수 = 과매칭 없음)
    ("vin",     r"[A-HJ-NPR-Z0-9]{16,17}"),            # 차대번호 VIN(17자리)
    ("rrn",     r"\d{6}-\d{7}"),                       # 주민/법인 등록번호(하이픈)
    ("bizno",   r"\d{3}-\d{2}-\d{5}"),                 # 사업자등록번호
    ("docno",   r"\d{2,3}-\d{2,3}-\d{5,6}|\d{2,3}-\d{5,6}"),  # 문서발급/확인번호 98-90-061766
    ("phone",   r"01[016-9]-?\d{3,4}-?\d{4}"),         # 휴대전화
]


def detect_document_pii(image_path: str) -> list[dict]:
    """문서 식별번호를 OCR+strict 정규식으로 국소화 → 정확한 단어 bbox(vision bbox 실패 근본해결).
    OCR은 psm 3(기본)+psm 11(sparse) 이중 패스 — 조밀한 표의 작은 등록번호를 기본 세그가 누락하는
    문제 근본해결. 번호판은 한글 중간자 필수라 주행거리·날짜·제원 과매칭 없음. 인접 셀 오병합 방지 위해
    x간격 크면 분리자 삽입 + 컴팩트 병행. tesseract 없으면 [](graceful). 업종 중립."""
    import shutil as _sh
    if not (image_path and os.path.exists(image_path) and _sh.which("tesseract")):
        return []
    try:
        import re as _re
        import subprocess as _sp
        import tempfile as _tf
        from collections import defaultdict
        from PIL import Image
        W, H = Image.open(image_path).size

        def _ocr_words(psm):
            with _tf.TemporaryDirectory(prefix="ocr_") as td:
                out = os.path.join(td, "o")
                _sp.run(["tesseract", image_path, out, "-l", "kor+eng", "--psm", psm, "tsv"],
                        capture_output=True, timeout=90)
                tsv = ""
                if os.path.exists(out + ".tsv"):
                    with open(out + ".tsv", encoding="utf-8") as f:
                        tsv = f.read()
            ws = []
            for line in tsv.splitlines()[1:]:
                c = line.split("\t")
                if len(c) >= 12 and c[11].strip():
                    try:
                        if float(c[10]) < 30:
                            continue
                        # psm 접두로 줄키 분리(패스 간 블록/문단 번호 충돌 방지)
                        ws.append((c[11].strip(), int(c[6]), int(c[7]), int(c[8]), int(c[9]),
                                   (psm, c[2], c[3], c[4])))
                    except Exception:
                        continue
            return ws

        # psm 3(기본 레이아웃): 대부분 검출. psm 11(sparse text): 조밀한 표의 작은 식별번호 추가 검출
        #   (등록증 ①번 등록번호는 기본 세그멘테이션이 통째로 누락 → psm 11이 근본해결). 박스는 dedup.
        words = _ocr_words("3") + _ocr_words("11")
        lines = defaultdict(list)
        for w in words:
            lines[w[5]].append(w)
        # 페이지 대표 글자높이(중앙값) — 셀 경계선(|)이 병합돼 부풀려진 토큰의 세로범위 클램프 기준
        _hs = sorted(w[4] for w in words) if words else []
        med_h = _hs[len(_hs) // 2] if _hs else 10
        boxes = []
        seen = set()
        for lk, ws in lines.items():
            ws.sort(key=lambda w: w[1])
            # 두 문자열로 매칭: (1) s=셀경계 분리자 삽입판(오병합 방지), (2) s_c=분리자 없는 컴팩트판.
            #   표 셀 간격 때문에 '370  다4358'처럼 끊겨도 컴팩트판이 등록번호를 잡는다(누락 근본해결).
            s, owner = "", []
            prev_x1 = None
            avg_h = (sum(w[4] for w in ws) / len(ws)) if ws else 10
            s_c, owner_c = "", []
            for wi, w in enumerate(ws):
                if prev_x1 is not None and (w[1] - prev_x1) > 1.2 * avg_h:  # 큰 x간격 = 셀 경계 → 분리
                    s += "  "; owner.append(-1); owner.append(-1)
                for _ in w[0]:
                    owner.append(wi); owner_c.append(wi)
                s += w[0]; s_c += w[0]
                prev_x1 = w[1] + w[3]
            for src, own in ((s, owner), (s_c, owner_c)):
                for name, pat in _DOC_ID_PATTERNS:
                    for m in _re.finditer(pat, src):
                        if name == "plate" and any(ch in "원년월일만천억개명회" for ch in m.group()):
                            continue                           # '100원0041' 등 단위/통화·날짜 오매칭 배제(호=구형번호판은 유지)
                        idx = {i for i in own[m.start():m.end()] if i >= 0}
                        xs = [ws[i] for i in idx]
                        if not xs:
                            continue
                        # 각 토큰 세로범위를 대표 글자높이로 클램프(중심 기준) → 셀 경계선(|)이 병합돼
                        #   높이 부풀려진 토큰이 박스를 인접 행까지 확장하는 문제 근본해결(차명 등 오마스킹 방지).
                        cap = 1.8 * med_h
                        def _top(w): return (w[2] + w[4] / 2) - min(w[4], cap) / 2
                        def _bot(w): return (w[2] + w[4] / 2) + min(w[4], cap) / 2
                        x0 = min(w[1] for w in xs); y0 = min(_top(w) for w in xs)
                        x1 = max(w[1] + w[3] for w in xs); y1 = max(_bot(w) for w in xs)
                        if (x1 - x0) > 0.5 * W:                # 과폭 = 오병합 방어
                            continue
                        key = (name, round(x0 / max(W, 1), 3), round(y0 / max(H, 1), 3))
                        if key in seen:                        # 두 문자열 매칭 중복 제거
                            continue
                        seen.add(key)
                        px = (x1 - x0) * 0.12; py = (y1 - y0) * 0.3
                        boxes.append({"type": "doc:" + name, "value": m.group()[:40],
                                      "x0": max(0.0, (x0 - px) / W), "y0": max(0.0, (y0 - py) / H),
                                      "x1": min(1.0, (x1 + px) / W), "y1": min(1.0, (y1 + py) / H),
                                      "conf": 0.95})
        return boxes
    except Exception:
        return []


_LAST_VISION_RAW = ""   # 진단용: 마지막 detect_personal_info vision 원문(pii-test에서 노출)


def detect_plates_vision(image_path: str) -> list[dict]:
    """번호판 전용 집중 패스. Anthropic vision이 긴변 ~1568px로 리사이즈 → 큰 페이지의 작은 촬영
    번호판은 통째로 놓친다. 이미지를 2x2 타일(겹침)로 나눠 각 타일에 번호판만 묻고 좌표를 전체로
    환산·union → 촬영 실물 번호판(점검부·매물 사진의 앞뒤 번호판) 국소화. 무키/실패 시 []."""
    global _LAST_VISION_RAW
    if not (configured() and image_path and os.path.exists(image_path)):
        return []
    try:
        import json
        import re as _re
        import anthropic
        from PIL import Image
        W, H = Image.open(image_path).size
        client = anthropic.Anthropic()
        prompt = (
            "이 이미지에서 '자동차 번호판'만 찾아라(한국 번호판 예: '370다4358', '12가3456'). "
            "인쇄된 번호판, 그리고 ★사진 속 실제 차량 앞·뒤·측면에 부착된 번호판★ 모두 포함. "
            "번호판이 아닌 것(일반 숫자·차체·배경)은 넣지 마라. 각 번호판을 0~1 정규화 사각형으로.\n"
            'JSON 배열만: [{"x0":0.00,"y0":0.00,"x1":0.00,"y1":0.00,"conf":0.00}]  없으면 [].'
        )
        # 2x2 타일(0.12 겹침) — 작은 촬영 번호판을 vision 유효해상도 내로 확대
        ov = 0.12
        tiles = [(0.0, 0.0, 0.5 + ov, 0.5 + ov), (0.5 - ov, 0.0, 1.0, 0.5 + ov),
                 (0.0, 0.5 - ov, 0.5 + ov, 1.0), (0.5 - ov, 0.5 - ov, 1.0, 1.0)]
        out = []
        raws = []
        import tempfile as _tf
        for (fx0, fy0, fx1, fy1) in tiles:
            px0, py0, px1, py1 = int(fx0 * W), int(fy0 * H), int(fx1 * W), int(fy1 * H)
            with _tf.TemporaryDirectory(prefix="tile_") as td:
                tp = os.path.join(td, "t.jpg")
                Image.open(image_path).convert("RGB").crop((px0, py0, px1, py1)).save(tp, quality=92)
                _mt, data = _b64_for_vision(tp, max_px=1568)
            resp = client.messages.create(
                model=MODEL, max_tokens=600,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": _mt, "data": data}},
                    {"type": "text", "text": prompt}]}])
            txt = next((b.text for b in resp.content if b.type == "text"), "")
            raws.append(txt[:200])
            m = _re.search(r"\[.*\]", txt, _re.S)
            if not m:
                continue
            try:
                arr = json.loads(m.group(0))
            except Exception:
                continue
            tw, th = (px1 - px0), (py1 - py0)
            trw, trh = _resized_dims(tw, th, 1568)        # 타일 리사이즈 dims(픽셀좌표 정규화 기준)
            for b in arr:
                if not (isinstance(b, dict) and all(k in b for k in ("x0", "y0", "x1", "y1"))):
                    continue
                nb = _norm_box(b, trw, trh)               # 타일 내 0~1로 통일(픽셀/정규화 혼용 방어)
                if nb is None:
                    continue
                try:
                    # 타일 정규화 좌표 → 전체 이미지 정규화 좌표
                    gx0 = (px0 + nb["x0"] * tw) / W; gy0 = (py0 + nb["y0"] * th) / H
                    gx1 = (px0 + nb["x1"] * tw) / W; gy1 = (py0 + nb["y1"] * th) / H
                    conf = float(b.get("conf", 0.7))
                except Exception:
                    continue
                if gx1 <= gx0 or gy1 <= gy0 or (gx1 - gx0) > 0.6 or (gy1 - gy0) > 0.4:
                    continue                          # 과대 박스 = 오탐 방어
                pxp = (gx1 - gx0) * 0.10; pyp = (gy1 - gy0) * 0.18   # 위치 미세오차 대비 여유 패딩
                out.append({"type": "plate", "x0": max(0.0, gx0 - pxp), "y0": max(0.0, gy0 - pyp),
                            "x1": min(1.0, gx1 + pxp), "y1": min(1.0, gy1 + pyp), "conf": conf})
        # 타일 겹침 중복 제거(중심 근접)
        dedup = []
        for b in out:
            cx, cy = (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2
            if any(abs(cx - (d["x0"] + d["x1"]) / 2) < 0.05 and abs(cy - (d["y0"] + d["y1"]) / 2) < 0.05
                   for d in dedup):
                continue
            dedup.append(b)
        _LAST_VISION_RAW = (_LAST_VISION_RAW + " || plates:" + " ; ".join(raws))[-1500:]
        return dedup
    except Exception:
        import traceback as _tb
        _LAST_VISION_RAW = "plate_EXC: " + _tb.format_exc()[-400:]
        return []


def detect_personal_info(image_path: str) -> list[dict]:
    """사진 속 개인정보 위치를 정규화 bbox로 반환 → 모자이크용. 실패/무키 시 []."""
    if not (configured() and image_path and os.path.exists(image_path)):
        return []
    try:
        import json
        import re as _re
        from PIL import Image as _Im
        _ow, _oh = _Im.open(image_path).size
        _mx = int(os.environ.get("SHOPCAST_PII_MAX_PX", "2048"))
        _rw, _rh = _resized_dims(_ow, _oh, _mx)
        # ★ 고해상(2048)으로 문서 표 안 작은 식별번호도 판독 가능하게(1568에선 등록번호·VIN 누락)
        _mt3, data = _b64_for_vision(image_path, max_px=_mx)
        import anthropic
        client = anthropic.Anthropic()
        # 업종 중립 — '가려야 할 식별번호/개인정보' 범주를 전수 열거(자동차·부동산·의료·일반 동일 틀).
        prompt = (
            "이 이미지에서 '가려야 할 개인정보·식별번호'를 하나도 빠짐없이 찾아라. 문서(등록증·점검부·계약서·"
            "신분증·명함 등)라면 표·작은 글씨 칸 안까지 반드시 훑어라. 유형:\n"
            "  · 차량 번호판(예 '370다4358') — 문서에 인쇄된 번호뿐 아니라 ★사진 속 실제 차량에 부착된 "
            "번호판(앞·뒤·측면 모두)도 반드시 각각 별도 박스로 포함★. 점검부·매물 사진에 차 앞뒤 번호판이 흔히 찍혀 있다.\n"
            "  · 차대번호 VIN(17자리 영숫자, 예 KMHF141DBNA491921)\n"
            "  · 주민등록번호(000000-0000000), 사업자등록번호(000-00-00000), 법인등록번호(000000-0000000)\n"
            "  · 계좌번호, 카드번호, 전화번호, 여권번호, 운전면허번호, 문서확인·발급번호\n"
            "  · 사람 얼굴, 주소, 이름·상호가 적힌 이름표\n"
            "각 항목을 이미지 기준 0~1 정규화 사각형으로. JSON 배열만 출력(설명·코드블록 없이):\n"
            '[{"type":"plate|vin|rrn|bizno|corpno|account|card|phone|passport|license|docno|face|address|label",'
            '"x0":0.00,"y0":0.00,"x1":0.00,"y1":0.00,"conf":0.00}]\n'
            "x0,y0=왼쪽위, x1,y1=오른쪽아래. 박스는 그 번호/정보에 딱 맞게(주변 여백 최소).\n"
            "conf=이게 정말 그 식별정보라는 확신도(0~1). 문서의 번호칸·사진 속 번호판이 확실하면 0.85 이상.\n"
            "★ 문서라면 번호가 여러 칸에 흩어져 있다 — 등록번호·차대번호·법인번호를 각각 별도 박스로 빠짐없이. "
            "차체·배경 자체는 넣지 마라(오탐 금지) — 단 ★차체에 부착된 번호판은 반드시 포함★. "
            "일반 텍스트(설명문·날짜·주행거리 수치)는 제외. 없으면 [] 만."
        )
        resp = client.messages.create(
            model=MODEL, max_tokens=1200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": _mt3, "data": data}},
                {"type": "text", "text": prompt},
            ]}])
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        global _LAST_VISION_RAW
        _LAST_VISION_RAW = ("main:" + txt[:400])
        m = _re.search(r"\[.*\]", txt, _re.S)
        boxes = json.loads(m.group(0)) if m else []
        out = []
        for b in boxes:
            if isinstance(b, dict) and all(k in b for k in ("x0", "y0", "x1", "y1")):
                nb = _norm_box(b, _rw, _rh)               # 픽셀/정규화 혼용 통일(좌표 스케일 버그 방어)
                if nb is None:
                    continue
                nb["type"] = b.get("type", "pii")
                try:
                    nb["conf"] = float(b.get("conf", 0.5))    # 미제공 시 중립(0.5) — 게이트가 판단
                except Exception:
                    nb["conf"] = 0.5
                out.append(nb)
        return out
    except Exception:
        import traceback as _tb
        _LAST_VISION_RAW = "main_EXC: " + _tb.format_exc()[-400:]
        return []


def detect_overlay(image_path: str) -> dict:
    """A-1: 사진 위 '오버레이성 표식' 구조화 판별 — 업체·플랫폼명 하드코딩 0(일반 '피사체가 아닌 덧씌운 그래픽' 판별).
    한 번의 호출로 '지워야 할 불투명 로고·문자·배지'를 모두 배열로 반환(반복 재탐지 스파이럴 방지).
    반환 {present, type, x0..y1, coverage, kind, overlays:[{x0..y1,coverage,kind}, ...]}. type:
      a=국소 불투명 로고·배지(위치 무관)  → 제거 대상(overlays에 개별 박스)
      b=전면 반투명형(넓게 깔림)          → 제거 불가(원본 유지·강등)
      c=피사체 부착물(번호판 가림막 등)   → 오버레이 아님(본인 가린 개인정보 오탐 금지)
    ★ 반사·글레어·흐림 얼룩·피사체 자체 무늬는 오버레이 아님. 확신 없으면 present=False. 무키/실패 시 {present:False}."""
    if not (configured() and image_path and os.path.exists(image_path)):
        return {"present": False}
    try:
        import json
        import re as _re
        _mt, data = _b64_for_vision(image_path)
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "이 사진 위에 '촬영된 피사체가 아니라 나중에 덧씌워진 불투명 그래픽'(로고·브랜드 문자·배지·"
            "라벨·페이지 카운터·재생 UI 등)을 모두 찾아라. 특정 업체·플랫폼·브랜드명과 무관하게 판단한다.\n"
            "반드시 '지워야 할 것'만: 뚜렷하고 불투명한 인공 그래픽. 다음은 오버레이가 '아니다'(절대 포함 금지):\n"
            "  · 유리·차체에 비친 반사/글레어, 흐릿한 얼룩·그림자, 피사체 자체의 무늬·엠블럼·번호판\n"
            "  · 화면을 넓게 덮는 전면 반투명 워터마크 밴드(이건 제거 불가 유형 b)\n"
            "  · 피사체에 물리적으로 부착된 종이·가림막·스티커(유형 c)\n"
            "JSON 객체 하나만 출력(설명·코드블록 없이):\n"
            '{"present":true|false,"type":"a|b|c","overlays":[{"x0":0.0,"y0":0.0,"x1":0.0,"y1":0.0,"coverage":0.0,"conf":0.0,"kind":"무엇"}]}\n'
            "overlays=지워야 할 불투명 그래픽들의 배열. x0,y0=왼쪽위 x1,y1=오른쪽아래(0~1). 박스는 그래픽 범위에 딱 맞게(여백 최소).\n"
            "conf=이게 정말 '덧씌운 인공 그래픽'이라는 확신도(0~1). 정상 차체·반사·무늬면 애초에 넣지 말고, 애매하면 conf를 낮게.\n"
            "type: 지울 국소 그래픽이 하나라도 있으면 'a', 전면 반투명뿐이면 'b', 부착물뿐이면 'c'.\n"
            "지울 그래픽이 없거나 확신 없으면 present=false, overlays=[]. 반사·흐림을 그래픽으로 착각하지 마라. 오탐보다 미탐이 낫다."
        )
        resp = client.messages.create(
            model=MODEL, max_tokens=500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": _mt, "data": data}},
                {"type": "text", "text": prompt},
            ]}])
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        m = _re.search(r"\{.*\}", txt, _re.S)
        d = json.loads(m.group(0)) if m else {}
        if not isinstance(d, dict) or not d.get("present"):
            return {"present": False}
        if d.get("type") == "c":                              # 피사체 부착물 → 오버레이 아님(오탐 방지)
            return {"present": False, "type": "c"}
        ovs = [o for o in (d.get("overlays") or []) if isinstance(o, dict)
               and all(k in o for k in ("x0", "y0", "x1", "y1"))]
        for o in ovs:                                         # conf 정규화(미제공 시 중립 0.5 — 게이트가 판단)
            try:
                o["conf"] = float(o.get("conf", 0.5))
            except Exception:
                o["conf"] = 0.5
        if d.get("type") == "a" and not ovs:                  # a인데 박스 없음 → 신뢰 불가
            return {"present": False}
        d["overlays"] = ovs
        if ovs:                                               # 하위호환: 대표(첫) 박스를 top-level에도
            first = ovs[0]
            for k in ("x0", "y0", "x1", "y1", "coverage", "kind"):
                d.setdefault(k, first.get(k))
        return d
    except Exception:
        return {"present": False}
