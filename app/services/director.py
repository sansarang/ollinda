"""🎬 AI 디렉터 (PHASE 2-B) — 연출 결정권을 로직에서 AI로 이양.
입력: 본문 전문 + 사진 카탈로그 + canonical + 채널 스펙 → 출력: 콘티 JSON(contract/render_v1.json 계약).
원칙(프롬프트 강제): ① 글의 서사 순서 추종(사진 업로드 순서 아님) ② 문장→사진 의미 기반 배정(순서 금지 —
line 대목과 카탈로그 part 대조: 서류 얘기→서류 사진, 엔진룸 얘기→엔진룸 사진) ③ 대응 사진 없으면 카드/생략
(억지 배정 금지) ④ 씬 수·리듬은 콘텐츠가 결정(고정 씬·균일 길이 금지) ⑤ 품질 플래그 사진은 훅·대표 금지.
새 사실 0(본문 사실 범위 내 재작성) — 기존 정직·사실 게이트는 호출부에서 line에 적용. 업종 중립. Haiku 우선."""
from __future__ import annotations

import json
import re

_ROLES = ("hook", "empathy", "reveal", "inspect", "docs", "data_card", "honesty", "cta")
_CHANNEL_SPEC = {
    "naver": {"aspect": "9:16", "dur": 25, "scenes": "5~8"},
    "shorts": {"aspect": "9:16", "dur": 20, "scenes": "5~7"},
    "reels": {"aspect": "1:1", "dur": 20, "scenes": "5~7"},
}


def _catalog_block(catalog: list) -> str:
    lines = []
    for c in catalog:
        fl = ("/플래그:" + ",".join(c.get("flags") or [])) if c.get("flags") else ""
        tx = (f" 글자:'{c['text']}'" if c.get("text") else "")
        lines.append(f"[사진{c.get('id')}] 부위:{c.get('part','')} | {c.get('subject','')} | {c.get('shot','전체')}{tx}{fl}")
    return "\n".join(lines)


