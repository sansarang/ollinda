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
    c = sqlite3.connect(DB_PATH, timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")     # 동시 읽기/쓰기 허용(백그라운드 스레드 대비, B8)
        c.execute("PRAGMA busy_timeout=5000")    # 잠금 대기 5초(database is locked 완화)
    except Exception:
        pass
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
                         ("publish_schedule", "INTEGER DEFAULT 0"), ("is_demo", "INTEGER DEFAULT 0"),
                         ("lat", "REAL"), ("lon", "REAL"),        # 가게 좌표(사진 GPS 지오태그)
                         ("topic_axis", "TEXT"),                  # 전문 주제 축(C-Rank 주제 집중, 성장 PHASE 7)
                         ("naver_blog_url", "TEXT"),              # 사용자 네이버 블로그 URL(블로그등록 PHASE 1)
                         ("blog_id", "TEXT"),                     # 정규화된 블로그 아이디(RSS·순위매칭용)
                         ("parking", "TEXT"),                     # 주차 안내(블로그템플릿 PHASE 1 고정정보)
                         ("briefing_hour", "INTEGER DEFAULT 8"),  # 아침 브리핑 시각(KST, 브리핑 PHASE 2)
                         ("briefing_on", "INTEGER DEFAULT 1"),    # 브리핑 on/off
                         ("guide_dismissed", "INTEGER DEFAULT 0")]:  # 시작 가이드 '다음에 하기'(온보딩 P1)
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
        try:      # 블로그 키워드 순위 vs 플레이스 노출 순위 분리 추적(성장 PHASE 8)
            c.execute("ALTER TABLE rank_snapshots ADD COLUMN kind TEXT DEFAULT 'blog'")
        except sqlite3.OperationalError:
            pass
        # 다중 가게 — 한 사용자가 여러 가게(tenant)를 등록·전환
        c.execute("CREATE TABLE IF NOT EXISTS user_stores("
                  "user_id TEXT, tenant_id TEXT, created_at TEXT, PRIMARY KEY(user_id, tenant_id))")
        # 블로그 발행 기록(블로그등록 PHASE 2) — RSS 자동매칭(rss) / 사용자 확인(manual)
        c.execute("CREATE TABLE IF NOT EXISTS blog_publishes("
                  "piece_id TEXT PRIMARY KEY, tenant_id TEXT, published_url TEXT, "
                  "published_at TEXT, matched_by TEXT, match_score REAL, post_title TEXT, created_at TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_blogpub_t ON blog_publishes(tenant_id, published_at)")
        # 주간 성과 리포트(블로그등록 PHASE 4) — 발행 수·순위 변화 종합(앱내 + 이메일/카톡 스텁)
        c.execute("CREATE TABLE IF NOT EXISTS weekly_reports("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, week TEXT, "
                  "data TEXT, sent_email INTEGER DEFAULT 0, created_at TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_weekrep ON weekly_reports(tenant_id, week)")
        # 매일 아침 브리핑(브리핑 PHASE 1) — tenant×날짜 1건(1일 1회 원칙)
        c.execute("CREATE TABLE IF NOT EXISTS daily_briefings("
                  "tenant_id TEXT, date TEXT, data TEXT, passed INTEGER DEFAULT 0, "
                  "created_at TEXT, PRIMARY KEY(tenant_id, date))")
        for col, ddl in [("sent", "INTEGER DEFAULT 0"),        # 아침 발송 1일 1회 락(브리핑 PHASE 2)
                         ("evening_sent", "INTEGER DEFAULT 0")]:  # 저녁 피드백 1일 1회(PHASE 4)
            try:
                c.execute(f"ALTER TABLE daily_briefings ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        # 앱내 알림(상위노출 PHASE 2) — 발행 리마인더 등. read=0 이면 대시보드 배너 표시
        c.execute("CREATE TABLE IF NOT EXISTS notices("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, kind TEXT, "
                  "text TEXT, read INTEGER DEFAULT 0, created_at TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notices_t ON notices(tenant_id, read)")
        # ── 신규기능①: 경쟁사 추적기 ──
        c.execute("CREATE TABLE IF NOT EXISTS competitors("
                  "id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT, region TEXT, "
                  "keywords TEXT, created_at TEXT, active INTEGER DEFAULT 1)")
        c.execute("CREATE TABLE IF NOT EXISTS competitor_snapshots("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, competitor_id TEXT, keyword TEXT, "
                  "my_rank INTEGER, competitor_rank INTEGER, checked_at TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_comp_snap ON competitor_snapshots(competitor_id, checked_at)")
        # 마이그레이션: users.tenant_id (구독자 ↔ 본인 가게), free_used (무료 생성 횟수)
        for col, ddl in [("tenant_id", "TEXT"), ("free_used", "INTEGER DEFAULT 0"),
                         ("usage_month", "TEXT"), ("month_used", "INTEGER DEFAULT 0"),
                         ("agency_note", "TEXT"),   # 대행 고객 담당 메모(성장 PHASE 4)
                         ("feat_usage_month", "TEXT"),          # 신규기능 월간 사용량 리셋 기준
                         ("competitor_scans_used", "INTEGER DEFAULT 0"),
                         ("print_items_used", "INTEGER DEFAULT 0"),
                         ("angle_variants_used", "INTEGER DEFAULT 0")]:   # 앵글 변형 생성(상위노출 PHASE 4)
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass


def _now() -> str:
    return datetime.utcnow().isoformat()


def get_prev_rank(tenant_id: str, keyword: str, kind: str = "") -> "int | None":
    """이 키워드의 '오늘 이전' 마지막 순위(변화 계산용). 같은 날 재조회에도 안정. 없으면 None.
    kind 지정 시 해당 소스(blog|place|blog_search)만 — 미지정은 기존 동작(전체) 유지."""
    today = _now()[:10]
    with _conn() as c:
        if kind:
            try:
                r = c.execute("SELECT rank FROM rank_snapshots WHERE tenant_id=? AND keyword=? "
                              "AND checked_at NOT LIKE ? AND COALESCE(kind,'blog')=? "
                              "ORDER BY checked_at DESC LIMIT 1",
                              (tenant_id, keyword, today + "%", kind)).fetchone()
                return (r["rank"] if r else None)
            except sqlite3.OperationalError:
                pass
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


def save_place_rank(tenant_id: str, keyword: str, rank: "int | None") -> None:
    """플레이스 노출 순위 스냅샷(블로그 키워드 순위와 분리 추적, 성장 PHASE 8)."""
    save_rank_snapshot(tenant_id, keyword, rank, kind="place")


def save_rank_snapshot(tenant_id: str, keyword: str, rank: "int | None", kind: str = "blog") -> None:
    """순위 스냅샷 기록(하루 1개로 제한 — 같은 날 재조회는 갱신). kind=blog|place로 분리 추적."""
    if rank is None:
        return
    today = _now()[:10]
    with _conn() as c:
        try:      # 같은 날 dedup은 kind별로 분리(blog/place 충돌 방지, PHASE 8)
            ex = c.execute("SELECT id FROM rank_snapshots WHERE tenant_id=? AND keyword=? "
                           "AND checked_at LIKE ? AND COALESCE(kind,'blog')=?",
                           (tenant_id, keyword, today + "%", kind)).fetchone()
        except sqlite3.OperationalError:
            ex = c.execute("SELECT id FROM rank_snapshots WHERE tenant_id=? AND keyword=? AND checked_at LIKE ?",
                           (tenant_id, keyword, today + "%")).fetchone()
        if ex:
            c.execute("UPDATE rank_snapshots SET rank=?, checked_at=? WHERE id=?", (rank, _now(), ex["id"]))
        else:
            try:
                c.execute("INSERT INTO rank_snapshots(tenant_id, keyword, rank, checked_at, kind) VALUES(?,?,?,?,?)",
                          (tenant_id, keyword, rank, _now(), kind))
                return
            except sqlite3.OperationalError:
                pass
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
                  publish_schedule=g("publish_schedule", 0) or 0,
                  lat=(r["lat"] if "lat" in keys else None), lon=(r["lon"] if "lon" in keys else None),
                  topic_axis=g("topic_axis"),
                  naver_blog_url=g("naver_blog_url"), blog_id=g("blog_id"),
                  parking=g("parking"),
                  # g()는 falsy를 기본값으로 바꿔버리므로(0→1) on/off는 raw로 읽는다
                  briefing_hour=(r["briefing_hour"] if "briefing_hour" in keys and r["briefing_hour"] else 8),
                  briefing_on=(0 if ("briefing_on" in keys and r["briefing_on"] == 0) else 1),
                  guide_dismissed=(1 if ("guide_dismissed" in keys and r["guide_dismissed"]) else 0))


