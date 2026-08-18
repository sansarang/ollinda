"""📤 발행 큐 — 미리보기에서 누른 '발행'을 로컬 에이전트가 집어간다.

2026-08-18 사장님 지시:
  "콘텐츠가 만들어진 미리보기 창에서 내가 확인하고 발행을 누르면
   네이버에서 실제로 작성하듯이 작성되어야 한다. 사진 배치 및 글."

왜 큐가 필요한가 — 브라우저는 서버에서 못 돈다:
  Railway 컨테이너에는 화면이 없고, 네이버 세션은 **가게마다 다른 계정**이라
  사장님 PC의 브라우저 프로필에만 산다. 그래서 구조가 이렇게 갈린다.
      서버:  글을 만들고 큐에 넣는다(무엇을 발행할지)
      로컬:  큐를 집어가 실제로 작성한다(어떻게 발행할지)
  둘을 잇는 것이 이 파일이다.

★ 가게마다 계정이 다르다 — 큐 항목은 반드시 tenant_id를 들고 다닌다.
  로컬 에이전트가 그 값으로 브라우저 프로필(~/.browser/{tenant_id})을 고른다.
  이걸 놓치면 남의 블로그에 글이 올라간다 — 되돌릴 수 없는 사고다.

★ 이중 발행 금지 — claim()이 원자적으로 한 건만 잡는다.
  buddy.py가 2026-08-11에 겪은 이중발송 사고(두 배치가 겹쳐 같은 사람에게 두 번)와 같은 계열.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from app import db

_log = logging.getLogger("shopcast.pubqueue")

#: 집어간 뒤 이 시간이 지나도 결과가 없으면 죽은 것으로 보고 되돌린다.
#: 로컬 맥북이 꺼지거나 브라우저가 멈춘 경우 — 큐에 영원히 잠기면 안 된다.
STALE_MIN = 30


def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS publish_queue("
              "id INTEGER PRIMARY KEY AUTOINCREMENT,"
              "tenant_id TEXT NOT NULL, piece_id TEXT NOT NULL, asset_id TEXT,"
              "status TEXT DEFAULT 'queued',"      # queued · claimed · done · failed
              "payload TEXT, result TEXT, error TEXT,"
              "queued_at TEXT, claimed_at TEXT, done_at TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pq_status ON publish_queue(status, id)")


def enqueue(tenant_id: str, piece, asset_id: str = "") -> dict:
    """미리보기에서 '발행' — 큐에 넣는다. 이미 대기 중이면 중복 등록하지 않는다."""
    pid = getattr(piece, "id", "") or ""
    if not (tenant_id and pid):
        return {"ok": False, "error": "tenant_id·piece_id 필요"}
    pl = getattr(piece, "payload", None) or {}
    body = pl.get("body") or ""
    if not body.strip():
        return {"ok": False, "error": "본문이 비어 있다"}
    item = {
        "title": pl.get("title") or "",
        "body": body,
        "tags": pl.get("tags") or pl.get("seo_keywords") or [],
        "image_paths": pl.get("image_paths") or [],
        # 사진을 본문 어디에 넣을지 — [사진N] 마커가 본문에 그대로 있다.
        "photo_markers": pl.get("photo_markers") or [],
    }
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with db._conn() as c:
            _ensure(c)
            dup = c.execute("SELECT id FROM publish_queue WHERE piece_id=? "
                            "AND status IN ('queued','claimed')", (pid,)).fetchone()
            if dup:
                return {"ok": True, "id": dup["id"], "dup": True}
            cur = c.execute(
                "INSERT INTO publish_queue(tenant_id,piece_id,asset_id,status,payload,queued_at)"
                " VALUES(?,?,?,'queued',?,?)",
                (tenant_id, pid, asset_id or getattr(piece, "asset_id", "") or "",
                 json.dumps(item, ensure_ascii=False), now))
            qid = cur.lastrowid
    except Exception:
        _log.exception("[pubqueue] 등록 실패 piece=%s", pid)
        return {"ok": False, "error": "등록 실패"}
    try:
        from app.agents import OPS, journal
        journal.write(OPS, f"발행 대기 등록 — {item['title'][:40]}",
                      why="사장님이 미리보기에서 확인 후 발행을 눌렀다. 로컬 에이전트가 집어간다.",
                      kind="act", tenant_id=tenant_id, piece_id=pid)
    except Exception:
        pass
    return {"ok": True, "id": qid}


def _release_stale(c) -> int:
    """죽은 claim 되돌리기 — 로컬이 꺼지면 큐가 영원히 잠긴다(시간 기준 자동 해제)."""
    cut = (datetime.utcnow() - timedelta(minutes=STALE_MIN)).isoformat(timespec="seconds")
    cur = c.execute("UPDATE publish_queue SET status='queued', claimed_at=NULL "
                    "WHERE status='claimed' AND (claimed_at IS NULL OR claimed_at < ?)", (cut,))
    return cur.rowcount or 0


def claim(tenant_id: str = "") -> "dict | None":
    """로컬 에이전트가 한 건 집어간다. **한 번에 하나만** — 이중 발행 금지."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with db._conn() as c:
            _ensure(c)
            n = _release_stale(c)
            if n:
                _log.warning("[pubqueue] 죽은 작업 %d건 되돌림(%d분 초과)", n, STALE_MIN)
            q = "SELECT * FROM publish_queue WHERE status='queued'"
            a: list = []
            if tenant_id:
                q += " AND tenant_id=?"
                a.append(tenant_id)
            q += " ORDER BY id LIMIT 1"
            row = c.execute(q, a).fetchone()
            if not row:
                return None
            c.execute("UPDATE publish_queue SET status='claimed', claimed_at=? WHERE id=? "
                      "AND status='queued'", (now, row["id"]))
            if c.total_changes == 0:
                return None                       # 다른 실행이 먼저 집어갔다
            d = dict(row)
    except Exception:
        _log.exception("[pubqueue] claim 실패")
        return None
    try:
        d["payload"] = json.loads(d.get("payload") or "{}")
    except Exception:
        d["payload"] = {}
    return d