def build_storyboard(body: str, catalog: list, canonical: str, channel: str = "naver",
                     data_values: list | None = None, model_hint: str = "") -> dict:
    """콘티 JSON 생성(계약 render_v1). Haiku 1콜 + 스키마 검증 1회 재시도. 실패 시 {}(호출부가 현행 로직 폴백).
    data_values=세트 실값 목록[(value,label)] — data_card는 이 목록에서만(임의 수치 금지)."""
    if not (body and catalog):
        return {}
    from app import llm as _llm
    spec = _CHANNEL_SPEC.get(channel, _CHANNEL_SPEC["naver"])
    dv = "; ".join(f"{v}({l})" for v, l in (data_values or [])) or "(없음 — data_card 쓰지 마라)"
    base = (
        "너는 소상공인 마케팅 영상 디렉터다. 아래 [본문]과 [사진 카탈로그]로 세로 영상 '콘티'를 짜라.\n"
        "콘티는 JSON 하나만 출력(설명·코드블록 없이). 형식:\n"
        '{"version":"render_v1","meta":{"channel":"' + channel + '","aspect":"' + spec["aspect"] + '",'
        '"canonical":"' + (canonical or "") + '"},"scenes":[{"role":..,"line":..,"shot":..,"duration_weight":..,"beat":..}]}\n'
        f"role∈{list(_ROLES)}. shot은 사진배정 {{\"photo_id\":N,\"crop\":\"full|closeup\",\"reason\":\"왜 이 사진\"}} "
        '또는 카드 {"card":{"value":"세트 실값","label":"라벨"}} 택1.\n'
        "★ 연출 원칙(반드시):\n"
        "1. 글의 서사 순서를 따른다 — 사진 업로드 순서가 아니라. 본문이 렌트를 먼저 깠으면 콘티도 먼저 깐다.\n"
        "2. 문장→사진은 '의미'로 배정. line이 서류 얘기면 카탈로그 부위:서류 사진, 엔진룸 얘기면 부위:엔진룸 사진. "
        "순서로 기계 배정 금지. reason에 근거를 적어라.\n"
        "3. 대응 사진이 없는 대목은 card(실값 있으면) 또는 생략. 없는 걸 억지로 사진에 붙이지 마라.\n"
        "4. 씬 수·길이는 콘텐츠가 정한다(권장 " + spec["scenes"] + "씬, 목표 " + str(spec["dur"]) + "초). "
        "duration_weight 균일 금지 — 훅·정직고지·카드 길게(1.5~3), 나열 짧게(0.5~1).\n"
        "5. 품질 플래그(흐림·표식·저해상) 사진은 hook·reveal 등 대표 씬에 쓰지 마라.\n"
        "6. line은 본문에 있는 사실만(새 수치·차종·이력 추가 금지). data_card value는 아래 실값 목록에서만.\n"
        f"\n[채널] {channel} ({spec['aspect']}, ~{spec['dur']}초)\n[canonical] {canonical}\n"
        f"[세트 실값(data_card 전용)] {dv}\n[사진 카탈로그]\n{_catalog_block(catalog)}\n\n[본문]\n{body[:3500]}")
    valid_ids = {c.get("id") for c in catalog}
    feedback = ""
    for _try in (1, 2):
        try:
            raw = _llm.call_task("spoken", base + feedback, max_tokens=1600)   # spoken=Haiku 라우팅(원가)
        except Exception:
            return {}
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            feedback = "\n\n[재시도] JSON 객체 하나만 출력하라."
            continue
        try:
            sb = json.loads(m.group(0))
        except Exception:
            feedback = "\n\n[재시도] 유효한 JSON이 아니다. 형식을 정확히 지켜라."
            continue
        ok, why = _validate(sb, valid_ids)
        if ok:
            sb.setdefault("meta", {})["channel"] = channel
            sb["meta"]["aspect"] = spec["aspect"]
            sb["meta"]["canonical"] = canonical or ""
            return sb
        feedback = f"\n\n[재시도] 콘티 규칙 위반: {why}. 고쳐서 다시."
    return {}


def _validate(sb: dict, valid_ids: set) -> tuple:
    """계약 최소 검증 — scenes 존재·role 유효·shot 유효(photo_id는 실사진 id만)·weight 범위·억지배정 없음."""
    if not isinstance(sb, dict) or not isinstance(sb.get("scenes"), list) or not (2 <= len(sb["scenes"]) <= 12):
        return False, "scenes 2~12개 필요"
    weights = []
    for i, s in enumerate(sb["scenes"]):
        if not isinstance(s, dict):
            return False, f"scene {i} 형식"
        if s.get("role") not in _ROLES:
            return False, f"scene {i} role 무효({s.get('role')})"
        if not (s.get("line") and isinstance(s["line"], str)):
            return False, f"scene {i} line 없음"
        sh = s.get("shot") or {}
        if "photo_id" in sh:
            if sh["photo_id"] not in valid_ids:               # 픽셀 생성/유령 사진 금지 — 실사진 id만
                return False, f"scene {i} photo_id {sh.get('photo_id')} 카탈로그에 없음(억지/유령 배정 금지)"
            if sh.get("crop") not in ("full", "closeup"):
                return False, f"scene {i} crop 무효"
        elif "card" in sh:
            if not (isinstance(sh["card"], dict) and sh["card"].get("value")):
                return False, f"scene {i} card value 없음"
        else:
            return False, f"scene {i} shot 없음(photo 또는 card 필요)"
        try:
            w = float(s.get("duration_weight", 1))
        except Exception:
            return False, f"scene {i} duration_weight 숫자 아님"
        weights.append(w)
    if len(weights) >= 3 and len(set(round(w, 1) for w in weights)) == 1:
        return False, "duration_weight 전부 동일(균일 길이 금지 — 훅·카드 길게)"
    return True, ""
