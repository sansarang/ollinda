"""
사진 자동 보정 — 폰으로 대충 찍은 사진을 '전문가 톤'으로.
PIL 기반이라 키·결제 없이 항상 작동(즉시). 업종별 톤(음식=먹음직, 상품=선명).
과하지 않게(은은하게) — 원본 왜곡 없이 '잘 찍은 사진' 느낌만.
"""
from __future__ import annotations

import os

try:                                    # HEIC(아이폰 기본 포맷) 지원 — 없으면 조용히 통과(V2)
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

# 음식/먹거리 업종 키워드 — 채도·따뜻함 강조(먹음직)
_FOOD = ("음식", "식당", "맛집", "카페", "베이커리", "빵", "고기", "정육", "분식", "한식",
         "일식", "중식", "양식", "치킨", "피자", "디저트", "떡", "반찬", "포차", "술집", "횟집", "국밥")


def _is_food(industry: str) -> bool:
    ind = industry or ""
    return any(w in ind for w in _FOOD)


def _dms(deg: float):
    """십진 도 → EXIF GPS (도,분,초) 유리수."""
    deg = abs(deg)
    d = int(deg)
    m = int((deg - d) * 60)
    s = int(round((deg - d - m / 60) * 3600 * 100))
    return ((d, 1), (m, 1), (s, 100))


def _build_exif(meta: dict):
    """검색노출용 EXIF — 설명(지역+업종)·키워드·작성자(상호)·GPS(가게 좌표)."""
    try:
        import piexif
        z = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        desc = (meta.get("description") or "")[:250]
        if desc:
            z["0th"][piexif.ImageIFD.ImageDescription] = desc.encode("utf-8", "ignore")
        artist = (meta.get("artist") or "")
        if artist:
            z["0th"][piexif.ImageIFD.Artist] = artist.encode("utf-8", "ignore")
        kw = (meta.get("keywords") or "")
        if kw:
            z["0th"][piexif.ImageIFD.XPKeywords] = kw.encode("utf-16-le")     # Windows 키워드 태그
        lat, lon = meta.get("lat"), meta.get("lon")
        if lat and lon:
            z["GPS"][piexif.GPSIFD.GPSLatitudeRef] = ("N" if lat >= 0 else "S")
            z["GPS"][piexif.GPSIFD.GPSLatitude] = _dms(lat)
            z["GPS"][piexif.GPSIFD.GPSLongitudeRef] = ("E" if lon >= 0 else "W")
            z["GPS"][piexif.GPSIFD.GPSLongitude] = _dms(lon)
        return piexif.dump(z)
    except Exception:
        return None