def finish(qid: int, ok: bool, url: str = "", error: str = "") -> dict:
    """로컬이 결과를 돌려준다. 성공이면 URL이 있어야 한다 — 없으면 성공이 아니다."""
    if ok and not (url or "").strip():
        ok, error = False, (error or "발행 URL이 없다(성공으로 볼 수 없다)")
    now = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with db._conn() as c:
            _ensure(c)
            row = c.execute("SELECT * FROM publish_queue WHERE id=?", (qid,)).fetchone()
            if not row:
                return {"ok": False, "error": "없는 작업"}
            c.execute("UPDATE publish_queue SET status=?, result=?, error=?, done_at=? WHERE id=?",
                      ("done" if ok else "failed", url[:500], error[:300], now, qid))
            item = dict(row)
    except Exception:
        _log.exception("[pubqueue] finish 실패 id=%s", qid)
        return {"ok": False, "error": "저장 실패"}
    try:
        from app.agents import OPS, journal
        journal.write(OPS, ("발행 완료 — " + url[:60]) if ok else "발행 실패",
                      why=error[:200] if not ok else "로컬 에이전트가 네이버에 올렸다.",
                      kind="act" if ok else "alert",
                      tenant_id=item.get("tenant_id", ""), piece_id=item.get("piece_id", ""))
    except Exception:
        pass
    return {"ok": True, "published": ok}


def pending(tenant_id: str = "") -> list:
    try:
        with db._conn() as c:
            _ensure(c)
            q = "SELECT id,tenant_id,piece_id,status,queued_at,error FROM publish_queue " \
                "WHERE status IN ('queued','claimed')"
            a: list = []
            if tenant_id:
                q += " AND tenant_id=?"
                a.append(tenant_id)
            return [dict(r) for r in c.execute(q + " ORDER BY id", a).fetchall()]
    except Exception:
        return []


def status_of(piece_id: str) -> str:
    """그 글의 발행 상태 — 미리보기 화면이 버튼 대신 상태를 보여줄 때 쓴다."""
    try:
        with db._conn() as c:
            _ensure(c)
            r = c.execute("SELECT status FROM publish_queue WHERE piece_id=? "
                          "ORDER BY id DESC LIMIT 1", (piece_id,)).fetchone()
            return (r["status"] if r else "")
    except Exception:
        return ""