def dismiss_guide(tid: str) -> None:
    """시작 가이드 '다음에 하기'(온보딩 P1) — 다시 띄우지 않음."""
    with _conn() as c:
        c.execute("UPDATE tenants SET guide_dismissed=1 WHERE id=?", (tid,))


def set_tenant_coords(tid: str, lat: float, lon: float) -> None:
    """가게 좌표 저장(사진 GPS 지오태그용) — 한국 범위 검증."""
    try:
        lat, lon = float(lat), float(lon)
    except Exception:
        return
    if not (33 <= lat <= 39 and 124 <= lon <= 132):     # 한국 밖이면 무시(잘못된 좌표 방지)
        return
    with _conn() as c:
        c.execute("UPDATE tenants SET lat=?, lon=? WHERE id=?", (lat, lon, tid))


def update_store_info(tid: str, phone: str, address: str, hours: str,
                      parking: str, map_url: str) -> None:
    """매장 고정정보 저장(블로그템플릿 PHASE 1) — 한 번 입력하면 모든 글에 재사용."""
    with _conn() as c:
        try:
            c.execute("UPDATE tenants SET phone=?, address=?, hours=?, parking=?, map_url=? WHERE id=?",
                      (phone.strip(), address.strip(), hours.strip(), parking.strip(),
                       map_url.strip(), tid))
        except sqlite3.OperationalError:      # parking 컬럼 없던 구DB — 기존 필드만
            c.execute("UPDATE tenants SET phone=?, address=?, hours=?, map_url=? WHERE id=?",
                      (phone.strip(), address.strip(), hours.strip(), map_url.strip(), tid))


