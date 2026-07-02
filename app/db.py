"""
shopcast 영속화 — SQLite (MVP). 추후 Postgres/Supabase로 교체 가능.
payload/meta/result 는 JSON 문자열로 저장하고 dict로 복원한다.
토큰류는 MVP에선 그대로 저장하나, 운영에선 반드시 암호화(_enc 필드).
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from app.domain.models import (Asset, AssetType, Channel, ChannelAccount,
                               ContentKind, ContentPiece, ContentStatus, Tenant)

DB_PATH = os.environ.get("SHOPCAST_DB", "shopcast.sqlite")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants(
              id TEXT PRIMARY KEY, name TEXT, industry TEXT, region TEXT,
              upload_token TEXT UNIQUE, created_at TEXT);
            CREATE TABLE IF NOT EXISTS channel_accounts(
              id TEXT PRIMARY KEY, tenant_id TEXT, channel TEXT,
              access_token_enc TEXT, refresh_token_enc TEXT, meta TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS assets(
              id TEXT PRIMARY KEY, tenant_id TEXT, type TEXT, path TEXT, note TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS content_pieces(
              id TEXT PRIMARY KEY, tenant_id TEXT, asset_id TEXT, channel TEXT, kind TEXT,
              payload TEXT, status TEXT, scheduled_at TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS publications(
              id TEXT PRIMARY KEY, content_id TEXT, channel TEXT, external_id TEXT,
              published_at TEXT, result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS industry_profiles(
              key TEXT PRIMARY KEY, name TEXT, data TEXT, source TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS users(
              id TEXT PRIMARY KEY, email TEXT UNIQUE, pw_hash TEXT, salt TEXT,
              kakao_id TEXT, name TEXT, plan TEXT DEFAULT 'free', created_at TEXT);
            CREATE TABLE IF NOT EXISTS subscriptions(
              id TEXT PRIMARY KEY, user_id TEXT UNIQUE, plan TEXT, status TEXT,
              billing_key TEXT, customer_key TEXT, amount INTEGER,
              started_at TEXT, expires_at TEXT, last_payment_at TEXT, created_at TEXT);
            """
        )
        # 마이그레이션: tenants 신규 컬럼(연락처·장소·자율레벨·사업형태)
        for col, ddl in [("phone", "TEXT"), ("address", "TEXT"), ("hours", "TEXT"),
                         ("map_url", "TEXT"), ("autonomy", "INTEGER DEFAULT 0"),
                         ("biz_type", "TEXT DEFAULT 'local'"), ("marketplace", "TEXT"),
                         ("buy_url", "TEXT"), ("search_kw", "TEXT"), ("brand_name", "TEXT"),
                         ("publish_schedule", "INTEGER DEFAULT 0")]:
            try:
                c.execute(f"ALTER TABLE tenants ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        # 마이그레이션: users.tenant_id (구독자 ↔ 본인 가게), free_used (무료 생성 횟수)
        for col, ddl in [("tenant_id", "TEXT"), ("free_used", "INTEGER DEFAULT 0"),
                         ("usage_month", "TEXT"), ("month_used", "INTEGER DEFAULT 0")]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass


def _now() -> str:
    return datetime.utcnow().isoformat()


# ── Tenant ─────────────────────────────────────────────
def create_tenant(name: str, industry: str, region: str = "", biz_type: str = "local") -> Tenant:
    tid = str(uuid.uuid4())
    token = uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO tenants(id,name,industry,region,biz_type,upload_token,created_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (tid, name, industry, region, biz_type or "local", token, _now()))
    return Tenant(id=tid, name=name, industry=industry, region=region, biz_type=biz_type or "local")


def _row_to_tenant(r: sqlite3.Row) -> Tenant:
    keys = r.keys()
    g = lambda k, d="": (r[k] if k in keys else d) or d
    return Tenant(id=r["id"], name=r["name"], industry=r["industry"], region=r["region"] or "",
                  phone=g("phone"), address=g("address"), hours=g("hours"), map_url=g("map_url"),
                  autonomy=g("autonomy", 0) or 0,
                  biz_type=g("biz_type", "local") or "local",
                  marketplace=g("marketplace"), buy_url=g("buy_url"),
                  search_kw=g("search_kw"), brand_name=g("brand_name"),
                  publish_schedule=g("publish_schedule", 0) or 0)


def update_tenant_profile(tid: str, phone: str, address: str, hours: str, map_url: str) -> None:
    with _conn() as c:
        c.execute("UPDATE tenants SET phone=?, address=?, hours=?, map_url=? WHERE id=?",
                  (phone, address, hours, map_url, tid))


def rename_tenant(tid: str, name: str, industry: str, region: str) -> None:
    """상호/업종/지역 갱신(구독자 본인 가게 설정)."""
    with _conn() as c:
        c.execute("UPDATE tenants SET name=?, industry=?, region=? WHERE id=?",
                  (name.strip() or "내 가게", industry.strip(), region.strip(), tid))


def set_autonomy(tid: str, level: int) -> None:
    with _conn() as c:
        c.execute("UPDATE tenants SET autonomy=? WHERE id=?", (int(level), tid))


def set_publish_schedule(tid: str, n: int) -> None:
    with _conn() as c:
        c.execute("UPDATE tenants SET publish_schedule=? WHERE id=?", (int(n), tid))


def tenant_ops_stats(tid: str) -> dict:
    """대행 관제탑용 — 검수대기·전체·이번주 발행 수."""
    from datetime import datetime, timedelta
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    with _conn() as c:
        draft = c.execute("SELECT COUNT(*) FROM content_pieces WHERE tenant_id=? AND status='draft'",
                          (tid,)).fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM content_pieces WHERE tenant_id=?", (tid,)).fetchone()[0]
        pub_week = c.execute(
            "SELECT COUNT(*) FROM publications p JOIN content_pieces cp ON p.content_id=cp.id "
            "WHERE cp.tenant_id=? AND p.published_at>=?", (tid, week_ago)).fetchone()[0]
    return {"draft": draft, "total": total, "pub_week": pub_week}


def update_tenant_classification(tid: str, biz_type: str, marketplace: str = "",
                                 buy_url: str = "", search_kw: str = "", brand_name: str = "") -> None:
    """사업형태(분류축) + 셀러 부가정보 저장."""
    with _conn() as c:
        c.execute("UPDATE tenants SET biz_type=?, marketplace=?, buy_url=?, search_kw=?, brand_name=? "
                  "WHERE id=?",
                  ((biz_type or "local").strip(), marketplace.strip(), buy_url.strip(),
                   search_kw.strip(), brand_name.strip(), tid))


# ── 업종 프로필(AI 자동생성/수정) ──────────────────────
def save_industry_profile(key: str, name: str, data: dict, source: str = "ai") -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO industry_profiles(key,name,data,source,created_at) VALUES(?,?,?,?,?)",
                  (key, name, json.dumps(data, ensure_ascii=False), source, _now()))


