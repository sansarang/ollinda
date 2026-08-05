"""📒 사고 원장 — 면역계의 기억.

★ 원장 자체가 정직성 게이트를 받는다(R1).
  - 각 행에 수정 커밋 해시 실물이 붙는다. 해시 없는 사고는 '구전(미확정)'이다.
  - 원인 유형은 **커밋 본문이 스스로 그렇게 말한 문구**를 근거로 인용할 때만 확정한다.
    문구가 없으면 '미분류'다 — 내가 읽어서 짐작한 것을 확정으로 적으면 그게 날조된 항체다.
  - 분류 근거 문구(evidence)를 행마다 남긴다. 사람이 원장을 검증할 수 있어야 한다.

미분류 과거 커밋(~750개)은 손대지 않는다. '모른다'가 정직한 상태다(착수 결정).
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from app.services.immune import path as _ipath

# ★ 2026-08-05 정정: 원장만은 볼륨이 아니라 **코드 트리**에 산다.
#   원장은 git 이력에서 파생되는 산출물인데, 배포 이미지에는 .git이 없다(.dockerignore).
#   볼륨에 두면 프로덕션이 원장을 만들 수도, 읽을 수도 없다(실측: rebuild → 0행).
#   런타임 산출물(백업·진단서·규칙 상태)은 볼륨이 맞다 — 둘의 성격이 다르다.
LEDGER_PATH = os.environ.get("SHOPCAST_LEDGER", "") or "data/incidents.jsonl"
DOC_PATH = "docs/incidents.md"

# 원인 유형 — 실사고에서 귀납한 것이지 미리 정한 목록이 아니다.
#   값은 '그 유형이라고 커밋 본문이 스스로 말한 문구'다. 이 문구가 없으면 분류하지 않는다.
CAUSE_SIGNS = {
    "경로 이원화": ("두 곳", "둘로", "이원화", "따로 살", "갈라져", "한쪽만", "단일화", "한 함수로",
                 "같은 함수 하나", "두 경로", "복제"),
    "침묵 폴백": ("침묵 폴백", "조용한 실패", "조용히", "빈 반환", "기본값으로 채", "템플릿으로 채",
                "폴백이", "사유를 안 남", "조용히 넘어"),
    "스테일 참조": ("첫 매치", "옛 스냅샷", "낡은", "스테일", "구본문", "옛 번호", "옛 기록", "덮어쓰기 전"),
    "대조 설계 결함": ("대조", "검증이 느슨", "4/4", "부분 표면", "존재가 아니라 사용", "확인 못 한",
                  "일부만 보", "합집합"),
    "식별자 혼동": ("오배송", "이름으로 특정", "tenant_id", "내부 표기", "실값이 아니", "식별자",
                 "엉뚱한 세트", "다른 가게"),
    "게이트 사각": ("게이트 없", "게이트가 없", "검사가 없", "막지 못", "게이트를 우회", "게이트 탈락",
                 "게이트에 넣"),
    "세션 간 덮어쓰기": ("행 전체를 덮어", "덮어썼", "병합", "스레드는 옛", "동시에 들어온"),
    "예산 불충분": ("max_tokens", "토큰이 모자", "완성될 수 없는 요청", "절단", "예산"),
    "기계검출불가": ("의미 판정", "런타임 경합", "정적 검진 밖"),
}

_HASH = re.compile(r"\b[0-9a-f]{7,40}\b")
_FILELINE = re.compile(r"([a-zA-Z_][\w/]*\.(?:py|sh|md)):(\d+)")
_GOLDEN = re.compile(r"(tests/[\w/]+\.py(?:::\w+)?)")
# 수정성 커밋 — 사고를 고친 커밋만 원장 후보다(기능 추가는 사고가 아니다)
FIX_SIGNS = re.compile(
    r"(수정|고침|봉인|사고|결함|버그|재발|오탐|실패|누락|깨짐|원인|종결|청산|제거|정정|방지)")
# 트레일러(신규 사고 기록 의무) — 사람이 쓰는 규약
TRAILER = re.compile(r"^\s*(Incident|Recurrence|Golden)\s*:\s*(.+?)\s*$", re.M)


def _run(args: list) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return ""


def parse_trailer(body: str) -> dict:
    """커밋 트레일러 파싱 — Incident/Recurrence/Golden."""
    out = {}
    for k, v in TRAILER.findall(body or ""):
        out[k.lower()] = v.strip()
    return out


def classify(body: str) -> tuple:
    """원인 유형 판정 — **본문이 스스로 말한 문구**로만. 반환 (유형들, 근거문구들).

    ★ 짐작으로 유형을 적지 않는다. 근거 문구가 없으면 빈 결과이고 그 행은 '미분류'다.
      근거를 함께 반환하는 이유는 사람이 원장을 검증할 수 있어야 하기 때문이다.
    """
    txt = body or ""
    types, ev = [], []
    for ctype, signs in CAUSE_SIGNS.items():
        hit = [s for s in signs if s in txt]
        if hit:
            types.append(ctype)
            ev.append({"type": ctype, "signs": hit[:3]})
    return types, ev


def commit_exists(h: str) -> bool:
    """해시 실물 확인 — 원장에 적힌 커밋이 정말 있는가(R1)."""
    if not h:
        return False
    return bool(_run(["git", "cat-file", "-t", h]).strip() == "commit")


def extract_from_git(limit: int = 2000) -> list:
    """git 이력에서 확정분 추출 — 해시는 커밋 자체가 실물이므로 항상 확인된다.

    확정(confirmed)의 조건: 수정성 커밋 + 원인 유형 근거 문구 존재.
    유형 근거가 없으면 '미분류'로 남기되 원장에는 넣지 않는다(잡음).
    """
    raw = _run(["git", "log", f"-{limit}", "--format=%H%x01%at%x01%s%x01%b%x02"])
    rows = []
    for rec in raw.split("\x02"):
        if not rec.strip():
            continue
        parts = rec.strip().split("\x01")
        if len(parts) < 3:
            continue
        h, at, subj = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ""
        full = f"{subj}\n{body}"
        if not FIX_SIGNS.search(subj):
            continue                                  # 기능 추가·진단 추가는 사고가 아니다
        types, ev = classify(full)
        if not types:
            continue                                  # 근거 없는 것은 짐작하지 않는다
        tr = parse_trailer(body)
        rows.append({
            "id": h[:8],
            "commit": h,
            "at": int(at or 0),
            "symptom": subj[:160],
            "surfaces": sorted({f"{m[0]}:{m[1]}" for m in _FILELINE.findall(full)})[:6],
            "cause_types": types,
            "evidence": ev,
            "goldens": sorted(set(_GOLDEN.findall(full)))[:6],
            "trailer": tr,
            "confirmed": True,                        # 커밋 해시 실물이 붙는다
            "source": "git",
            "found_by": _found_by(full),
        })
    return rows


# 발견 주체 — 사장님이 먼저 봤나, 시스템이 먼저 잡았나(학습 폐루프의 분자)
_USER_SIGNS = ("사장님 지적", "사장님 실측", "사장님이 발견", "사장님 요청", "실물 판정",
               "사장님 화면", "사장님이 주신", "지적에서 시작")
# ★ 2026-08-05 보강: '자가 점검'으로 잡은 건이 첫 사례로 들어왔다.
#   골든·스캔 같은 장치만 시스템 발견으로 세면, 마무리 전 스스로 물어서 잡은 건이
#   '미상'으로 빠진다. 목표는 '사용자보다 시스템이 먼저 발견'이고 이것도 그 한 건이다.
_SYS_SIGNS = ("골든이 잡", "테스트가 잡", "감사에서", "스캔이", "자동 탐지", "게이트가 잡",
              "자가 점검", "마무리 전 스스로", "스스로 물어서", "스스로 잡")


def _found_by(text: str) -> str:
    t = text or ""
    if any(s in t for s in _SYS_SIGNS):
        return "시스템"
    if any(s in t for s in _USER_SIGNS):
        return "사용자"
    return "미상"


def extract_from_lessons(path: str = "docs/lessons.md") -> list:
    """lessons.md의 서사 사고 — 해시가 없으면 '구전(미확정)'이다.

    ★ 구전 행에서는 검진 규칙을 파생하지 않는다(R1). 확인 안 된 것으로 항체를 만들면
      그 항체가 무엇을 막는지 아무도 모른다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return []
    rows = []
    for m in re.finditer(r"^#{2,3}\s*(.+)$", txt, re.M):
        title = m.group(1).strip()
        seg = txt[m.end():m.end() + 900]
        hs = [h for h in _HASH.findall(seg) if commit_exists(h)]
        types, ev = classify(title + "\n" + seg)
        rows.append({
            "id": "L" + re.sub(r"\W+", "", title)[:10],
            "commit": hs[0] if hs else "",
            "at": 0,
            "symptom": title[:160],
            "surfaces": sorted({f"{x[0]}:{x[1]}" for x in _FILELINE.findall(seg)})[:6],
            "cause_types": types,
            "evidence": ev,
            "goldens": sorted(set(_GOLDEN.findall(seg)))[:6],
            "trailer": {},
            "confirmed": bool(hs),
            "source": "lessons",
            "found_by": _found_by(seg),
        })
    return rows


