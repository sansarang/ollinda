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
# dur=목표초, dmin/dmax=채널 예산(콘티 검증 조건 — 초과 시 반려·재생성). 예산은 렌더 실측 길이 기준.
_CHANNEL_SPEC = {
    "naver": {"aspect": "9:16", "dur": 45, "dmin": 30, "dmax": 60, "scenes": "6~9"},
    "shorts": {"aspect": "9:16", "dur": 30, "dmin": 25, "dmax": 35, "scenes": "5~7"},
    "reels": {"aspect": "1:1", "dur": 28, "dmin": 20, "dmax": 35, "scenes": "5~7"},
}
# 한국어 TTS 실측 근사: 씬 길이 ≈ 글자수×CPS_SEC + 씬당 여유. 렌더가 TTS 길이만 쓰도록 정합(weight 인플레 제거).
#   실측 보정: 숫자·문장부호로 낭독이 느려 CPS≈0.22였음 → 0.23(약간 보수적, 상한 초과 방지 마진).
_CPS_SEC = 0.23
_SCENE_PAD = 0.4


def estimate_duration(scenes: list) -> float:
    """콘티 line 길이로 총 렌더 길이 추정(예산 게이트용). 렌더는 TTS 실측을 쓰므로 근사값이다."""
    tot = 0.0
    for s in scenes:
        ln = len((s or {}).get("line") or "")
        tot += min(15.0, max(2.2, ln * _CPS_SEC + _SCENE_PAD))
    return round(tot, 1)


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
        "4. ★ 길이 예산 필수: 이 영상은 나레이션 합계가 반드시 " + str(spec.get("dmin", 20)) + "~"
        + str(spec.get("dmax", 60)) + "초여야 한다(목표 " + str(spec["dur"]) + "초, 권장 " + spec["scenes"] + "씬). "
        "한국어 나레이션은 대략 글자수×0.17초가 걸린다 — line이 길면 씬을 줄이고, 각 line은 핵심만 짧게 써라. "
        "duration_weight 균일 금지 — 훅·정직고지·카드 길게(1.5~3), 나열 짧게(0.5~1).\n"
        "5. 품질 플래그(흐림·표식·저해상) 사진은 hook·reveal 등 대표 씬에 쓰지 마라.\n"
        "6. line은 본문에 있는 사실만(새 수치·차종·이력 추가 금지). data_card value는 아래 실값 목록에서만.\n"
        "   ★ 가격: '판매가'로 말할 수 있는 건 아래 실값의 판매가뿐이다. 서류의 출고가·취득가를 판매가처럼 쓰지 마라. "
        "출고가를 굳이 쓰려면 반드시 '신차 출고가 N' 처럼 항목명을 붙여 대비로만. 판매가 실값이 없으면 가격은 아예 말하지 마라.\n"
        "   ★ 자막이 계기판 숫자·기록부 수치 등 '사진에서 읽어야 할 증거'를 지목하면, 그 사진 shot의 crop은 full로 둬라(과확대로 증거를 자르지 마라).\n"
        f"\n[채널] {channel} ({spec['aspect']}, 예산 {spec.get('dmin',20)}~{spec.get('dmax',60)}초)\n[canonical] {canonical}\n"
        f"[세트 실값(data_card 전용)] {dv}\n[사진 카탈로그]\n{_catalog_block(catalog)}\n\n[본문]\n{body[:3500]}")
    global _SB_LAST_FAIL, _SB_TRACE
    _SB_LAST_FAIL = ""
    _SB_TRACE = []          # 승급 로그(Haiku 시도→실패 사유→Sonnet 성공) + 콜별 실측 토큰·원가
    valid_ids = {c.get("id") for c in catalog}
    feedback = ""
    # Haiku 우선(원가), 2회 실패 시 Sonnet 에스컬레이션. 스키마+예산 2중 게이트라 수렴 여유로 4시도.
    for _try, _mdl in ((1, None), (2, None), (3, "claude-sonnet-5"), (4, "claude-sonnet-5")):
        _ent = {"try": _try, "requested": _mdl or "haiku"}
        try:
            raw = _llm.call_task("spoken", base + feedback, max_tokens=1800, default_model=_mdl)
        except Exception as _e:
            _SB_LAST_FAIL = f"콜 실패: {repr(_e)[:80]}"
            _ent["outcome"] = f"콜 실패: {repr(_e)[:60]}"
            _SB_TRACE.append(_ent)
            continue
        _u = dict(getattr(_llm, "LAST_USAGE", {}) or {})   # 방금 콜의 실측 토큰
        _ent.update(model=_u.get("model") or (_mdl or "haiku"),
                    in_tok=_u.get("in", 0), out_tok=_u.get("out", 0),
                    cost_usd=_llm.usd_cost(_u.get("model", ""), _u.get("in", 0), _u.get("out", 0)))
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            _SB_LAST_FAIL = f"JSON 없음(모델={_mdl or 'haiku'}) raw[:80]={ (raw or '')[:80]!r}"
            feedback = "\n\n[재시도] JSON 객체 하나만 출력하라(설명 금지)."
            _ent["outcome"] = "실패: JSON 없음"
            _SB_TRACE.append(_ent)
            continue
        try:
            sb = json.loads(m.group(0))
        except Exception:
            _SB_LAST_FAIL = f"JSON 파싱 실패(모델={_mdl or 'haiku'})"
            feedback = "\n\n[재시도] 유효한 JSON이 아니다. 형식을 정확히 지켜라."
            _ent["outcome"] = "실패: JSON 파싱"
            _SB_TRACE.append(_ent)
            continue
        ok, why = _validate(sb, valid_ids)
        if ok:
            # ★ 길이 예산 게이트(콘티 검증 조건) — 초과 콘티는 반려·재생성(렌더 자르기 금지).
            est = estimate_duration(sb.get("scenes", []))
            dmin, dmax = spec.get("dmin", 20), spec.get("dmax", 60)
            if not (dmin <= est <= dmax):
                _SB_LAST_FAIL = f"예산 초과(모델={_mdl or 'haiku'}): 추정 {est}s ∉ [{dmin},{dmax}]s"
                _ent["outcome"] = f"실패(예산): 추정 {est}s ∉ [{dmin},{dmax}]"
                _ent["est_sec"] = est
                _SB_TRACE.append(_ent)
                if est > dmax:
                    feedback = (f"\n\n[재시도] 총 길이 추정 {est}초가 예산 {dmin}~{dmax}초를 초과했다. "
                                "씬 수를 줄이거나 각 line을 더 짧게(핵심만) 압축해 다시 짜라. 정보는 유지하되 군더더기 제거.")
                else:
                    feedback = (f"\n\n[재시도] 총 길이 추정 {est}초가 예산 {dmin}~{dmax}초에 못 미친다. "
                                "씬을 더 넣거나 line을 조금 더 충실히 채워 다시 짜라.")
                continue
            sb.setdefault("meta", {})["channel"] = channel
            sb["meta"]["aspect"] = spec["aspect"]
            sb["meta"]["canonical"] = canonical or ""
            sb["meta"]["est_sec"] = est
            _ent["outcome"] = "성공"
            _ent["est_sec"] = est
            _SB_TRACE.append(_ent)
            return sb
        _SB_LAST_FAIL = f"검증 실패(모델={_mdl or 'haiku'}): {why}"
        feedback = f"\n\n[재시도] 콘티 규칙 위반: {why}. 고쳐서 다시."
        _ent["outcome"] = f"실패(검증): {why[:50]}"
        _SB_TRACE.append(_ent)
    return {}


_SB_LAST_FAIL = ""   # 진단: 마지막 콘티 실패 사유
_SB_TRACE: list = []   # 승급 로그(시도별 모델·결과·실측 토큰·원가)


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
