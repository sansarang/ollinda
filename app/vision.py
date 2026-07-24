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


def _b64_for_vision(image_path: str) -> tuple[str, str]:
    """전송용 (media_type, b64) — 긴 변 1568px·JPEG 재인코딩.
    원본 대용량(스마트폰 4~8MB)은 Anthropic 이미지 제한(5MB/장)에 걸려 폴백 vision이
    침묵 실패(주안 캡션 재분석 청크 1·2 실증). gemini도 작은 페이로드가 안전·저비용."""
    try:
        import io
        from PIL import Image, ImageOps
        im = Image.open(image_path)
        im = ImageOps.exif_transpose(im).convert("RGB")
        if max(im.size) > 1568:
            im.thumbnail((1568, 1568))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()
    except Exception:
        with open(image_path, "rb") as f:
            return _media_type(image_path), base64.standard_b64encode(f.read()).decode()


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
                context: str | None = None) -> str:
    """여러 사진 분석 — 사진 제한 해제(안전 상한 30). 6장 초과는 청크(6장)로 나눠 배치 호출하고
    [사진N] 번호를 전체 기준으로 이어붙임(Gemini 무료 rate limit 대응: 청크 간 짧은 대기)."""
    paths = [p for p in (image_paths or []) if p and os.path.exists(p)][:max_imgs]
    if not (configured() and paths):
        return ""
    if len(paths) == 1:
        return analyze(paths[0], industry_name, context)
    if len(paths) > 6:                                   # 배치 처리(비용·타임아웃·rate limit 관리)
        import re as _r
        import time as _t
        out = []
        for ci in range(0, len(paths), 6):
            chunk = paths[ci:ci + 6]
            part = analyze_all(chunk, industry_name, max_imgs=6,
                               context=(context if ci + 6 >= len(paths) else None))  # 해석·[전체]는 마지막 청크만
            part = _r.sub(r"\[사진(\d+)\]", lambda m: f"[사진{int(m.group(1)) + ci}]", part or "")
            if part:
                out.append(part)
            if ci + 6 < len(paths):
                _t.sleep(2)
        return "\n".join(out).strip()
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
    for ci in range(0, len(uncached), _CH):
        if ci > 0:
            _tq.sleep(float(os.environ.get("SHOPCAST_VISION_GAP", "6")))
        chunk = uncached[ci:ci + _CH]
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
                "part는 '이 사진이 무엇을 보여주는 컷인지'다 — 서류 사진은 '서류', 엔진룸 사진은 '엔진룸'으로 정확히.")
            resp = ""
            for _try in (1, 2):
                resp = (_llm.call_task("vision", prompt, 1400, default_model=MODEL, images=imgs64) or "").strip()
                _calls += 1
                if resp:
                    break
                _tq.sleep(2)
            if not _CATALOG_LAST_RAW:
                _CATALOG_LAST_RAW = resp[:600]
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
            if arr:
                _oks += 1
            for i, e in enumerate(arr):
                if isinstance(e, dict) and i < len(chunk):
                    p, _hh = chunk[i]
                    ent = _norm(e)
                    entries[p] = ent
                    if _hh:
                        _db.save_catalog_cache(_hh, ent)     # 캐시 적재 → 다음 regen 재분석 0
        except Exception:
            continue
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


def detect_personal_info(image_path: str) -> list[dict]:
    """사진 속 개인정보 위치를 정규화 bbox로 반환 → 모자이크용. 실패/무키 시 []."""
    if not (configured() and image_path and os.path.exists(image_path)):
        return []
    try:
        import json
        import re as _re
        _mt3, data = _b64_for_vision(image_path)
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "이 사진에서 '가려야 할 개인정보'의 위치를 모두 찾아라: "
            "차량 번호판, 사람 얼굴, 전화번호, 이름표·차량정보 라벨·차대번호(VIN), 주소·명함.\n"
            "각 항목을 이미지 기준 0~1로 정규화한 사각형으로, JSON 배열만 출력(설명·코드블록 없이):\n"
            '[{"type":"plate|face|phone|label|address","x0":0.00,"y0":0.00,"x1":0.00,"y1":0.00,"conf":0.00}]\n'
            "x0,y0=왼쪽위, x1,y1=오른쪽아래. conf=이게 정말 그 개인정보라는 확신도(0~1, 애매하면 낮게).\n"
            "없으면 [] 만 출력. ★ 정상 차체·배경을 개인정보로 착각하지 마라 — 확신 없으면 아예 넣지 마라(오탐 금지)."
        )
        resp = client.messages.create(
            model=MODEL, max_tokens=700,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": _mt3, "data": data}},
                {"type": "text", "text": prompt},
            ]}])
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        m = _re.search(r"\[.*\]", txt, _re.S)
        boxes = json.loads(m.group(0)) if m else []
        out = []
        for b in boxes:
            if isinstance(b, dict) and all(k in b for k in ("x0", "y0", "x1", "y1")):
                try:
                    b["conf"] = float(b.get("conf", 0.5))     # 미제공 시 중립(0.5) — 게이트가 판단
                except Exception:
                    b["conf"] = 0.5
                out.append(b)
        return out
    except Exception:
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
