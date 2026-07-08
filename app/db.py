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
                         ("publish_schedule", "INTEGER DEFAULT 0"), ("is_demo", "INTEGER DEFAULT 0")]:
            try:
                c.execute(f"ALTER TABLE tenants ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        c.execute("CREATE TABLE IF NOT EXISTS demo_usage(ip TEXT PRIMARY KEY, count INTEGER, last TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS place_news("
                  "id TEXT PRIMARY KEY, tenant_id TEXT, text TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS links("
                  "code TEXT PRIMARY KEY, tenant_id TEXT, target TEXT, label TEXT, "
                  "clicks INTEGER DEFAULT 0, created_at TEXT)")
        # 순위 스냅샷 — 키워드별 순위 이력('5위→2위⬆️' 성장 추적)
        c.execute("CREATE TABLE IF NOT EXISTS rank_snapshots("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, keyword TEXT, "
                  "rank INTEGER, checked_at TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rank_tk ON rank_snapshots(tenant_id, keyword, checked_at)")
        # 다중 가게 — 한 사용자가 여러 가게(tenant)를 등록·전환
        c.execute("CREATE TABLE IF NOT EXISTS user_stores("
                  "user_id TEXT, tenant_id TEXT, created_at TEXT, PRIMARY KEY(user_id, tenant_id))")
        # 마이그레이션: users.tenant_id (구독자 ↔ 본인 가게), free_used (무료 생성 횟수)
        for col, ddl in [("tenant_id", "TEXT"), ("free_used", "INTEGER DEFAULT 0"),
                         ("usage_month", "TEXT"), ("month_used", "INTEGER DEFAULT 0")]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass


def _now() -> str:
    return datetime.utcnow().isoformat()


def get_prev_rank(tenant_id: str, keyword: str) -> "int | None":
    """이 키워드의 '오늘 이전' 마지막 순위(변화 계산용). 같은 날 재조회에도 안정. 없으면 None."""
    today = _now()[:10]
    with _conn() as c:
        r = c.execute("SELECT rank FROM rank_snapshots WHERE tenant_id=? AND keyword=? "
                      "AND checked_at NOT LIKE ? ORDER BY checked_at DESC LIMIT 1",
                      (tenant_id, keyword, today + "%")).fetchone()
    return (r["rank"] if r else None)


def improving_keywords(tenant_id: str, limit: int = 5) -> list[dict]:
    """순위가 개선된(또는 진입한) 키워드 — 학습 루프용. [{keyword, first, last, gain}]."""
    out = []
    with _conn() as c:
        kws = c.execute("SELECT DISTINCT keyword FROM rank_snapshots WHERE tenant_id=?", (tenant_id,)).fetchall()
        for kr in kws:
            k = kr["keyword"]
            rows = c.execute("SELECT rank, checked_at FROM rank_snapshots WHERE tenant_id=? AND keyword=? "
                             "ORDER BY checked_at ASC", (tenant_id, k)).fetchall()
            if len(rows) < 2:
                continue
            first = rows[0]["rank"] if rows[0]["rank"] else 6      # 0(밖)=6로 취급
            last = rows[-1]["rank"] if rows[-1]["rank"] else 6
            gain = first - last                                    # +면 순위 상승(숫자 작아짐)
            if gain > 0:
                out.append({"keyword": k, "first": rows[0]["rank"], "last": rows[-1]["rank"], "gain": gain})
    out.sort(key=lambda x: -x["gain"])
    return out[:limit]


def save_rank_snapshot(tenant_id: str, keyword: str, rank: "int | None") -> None:
    """순위 스냅샷 기록(하루 1개로 제한 — 같은 날 재조회는 갱신)."""
    if rank is None:
        return
    today = _now()[:10]
    with _conn() as c:
        ex = c.execute("SELECT id FROM rank_snapshots WHERE tenant_id=? AND keyword=? AND checked_at LIKE ?",
                       (tenant_id, keyword, today + "%")).fetchone()
        if ex:
            c.execute("UPDATE rank_snapshots SET rank=?, checked_at=? WHERE id=?", (rank, _now(), ex["id"]))
        else:
            c.execute("INSERT INTO rank_snapshots(tenant_id, keyword, rank, checked_at) VALUES(?,?,?,?)",
                      (tenant_id, keyword, rank, _now()))


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


def link_store(user_id: str, tenant_id: str) -> None:
    """가게(tenant)를 사용자 소유 목록에 등록(중복 무시)."""
    if not (user_id and tenant_id):
        return
    with _conn() as c:
        try:
            c.execute("INSERT INTO user_stores(user_id,tenant_id,created_at) VALUES(?,?,?)",
                      (user_id, tenant_id, _now()))
        except sqlite3.IntegrityError:
            pass


def list_user_stores(user_id: str) -> list:
    """사용자가 등록한 모든 가게(Tenant) 목록(등록순)."""
    with _conn() as c:
        rows = c.execute("SELECT tenant_id FROM user_stores WHERE user_id=? ORDER BY created_at ASC",
                         (user_id,)).fetchall()
    out = []
    for r in rows:
        t = get_tenant(r["tenant_id"])
        if t:
            out.append(t)
    return out


def add_store(user_id: str):
    """새 가게 생성 + 소유 등록 + 활성으로 전환. 생성된 Tenant 반환."""
    t = create_tenant(name="새 가게", industry="", region="", biz_type="local")
    link_store(user_id, t.id)
    set_user_tenant(user_id, t.id)
    return t


def switch_store(user_id: str, tenant_id: str) -> bool:
    """활성 가게 전환 — 본인 소유일 때만."""
    with _conn() as c:
        r = c.execute("SELECT 1 FROM user_stores WHERE user_id=? AND tenant_id=?",
                      (user_id, tenant_id)).fetchone()
    if r:
        set_user_tenant(user_id, tenant_id)
        return True
    return False


def delete_store(user_id: str, tenant_id: str) -> bool:
    """가게 삭제(본인 소유·마지막 1개 아님) 후 다른 가게로 전환. 실수 추가 취소용."""
    stores = list_user_stores(user_id)
    if len(stores) <= 1 or tenant_id not in [s.id for s in stores]:
        return False
    with _conn() as c:
        c.execute("DELETE FROM user_stores WHERE user_id=? AND tenant_id=?", (user_id, tenant_id))
        c.execute("DELETE FROM content_pieces WHERE tenant_id=?", (tenant_id,))
        c.execute("DELETE FROM tenants WHERE id=?", (tenant_id,))
    other = [s for s in stores if s.id != tenant_id]
    if other:
        set_user_tenant(user_id, other[-1].id)      # 직전(마지막) 가게로 복귀
    return True


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


def delete_set(asset_id: str, tenant_id: str) -> None:
    """콘텐츠 세트 삭제(본인 가게 것만) — 이력 관리."""
    with _conn() as c:
        c.execute("DELETE FROM content_pieces WHERE asset_id=? AND tenant_id=?", (asset_id, tenant_id))


# ── 랜딩 무료체험(미가입) — IP 기준 횟수 ────────────────
def demo_ip_count(ip: str) -> int:
    """IP당 무료 미리보기 누적 사용 횟수(리셋 없음 — 2회 후 가입 유도)."""
    with _conn() as c:
        r = c.execute("SELECT count FROM demo_usage WHERE ip=?", (ip,)).fetchone()
    return (r["count"] or 0) if r else 0


def incr_demo_ip(ip: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO demo_usage(ip,count,last) VALUES(?,1,?) "
                  "ON CONFLICT(ip) DO UPDATE SET count=count+1, last=excluded.last", (ip, _now()))


def reset_demo_usage(ip: str = "") -> None:
    """무료 체험 사용량 초기화(ip 지정 시 해당 IP만, 없으면 전체)."""
    with _conn() as c:
        if ip:
            c.execute("DELETE FROM demo_usage WHERE ip=?", (ip,))
        else:
            c.execute("DELETE FROM demo_usage")


def mark_tenant_demo(tid: str) -> None:
    with _conn() as c:
        c.execute("UPDATE tenants SET is_demo=1 WHERE id=?", (tid,))


# ── 플레이스 소식 ─────────────────────────────────────
def add_place_news(tenant_id: str, text: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO place_news(id,tenant_id,text,created_at) VALUES(?,?,?,?)",
                  (str(uuid.uuid4()), tenant_id, text, _now()))


def list_place_news(tenant_id: str, limit: int = 6) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT id,text,created_at FROM place_news WHERE tenant_id=? "
                             "ORDER BY created_at DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ── 제휴/추적 단축링크 ────────────────────────────────
def create_link(tenant_id: str, target: str, label: str = "") -> str:
    code = uuid.uuid4().hex[:7]
    with _conn() as c:
        c.execute("INSERT INTO links(code,tenant_id,target,label,clicks,created_at) VALUES(?,?,?,?,0,?)",
                  (code, tenant_id, target, label, _now()))
    return code


def ensure_track_link(tenant_id: str, target: str, label: str = "") -> Optional[dict]:
    """목적지(target)로 가는 추적 링크 get-or-create(중복 방지). 성과 실측용."""
    if not (target or "").strip():
        return None
    for l in list_links(tenant_id, 50):
        if l.get("target") == target:
            return l
    return get_link(create_link(tenant_id, target, label))


def get_link(code: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM links WHERE code=?", (code,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None


def incr_link_click(code: str) -> None:
    with _conn() as c:
        c.execute("UPDATE links SET clicks=clicks+1 WHERE code=?", (code,))


def list_links(tenant_id: str, limit: int = 20) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM links WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                             (tenant_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def asset_is_demo(asset_id: str) -> bool:
    """자산이 '무료체험(데모)' 소속인지 — 데모 다운로드 보안 게이트."""
    with _conn() as c:
        r = c.execute("SELECT t.is_demo FROM content_pieces cp JOIN tenants t ON cp.tenant_id=t.id "
                      "WHERE cp.asset_id=? LIMIT 1", (asset_id,)).fetchone()
    return bool(r and r["is_demo"])


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


def list_sets(statuses: Optional[list[str]] = None, limit: int = 100,
              tenant_id: Optional[str] = None) -> list[dict]:
    """검수 큐를 '세트(asset_id)' 단위로 묶어 최신순 반환. tenant_id로 특정 가게만."""
    q = ("SELECT cp.asset_id, cp.tenant_id, t.name AS tname, MAX(cp.created_at) AS created, "
         "COUNT(*) AS n FROM content_pieces cp LEFT JOIN tenants t ON cp.tenant_id=t.id ")
    conds, args = [], []
    if statuses:
        conds.append("cp.status IN (%s)" % ",".join("?" * len(statuses))); args += statuses
    if tenant_id:
        conds.append("cp.tenant_id=?"); args.append(tenant_id)
    if conds:
        q += "WHERE " + " AND ".join(conds) + " "
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
