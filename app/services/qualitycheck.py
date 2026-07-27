"""
📏 품질 회귀 검사(골든세트) — "사장님이 QA가 되는 구조"를 끝내는 장치(2026-07-28 사장님 결정).

고정된 골든 사진으로 실제 생성을 돌리고, 산출물(블로그·캡션·X)을 결정적 규칙으로 채점한다.
배포 후 이 검사를 돌리면 품질 후퇴(클리셰 복귀·장치 누락·소재 이탈·구조 붕괴)를 사장님보다
먼저 발견한다. LLM 채점이 아니라 기계 채점 — 재현 가능, 비용 0.

진입점: run_checks(asset_id) → {"pass": n, "fail": n, "checks": [{name, ok, detail}]}
"""
from __future__ import annotations

import re

from app import db
from app.domain.models import ContentKind

# 사람이 안 쓰는 AI 클리셰·낚시 상투구(HUMAN_TOUCH 금지 목록과 동기) — 제목·본문·캡션·X 공통
BANNED_PHRASES = ("호구", "안녕하세요~", "알아보겠습니다", "도움이 되셨길", "어떠셨나요",
                  "소개해드리겠습니다", "지금까지 ~였습니다", "강력추천", "최저가", "100%", "무조건")

# 어미 다양성 증거(최소 2종 등장해야 통과) — 같은 어미 도배 = 기계 티
_ENDING_VARIETY = ("요.", "죠.", "거든요", "더라고", "더군요", "습니다.")


def _get(pieces, kind):
    return next((p for p in pieces if p.kind == kind), None)


def _chk(out: list, name: str, ok: bool, detail: str = ""):
    out.append({"name": name, "ok": bool(ok), "detail": detail[:200]})


def run_checks(asset_id: str) -> dict:
    pieces = db.get_set_pieces(asset_id)
    blog = _get(pieces, ContentKind.BLOG)
    cap = _get(pieces, ContentKind.CAPTION)
    x = _get(pieces, ContentKind.X_POST)
    checks: list[dict] = []

    _chk(checks, "블로그 존재", blog is not None)
    _chk(checks, "캡션 존재", cap is not None)
    _chk(checks, "X 존재", x is not None)

    if blog:
        pl = blog.payload or {}
        body = pl.get("body") or ""
        title = pl.get("title") or ""
        plain = re.sub(r"\[사진\d+\]", "", body)
        n_chars = len(re.sub(r"\s", "", plain))
        _chk(checks, "블로그 분량(공백 제외 1200자+)", n_chars >= 1200, f"{n_chars}자")
        _bad = [w for w in BANNED_PHRASES if w in title or w in body]
        _chk(checks, "블로그 금지 클리셰 없음", not _bad, ",".join(_bad))
        # 체류 3장치(발현률 게이트와 동일 검사기 재사용)
        try:
            from app.generators.text_claude import _audit_dwell_devices
            _miss = _audit_dwell_devices(body)
            _chk(checks, "체류 3장치 발현", not _miss, ",".join(_miss))
        except Exception:
            _chk(checks, "체류 3장치 발현", False, "검사기 로드 실패")
        _chk(checks, "표 존재", "|" in body)
        _chk(checks, "FAQ 존재", ("자주 묻는" in body) or ("자주묻는" in body))
        _chk(checks, "한눈 요약 존재", "한눈 요약" in body)
        _mk = re.findall(r"\[사진(\d+)\]", body)
        _chk(checks, "사진 마커 존재·중복 없음", bool(_mk) and len(_mk) == len(set(_mk)),
             f"{len(_mk)}개")
        _chk(checks, "제목 길이 15~45자", 15 <= len(title) <= 45, f"{len(title)}자: {title}")
        _var = [e for e in _ENDING_VARIETY if e in body]
        _chk(checks, "어미 다양성(2종+)", len(_var) >= 2, ",".join(_var))
        _sc = pl.get("subject_check")
        _chk(checks, "블로그 소재 정합", _sc in ("ok", "", None), str(_sc))
        _dg = pl.get("dwell_gate") or {}
        _chk(checks, "발현률 게이트 기록", isinstance(_dg, dict),
             f"missing={_dg.get('missing')} fixed={_dg.get('fixed')}")

    if cap:
        t = (cap.payload or {}).get("text") or ""
        _bad = [w for w in BANNED_PHRASES if w in t]
        _chk(checks, "캡션 금지 클리셰 없음", not _bad, ",".join(_bad))
        _chk(checks, "캡션 해시태그(#) 존재", "#" in t)
        _chk(checks, "캡션 소재 정합", (cap.payload or {}).get("subject_check") in ("ok", "retried_ok", "", None),
             str((cap.payload or {}).get("subject_check")))
        _chk(checks, "캡션 라우팅=클로드(사진 분석 전달 보장)",
             ((cap.payload or {}).get("llm_route") or {}).get("provider") in ("anthropic", None),
             str((cap.payload or {}).get("llm_route")))

    if x:
        t = (x.payload or {}).get("text") or ""
        _chk(checks, "X 280자 이내", len(t) <= 280, f"{len(t)}자")
        _bad = [w for w in BANNED_PHRASES if w in t]
        _chk(checks, "X 금지 클리셰 없음", not _bad, ",".join(_bad))
        _chk(checks, "X 소재 정합", (x.payload or {}).get("subject_check") in ("ok", "retried_ok", "", None),
             str((x.payload or {}).get("subject_check")))

    n_ok = sum(1 for c in checks if c["ok"])
    return {"asset_id": asset_id, "pass": n_ok, "fail": len(checks) - n_ok, "checks": checks}