def auto_enhance(src: str, out: str | None = None, industry: str = "", meta: dict | None = None) -> str:
    """폰 사진 → 전문가 톤 보정 + (meta 있으면) 검색노출용 EXIF·GPS 삽입. 실패 시 원본 경로 반환."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except Exception:
        return src
    try:
        im = Image.open(src)
        im = ImageOps.exif_transpose(im)      # 세로로 찍은 폰 사진이 눕는 문제 방지(V1)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        elif im.mode == "L":
            im = im.convert("RGB")
        # 1) 자동 레벨(칙칙한 폰 사진의 톤 복원) — 극단값 1% 컷
        im = ImageOps.autocontrast(im, cutoff=1)
        food = _is_food(industry)
        # 2) 밝기·대비(은은하게)
        im = ImageEnhance.Brightness(im).enhance(1.06)
        im = ImageEnhance.Contrast(im).enhance(1.08)
        # 3) 채도 — 음식은 먹음직(강), 상품·매장은 자연스럽게(약)
        im = ImageEnhance.Color(im).enhance(1.22 if food else 1.12)
        # 4) 선명도(디테일 살리기)
        im = ImageEnhance.Sharpness(im).enhance(1.25)
        out = out or src
        exif_bytes = _build_exif(meta) if meta else None
        if exif_bytes:
            im.save(out, "JPEG", quality=90, exif=exif_bytes)      # 검색노출용 메타 삽입
        else:
            im.save(out, "JPEG", quality=90)
        return out
    except Exception:
        return src


def enhance_all(paths: list[str], industry: str = "", meta: dict | None = None) -> int:
    """여러 장 일괄 보정(제자리) + 개인정보 자동 모자이크 + EXIF·GPS 삽입. 보정 성공 개수 반환.
    ★ 마스킹은 신뢰도 게이트 통과 박스만(오폭 방지). 부착물(type-c)은 미제거 + attached 플래그(UI 경고)."""
    global _MASK_LAST_LOG
    _MASK_LAST_LOG = []          # 이 배치의 [사진/박스/유형/신뢰도/처리여부] 로그 시작
    attached_photos = []
    n = 0
    for p in paths:
        if p and os.path.exists(p):
            # ★ 순서 고정: 오버레이 인페인트 → PII 모자이크. 반대로 하면 vision이 방금 씌운
            #   번호판 모자이크를 '오버레이'로 오인해 LaMa가 그 위를 복원(마스킹 무효화) — 실측 재발 방지.
            if os.environ.get("SHOPCAST_OVERLAY_REMOVE", "1") != "0":   # 기본 ON. 끄려면 =0
                try:                                                     # 유형 a 국소 오버레이(신뢰도 게이트)
                    _r = remove_overlay(p)
                    if _r.get("attached"):                               # 부착물 가림막 잔존 → UI 경고 대상
                        attached_photos.append(os.path.basename(p))
                except Exception:
                    pass
            mask_personal_info(p)   # 🔒 번호판·얼굴·전화·라벨 자동 가림(신뢰도 게이트)
            if auto_enhance(p, p, industry, meta) == p:
                n += 1
    if attached_photos:                                                  # 배치 요약(호출부가 세트에 경고 저장)
        _MASK_LAST_LOG.append({"src": "summary", "attached_photos": attached_photos})
    return n


def _pixelate_region(im, box) -> bool:
    """정규화 bbox 영역을 모자이크(픽셀화). PIL 기반, 추가 설치 불필요."""
    from PIL import Image
    W, H = im.size
    try:
        x0 = max(0, int(float(box["x0"]) * W)); y0 = max(0, int(float(box["y0"]) * H))
        x1 = min(W, int(float(box["x1"]) * W)); y1 = min(H, int(float(box["y1"]) * H))
    except Exception:
        return False
    pw = int((x1 - x0) * 0.15); ph = int((y1 - y0) * 0.15)   # LLM 박스 정밀도 보정(패딩 15%)
    x0 = max(0, x0 - pw); y0 = max(0, y0 - ph); x1 = min(W, x1 + pw); y1 = min(H, y1 + ph)
    if x1 - x0 < 6 or y1 - y0 < 6:
        return False
    region = im.crop((x0, y0, x1, y1))
    small = region.resize((max(1, (x1 - x0) // 14), max(1, (y1 - y0) // 14)))   # 축소→확대 = 모자이크
    im.paste(small.resize((x1 - x0, y1 - y0), Image.NEAREST), (x0, y0))
    return True


# 좌표 신뢰도 게이트 — 확신 없는 박스는 처리 안 함(오폭 블러보다 워터마크·개인정보 잔존이 낫다).
#   '어설픈 제거보다 원본' 품질게이트 원칙을 좌표 신뢰도에도 적용. 미달 박스는 스킵+로그.
PII_CONF_MIN = float(os.environ.get("SHOPCAST_PII_CONF_MIN", "0.7"))
OVERLAY_CONF_MIN = float(os.environ.get("SHOPCAST_OVERLAY_CONF_MIN", "0.7"))
_MASK_LAST_LOG: list = []   # 진단: 마지막 마스킹의 [사진/박스/유형/신뢰도/처리여부]


def mask_personal_info(path: str) -> int:
    """사진 속 개인정보(번호판·얼굴·전화·라벨·주소) 자동 모자이크(제자리). 가린 개수 반환.
    ★ 신뢰도 게이트: conf<PII_CONF_MIN 박스는 처리 안 함(정상 차체 오폭 방지). 끄기: SHOPCAST_PII_MASK=0."""
    global _MASK_LAST_LOG
    if os.environ.get("SHOPCAST_PII_MASK", "1") == "0":
        return 0
    try:
        from app import vision
        boxes = []
        # ① 번호판·얼굴 — YOLO 워커 우선(크레딧 독립·엔카 로고 등 브랜드 텍스트 오폭 없음).
        #    워커 미배포/불통이면 None → 기존 vision으로 폴백(무중단).
        from app.services import blur_client
        _wb = blur_client.detect(path) if blur_client.configured() else None
        if _wb is not None:
            boxes += _wb                                  # YOLO 번호판·얼굴 박스
        else:
            # 폴백: 워커 없을 때만 LLM vision(얼굴·번호판·라벨) + 촬영 번호판 타일
            boxes += list(vision.detect_personal_info(path))
            if os.environ.get("SHOPCAST_PLATE_TILE", "1") != "0":
                try:
                    boxes += vision.detect_plates_vision(path)
                except Exception:
                    pass
        # ② 문서 식별번호(등록번호·VIN·법인번호) — OCR 담당(워커와 무관, 항상 실행).
        if os.environ.get("SHOPCAST_DOC_PII", "1") != "0":
            try:
                boxes += vision.detect_document_pii(path)
            except Exception:
                pass
        if not boxes:
            return 0
        from PIL import Image
        im = Image.open(path).convert("RGB")
        cnt = 0
        for b in boxes:
            conf = float(b.get("conf", 0.5))
            entry = {"photo": os.path.basename(path), "src": "pii", "type": b.get("type"),
                     "conf": round(conf, 2), "box": [round(float(b.get(k, 0)), 3) for k in ("x0", "y0", "x1", "y1")]}
            if conf < PII_CONF_MIN:                    # 신뢰도 미달 → 스킵(오폭 금지) + 로그
                entry["processed"] = False
                entry["reason"] = f"conf<{PII_CONF_MIN}"
                _MASK_LAST_LOG.append(entry)
                continue
            done = _pixelate_region(im, b)
            entry["processed"] = bool(done)
            if not done:
                entry["reason"] = "box_too_small"
            _MASK_LAST_LOG.append(entry)
            if done:
                cnt += 1
        if cnt:
            im.save(path, quality=90)
        return cnt
    except Exception:
        return 0


# ── A-2/A-3: 워터마크 오버레이 제거(생성AI 금지 — cv2 고전기법·크롭·패치만) ──────────
SMUDGE_REL = 0.45           # 인페인트 영역 std가 '주변 밴드' std의 이 비율 미만이면 얼룩(뭉갬) 의심 → 폴백
_REMOVE_MAX_COV = 0.12      # 유형 a라도 이보다 넓으면 인페인트 부담 → 보류(제거 안 함)


def _norm_box(box: dict, W: int, H: int):
    try:
        x0 = max(0, min(W - 1, int(float(box.get("x0", 0)) * W)))
        y0 = max(0, min(H - 1, int(float(box.get("y0", 0)) * H)))
        x1 = max(x0 + 1, min(W, int(float(box.get("x1", 0)) * W)))
        y1 = max(y0 + 1, min(H, int(float(box.get("y1", 0)) * H)))
        return x0, y0, x1, y1
    except Exception:
        return None


def _cv_inpaint(im, box: dict, method: str = "telea"):
    """cv2.inpaint로 bbox 영역 복원(telea/ns). cv2·numpy 없으면 None(안전 폴백)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    W, H = im.size
    nb = _norm_box(box, W, H)
    if not nb:
        return None
    x0, y0, x1, y1 = nb
    from PIL import Image
    arr = np.array(im.convert("RGB"))[:, :, ::-1].copy()          # RGB→BGR
    mask = np.zeros(arr.shape[:2], np.uint8)
    pw = max(int((x1 - x0) * 0.08), int(W * 0.012)); ph = max(int((y1 - y0) * 0.08), int(H * 0.012))         # LLM 박스 정밀도 보정
    mask[max(0, y0 - ph):min(H, y1 + ph), max(0, x0 - pw):min(W, x1 + pw)] = 255
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    res = cv2.inpaint(arr, mask, 3, flag)
    return Image.fromarray(res[:, :, ::-1])                        # BGR→RGB


