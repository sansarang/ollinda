"""
사진 분석(비전) — Claude(opus-4-8) 멀티모달로 업로드 사진을 실제로 '보고' 분석.
업로드당 1회 호출 → 결과를 글/영상 생성 프롬프트에 넣어 '사진과 일치'하게.
키 없으면 "" 반환(graceful, 메모만으로 생성).
"""
from __future__ import annotations

import base64
import os

MODEL = "claude-opus-4-8"


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


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
        with open(image_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        import anthropic
        client = anthropic.Anthropic()
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
        resp = client.messages.create(
            model=MODEL, max_tokens=500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": _media_type(image_path), "data": data}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception:
        return ""


def analyze_all(image_paths: list[str], industry_name: str = "", max_imgs: int = 6,
                context: str | None = None) -> str:
    """여러 사진을 '한 번의 호출'로 전부 분석 — 사진마다 뭐가 담겼는지 + 이어지는 이야기.
    사진을 여러 장 줘도 1장만 반영되던 문제 해결(비전 강화). 실패/무키 시 ""."""
    paths = [p for p in (image_paths or []) if p and os.path.exists(p)][:max_imgs]
    if not (configured() and paths):
        return ""
    if len(paths) == 1:
        return analyze(paths[0], industry_name, context)
    try:
        import anthropic
        content = []
        for i, p in enumerate(paths):
            with open(p, "rb") as f:
                data = base64.standard_b64encode(f.read()).decode()
            content.append({"type": "text", "text": f"[사진{i + 1}]"})
            content.append({"type": "image", "source": {"type": "base64",
                            "media_type": _media_type(p), "data": data}})
        content.append({"type": "text", "text": (
            f"위 사진 {len(paths)}장을 한국 소상공인 마케팅 관점에서 분석하라. 업종: {industry_name or '일반'}.\n"
            "각 사진마다 '[사진N]'으로 구분해서 무엇이 보이는지 구체적으로(피사체·제품·차종·전후 변화·사진 속 글자 그대로).\n"
            "마지막에 '[전체]'로, 사진들이 이어지는 하나의 이야기를 한 줄로(예: 시공 전→과정→완성, 제품→사용→결과).\n"
            "※ 사진에 실제로 보이는 것만. 추측·과장 금지. 각 항목 간결히."
            + ("\n" + _context_block(context) if context is not None else ""))})
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL, max_tokens=1000,
            messages=[{"role": "user", "content": content}])
        return next((b.text for b in resp.content if b.type == "text"), "").strip()
    except Exception:
        return ""


def detect_personal_info(image_path: str) -> list[dict]:
    """사진 속 개인정보 위치를 정규화 bbox로 반환 → 모자이크용. 실패/무키 시 []."""
    if not (configured() and image_path and os.path.exists(image_path)):
        return []
    try:
        import json
        import re as _re
        with open(image_path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "이 사진에서 '가려야 할 개인정보'의 위치를 모두 찾아라: "
            "차량 번호판, 사람 얼굴, 전화번호, 이름표·차량정보 라벨·차대번호(VIN), 주소·명함.\n"
            "각 항목을 이미지 기준 0~1로 정규화한 사각형으로, JSON 배열만 출력(설명·코드블록 없이):\n"
            '[{"type":"plate|face|phone|label|address","x0":0.00,"y0":0.00,"x1":0.00,"y1":0.00}]\n'
            "x0,y0=왼쪽위, x1,y1=오른쪽아래. 없으면 [] 만 출력. 확실한 것만, 넉넉하게 잡아라."
        )
        resp = client.messages.create(
            model=MODEL, max_tokens=700,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": _media_type(image_path), "data": data}},
                {"type": "text", "text": prompt},
            ]}])
        txt = next((b.text for b in resp.content if b.type == "text"), "")
        m = _re.search(r"\[.*\]", txt, _re.S)
        boxes = json.loads(m.group(0)) if m else []
        return [b for b in boxes if isinstance(b, dict) and all(k in b for k in ("x0", "y0", "x1", "y1"))]
    except Exception:
        return []