def set_topic_axis(tid: str, axis: str) -> None:
    """'전문 주제 축' 저장 — 이 블로그가 밀 핵심 주제/키워드군(C-Rank 주제 집중, 상위노출 PHASE 2)."""
    with _conn() as c:
        try:
            c.execute("UPDATE tenants SET topic_axis=? WHERE id=?", ((axis or "").strip()[:120], tid))
        except sqlite3.OperationalError:
            pass


def today_feedback_stats(tenant_id: str) -> dict:
    """저녁 피드백 원자료(브리핑 PHASE 4, 전부 실측) — 오늘 만든 콘텐츠 수·
    오늘 추적링크 클릭 수·오늘 스냅샷의 순위 변화(어제 대비)."""
    today = _now()[:10]
    out = {"made_today": 0, "clicks_today": 0, "rank_moves": []}
    try:
        with _conn() as c:
            out["made_today"] = c.execute(
                "SELECT COUNT(DISTINCT asset_id) FROM content_pieces WHERE tenant_id=? AND created_at LIKE ?",
                (tenant_id, today + "%")).fetchone()[0]
            try:
                out["clicks_today"] = c.execute(
                    "SELECT COUNT(*) FROM link_clicks lc JOIN links l ON lc.code=l.code "
                    "WHERE l.tenant_id=? AND lc.ts LIKE ?", (tenant_id, today + "%")).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            rows = c.execute(
                "SELECT keyword, rank FROM rank_snapshots WHERE tenant_id=? AND checked_at LIKE ? "
                "AND rank IS NOT NULL GROUP BY keyword", (tenant_id, today + "%")).fetchall()
        for r in rows:
            prev = get_prev_rank(tenant_id, r["keyword"])
            if prev is not None and prev != r["rank"]:
                out["rank_moves"].append({"keyword": r["keyword"], "before": prev, "after": r["rank"]})
    except Exception:
        pass
    return out


# ── 매일 아침 브리핑(브리핑 PHASE 1·2) ──
def save_briefing(tenant_id: str, date: str, data: dict) -> None:
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO daily_briefings(tenant_id, date, data, passed, created_at) "
                      "VALUES(?,?,?,COALESCE((SELECT passed FROM daily_briefings WHERE tenant_id=? AND date=?),0),?)",
                      (tenant_id, date, json.dumps(data, ensure_ascii=False), tenant_id, date, _now()))
    except sqlite3.OperationalError:
        pass


def get_briefing(tenant_id: str, date: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT data, passed FROM daily_briefings WHERE tenant_id=? AND date=?",
                          (tenant_id, date)).fetchone()
        if not r:
            return None
        d = json.loads(r["data"] or "{}")
        d["passed"] = bool(r["passed"])
        return d
    except (sqlite3.OperationalError, ValueError):
        return None


def briefing_sent(tenant_id: str, date: str, col: str = "sent") -> bool:
    col = col if col in ("sent", "evening_sent") else "sent"
    try:
        with _conn() as c:
            r = c.execute(f"SELECT {col} FROM daily_briefings WHERE tenant_id=? AND date=?",
                          (tenant_id, date)).fetchone()
        return bool(r and r[0])
    except sqlite3.OperationalError:
        return False


def mark_briefing_sent(tenant_id: str, date: str, col: str = "sent") -> None:
    col = col if col in ("sent", "evening_sent") else "sent"
    try:
        with _conn() as c:
            c.execute(f"UPDATE daily_briefings SET {col}=1 WHERE tenant_id=? AND date=?", (tenant_id, date))
    except sqlite3.OperationalError:
        pass


def pass_briefing(tenant_id: str, date: str) -> None:
    """'오늘은 패스' — 부담 없이 넘기기(브리핑 PHASE 3)."""
    try:
        with _conn() as c:
            c.execute("UPDATE daily_briefings SET passed=1 WHERE tenant_id=? AND date=?", (tenant_id, date))
    except sqlite3.OperationalError:
        pass


def set_briefing_pref(tid: str, hour: int, on: bool) -> None:
    try:
        with _conn() as c:
            c.execute("UPDATE tenants SET briefing_hour=?, briefing_on=? WHERE id=?",
                      (max(5, min(12, int(hour))), 1 if on else 0, tid))
    except sqlite3.OperationalError:
        pass