# 자체 수정 가능 항목(문장 '표면'만 — 사실·구조·마커·링크 불변 원칙)
_FIXABLE_KEYS = ("금지 클리셰", "어미 다양성")


def _revise_text(text: str, problems: list[str], is_blog: bool) -> str:
    """걸린 항목만 고치는 표면 수정 1콜 — 실패·빈 응답이면 원문 유지(안전)."""
    try:
        from app.generators.text_claude import _call_llm
        keep = ("소제목(##)·표·FAQ·[사진N] 마커·링크·숫자·사실 전부 그대로 유지, "
                if is_blog else "해시태그·사실·숫자 그대로 유지, ")
        out = _call_llm(
            "아래 글의 '문제'만 고쳐서 전체를 다시 출력하라. 문장 표현만 다듬고 "
            + keep + "내용 추가·삭제 금지. 설명 없이 결과 텍스트만.\n"
            f"[문제] {'; '.join(problems)}\n\n[글]\n{text}",
            max_tokens=(6000 if is_blog else 900))
        out = (out or "").strip()
        # 안전 게이트: 지나친 축소·마커 소실이면 원문 유지
        if is_blog:
            import re as _r
            if len(out) < len(text) * 0.7 or len(_r.findall(r"\[사진\d+\]", out)) != len(_r.findall(r"\[사진\d+\]", text)):
                return text
        elif len(out) < len(text) * 0.5:
            return text
        return out or text
    except Exception:
        return text


def self_review(asset_id: str, max_rounds: int = 1) -> dict:
    """📏 글올리기 전 자체 검사 AI(2026-07-28 사장님 결정) — 터미널 에이전트 방식 그대로:
    생성 직후 스스로 검사 → 걸린 것만 스스로 고침 → 재검사. 통과한 글만 사장님 눈에 닿는다.
    반환: 최종 run_checks 결과 + {"rounds", "fixed"}."""
    rep = run_checks(asset_id)
    fixed: list[str] = []
    rounds = 0
    for _ in range(max(0, max_rounds)):
        fails = [c for c in rep["checks"] if not c["ok"] and any(k in c["name"] for k in _FIXABLE_KEYS)]
        if not fails:
            break
        rounds += 1
        pieces = db.get_set_pieces(asset_id)
        for kind, label, key in ((ContentKind.BLOG, "블로그", "body"),
                                 (ContentKind.CAPTION, "캡션", "text"),
                                 (ContentKind.X_POST, "X", "text")):
            probs = [f"{c['name']}({c['detail']})" for c in fails if c["name"].startswith(label)]
            if kind == ContentKind.BLOG:      # 블로그 공통 항목(어미 다양성)도 블로그로 귀속
                probs += [f"{c['name']}({c['detail']})" for c in fails if c["name"].startswith("어미")]
            if not probs:
                continue
            p = _get(pieces, kind)
            if not p:
                continue
            old = (p.payload or {}).get(key) or ""
            new = _revise_text(old, probs, is_blog=(kind == ContentKind.BLOG))
            if new != old:
                p.payload[key] = new
                db.save_piece(p)
                fixed += probs
        rep = run_checks(asset_id)
    rep["rounds"] = rounds
    rep["fixed"] = fixed
    return rep