def _is_smudge(im, box: dict) -> bool:
    """인페인트 얼룩(뭉갬) 상대 검사 — 영역 std가 '주변 밴드' std보다 크게 낮으면 얼룩.
    어두운/균일 배경(스튜디오 컷)에서는 깨끗한 인페인트도 std가 낮으므로 절대값이 아닌 주변 대비로 판정.
    주변도 균일(밴드 std가 매우 낮음)하면 얼룩 판정 안 함(폴백 오작동 방지)."""
    try:
        from PIL import ImageStat
        g = im.convert("L")
        W, H = im.size
        nb = _norm_box(box, W, H)
        if not nb:
            return False
        x0, y0, x1, y1 = nb
        reg_std = ImageStat.Stat(g.crop((x0, y0, x1, y1))).stddev[0]
        bw = (x1 - x0); bh = (y1 - y0)
        ox0 = max(0, x0 - bw); oy0 = max(0, y0 - bh); ox1 = min(W, x1 + bw); oy1 = min(H, y1 + bh)
        out_std = ImageStat.Stat(g.crop((ox0, oy0, ox1, oy1))).stddev[0]
        if out_std < 12.0:                      # 주변이 충분히 텍스처하지 않으면 판정 안 함(코너 스탬프=대개 배경 위)
            return False
        return reg_std < SMUDGE_REL * out_std   # 텍스처 배경인데 인페인트 영역만 뭉갬 → 얼룩
    except Exception:
        return False


