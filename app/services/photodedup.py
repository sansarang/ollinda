"""🧹 같은 사진 중복 정리 — 참조를 먼저 옮기고, 확인한 뒤에만 지운다.

2026-08-18 사장님:
  "같은 사진이면 남길 이유가 있냐???"

실측(프로덕션): 원본 1,001장 중 **403장(830.9MB)이 내용이 완전히 같은 중복**이었다.
디스크는 4.7GB뿐이고 그날 여유가 811MB까지 떨어져 있었다.

★ 파일만 지우면 글의 사진이 깨진다.
  각 글의 payload.image_paths가 그 파일 경로를 직접 들고 있다. 그래서 순서가 계약이다:
      ① 계획(무엇을 무엇으로 합칠지)  ② DB 참조 교체  ③ 참조 0 확인  ④ 파일 삭제
  이 순서를 바꾸면 사진이 사라진 글이 남는다. 되돌릴 수 없다.

★ 가게(tenant) 경계를 넘지 않는다.
  같은 사진이라도 가게가 다르면 각자 보관한다. 합치면 A가 지운 사진 때문에 B의 글이
  깨지고, 남의 가게 파일을 참조하는 상태가 된다(2026-08-03 '생성물이 남의 가게로 갔다'와 같은 계열).

★ 계획과 실행은 분리한다. plan()은 아무것도 바꾸지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

from app import db

_log = logging.getLogger("shopcast.photodedup")

#: 파생본 폴더 — 원본이 아니므로 중복 판정 대상이 아니다(원본을 지우면 같이 지운다).
_DERIV = (".web", ".thumbs")
_IMG_EXT = ("jpg", "jpeg", "png", "webp")


def _storage_root() -> str:
    from app.storage import STORAGE_DIR
    return STORAGE_DIR


def _tenant_of(path: str) -> str:
    """{storage}/{tenant_id}/{fname} → tenant_id. 구조가 다르면 빈 문자열(=합치지 않음)."""
    root = os.path.realpath(_storage_root())
    rp = os.path.realpath(path)
    if not rp.startswith(root + os.sep):
        return ""
    rest = rp[len(root) + 1:].split(os.sep)
    return rest[0] if len(rest) >= 2 else ""


def _originals() -> list:
    """원본 사진 경로 목록 — 파생본 폴더는 제외."""
    out = []
    for cur, dirs, fs in os.walk(_storage_root()):
        dirs[:] = [d for d in dirs if d not in _DERIV]
        for fn in fs:
            if fn.rsplit(".", 1)[-1].lower() in _IMG_EXT:
                out.append(os.path.join(cur, fn))
    return out


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan(tenant_id: str = "") -> dict:
    """중복 묶음 계획. **아무것도 바꾸지 않는다.**

    반환: {"groups": [{"tenant","keep","drop":[...],"bytes"}], "n_drop", "mb", "n_groups"}
    대표(keep)는 **가장 오래된 파일** — 먼저 올린 것이 원본일 가능성이 높고,
    무엇보다 판정이 실행할 때마다 흔들리지 않아야 한다(같은 입력 → 같은 계획).
    """
    by_size: dict = {}
    for p in _originals():
        tid = _tenant_of(p)
        if not tid or (tenant_id and tid != tenant_id):
            continue
        try:
            sz = os.path.getsize(p)
        except Exception:
            continue
        if sz > 0:
            by_size.setdefault((tid, sz), []).append(p)      # ★ 가게별로 나눠 담는다
    groups, n_drop, total = [], 0, 0
    for (tid, sz), paths in by_size.items():
        if len(paths) < 2:
            continue                                          # 크기가 유일하면 중복일 수 없다
        same: dict = {}
        for p in paths:
            try:
                same.setdefault(_sha(p), []).append(p)
            except Exception:
                _log.warning("[dedup] 해시 실패 — 건너뜀 %s", p)
        for _h, members in same.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda x: (os.path.getmtime(x), x))
            keep, drop = members[0], members[1:]
            groups.append({"tenant": tid, "keep": keep, "drop": drop, "bytes": sz * len(drop)})
            n_drop += len(drop)
            total += sz * len(drop)
    groups.sort(key=lambda g: -g["bytes"])
    return {"groups": groups, "n_groups": len(groups), "n_drop": n_drop,
            "mb": round(total / 1e6, 1)}


def _swap_refs(mapping: dict) -> int:
    """DB의 모든 피스 payload에서 drop 경로 → keep 경로로 교체. 바뀐 행 수 반환.

    payload는 중첩 구조라 문자열을 재귀로 훑는다. 경로가 어디에 박혀 있든 같이 옮긴다
    (image_paths·image_path·photo_markers 등 — 소비자가 여럿이라 키 이름으로 찾지 않는다).
    """
    def _walk(v):
        if isinstance(v, str):
            return mapping.get(os.path.realpath(v), v) if v.startswith("/") else v
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        return v

    changed = 0
    with db._conn() as c:
        rows = c.execute("SELECT id, payload FROM content_pieces").fetchall()
        for r in rows:
            raw = r["payload"] or "{}"
            if not any(os.path.basename(d) in raw for d in mapping):
                continue                                      # 값싼 선필터
            try:
                pl = json.loads(raw)
            except Exception:
                continue
            new = _walk(pl)
            if new != pl:
                c.execute("UPDATE content_pieces SET payload=? WHERE id=?",
                          (json.dumps(new, ensure_ascii=False), r["id"]))
                changed += 1
    return changed


def _derived_of(path: str) -> list:
    """그 원본의 파생본 경로들 — 원본을 지우면 같이 지운다(살려두면 고아가 된다)."""
    d, fn = os.path.dirname(path), os.path.basename(path)
    stem = os.path.splitext(fn)[0]
    return [os.path.join(d, sub, stem + ".jpg") for sub in _DERIV]


def apply(tenant_id: str = "", limit_mb: float = 0.0) -> dict:
    """계획 → DB 교체 → **참조 0 확인** → 파일 삭제. 이 순서를 지키는 것이 이 함수의 전부다.

    limit_mb > 0이면 그만큼만 정리한다(한 번에 다 하지 않고 확인하며 진행할 때).
    """
    pl = plan(tenant_id)
    groups = pl["groups"]
    if limit_mb > 0:
        picked, acc = [], 0.0
        for g in groups:
            if acc / 1e6 >= limit_mb:
                break
            picked.append(g)
            acc += g["bytes"]
        groups = picked
    if not groups:
        return {"ok": True, "n_groups": 0, "freed_mb": 0.0, "note": "정리할 중복이 없다"}

    mapping = {}
    for g in groups:
        for d in g["drop"]:
            mapping[os.path.realpath(d)] = g["keep"]

    # 🧾 롤백 근거 — 무엇을 무엇으로 바꿨는지 남긴다(원본 보존 원칙: 지우기 전에 기록).
    try:
        import time as _t
        rb = os.path.join(_storage_root(), f".dedup-{int(_t.time())}.json")
        with open(rb, "w") as f:
            json.dump({"mapping": mapping}, f, ensure_ascii=False)
    except Exception:
        rb = ""
        _log.warning("[dedup] 롤백 기록 실패 — 그래도 진행한다(DB 교체는 되돌릴 수 있다)")

    changed = _swap_refs(mapping)

    # ✅ 삭제 전 확인 — 지우려는 파일을 아직도 누가 참조하면 지우지 않는다.
    from app.main import _referenced_media
    refs = _referenced_media()
    freed, removed, held = 0, 0, 0
    for d in mapping:
        if d in refs:
            held += 1
            _log.warning("[dedup] 아직 참조 중이라 남긴다: %s", d)
            continue
        for target in [d] + _derived_of(d):
            try:
                sz = os.path.getsize(target)
                os.remove(target)
                freed += sz
                removed += 1
            except FileNotFoundError:
                pass
            except Exception:
                _log.exception("[dedup] 삭제 실패 %s", target)
    return {"ok": True, "n_groups": len(groups), "pieces_updated": changed,
            "files_removed": removed, "held_still_referenced": held,
            "freed_mb": round(freed / 1e6, 1), "rollback": os.path.basename(rb) if rb else ""}
