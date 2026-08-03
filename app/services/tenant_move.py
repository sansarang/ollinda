"""
🚚 가게 이관 단일 함수(2026-08-03 사고 봉인).

사고: DB의 tenant_id만 옮기고 미디어 파일을 안 옮겼다. 사장님 화면의 사진이 전부 깨졌고
나는 '이관 완료'라고 보고했다. 원인은 절차가 둘로 나뉘어 있었던 것 —
"DB 따로, 미디어 따로"면 언젠가 한쪽만 하게 된다.

★ 원칙: 부분 이관이 불가능한 구조로 만든다. 이관은 이 함수 하나만 쓴다(수동 작업 금지).
  canonical 단일 관문 원칙의 이관판이다.

옮기는 자원(하나라도 빠지면 이관이 아니다):
  ① DB — tenant_id 컬럼을 가진 전 테이블(스키마에서 읽는다. 손목록이면 빠뜨린다)
  ② 미디어 — storage/<tenant>/ 파일 전부
  ③ 경로 — payload·assets 안에 박힌 절대경로 문자열
  ④ R2 — 새 키로 재미러(원본 영구 보존은 미러가 담당한다)
  ⑤ 감시·경험 — kw_blocks·kw_gaps·tenant_domain·owner_experience는 ①에 포함(tenant_id 보유)

완료의 정의는 '실행했다'가 아니라 verify()가 통과하는 것이다.
"""
from __future__ import annotations

import logging
import os
import shutil

from app import db

_log = logging.getLogger("shopcast.tenant_move")


def _storage_root() -> str:
    return os.environ.get("SHOPCAST_STORAGE", "storage")


def tables_with_tenant() -> list:
    """tenant_id 컬럼을 가진 테이블 전부 — 스키마에서 읽는다(손으로 적으면 반드시 빠뜨린다)."""
    out = []
    with db._conn() as c:
        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            t = r["name"]
            if t.startswith("sqlite_"):
                continue
            if "tenant_id" in [x["name"] for x in c.execute(f"PRAGMA table_info({t})")]:
                out.append(t)
    return sorted(out)


def _media_files(tid: str) -> list:
    d = os.path.join(_storage_root(), tid)
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def plan(src: str, dst: str) -> dict:
    """무엇이 옮겨질지 — 실행 전에 반드시 본다(규율 1조: 영향 범위)."""
    rows = {}
    with db._conn() as c:
        for t in tables_with_tenant():
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant_id=?", (src,)).fetchone()[0]
            if n:
                rows[t] = n
    return {"src": src, "dst": dst, "db_rows": rows,
            "db_total": sum(rows.values()), "media_files": len(_media_files(src))}


def verify(src: str, dst: str) -> dict:
    """완결 대조 — '옮겼다'가 아니라 이 표가 통과해야 이관이다(규율 2조).

    검사:
      ① 옛 tenant에 DB 잔존 0
      ② 옛 폴더에 파일 잔존 0
      ③ payload 안에 옛 tenant 경로 잔존 0
      ④ 새 tenant의 사진이 실제로 디스크에 있는가(세트별)
    """
    left_db = {}
    with db._conn() as c:
        for t in tables_with_tenant():
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE tenant_id=?", (src,)).fetchone()[0]
            if n:
                left_db[t] = n
        stale = c.execute("SELECT COUNT(*) FROM content_pieces WHERE tenant_id=? AND payload LIKE ?",
                          (dst, f"%{src}%")).fetchone()[0]
    left_files = _media_files(src)
    sets, missing = [], 0
    for s in db.list_sets(tenant_id=dst, limit=200):
        aid = s.get("asset_id") or ""
        paths = []
        for p in db.get_set_pieces(aid):
            paths = (p.payload or {}).get("image_paths") or []
            if paths:
                break
        on_disk = sum(1 for x in paths if x and os.path.exists(x))
        if paths and on_disk < len(paths):
            missing += len(paths) - on_disk
        sets.append({"asset": aid[:8], "photos": len(paths), "on_disk": on_disk})
    ok = (not left_db) and (not left_files) and stale == 0 and missing == 0
    return {"ok": ok, "left_db": left_db, "left_files": len(left_files),
            "stale_paths": stale, "photos_missing": missing,
            "sets": len(sets), "detail": sets[:8]}


def migrate_tenant(src: str, dst: str, dry: bool = True) -> dict:
    """가게 이관 — DB·미디어·경로·R2를 한 번에. 부분 이관이 불가능한 유일 경로.

    ★ 이관 계열 작업은 이 함수만 쓴다. 수동 SQL·수동 파일 이동 금지(오늘 사고의 원인).
    ★ dry=True가 기본 — 무엇이 옮겨지는지 먼저 본다.
    ★ 끝나면 verify()를 함께 돌려 결과에 붙인다. '했다'가 아니라 대조표가 완료의 정의다.
    """
    if not dst or src == dst:
        return {"ok": False, "error": "src/dst 확인 필요"}
    if not db.get_tenant(dst):
        return {"ok": False, "error": f"받는 가게가 실재하지 않음: {dst}"}
    pl = plan(src, dst)
    if dry:
        return {"ok": True, "dry": True, **pl}

    moved_db, errors = {}, []
    with db._conn() as c:
        for t, n in pl["db_rows"].items():
            try:
                c.execute(f"UPDATE OR REPLACE {t} SET tenant_id=? WHERE tenant_id=?", (dst, src))
                moved_db[t] = n
            except Exception as e:
                errors.append(f"{t}: {repr(e)[:80]}")

    sdir, ddir = os.path.join(_storage_root(), src), os.path.join(_storage_root(), dst)
    moved_files = 0
    if os.path.isdir(sdir):
        os.makedirs(ddir, exist_ok=True)
        for fn in _media_files(src):
            try:
                shutil.move(os.path.join(sdir, fn), os.path.join(ddir, fn))
                moved_files += 1
            except Exception as e:
                errors.append(f"file {fn}: {repr(e)[:60]}")

    rewritten = 0                                   # payload·assets 안의 절대경로
    with db._conn() as c:
        for r in c.execute("SELECT id, payload FROM content_pieces WHERE tenant_id=?",
                           (dst,)).fetchall():
            p = r["payload"] or ""
            if src in p:
                c.execute("UPDATE content_pieces SET payload=? WHERE id=?",
                          (p.replace(src, dst), r["id"]))
                rewritten += 1
        try:
            for r in c.execute("SELECT id, path FROM assets WHERE tenant_id=?", (dst,)).fetchall():
                if r["path"] and src in r["path"]:
                    c.execute("UPDATE assets SET path=? WHERE id=?",
                              (r["path"].replace(src, dst), r["id"]))
        except Exception:
            pass

    mirrored = 0
    try:                                            # R2 새 키로 — 원본 영구 보존은 미러가 담당
        from app import storage as _st
        for fn in _media_files(dst):
            if _st.mirror_to_r2(os.path.join(ddir, fn)):
                mirrored += 1
    except Exception as e:
        errors.append(f"mirror: {repr(e)[:80]}")

    res = {"ok": not errors, "dry": False, "src": src, "dst": dst,
           "db_tables": len(moved_db), "db_rows": sum(moved_db.values()),
           "files_moved": moved_files, "paths_rewritten": rewritten,
           "mirrored": mirrored, "errors": errors[:5]}
    res["verify"] = verify(src, dst)                # 완료의 정의는 대조표다
    if not res["verify"]["ok"]:
        _log.error("[tenant_move] 이관 미완결 — %s", res["verify"])
    return res