def remove_overlay(path: str, out: str | None = None) -> dict:
    """A-2/A-3: 단일 vision 호출로 '지워야 할 불투명 로고·배지'를 모두 받아(overlays 배열) 각각 telea 인페인트로
    제거(한 사진에 코너 로고+중앙 배지 등 다중 대응, 반복 재탐지 스파이럴 없음 → 반사·글레어 과잉제거 방지).
    유형 b(전면 반투명)·c(부착물)는 제거 안 함. coverage 과대 박스는 개별 skip. 생성AI 금지 — cv2 고전기법만.
    반환 {detected,type,coverage,action,removed,kinds,restored}."""
    global _MASK_LAST_LOG
    rep = {"detected": False, "type": None, "coverage": None, "action": "none",
           "removed": 0, "kinds": [], "restored": False, "attached": False}
    tmp = out or path
    if not (path and os.path.exists(path)):
        return rep
    try:
        from app import vision
        from PIL import Image, ImageOps
        cur = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception:
        return rep
    try:
        det = vision.detect_overlay(path)
    except Exception:
        return rep
    # type-c(피사체 부착물: 번호판 가림막·스티커) — detect_overlay가 present:False,type:c로 알림.
    #   제거 대상 아님이 맞다(오탐 방지) — 단 '지워지지 않는다'를 UI가 경고하도록 attached 기록.
    if det.get("type") == "c":
        rep.update(type="c", attached=True, action="attached_skip")
        _MASK_LAST_LOG.append({"photo": os.path.basename(path), "src": "overlay", "type": "c",
                               "conf": None, "processed": False, "reason": "부착물(가림막)-제거불가·UI경고"})
        return rep
    if not det.get("present"):
        return rep
    rep.update(detected=True, type=det.get("type"), coverage=det.get("coverage"))
    if det.get("type") != "a":                                    # b=전면 반투명(제거 불가·UI 강등)
        rep["action"] = "skip_type_b"
        return rep
    overlays = det.get("overlays") or []
    kinds, skipped_large, skipped_lowconf = [], 0, 0
    gated = []                                                    # 게이트 통과 박스 [(box, entry, kind)]
    for ov in overlays:
        conf = float(ov.get("conf", 0.5))
        cov = ov.get("coverage") or 1.0
        entry = {"photo": os.path.basename(path), "src": "overlay", "type": "a",
                 "kind": ov.get("kind"), "conf": round(conf, 2), "coverage": cov,
                 "box": [round(float(ov.get(k, 0)), 3) for k in ("x0", "y0", "x1", "y1")]}
        if conf < OVERLAY_CONF_MIN:                              # ★ 신뢰도 미달 → 오폭 금지, 스킵+로그
            skipped_lowconf += 1
            entry.update(processed=False, reason=f"conf<{OVERLAY_CONF_MIN}")
            _MASK_LAST_LOG.append(entry)
            continue
        if cov > _REMOVE_MAX_COV:                                # 국소치고 과대 → 오탐 의심 → 이 박스만 skip
            skipped_large += 1
            entry.update(processed=False, reason=f"coverage>{_REMOVE_MAX_COV}")
            _MASK_LAST_LOG.append(entry)
            continue
        box = {k: float(ov.get(k, 0)) for k in ("x0", "y0", "x1", "y1")}
        # ★ LLM 박스는 실제 문구보다 작게 오는 경향(실측: Trust Encar 우상단 잔해) →
        #   변 길이 15%·최소 2% 패딩으로 넉넉히 덮는다(LaMa 경로 포함 — 기존엔 telea만 8% 패딩).
        _pw = max((box["x1"] - box["x0"]) * 0.15, 0.02); _ph = max((box["y1"] - box["y0"]) * 0.15, 0.02)
        box["x0"] = max(0.0, box["x0"] - _pw); box["x1"] = min(1.0, box["x1"] + _pw)
        box["y0"] = max(0.0, box["y0"] - _ph); box["y1"] = min(1.0, box["y1"] + _ph)
        # ★ 코너/가장자리 UI(엔카 뷰어 19/30·화살표 등)는 LLM 좌표가 부정확 → 해당 가장자리로 확장해 확실히 덮음.
        #   (커버리지 게이트 통과분만 여기 옴 = 이미 작은 국소 오버레이라 확장해도 과대 아님)
        if box["x1"] >= 0.86:                                     # 우측 코너 → 오른쪽 끝까지
            box["x1"] = 1.0; box["x0"] = min(box["x0"], 0.85)
        if box["x0"] <= 0.14:                                     # 좌측 코너 → 왼쪽 끝까지
            box["x0"] = 0.0; box["x1"] = max(box["x1"], 0.15)
        if box["y1"] >= 0.90:                                     # 하단 → 아래 끝까지
            box["y1"] = 1.0; box["y0"] = min(box["y0"], 0.90)
        if box["y0"] <= 0.10:                                     # 상단 → 위 끝까지
            box["y0"] = 0.0; box["y1"] = max(box["y1"], 0.10)
        gated.append((box, entry, ov.get("kind")))
    if not gated:
        rep["action"] = ("skip_lowconf" if skipped_lowconf else
                         "skip_large" if skipped_large else "none")
        return rep
    # ── 인페인트: LaMa(blurworker, 딥 인페인트) 우선 → 불통이면 telea(cv2) 폴백 ──
    #    telea는 로고 자리에 얼룩을 남김 → LaMa로 자연 복원(워터마크 제거 품질↑). LaMa 없으면 무중단 telea.
    method = None
    try:
        from app.services import blur_client
        png = blur_client.inpaint(path, [b for b, _, _ in gated]) if blur_client.configured() else None
    except Exception:
        png = None
    if png:
        try:
            import io as _io
            cur = ImageOps.exif_transpose(Image.open(_io.BytesIO(png))).convert("RGB")
            method = "lama"
        except Exception:
            method = None
    if method != "lama":                                         # telea 폴백(박스별)
        for box, _e, _k in gated:
            fixed = _cv_inpaint(cur, box, "telea")
            if fixed is None:
                rep["action"] = "no_cv2"                          # cv2도 없음 → 원본 그대로
                return rep
            cur = fixed
        method = "telea"
    for box, entry, kind in gated:
        entry.update(processed=True, method=method)
        _MASK_LAST_LOG.append(entry)
        if kind:
            kinds.append(kind)
    rep.update(removed=len(gated), kinds=kinds, method=method)
    try:
        cur.save(tmp, "JPEG", quality=92)                         # 제거 결과 저장
    except Exception:
        rep.update(action="error", restored=True)
        return rep
    rep["action"] = "inpainted_partial" if skipped_large else "inpainted"
    # 잔해 재검(최대 2회 — 스파이럴 금지): 문구가 박스보다 커서 조각이 남는 undershoot 대응.
    #   이전 제거 영역 부근(중심점 ±0.08)의 신규 박스만 재인페인트 — 위치 사전확률이 강하므로
    #   근접 박스는 conf 0.5부터 허용(희미한 반토막 글자는 검출 확신이 낮게 옴). 다른 위치 신규 검출은 무시.
    try:
        prev = [dict(b) for b, _, _ in gated]
        for _rpass in range(2):
            det2 = vision.detect_overlay(tmp)
            resid = []
            if det2.get("present") and det2.get("type") == "a":
                for o in (det2.get("overlays") or []):
                    if float(o.get("conf", 0.5)) < 0.5 or (o.get("coverage") or 1.0) > _REMOVE_MAX_COV:
                        continue
                    cx = (float(o.get("x0", 0)) + float(o.get("x1", 0))) / 2
                    cy = (float(o.get("y0", 0)) + float(o.get("y1", 0))) / 2
                    if not any(pb["x0"] - 0.08 <= cx <= pb["x1"] + 0.08
                               and pb["y0"] - 0.08 <= cy <= pb["y1"] + 0.08 for pb in prev):
                        continue
                    b2 = {k: float(o.get(k, 0)) for k in ("x0", "y0", "x1", "y1")}
                    pw2 = max((b2["x1"] - b2["x0"]) * 0.25, 0.03); ph2 = max((b2["y1"] - b2["y0"]) * 0.25, 0.03)
                    b2["x0"] = max(0.0, b2["x0"] - pw2); b2["x1"] = min(1.0, b2["x1"] + pw2)
                    b2["y0"] = max(0.0, b2["y0"] - ph2); b2["y1"] = min(1.0, b2["y1"] + ph2)
                    resid.append(b2)
            if not resid:
                break
            from app.services import blur_client as _bc2
            try:
                png2 = _bc2.inpaint(tmp, resid) if _bc2.configured() else None
            except Exception:
                png2 = None
            if png2:
                import io as _io2
                cur = ImageOps.exif_transpose(Image.open(_io2.BytesIO(png2))).convert("RGB")
            else:
                _cv_ok = True
                for b2 in resid:
                    fixed = _cv_inpaint(cur, b2, "telea")
                    if fixed is None:
                        _cv_ok = False
                        break
                    cur = fixed
                if not _cv_ok:
                    break
            cur.save(tmp, "JPEG", quality=92)
            rep["residual_pass"] = rep.get("residual_pass", 0) + len(resid)
            _MASK_LAST_LOG.append({"photo": os.path.basename(path), "src": "overlay", "type": "a",
                                   "kind": "잔해 재인페인트", "pass": _rpass + 1, "n": len(resid),
                                   "processed": True})
            prev = prev + resid                    # 다음 패스의 근접 판정 기준에 방금 지운 영역 포함
    except Exception:
        pass
    return rep