# ── 앱내 알림(상위노출 PHASE 2) ──
def add_notice(tenant_id: str, kind: str, text: str) -> None:
    """같은 종류의 미읽음 알림이 있으면 중복 생성하지 않음(리마인더 도배 방지)."""
    try:
        with _conn() as c:
            ex = c.execute("SELECT id FROM notices WHERE tenant_id=? AND kind=? AND read=0",
                           (tenant_id, kind)).fetchone()
            if ex:
                c.execute("UPDATE notices SET text=?, created_at=? WHERE id=?", (text, _now(), ex["id"]))
            else:
                c.execute("INSERT INTO notices(tenant_id, kind, text, created_at) VALUES(?,?,?,?)",
                          (tenant_id, kind, text, _now()))
    except sqlite3.OperationalError:
        pass


def unread_notices(tenant_id: str, limit: int = 5) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM notices WHERE tenant_id=? AND read=0 "
                             "ORDER BY created_at DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def mark_notices_read(tenant_id: str) -> None:
    try:
        with _conn() as c:
            c.execute("UPDATE notices SET read=1 WHERE tenant_id=?", (tenant_id,))
    except sqlite3.OperationalError:
        pass


def publish_activity(tenant_id: str) -> dict:
    """발행 일관성 원자료 — 마지막 발행/생성일·이번 주 발행 수·최근 4주 주별 수.
    '발행'은 blog_publishes(확인된 실제 발행)+publications, 폴백으로 콘텐츠 생성일 사용."""
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    dates: list[str] = []
    with _conn() as c:
        try:
            dates += [r["published_at"] for r in c.execute(
                "SELECT published_at FROM blog_publishes WHERE tenant_id=?", (tenant_id,)).fetchall()]
        except sqlite3.OperationalError:
            pass
        dates += [r["published_at"] for r in c.execute(
            "SELECT p.published_at FROM publications p JOIN content_pieces cp ON p.content_id=cp.id "
            "WHERE cp.tenant_id=?", (tenant_id,)).fetchall()]
        created = [r["created_at"] for r in c.execute(
            "SELECT created_at FROM content_pieces WHERE tenant_id=? AND kind='blog'", (tenant_id,)).fetchall()]
    pub_dates = sorted([d for d in dates if d], reverse=True)
    basis = "published"
    if not pub_dates:                       # 발행 기록이 없으면 생성 활동으로 폴백(라벨로 구분)
        pub_dates = sorted([d for d in created if d], reverse=True)
        basis = "created"
    counts = [0, 0, 0, 0]
    for d in pub_dates:
        try:
            dt = datetime.fromisoformat(d[:19])
        except Exception:
            continue
        for i in range(4):
            lo = week_start - timedelta(weeks=i)
            if lo <= dt < lo + timedelta(weeks=1):
                counts[i] += 1
                break
    last = pub_dates[0] if pub_dates else ""
    gap = None
    if last:
        try:
            gap = (now - datetime.fromisoformat(last[:19])).days
        except Exception:
            gap = None
    streak = 0
    for i in range(4):
        if counts[i] > 0:
            streak += 1
        elif i > 0:
            break
    return {"basis": basis, "last_at": last, "gap_days": gap,
            "this_week": counts[0], "week_counts": list(reversed(counts)), "streak_weeks": streak}


def set_tenant_blog(tid: str, url: str, blog_id: str) -> None:
    """네이버 블로그 연결 저장(빈 값이면 연결 해제). 블로그등록 PHASE 1."""
    with _conn() as c:
        try:
            c.execute("UPDATE tenants SET naver_blog_url=?, blog_id=? WHERE id=?",
                      ((url or "").strip(), (blog_id or "").strip(), tid))
        except sqlite3.OperationalError:
            pass


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


def set_agency_note(uid: str, note: str) -> None:
    """대행 고객 담당 메모 저장(성장 PHASE 4)."""
    with _conn() as c:
        try:
            c.execute("UPDATE users SET agency_note=? WHERE id=?", (note, uid))
        except sqlite3.OperationalError:
            pass


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
        c.execute("UPDATE users SET free_used = MAX(0, COALESCE(free_used,0) + ?) WHERE id=?", (n, uid))


def _ym() -> str:
    return datetime.utcnow().strftime("%Y%m")


def record_perf_event(tenant_id: str, keyword: str, rank: int) -> None:
    """성과형 과금 스텁 — 1페이지(상위 N위) 진입 이벤트 기록. 실제 과금 로직은 추후(성장 PHASE 3)."""
    try:
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS performance_events("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, keyword TEXT, "
                      "rank INTEGER, at TEXT, billed INTEGER DEFAULT 0)")
            # 같은 키워드 중복 방지(미청구분만)
            ex = c.execute("SELECT id FROM performance_events WHERE tenant_id=? AND keyword=? AND billed=0",
                           (tenant_id, keyword)).fetchone()
            if not ex:
                c.execute("INSERT INTO performance_events(tenant_id,keyword,rank,at) VALUES(?,?,?,?)",
                          (tenant_id, keyword, rank, _now()))
    except Exception:
        pass


