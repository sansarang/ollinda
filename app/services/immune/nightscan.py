"""🌙 야간 자가 스캔 — 사장님보다 시스템이 먼저 발견한다.

★ 자동 수정은 무비용 기계 수선만(R7). 재생성·코드 수정은 절대 자동 실행하지 않는다.
  야간 전량 재생성이 크레딧을 말려 아침 생성을 죽이는 것이 면역계가 만드는 새 사고다 —
  실제로 2026-08-04에 게이트 재실행 한 번이 300초를 먹고 크레딧을 말렸다.
★ 수정하면 원본을 먼저 보존하고 전후 diff를 남긴다(R2). diff 없는 침묵 수정은 그 자체가 사고다.
★ 크레딧 잔량을 먼저 본다. 부족하면 탐지만 하고 멈춘다.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import time

from app import db

_log = logging.getLogger("shopcast.immune")
from app.services.immune import is_persistent as _persistent
from app.services.immune import path as _ipath

BACKUP_DIR = os.environ.get("SHOPCAST_IMMUNE_BACKUP", "") or _ipath("immune_backup")
DIAG_PATH = os.environ.get("SHOPCAST_IMMUNE_DIAG", "") or _ipath("immune_diagnoses.jsonl")
MAX_SETS = int(os.environ.get("SHOPCAST_SCAN_SETS", "40"))


# ── 탐지: 산출물 표면 ─────────────────────────────────────────────────────
def _scan_blog(pl: dict) -> list:
    """글 표면 — 원장 유형이 실제로 남긴 자국을 찾는다."""
    from app.services import qualitycheck as _qc
    from app.services import photodesc as _pd
    from app import seo as _seo
    out = []
    body = pl.get("body") or ""
    if body and _qc.fix_orphan_parens(body) != body:
        out.append({"kind": "orphan-paren", "cause": "기계 수선 가능",
                    "detail": "짝 없는 괄호", "fixable": True})
    if body and _qc.prose_photo_refs(body):
        out.append({"kind": "photo-deixis", "cause": "경로 이원화",
                    "detail": f"본문이 사진을 가리킨다: {_qc.prose_photo_refs(body)[:3]}",
                    "fixable": False})
    caps = ((pl.get("photo_captions") or {}).get("caps") or [])
    bad = [(i + 1, _pd.caption_ok(c)) for i, c in enumerate(caps) if c and _pd.caption_ok(c)]
    if bad:
        out.append({"kind": "caption-gate", "cause": "게이트 사각",
                    "detail": f"게이트 탈락 캡션 {len(bad)}건: {bad[:3]}", "fixable": False})
    n_img = _seo.photo_count(pl)
    if n_img and len(caps) and len(caps) != n_img:
        out.append({"kind": "caption-count", "cause": "대조 설계 결함",
                    "detail": f"사진 {n_img}장인데 캡션 {len(caps)}개", "fixable": False})
    return out


def _scan_runtime(tenant_id: str) -> list:
    """런타임 표면 — diff에는 안 보이는 것(경합·상태). 정적 검진이 못 잡는 몫이다."""
    out = []
    pr = db.get_gen_progress(tenant_id) or {}
    if (pr.get("status") == "done") and not (pr.get("asset_id") or ""):
        out.append({"kind": "progress-noasset", "cause": "세션 간 덮어쓰기",
                    "detail": "생성 완료인데 세트 ID가 비었다 — 결과 화면으로 못 넘어간다",
                    "fixable": False})
    return out


# ── 무비용 기계 수선(R7) ──────────────────────────────────────────────────
def _backup(piece_id: str, before: str) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    p = os.path.join(BACKUP_DIR, f"{piece_id}.{int(time.time())}.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(before)
    return p


def _fix_free(piece, findings: list) -> dict | None:
    """LLM 0원 수선만. 원본 보존 + 전후 diff를 남긴 뒤에만 저장한다(R2)."""
    from app.services import qualitycheck as _qc
    if not any(f.get("fixable") for f in findings):
        return None
    before = (piece.payload or {}).get("body") or ""
    after = _qc.fix_orphan_parens(before)
    if after == before or not after.strip():
        return None
    bpath = _backup(piece.id, before)                 # ★ 보존이 먼저다
    diff = "\n".join(difflib.unified_diff(
        before.splitlines(), after.splitlines(), "before", "after", lineterm="", n=1))[:4000]
    try:
        db.update_piece_payload(piece.id, {"body": after})
    except Exception as e:
        _log.warning("[immune] 수선 저장 실패(원본 유지): %r", repr(e)[:80])
        return None
    _log.info("[immune] 기계 수선 %s — 백업 %s", piece.id[:8], bpath)
    return {"piece": piece.id, "backup": bpath, "diff": diff}


def _diagnose(asset_id: str, tenant_id: str, findings: list) -> dict:
    """코드 수정·재생성이 필요한 것 — 자동 실행 금지, 진단서로 대기시킨다(R7)."""
    return {"at": int(time.time()), "asset_id": asset_id, "tenant_id": tenant_id,
            "findings": [f for f in findings if not f.get("fixable")],
            "status": "대기", "action": "사람 승인 필요(재생성 또는 코드 수정)"}


def run(limit_sets: int = 0, allow_fix: bool = True) -> dict:
    """야간 스캔 1회. 크레딧이 없으면 탐지만 하고 멈춘다(R7)."""
    from app.domain.models import ContentKind as _CK
    from app import llm as _llm
    credit_ok = not _llm.credit_out()
    # ★ 백업이 배포를 못 넘기는 경로면 수선하지 않는다(R2를 구조로 지킨다).
    #   보존한다고 해놓고 배포 때 지워지면 그건 침묵 수정이다.
    persist = _persistent()
    if allow_fix and not persist:
        _log.warning("[immune] 백업 경로가 영속이 아니다(%s) — 탐지만 한다", BACKUP_DIR)
        allow_fix = False
    detected, fixed, diags = [], [], []
    tenants = {}
    for s in db.list_sets(limit=limit_sets or MAX_SETS) or []:
        aid = s.get("asset_id") or ""
        if not aid:
            continue
        pieces = db.get_set_pieces(aid)
        blog = next((p for p in pieces if p.kind == _CK.BLOG), None)
        if not blog:
            continue
        tenants[blog.tenant_id] = True
        f = _scan_blog(blog.payload or {})
        if not f:
            continue
        detected.append({"asset_id": aid, "tenant_id": blog.tenant_id, "findings": f})
        if allow_fix:
            r = _fix_free(blog, f)
            if r:
                fixed.append({"asset_id": aid, **r})
        if any(not x.get("fixable") for x in f):
            diags.append(_diagnose(aid, blog.tenant_id, f))
    for tid in tenants:
        rf = _scan_runtime(tid)
        if rf:
            detected.append({"tenant_id": tid, "findings": rf})
            diags.append(_diagnose("", tid, rf))
    if diags:
        os.makedirs(os.path.dirname(DIAG_PATH) or ".", exist_ok=True)
        with open(DIAG_PATH, "a", encoding="utf-8") as fh:
            for d in diags:
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    return {"credit_ok": credit_ok, "persistent": persist, "backup_dir": BACKUP_DIR,
            "scanned": len(tenants),
            "detected": detected, "fixed": fixed, "diagnoses": diags,
            "note": ("크레딧이 없어 탐지만 했습니다(수정·재생성 안 함)" if not credit_ok
                     else ("백업이 배포를 못 넘기는 경로라 탐지만 했습니다" if not persist else ""))}


def pending_diagnoses(limit: int = 50) -> list:
    try:
        with open(DIAG_PATH, encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        return rows[-limit:]
    except Exception:
        return []