def get_industry_profile(key: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT data FROM industry_profiles WHERE key=?", (key,)).fetchone()
        return json.loads(r["data"]) if r else None
    except sqlite3.OperationalError:
        return None


def create_user(email: str = "", pw_hash: str = "", salt: str = "",
                kakao_id: str = "", name: str = "") -> dict:
    uid = str(uuid.uuid4())
    with _conn() as c:
        c.execute("INSERT INTO users(id,email,pw_hash,salt,kakao_id,name,plan,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (uid, email.lower().strip(), pw_hash, salt, kakao_id, name, "free", _now()))
    return get_user(uid)


def get_user(uid: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def get_user_by_email(email: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
    return dict(r) if r else None


def get_user_by_kakao(kakao_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE kakao_id=?", (str(kakao_id),)).fetchone()
    return dict(r) if r else None


def set_user_plan(uid: str, plan: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET plan=? WHERE id=?", (plan, uid))


def set_user_tenant(uid: str, tid: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET tenant_id=? WHERE id=?", (tid, uid))


def incr_user_free(uid: str, n: int = 1) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET free_used = COALESCE(free_used,0) + ? WHERE id=?", (n, uid))


def _ym() -> str:
    return datetime.utcnow().strftime("%Y%m")


def month_usage(uid: str) -> int:
    """이번 달 사용량(월이 바뀌면 0)."""
    with _conn() as c:
        r = c.execute("SELECT usage_month, month_used FROM users WHERE id=?", (uid,)).fetchone()
    if not r or (r["usage_month"] or "") != _ym():
        return 0
    return r["month_used"] or 0


def incr_month_usage(uid: str, n: int = 1) -> None:
    """이번 달 사용량 +n (월 바뀌면 리셋 후 카운트)."""
    ym = _ym()
    with _conn() as c:
        r = c.execute("SELECT usage_month, month_used FROM users WHERE id=?", (uid,)).fetchone()
        cur = (r["month_used"] or 0) if r and (r["usage_month"] or "") == ym else 0
        c.execute("UPDATE users SET usage_month=?, month_used=? WHERE id=?", (ym, cur + n, uid))


def list_users() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def reset_usage(uid: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET free_used=0, month_used=0 WHERE id=?", (uid,))


# ── 구독(결제) ─────────────────────────────────────────
def get_subscription(user_id: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None


def upsert_subscription(user_id: str, plan: str, status: str, billing_key: str = "",
                        customer_key: str = "", amount: int = 0, expires_at: str = "") -> None:
    existing = get_subscription(user_id)
    sid = existing["id"] if existing else str(uuid.uuid4())
    started = existing["started_at"] if existing else _now()
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO subscriptions"
            "(id,user_id,plan,status,billing_key,customer_key,amount,started_at,expires_at,last_payment_at,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, user_id, plan, status, billing_key or (existing or {}).get("billing_key", ""),
             customer_key or (existing or {}).get("customer_key", ""), amount,
             started, expires_at, _now(), (existing or {}).get("created_at") or _now()))


def subs_due_for_charge(within_days: int = 1) -> list[dict]:
    """만료 임박(within_days 내) 활성 구독 — 정기결제 갱신 대상."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() + timedelta(days=within_days)).isoformat()
    with _conn() as c:
        rows = c.execute("SELECT * FROM subscriptions WHERE status='active' AND billing_key!='' "
                         "AND expires_at<=?", (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_user_by_tenant(tid: str) -> Optional[dict]:
    """이 가게(tenant)를 소유한 구독자(user) 조회 — OAuth 콜백 분기용."""
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM users WHERE tenant_id=?", (tid,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None


def list_industry_profiles() -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT key,name,data,source FROM industry_profiles ORDER BY created_at DESC").fetchall()
        return [{"key": r["key"], "name": r["name"], "source": r["source"], **json.loads(r["data"])} for r in rows]
    except sqlite3.OperationalError:
        return []


def get_tenant(tid: str) -> Optional[Tenant]:
    with _conn() as c:
        r = c.execute("SELECT * FROM tenants WHERE id=?", (tid,)).fetchone()
    return _row_to_tenant(r) if r else None


def get_tenant_by_token(token: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM tenants WHERE upload_token=?", (token,)).fetchone()
    if not r:
        return None, None
    return _row_to_tenant(r), r["upload_token"]


def list_tenants() -> list[Tenant]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()
    return [_row_to_tenant(r) for r in rows]


def tenant_token(tid: str) -> str:
    with _conn() as c:
        r = c.execute("SELECT upload_token FROM tenants WHERE id=?", (tid,)).fetchone()
    return r["upload_token"] if r else ""


# ── Asset ──────────────────────────────────────────────
def create_asset(tenant_id: str, type_: AssetType, path: str, note: str = "") -> Asset:
    aid = str(uuid.uuid4())
    with _conn() as c:
        c.execute("INSERT INTO assets(id,tenant_id,type,path,note,created_at) VALUES(?,?,?,?,?,?)",
                  (aid, tenant_id, type_.value, path, note, _now()))
    return Asset(id=aid, tenant_id=tenant_id, type=type_, path=path, note=note)


# ── ContentPiece ───────────────────────────────────────
def save_piece(p: ContentPiece) -> ContentPiece:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO content_pieces"
            "(id,tenant_id,asset_id,channel,kind,payload,status,scheduled_at,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (p.id, p.tenant_id, p.asset_id, p.channel.value, p.kind.value,
             json.dumps(p.payload, ensure_ascii=False), p.status.value,
             p.scheduled_at.isoformat() if p.scheduled_at else None, _now()))
    return p


def _row_to_piece(r: sqlite3.Row) -> ContentPiece:
    return ContentPiece(
        id=r["id"], tenant_id=r["tenant_id"], asset_id=r["asset_id"],
        channel=Channel(r["channel"]), kind=ContentKind(r["kind"]),
        payload=json.loads(r["payload"] or "{}"), status=ContentStatus(r["status"]))


def get_set_pieces(asset_id: str) -> list[ContentPiece]:
    """한 업로드(세트)에서 나온 모든 채널 콘텐츠."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM content_pieces WHERE asset_id=? ORDER BY created_at", (asset_id,)).fetchall()
    return [_row_to_piece(r) for r in rows]


def list_sets(statuses: Optional[list[str]] = None, limit: int = 100) -> list[dict]:
    """검수 큐를 '세트(asset_id)' 단위로 묶어 최신순 반환."""
    q = ("SELECT cp.asset_id, cp.tenant_id, t.name AS tname, MAX(cp.created_at) AS created, "
         "COUNT(*) AS n FROM content_pieces cp LEFT JOIN tenants t ON cp.tenant_id=t.id ")
    args: list = []
    if statuses:
        q += "WHERE cp.status IN (%s) " % ",".join("?" * len(statuses))
        args += statuses
    q += "GROUP BY cp.asset_id ORDER BY created DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [{"asset_id": r["asset_id"], "tenant_id": r["tenant_id"], "tenant": r["tname"] or "",
             "created": (r["created"] or "")[:16].replace("T", " "), "n": r["n"]} for r in rows]


def get_piece(pid: str) -> Optional[ContentPiece]:
    with _conn() as c:
        r = c.execute("SELECT * FROM content_pieces WHERE id=?", (pid,)).fetchone()
    return _row_to_piece(r) if r else None


def list_pieces(status: Optional[ContentStatus] = None) -> list[ContentPiece]:
    q, args = "SELECT * FROM content_pieces", []
    if status:
        q += " WHERE status=?"; args.append(status.value)
    q += " ORDER BY created_at DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_piece(r) for r in rows]


def list_jobs(tenant_id: Optional[str] = None, channel: Optional[str] = None,
              status: Optional[str] = None, q: str = "", limit: int = 300,
              date_from: str = "", date_to: str = "") -> list[dict]:
    """대시보드용 작업 목록 — 가게명·상태·점수·생성/발행시각 조인."""
    sql = ("SELECT cp.id, cp.tenant_id, t.name AS tname, cp.channel, cp.kind, cp.payload, "
           "cp.status, cp.created_at, "
           "(SELECT published_at FROM publications WHERE content_id=cp.id "
           " ORDER BY published_at DESC LIMIT 1) AS pub "
           "FROM content_pieces cp LEFT JOIN tenants t ON cp.tenant_id=t.id WHERE 1=1")
    args: list = []
    if tenant_id:
        sql += " AND cp.tenant_id=?"; args.append(tenant_id)
    if channel:
        sql += " AND cp.channel=?"; args.append(channel)
    if status:
        sql += " AND cp.status=?"; args.append(status)
    sql += " ORDER BY cp.created_at DESC LIMIT ?"; args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        pl = json.loads(r["payload"] or "{}")
        title = pl.get("title") or (pl.get("text") or "")[:40] or "(제목없음)"
        if q and q not in title:
            continue
        day = (r["created_at"] or "")[:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        out.append({"id": r["id"], "tenant_id": r["tenant_id"], "tenant": r["tname"] or "",
                    "channel": r["channel"], "kind": r["kind"], "title": title,
                    "status": r["status"], "score": (pl.get("ranking_audit") or {}).get("score"),
                    "reach": (pl.get("reach") or {}).get("label", ""),
                    "created_at": (r["created_at"] or "")[:16].replace("T", " "),
                    "published_at": (r["pub"] or "")[:16].replace("T", " ")})
    return out


def update_piece_text(pid: str, text: str) -> None:
    update_piece_payload(pid, {"text": text})


def update_piece_payload(pid: str, fields: dict) -> None:
    p = get_piece(pid)
    if not p:
        return
    p.payload.update(fields)
    save_piece(p)


def set_piece_status(pid: str, status: ContentStatus) -> None:
    with _conn() as c:
        c.execute("UPDATE content_pieces SET status=? WHERE id=?", (status.value, pid))


# ── ChannelAccount ─────────────────────────────────────
def save_channel_account(tenant_id: str, channel: Channel, access_token: str,
                         refresh_token: str = "", meta: Optional[dict] = None) -> None:
    """채널 연동 토큰 upsert. MVP는 평문 저장(운영 전 암호화 필수)."""
    existing = get_channel_account(tenant_id, channel)
    aid = existing.id if existing else str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO channel_accounts"
            "(id,tenant_id,channel,access_token_enc,refresh_token_enc,meta,status) "
            "VALUES(?,?,?,?,?,?,?)",
            (aid, tenant_id, channel.value, access_token, refresh_token,
             json.dumps(meta or {}, ensure_ascii=False), "active"))


def list_channel_accounts(tenant_id: str) -> list[ChannelAccount]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM channel_accounts WHERE tenant_id=?", (tenant_id,)).fetchall()
    out = []
    for r in rows:
        out.append(ChannelAccount(id=r["id"], tenant_id=r["tenant_id"], channel=Channel(r["channel"]),
                                  access_token_enc=r["access_token_enc"] or "",
                                  refresh_token_enc=r["refresh_token_enc"] or "",
                                  meta=json.loads(r["meta"] or "{}"), status=r["status"] or "active"))
    return out


def get_channel_account(tenant_id: str, channel: Channel) -> Optional[ChannelAccount]:
    with _conn() as c:
        r = c.execute("SELECT * FROM channel_accounts WHERE tenant_id=? AND channel=?",
                      (tenant_id, channel.value)).fetchone()
    if not r:
        return None
    return ChannelAccount(id=r["id"], tenant_id=r["tenant_id"], channel=Channel(r["channel"]),
                          access_token_enc=r["access_token_enc"] or "",
                          refresh_token_enc=r["refresh_token_enc"] or "",
                          meta=json.loads(r["meta"] or "{}"), status=r["status"] or "active")


# ── Publication ────────────────────────────────────────
def create_publication(content_id: str, channel: Channel, external_id: str,
                       result: dict, error: str = "") -> None:
    with _conn() as c:
        c.execute("INSERT INTO publications(id,content_id,channel,external_id,published_at,result,error) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), content_id, channel.value, external_id, _now(),
                   json.dumps(result, ensure_ascii=False), error))