def schedule_report(tenant_id: str, keyword: str, baseline_rank, due_at: str, channel: str = "email") -> None:
    """7일 순위 리포트 예약 — 발행 시 baseline 기록, due_at에 발송(성장 PHASE 2)."""
    try:
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS scheduled_reports("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, keyword TEXT, "
                      "baseline_rank INTEGER, due_at TEXT, channel TEXT, sent INTEGER DEFAULT 0, created_at TEXT)")
            c.execute("INSERT INTO scheduled_reports(tenant_id,keyword,baseline_rank,due_at,channel,created_at) "
                      "VALUES(?,?,?,?,?,?)",
                      (tenant_id, keyword, baseline_rank, due_at, channel, _now()))
    except Exception:
        pass


def due_reports(now_iso: str) -> list[dict]:
    """발송 시점 도달 + 미발송 리포트."""
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM scheduled_reports WHERE sent=0 AND due_at<=? ORDER BY due_at",
                             (now_iso,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def mark_report_sent(report_id: int) -> None:
    with _conn() as c:
        try:
            c.execute("UPDATE scheduled_reports SET sent=1 WHERE id=?", (report_id,))
        except Exception:
            pass


def claim_once(key: str) -> bool:
    """멱등 키를 최초 1회만 True 반환. 이미 처리된 키면 False(결제 이중청구 방지, B10)."""
    if not key:
        return True
    try:
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY, at TEXT)")
            c.execute("INSERT INTO idempotency(key, at) VALUES(?,?)",
                      (key, datetime.utcnow().isoformat()))
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception:
        return True   # 스토리지 오류 시 결제 흐름을 막지 않음(관대 처리)


def month_usage(uid: str) -> int:
    """이번 달 사용량(월이 바뀌면 0)."""
    with _conn() as c:
        r = c.execute("SELECT usage_month, month_used FROM users WHERE id=?", (uid,)).fetchone()
    if not r or (r["usage_month"] or "") != _ym():
        return 0
    return r["month_used"] or 0


def incr_month_usage(uid: str, n: int = 1) -> None:
    """이번 달 사용량 +n (월 바뀌면 리셋 후 카운트). 단일 UPDATE로 원자적 처리(B6)."""
    ym = _ym()
    with _conn() as c:
        c.execute(
            "UPDATE users SET month_used = MAX(0, CASE WHEN usage_month = ? "
            "THEN COALESCE(month_used,0) + ? ELSE ? END), usage_month = ? WHERE id=?",
            (ym, n, n, ym, uid))


# ── 스마트 입력 인사이트(콘텐츠생성 보강 — viral_hooks 점진 보강용 스텁) ──
def save_intake_insight(industry: str, answers: dict, experience: str = "") -> None:
    """업종별 스마트질문 답변 축적. TODO(viral_hooks): N건 쌓이면 업종 viral_hooks 생성 재료로."""
    try:
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS intake_insights("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, industry TEXT, "
                      "answers TEXT, experience TEXT, created_at TEXT)")
            c.execute("INSERT INTO intake_insights(industry, answers, experience, created_at) VALUES(?,?,?,?)",
                      ((industry or "").strip()[:60], json.dumps(answers or {}, ensure_ascii=False),
                       experience, _now()))
    except Exception:
        pass


# ── 블로그 발행 기록(블로그등록 PHASE 2) ──
def record_blog_publish(tenant_id: str, piece_id: str, url: str, published_at: str = "",
                        matched_by: str = "manual", score: float = 1.0, post_title: str = "") -> None:
    """발행 확인 기록 upsert(1글=1기록). matched_by: rss(자동매칭) | manual(사용자 확인)."""
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO blog_publishes"
                      "(piece_id, tenant_id, published_url, published_at, matched_by, match_score, post_title, created_at) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (piece_id, tenant_id, (url or "").strip(), published_at or _now(),
                       matched_by, score, post_title, _now()))
    except sqlite3.OperationalError:
        pass


