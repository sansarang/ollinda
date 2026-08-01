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
    _cost = (blog.payload or {}).get("api_cost") if blog else None
    return {"asset_id": asset_id, "pass": n_ok, "fail": len(checks) - n_ok, "checks": checks,
            "api_cost": _cost}


# 자체 수정 가능 항목(문장 '표면'만 — 사실·구조·마커·링크 불변 원칙)
_FIXABLE_KEYS = ("금지 클리셰", "어미 다양성")


def _revise_text(text: str, problems: list[str], is_blog: bool) -> str:
    """걸린 항목만 고치는 표면 수정 1콜 — 실패·빈 응답이면 원문 유지(안전)."""
    try:
        from app.generators.text_claude import _call_llm
        from app.llm import SONNET as _SN
        keep = ("소제목(##)·표·FAQ·[사진N] 마커·링크·숫자·사실 전부 그대로 유지, "
                if is_blog else "해시태그·사실·숫자 그대로 유지, ")
        out = _call_llm(
            "아래 글의 '문제'만 고쳐서 전체를 다시 출력하라. 문장 표현만 다듬고 "
            + keep + "내용 추가·삭제 금지. 설명 없이 결과 텍스트만.\n"
            f"[문제] {'; '.join(problems)}\n\n[글]\n{text}",
            model=_SN, max_tokens=(6000 if is_blog else 900))
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


PUBLISH_MIN = 80    # 발행 게이트 기준선(2026-07-28 사장님 결정: 미달 글은 발행 버튼 자체를 봉인)
# 표면 수선 목표선(2026-08-01 사장님 승인) — 80만 넘으면 손을 떼던 탓에 82점 글이 이모지·문단리듬·
# 키워드노출 감점을 그대로 안고 나갔다. 목표선 미만이면 값싼 표면 수선(기계+소형 콜 1회) 실행.
# 2026-08-01 실측: 88 적용 시 82→88(+6). 92로 올려봤으나 잔여 감점이 '내용 추가'가 필요한
# 종류(상호 미표기)라 수선이 발동조차 안 함 → 효과 0·콜만 증가로 88 복귀.
# 90점대는 수선이 아니라 '생성 단계'가 책임진다(사실 기반 누락은 처음부터 안 생기게).
POLISH_TARGET = 88


_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF❤️]")


def _trim_emoji(body: str, keep: int = 1) -> str:
    """이모지 초과분 기계 삭제(0원) — 채점 기준(네이버 0~1개)에 맞춤. 언어 원리, 업종 무관."""
    seen = [0]

    def _r(m):
        seen[0] += 1
        return m.group(0) if seen[0] <= keep else ""
    return _EMOJI_RE.sub(_r, body or "")


def _surface_fix(pl: dict, warns: list) -> "str | None":
    """표면 감점(클리셰·도입 훅·동어반복·문단 리듬)만 고치는 소형 콜 1회 — 전체 재작성($0.7)의
    1/10 비용. 감점 문장은 '이 글 자신'에서 추출된 것(하드코딩 0). 안전게이트(길이·마커 불변) 동일."""
    body = pl.get("body") or ""
    if not body:
        return None
    from app.generators.text_claude import _call_llm
    from app.llm import SONNET as _SN
    _n_mk = len(re.findall(r"\[사진\d+\]", body))
    raw = (_call_llm(
        "아래 블로그에서 '지적된 표면 문제'만 최소 수정으로 고쳐라 — 문장 다듬기 수준이며 "
        "내용·사실·수치·구조는 그대로다.\n"
        "규칙: ①소제목(##)·표·FAQ·링크 유지 ②[사진N] 마커는 정확히 "
        f"{_n_mk}개 그대로(추가·삭제 금지, 위치 이동은 허용) ③새 정보·과장 추가 금지 "
        "④지적 안 된 문장은 손대지 마라.\n"
        "고치는 법: 클리셰 표현은 그 자리에서 자연스러운 말로 교체 / '도입에 끝까지 읽을 이유 없음'은 "
        "첫 문단에 이 글이 답할 것을 예고하는 한 문장 추가(본문에 이미 있는 내용만 예고) / "
        "동어반복 문단은 겹치는 쪽을 압축 / 텍스트 문단 연속은 기존 [사진N] 마커 위치 재배치나 "
        "소제목 삽입으로 리듬을 끊어라 / '핵심키워드 노출 부족'은 이미 있는 문장 안에서 그 표현을 "
        "자연스럽게 쓰도록 어휘만 바꿔라(억지 삽입·도배 금지, 조사 없는 동사 직결 금지) / "
        "'과장·광고성 표현'은 사실 서술로 바꿔라(없는 근거를 만들지 마라).\n"
        "출력: 고친 전체 본문만(머리말·설명 금지).\n\n"
        f"[지적된 표면 문제]\n- " + "\n- ".join(w[:120] for w in warns[:5]) + f"\n\n[본문]\n{body}",
        model=_SN, max_tokens=min(6000, max(2500, int(len(body) * 0.9)))) or "").strip()
    raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", raw).strip()
    if (len(raw) >= len(body) * 0.75
            and len(re.findall(r"\[사진\d+\]", raw)) == _n_mk):
        return raw
    return None