def recurrence(rows: list) -> dict:
    """유형별 재발 횟수 — 차단 대상(2회 이상) 판정의 근거(R3)."""
    cnt = {}
    for r in rows:
        if not r.get("confirmed"):
            continue                                  # 구전은 세지 않는다
        for t in r.get("cause_types") or []:
            cnt[t] = cnt.get(t, 0) + 1
    return dict(sorted(cnt.items(), key=lambda x: -x[1]))


def build() -> dict:
    """원장 구축 — 확정분 + 구전분. 저장은 write()가 한다."""
    git_rows = extract_from_git()
    les_rows = extract_from_lessons()
    seen = {r["commit"] for r in git_rows if r["commit"]}
    les_rows = [r for r in les_rows if r["commit"] not in seen]
    rows = git_rows + les_rows
    return {"rows": rows, "recurrence": recurrence(rows),
            "confirmed": sum(1 for r in rows if r["confirmed"]),
            "hearsay": sum(1 for r in rows if not r["confirmed"])}


def write(data: dict, path: str = LEDGER_PATH) -> int:
    """원장 저장. ★ 빈 결과로 기존 원장을 덮어쓰지 않는다.

    실측(2026-08-05): git이 없는 환경에서 build()는 0행을 낸다. 그걸 그대로 쓰면
    원장이 통째로 사라진다 — 기억을 잃은 면역계는 항체가 아니라 껍데기다.
    """
    rows = data.get("rows") or []
    if not rows and read(path):
        raise RuntimeError("빈 원장으로 덮어쓰기 거부 — git 이력이 없는 환경으로 보인다")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in data.get("rows") or []:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(data.get("rows") or [])


def read(path: str = LEDGER_PATH) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except Exception:
        return []