def get_blog_publish(piece_id: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM blog_publishes WHERE piece_id=?", (piece_id,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None


def list_blog_publishes(tenant_id: str, limit: int = 30) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM blog_publishes WHERE tenant_id=? "
                             "ORDER BY published_at DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ── 주간 리포트(블로그등록 PHASE 4) ──
def save_weekly_report(tenant_id: str, week: str, data: dict, sent_email: bool = False) -> None:
    """주간 리포트 저장(같은 주 재실행은 갱신)."""
    try:
        with _conn() as c:
            ex = c.execute("SELECT id FROM weekly_reports WHERE tenant_id=? AND week=?",
                           (tenant_id, week)).fetchone()
            if ex:
                c.execute("UPDATE weekly_reports SET data=?, sent_email=?, created_at=? WHERE id=?",
                          (json.dumps(data, ensure_ascii=False), int(sent_email), _now(), ex["id"]))
            else:
                c.execute("INSERT INTO weekly_reports(tenant_id, week, data, sent_email, created_at) "
                          "VALUES(?,?,?,?,?)",
                          (tenant_id, week, json.dumps(data, ensure_ascii=False), int(sent_email), _now()))
    except sqlite3.OperationalError:
        pass


def latest_weekly_report(tenant_id: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM weekly_reports WHERE tenant_id=? ORDER BY week DESC LIMIT 1",
                          (tenant_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["data"] = json.loads(d.get("data") or "{}")
        return d
    except (sqlite3.OperationalError, ValueError):
        return None


def list_tenants_with_blog() -> list:
    """블로그 연결된 가게 전체(주간 리포트·발행확인 잡 대상)."""
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM tenants WHERE blog_id IS NOT NULL AND blog_id!=''").fetchall()
        return [_row_to_tenant(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def rank_history(tenant_id: str, keyword: str, kind: str = "", limit: int = 30) -> list[dict]:
    """키워드 순위 이력(오래된→최신) — 성장 그래프·주간 변화 계산용."""
    try:
        with _conn() as c:
            if kind:
                rows = c.execute("SELECT rank, checked_at, COALESCE(kind,'blog') AS kind FROM rank_snapshots "
                                 "WHERE tenant_id=? AND keyword=? AND COALESCE(kind,'blog')=? "
                                 "ORDER BY checked_at ASC LIMIT ?", (tenant_id, keyword, kind, limit)).fetchall()
            else:
                rows = c.execute("SELECT rank, checked_at, COALESCE(kind,'blog') AS kind FROM rank_snapshots "
                                 "WHERE tenant_id=? AND keyword=? ORDER BY checked_at ASC LIMIT ?",
                                 (tenant_id, keyword, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def tracked_keywords(tenant_id: str, limit: int = 10) -> list[str]:
    """스냅샷이 있는 추적 키워드(최근 기록 순)."""
    try:
        with _conn() as c:
            rows = c.execute("SELECT keyword, MAX(checked_at) m FROM rank_snapshots WHERE tenant_id=? "
                             "GROUP BY keyword ORDER BY m DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [r["keyword"] for r in rows]
    except sqlite3.OperationalError:
        return []


# ── 신규기능 월간 사용량(경쟁사 스캔 / 인쇄물) — month_used 패턴, 원자적 ──
_FEAT_COL = {"competitor_scans": "competitor_scans_used", "print_items": "print_items_used",
             "angle_variants": "angle_variants_used"}


def feature_usage(uid: str, feature: str) -> int:
    """이번 달 기능 사용량(월 바뀌면 0). feature: competitor_scans | print_items."""
    col = _FEAT_COL.get(feature)
    if not col:
        return 0
    with _conn() as c:
        try:
            r = c.execute(f"SELECT feat_usage_month, {col} FROM users WHERE id=?", (uid,)).fetchone()
        except sqlite3.OperationalError:
            return 0
    if not r or (r["feat_usage_month"] or "") != _ym():
        return 0
    return r[col] or 0


def incr_feature_usage(uid: str, feature: str, n: int = 1) -> None:
    """기능 사용량 +n (월 바뀌면 두 카운터 리셋 후 카운트). 단일 UPDATE 원자적."""
    col = _FEAT_COL.get(feature)
    if not (uid and col):
        return
    ym = _ym()
    others = [v for v in _FEAT_COL.values() if v != col]
    with _conn() as c:
        try:
            # 월이 바뀌면 대상 컬럼=n, 나머지 컬럼 전부 0 리셋. 같은 달이면 대상 +n.
            other_sql = ", ".join(
                f"{o} = CASE WHEN feat_usage_month = ? THEN COALESCE({o},0) ELSE 0 END" for o in others)
            args = [ym, n, n] + [ym] * len(others) + [ym, uid]
            c.execute(
                f"UPDATE users SET {col} = MAX(0, CASE WHEN feat_usage_month = ? "
                f"THEN COALESCE({col},0) + ? ELSE ? END), " + other_sql + ", "
                "feat_usage_month = ? WHERE id=?", args)
        except sqlite3.OperationalError:
            pass


# ── 경쟁사 추적 CRUD ──
def create_competitor(tenant_id: str, name: str, region: str = "", keywords: list | None = None) -> str:
    cid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute("INSERT INTO competitors(id,tenant_id,name,region,keywords,created_at,active) "
                  "VALUES(?,?,?,?,?,?,1)",
                  (cid, tenant_id, name, region, json.dumps(keywords or [], ensure_ascii=False), _now()))
    return cid


def list_competitors(tenant_id: str, active_only: bool = True) -> list[dict]:
    try:
        with _conn() as c:
            q = "SELECT * FROM competitors WHERE tenant_id=?" + (" AND active=1" if active_only else "")
            rows = c.execute(q + " ORDER BY created_at DESC", (tenant_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["keywords"] = json.loads(d.get("keywords") or "[]")
            except Exception:
                d["keywords"] = []
            out.append(d)
        return out
    except sqlite3.OperationalError:
        return []


def get_competitor(cid: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM competitors WHERE id=?", (cid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["keywords"] = json.loads(d.get("keywords") or "[]")
        except Exception:
            d["keywords"] = []
        return d
    except sqlite3.OperationalError:
        return None


def count_competitors(tenant_id: str) -> int:
    with _conn() as c:
        try:
            r = c.execute("SELECT COUNT(*) n FROM competitors WHERE tenant_id=? AND active=1", (tenant_id,)).fetchone()
            return r["n"] if r else 0
        except sqlite3.OperationalError:
            return 0


def delete_competitor(cid: str, tenant_id: str) -> None:
    with _conn() as c:
        c.execute("UPDATE competitors SET active=0 WHERE id=? AND tenant_id=?", (cid, tenant_id))


def list_active_competitors() -> list[dict]:
    """스케줄러용 — 전체 active 경쟁사."""
    return list_competitors_all_active()


def list_competitors_all_active() -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM competitors WHERE active=1").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["keywords"] = json.loads(d.get("keywords") or "[]")
            except Exception:
                d["keywords"] = []
            out.append(d)
        return out
    except sqlite3.OperationalError:
        return []


def save_print_job(tenant_id: str, ptype: str, path: str, url: str = "", label: str = "") -> str:
    """인쇄물 생성 기록(신규기능②). 테이블 자동 생성."""
    jid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS print_jobs("
                  "id TEXT PRIMARY KEY, tenant_id TEXT, ptype TEXT, label TEXT, "
                  "path TEXT, url TEXT, created_at TEXT)")
        c.execute("INSERT INTO print_jobs(id,tenant_id,ptype,label,path,url,created_at) VALUES(?,?,?,?,?,?,?)",
                  (jid, tenant_id, ptype, label, path, url, _now()))
    return jid


def list_print_jobs(tenant_id: str, limit: int = 50) -> list[dict]:
    try:
        with _conn() as c:
            rows = c.execute("SELECT * FROM print_jobs WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                             (tenant_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def get_print_job(jid: str) -> Optional[dict]:
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM print_jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None
    except sqlite3.OperationalError:
        return None


def save_competitor_snapshot(competitor_id: str, keyword: str, my_rank, competitor_rank) -> None:
    with _conn() as c:
        c.execute("INSERT INTO competitor_snapshots(competitor_id,keyword,my_rank,competitor_rank,checked_at) "
                  "VALUES(?,?,?,?,?)", (competitor_id, keyword, my_rank, competitor_rank, _now()))


def competitor_snapshots(competitor_id: str, keyword: str = "", limit: int = 30) -> list[dict]:
    try:
        with _conn() as c:
            if keyword:
                rows = c.execute("SELECT * FROM competitor_snapshots WHERE competitor_id=? AND keyword=? "
                                 "ORDER BY checked_at DESC LIMIT ?", (competitor_id, keyword, limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM competitor_snapshots WHERE competitor_id=? "
                                 "ORDER BY checked_at DESC LIMIT ?", (competitor_id, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


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


def decr_demo_ip(ip: str) -> None:
    """데모 카운터 환불 — 선예약(연타 한도우회 방지) 후 생성 실패 시 원복."""
    with _conn() as c:
        c.execute("UPDATE demo_usage SET count=MAX(0, count-1) WHERE ip=?", (ip,))


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


def incr_link_click(code: str, referrer: str = "", ua: str = "", utm_source: str = "",
                    content_id: str = "", channel: str = "") -> None:
    """클릭 집계(누적 카운터) + 행 단위 로깅(시각·리퍼러·UA·채널·콘텐츠). 콘텐츠별 실측(추적 P1)."""
    with _conn() as c:
        c.execute("UPDATE links SET clicks=clicks+1 WHERE code=?", (code,))
        try:
            c.execute("CREATE TABLE IF NOT EXISTS link_clicks("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, ts TEXT, "
                      "referrer TEXT, ua TEXT, utm_source TEXT)")
            for col in ("content_id", "channel"):
                try:
                    c.execute(f"ALTER TABLE link_clicks ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            c.execute("INSERT INTO link_clicks(code, ts, referrer, ua, utm_source, content_id, channel) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (code, _now(), referrer[:300], ua[:300], utm_source[:60],
                       (content_id or "")[:16], (channel or utm_source or "")[:40]))
        except Exception:
            pass


# ── 콘텐츠별 클릭 실측(추적 P2·P3) — 전부 link_clicks 행 기반. '조회수'가 아니라
#    '추적링크 경유 클릭'이다 — UI 표기도 이 이상 주장하지 않는다(정직). ──
def content_click_counts(tenant_id: str, days: int = 90) -> dict:
    """content_id(피스 id 앞 16자) → 클릭 수. '내 콘텐츠' 뱃지용."""
    try:
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with _conn() as c:
            rows = c.execute(
                "SELECT lc.content_id, COUNT(*) n FROM link_clicks lc JOIN links l ON lc.code=l.code "
                "WHERE l.tenant_id=? AND lc.content_id != '' AND lc.ts >= ? GROUP BY lc.content_id",
                (tenant_id, since)).fetchall()
        return {r["content_id"]: r["n"] for r in rows}
    except Exception:
        return {}


def content_click_ranking(tenant_id: str, days: int = 30, limit: int = 3) -> list[dict]:
    """가장 클릭 많이 데려온 콘텐츠 TOP N — [{content_id, channel, n}]."""
    try:
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with _conn() as c:
            rows = c.execute(
                "SELECT lc.content_id, COALESCE(lc.channel,'') channel, COUNT(*) n "
                "FROM link_clicks lc JOIN links l ON lc.code=l.code "
                "WHERE l.tenant_id=? AND lc.content_id != '' AND lc.ts >= ? "
                "GROUP BY lc.content_id ORDER BY n DESC LIMIT ?",
                (tenant_id, since, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def channel_click_split(tenant_id: str, days: int = 30) -> dict:
    """채널별 유입 비교 — {channel: n}. 빈 채널은 'direct'(추적 파라미터 없는 클릭)."""
    try:
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with _conn() as c:
            rows = c.execute(
                "SELECT COALESCE(NULLIF(lc.channel,''),'direct') ch, COUNT(*) n "
                "FROM link_clicks lc JOIN links l ON lc.code=l.code "
                "WHERE l.tenant_id=? AND lc.ts >= ? GROUP BY ch ORDER BY n DESC",
                (tenant_id, since)).fetchall()
        return {r["ch"]: r["n"] for r in rows}
    except Exception:
        return {}


def daily_click_series(tenant_id: str, days: int = 7) -> list[dict]:
    """최근 N일 일별 클릭 추이 — [{date:'MM-DD', n}] (빈 날 0 포함, 과거→오늘)."""
    out = []
    try:
        from datetime import timedelta
        with _conn() as c:
            for i in range(days - 1, -1, -1):
                d = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
                n = c.execute(
                    "SELECT COUNT(*) FROM link_clicks lc JOIN links l ON lc.code=l.code "
                    "WHERE l.tenant_id=? AND lc.ts LIKE ?", (tenant_id, d + "%")).fetchone()[0]
                out.append({"date": d[5:], "n": n})
    except Exception:
        pass
    return out


def find_piece_brief(tenant_id: str, id_prefix: str) -> Optional[dict]:
    """content_id(피스 id 앞부분) → {title, channel, kind, keywords, angle}. 성과 랭킹 표시용(추적 P2)."""
    if not (id_prefix or "").strip():
        return None
    try:
        with _conn() as c:
            r = c.execute("SELECT payload, channel, kind FROM content_pieces WHERE tenant_id=? AND id LIKE ?",
                          (tenant_id, id_prefix + "%")).fetchone()
        if not r:
            return None
        pl = json.loads(r["payload"] or "{}")
        title = (pl.get("title") or (pl.get("text") or "")[:40] or
                 (pl.get("product_names") or [""])[0] or "").strip()
        return {"title": title, "channel": r["channel"], "kind": r["kind"],
                "keywords": pl.get("target_keywords") or [], "angle": pl.get("angle") or ""}
    except Exception:
        return None


def clicks_on_date(tenant_id: str, date: str) -> int:
    """특정 날짜(YYYY-MM-DD)의 클릭 수 — 아침 브리핑 '어제 N명' 동기부여용(추적 P3)."""
    try:
        with _conn() as c:
            return c.execute(
                "SELECT COUNT(*) FROM link_clicks lc JOIN links l ON lc.code=l.code "
                "WHERE l.tenant_id=? AND lc.ts LIKE ?", (tenant_id, date + "%")).fetchone()[0]
    except Exception:
        return 0


def link_click_stats(code: str) -> dict:
    """링크별 클릭 통계(총합 + 채널별 분해). 성과 대시보드용(PHASE 6)."""
    try:
        with _conn() as c:
            rows = c.execute("SELECT utm_source, COUNT(*) n FROM link_clicks WHERE code=? "
                             "GROUP BY utm_source", (code,)).fetchall()
        by = {(r["utm_source"] or "direct"): r["n"] for r in rows}
        return {"total": sum(by.values()), "by_source": by}
    except sqlite3.OperationalError:
        return {"total": 0, "by_source": {}}


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
def get_asset(aid: str) -> Optional[Asset]:
    with _conn() as c:
        r = c.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    if not r:
        return None
    return Asset(id=r["id"], tenant_id=r["tenant_id"], type=AssetType(r["type"]),
                 path=r["path"], note=r["note"] or "")


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