def score_gate(asset_id: str, source: str = "", max_rounds: int = 2) -> dict:
    """📮 발행 게이트 — ranking_audit<80이면 감점 사유를 피드백으로 자동 재작성(최대 2회,
    사실·마커·구조 보존). 그래도 미달이면 payload.publish_blocked_score 봉인 플래그(발행 버튼 숨김).
    '상위노출 안 되는 글을 알면서 올릴 필요 없다' — 미달 글은 사장님 눈에 발행 대상으로 안 보이게."""
    from app import seo
    try:
        pieces = db.get_set_pieces(asset_id)
    except Exception:
        return {}
    blog = _get(pieces, ContentKind.BLOG)
    if not blog:
        return {}
    pl = blog.payload or {}
    au = pl.get("ranking_audit") or {}
    score = au.get("score")
    rounds = 0
    # 🧹 표면 수선 패스(2026-08-01 사장님 승인 — '한 번에 80점' 2겹): 값비싼 전체 재작성 전에
    #   ①기계 수선(이모지 초과 = regex, 0원) ②표면 감점만 고치는 소형 콜 1회.
    #   전부 업종·가게 무관 언어/구조 원리 — 하드코딩 0. 실패는 조용히(기존 루프 그대로 진행).
    if isinstance(score, int) and score < POLISH_TARGET:
        try:
            _body0 = pl.get("body") or ""
            _fixed = _trim_emoji(_body0, keep=1)
            if _fixed != _body0:
                pl["body"] = _fixed
            # 수선 대상 = '문장·구조만 손보면 되는' 감점(사실·수치 불변). 실측 반복 3종 포함:
            #   이모지(기계), 문단 연속, 도입 훅, 그리고 82점대에서 흔한 '키워드 노출 부족'·'과장 표현'
            _sw = [w for w in (au.get("warnings") or [])
                   if any(t in w for t in ("클리셰", "도입", "동어반복", "문단 연속", "연속(시각요소",
                                           "키워드", "과장", "이모지"))]
            if _sw:
                _sfx = _surface_fix(pl, _sw)
                if _sfx:
                    pl["body"] = _sfx
            au = seo.quality_audit(blog.channel.value, blog.kind.value, pl, source=source)
            pl["ranking_audit"] = au
            score = au.get("score")
            pl["surface_pass"] = {"applied": bool(_sw) or (_fixed != _body0), "after": score}
            db.save_piece(blog)
        except Exception as _e:
            pl.setdefault("score_gate_stops", []).append(f"surface: 예외 {repr(_e)[:60]}")
    while isinstance(score, int) and score < PUBLISH_MIN and rounds < max_rounds:
        rounds += 1
        warns = "; ".join((au.get("warnings") or [])[:6]) or "감점 사유 미상"
        body = pl.get("body") or ""
        try:
            from app.generators.text_claude import _call_llm, _parse_sections
            from app.llm import SONNET as _SN2, MODEL as _OP
            # 2026-07-29 개선: ①제목 감점도 고치게 [제목] 출력 포함 ②2라운드는 Opus 승격
            #   ③펜스·머리말 세척 후 안전 게이트 ④중단 사유 기록(70점 정체 조사 재발 방지)
            _n_mk = len(re.findall(r"\[사진\d+\]", body))
            _title0 = pl.get("title") or ""
            raw = (_call_llm(
                "아래 블로그가 상위노출 채점에서 감점됐다. '감점 사유'를 정확히 고쳐라. "
                "소제목(##)·표·FAQ·[사진N] 마커·링크·숫자·사실은 그대로 유지, 내용 추가·삭제 금지.\n"
                f"★[사진N] 마커는 지금 정확히 {_n_mk}개다 — 새 마커 추가 절대 금지, 삭제 금지"
                "(개수·번호 불변). '시각요소 부족' 감점은 마커 삽입이 아니라 기존 마커 위치 재배치와 "
                "소제목(##)·표 삽입으로만 해결하라(2026-08-01 실사고: 마커 수십 개 날조 → 보정 전체 폐기).\n"
                "출력 형식(머리표 유지, 설명 금지):\n[제목]\n(고친 제목 — 문제 없으면 원래 제목 그대로)\n"
                "[본문]\n(고친 전체 본문)\n\n"
                f"[감점 사유] {warns}\n[제목] {_title0}\n\n[본문]\n{body}",
                model=(_SN2 if rounds == 1 else _OP), max_tokens=6000) or "").strip()
            d = _parse_sections(raw, ["제목", "본문"])
            new = (d.get("본문") or raw).strip()
            new = re.sub(r"^```[a-z]*\n?|\n?```$", "", new).strip()      # 코드펜스 세척
            _nt = " ".join((d.get("제목") or "").split())
            if (len(new) >= len(body) * 0.7
                    and len(re.findall(r"\[사진\d+\]", new)) == len(re.findall(r"\[사진\d+\]", body))):
                pl["body"] = new
                if 12 <= len(_nt) <= 50:
                    pl["title"] = _nt
            else:
                pl.setdefault("score_gate_stops", []).append(
                    f"r{rounds}: 안전게이트(len {len(new)}/{len(body)}, 마커 "
                    f"{len(re.findall(chr(91)+'사진'+chr(92)+'d+'+chr(93), new))})")
                break                                    # 안전 게이트 위반 → 원문 유지·중단
        except Exception as _e:
            pl.setdefault("score_gate_stops", []).append(f"r{rounds}: 예외 {repr(_e)[:60]}")
            break
        au = seo.quality_audit(blog.channel.value, blog.kind.value, pl, source=source)
        pl["ranking_audit"] = au
        score = au.get("score")
        db.save_piece(blog)
    blocked = isinstance(score, int) and score < PUBLISH_MIN
    pl["publish_blocked_score"] = (score if blocked else None)
    pl["score_gate"] = {"rounds": rounds, "final": score}
    db.save_piece(blog)
    return {"rounds": rounds, "final": score, "blocked": blocked}


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

