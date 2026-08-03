"""
🕳 빈자리 선점 엔진 — 1단계: 판정만 한다(읽기 전용, 2026-08-02 사장님 승인).

무엇: "자리는 열려 있는데 우리가 아직 없는 검색어"를 찾고, 그 검색어가 **사장님이 실제로
하시는 일**인지 실데이터로 분류한다. 이 단계는 글감을 만들지도, 화면을 바꾸지도 않는다.
분류가 맞는지 사장님이 먼저 보시고 판단하실 재료를 만든다.

왜 판정만 먼저인가: 분류가 틀린 채로 글감 큐에 들어가면 쓸 수 없는 글감이 쌓인다.

두 관문을 모두 통과해야 '제안 자격'이 있다:
  ① 빈자리 — 검색 실측(첫 화면에 블로그 글이 실리는가 · 우리는 없는가 · 사람이 찾는가 ·
              경쟁이 약한가). 블록 '이름'은 쓰지 않는다 — 귀속이 미검증이다(실사고 2026-08-02).
  ② 사장님 영역 — 실데이터(실제로 다루시는 것인가)
실사진·실경험 없는 주제를 시키는 것은 날조 유도다. 그래서 ②가 없으면 제안하지 않는다.

업종 중립: 판정 재료는 그 가게의 실데이터와 업종 스키마 축뿐이다. 업종명·차종·메뉴 하드코딩 0.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta

from app import db

_log = logging.getLogger("shopcast.gapscout")

# 지면 지도의 유효기간 — 로컬 스캐너가 채우는 데이터라 노트북이 꺼지면 낡는다.
# 낡은 지도로 '빈자리'를 주장하면 허위 양성이다(측정 원칙: 허위 양성보다 미표시가 낫다).
MAP_TTL_DAYS = 14
MIN_VOLUME = 100          # 검색량 관문 — 기존 정찰 기준과 같은 잣대(queryscout.MIN_VOLUME)
STALE_TOP_DAYS = 365      # 상위 글이 이만큼 오래됐으면 '경쟁 약함' 신호(1년+)

# 점수 가중치 — 데이터 필드다(하드코딩 금지 원칙: 값을 코드 로직에 박지 않고 여기 모은다).
#   운영 중 조정 가능하도록 한 곳에 두고, 계산식은 이 표만 참조한다.
WEIGHTS = {
    "volume_log": 1.0,        # 수요(월검색량, 로그 스케일 — 큰 수가 다 먹지 않게)
    "surface": 2.0,           # 지면 존재(블로그 자리가 실제로 뜨는가) — 없으면 써도 안 보인다
    "weak_comp": 1.5,         # 경쟁 약도(문서 수 적음 · 상위 글 낡음)
}


def _ensure(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS kw_gaps("
              "tenant_id TEXT, keyword TEXT, has_surface INTEGER, mine INTEGER, "
              "volume INTEGER, doc_count INTEGER, top_age_days INTEGER, score REAL, "
              "domain TEXT, domain_why TEXT, status TEXT DEFAULT 'new', checked_at TEXT, "
              "PRIMARY KEY(tenant_id, keyword))")
    c.execute("CREATE INDEX IF NOT EXISTS idx_kwgaps_t ON kw_gaps(tenant_id, score DESC)")


# ── 재료 ①: 지면 지도(kw_blocks) ─────────────────────────────────
def _surface_rows(tenant_id: str) -> list[dict]:
    """지면 정찰 결과 중 '아직 유효한' 것만. 낡은 것은 판정에서 뺀다(빈자리라고 말하지 않는다)."""
    cut = (datetime.utcnow() - timedelta(days=MAP_TTL_DAYS)).isoformat()
    try:
        with db._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS kw_blocks("
                      "tenant_id TEXT, keyword TEXT, blocks TEXT, blog_blocks TEXT,"
                      "mine INTEGER, checked_at TEXT, PRIMARY KEY(tenant_id, keyword))")
            rows = c.execute("SELECT * FROM kw_blocks WHERE tenant_id=? AND checked_at>=?",
                             (tenant_id, cut)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        _log.exception("[gapscout] 지면 지도 조회 실패 t=%s", tenant_id)
        return []


# ── 재료 ②: 사장님 영역(실데이터) ────────────────────────────────
def _tok(s: str) -> set:
    return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", s or "")}


def owner_domain(tenant_id: str) -> dict:
    """사장님이 실제로 다루시는 것 — 실데이터에서만 모은다(추측 0).

    출처(중립 순서): ①발행 이력 ②과거 세트의 타깃 키워드·입력 ③실경험 Q&A
                     ④재고 맥락(보조 — 컬럼이 차량형이라 있으면 쓰고 없으면 넘어간다)
    반환: {"tokens": set, "sources": {토큰: 출처 문자열}}
    """
    toks: set = set()
    src: dict = {}

    def _add(words, where):
        for w in words:
            if len(w) >= 2 and w not in src:
                toks.add(w)
                src[w] = where

    try:
        for p in db.list_blog_publishes(tenant_id, limit=40):
            _add(_tok(p.get("post_title") or p.get("target_kw") or ""), "발행 이력")
    except Exception:
        pass
    try:
        for s in db.list_sets(tenant_id=tenant_id, limit=30):
            for pc in db.get_set_pieces(s.get("asset_id") or ""):
                pl = pc.payload or {}
                for kw in (pl.get("target_keywords") or [])[:6]:
                    _add(_tok(kw), "과거 세트")
                if pl.get("title"):
                    _add(_tok(pl["title"]), "과거 세트")
                break                                  # 세트당 조각 하나면 충분
    except Exception:
        pass
    try:
        for q in db.list_owner_experience(tenant_id, limit=20):
            _add(_tok(q.get("answer") or ""), "실경험 Q&A")
    except Exception:
        pass
    try:                                               # 보조 — 재고형 가게에만 있다
        for ctx in db.recent_inventory_context(tenant_id, limit=8):
            _add({v for v in (ctx.get("model"), ctx.get("car_class")) if v}, "재고 맥락")
    except Exception:
        pass
    for tok, where in confirmed_tokens(tenant_id).items():   # 사장님이 '해요'라고 답하신 것
        _add({tok}, where)
    return {"tokens": toks, "sources": src}


def _axis_tokens(t) -> list[dict]:
    """업종 스키마의 속성축 — '같은 축의 다른 값'을 인접으로 보기 위한 재료(업종 무관)."""
    try:
        from app.services import indschema as _isc
        sch = _isc.get_schema(getattr(t, "industry", "") or "",
                              getattr(t, "biz_type", "") or "local")
        return [{"axis": a.get("axis") or "", "tokens": [x for x in (a.get("tokens") or [])
                                                        if isinstance(x, str)]}
                for a in (sch.get("attribute_axes") or [])]
    except Exception:
        return []


def classify(keyword: str, dom: dict, axes: list[dict], excluded: set) -> tuple:
    """빈자리 키워드 하나를 사장님 영역과 대조 — (분류, 근거) 반환.

    확실: 키워드 안의 낱말이 실데이터에 그대로 있다
    인접: 실데이터에 있는 낱말과 '같은 속성축'에 있는 값이다(축 이름과 함께 근거로 남긴다)
    미지: 근거 0 — 제안도 언급도 하지 않는다(확인 질문 대상)
    제외: 사장님이 '안 해요'라고 답하신 것 — 다시 제안하지 않는다
    """
    kt = _tok(keyword)
    mine = dom.get("tokens") or set()
    if kt & excluded:
        hit = sorted(kt & excluded)[0]
        return "제외", f"사장님이 '{hit}'은(는) 안 하신다고 답하셨습니다"

    # ★ 속성축 값은 낱개로 검증한다(2026-08-02 박제 중 발견). 'GV80 신차패키지'는
    #   '신차패키지'가 실데이터에 있다는 이유로 '확실'이 됐다 — 정작 GV80은 근거가 0인데.
    #   그대로 두면 사장님이 다루지도 않는 차종으로 새 글을 시키게 된다(날조 유도).
    #   축에 속한 값이 하나라도 미검증이면 '확실'이 될 수 없다.
    for ax in axes:
        ax_toks = {x for x in ax["tokens"] if len(x) >= 2}
        unproven = (kt & ax_toks) - mine
        if not unproven:
            continue
        u = sorted(unproven, key=len, reverse=True)[0]
        sib = ax_toks & mine                          # 같은 축에 사장님의 실제 값이 있는가
        if sib:
            m = sorted(sib, key=len, reverse=True)[0]
            return "인접", f"'{u}'는 미검증 — 같은 축({ax['axis']})의 '{m}'만 실재"
        return "미지", f"'{u}'({ax['axis']})에 실데이터 근거 없음"

    hit = kt & mine
    if hit:
        w = sorted(hit, key=len, reverse=True)[0]
        return "확실", f"'{w}' — {dom['sources'].get(w, '실데이터')}에 실재"
    return "미지", "실데이터에 근거 없음"


# ── 점수 ─────────────────────────────────────────────────────────
def _score(volume: int, has_surface: bool, doc_count: int, top_age: int) -> float:
    """우선순위 = 수요 × 지면 존재 × 경쟁 약도. 가중치는 WEIGHTS(데이터 필드)만 참조한다.

    ★ 지면이 없으면 0이다 — 자리가 없는 판에서는 아무리 좋은 글도 노출로 이어지지 않는다(실측).
    ★ 문서 수 조회 실패(-1)는 '공급 0'이 아니다 — 모르면 경쟁 가점을 주지 않는다.
    """
    import math
    if not has_surface or volume < MIN_VOLUME:
        return 0.0
    s = WEIGHTS["volume_log"] * math.log10(max(10, volume))
    s += WEIGHTS["surface"]
    weak = 0.0
    if isinstance(doc_count, int) and doc_count > 0:
        weak += max(0.0, 1.0 - math.log10(max(10, doc_count)) / 6.0)   # 문서 적을수록 가점
    if isinstance(top_age, int) and top_age >= STALE_TOP_DAYS:
        weak += 0.5                                                    # 상위 글이 낡음
    return round(s + WEIGHTS["weak_comp"] * weak, 3)


# ── 본체 ─────────────────────────────────────────────────────────
def scan(tenant_id: str, limit: int = 40, with_competition: bool = True) -> dict:
    """빈자리 판정 + 영역 분류 → kw_gaps 저장. 읽기 전용(글감·화면 변화 0).

    조용한 실패 금지 — 재료가 없으면 왜 없는지 사유를 함께 돌려준다.
    """
    t = db.get_tenant(tenant_id)
    if not t:
        return {"ok": False, "error": "tenant 없음"}
    rows = _surface_rows(tenant_id)
    if not rows:
        return {"ok": True, "gaps": [], "skipped": 0,
                "note": f"유효한 지면 지도가 없습니다(최근 {MAP_TTL_DAYS}일). "
                        "야간 정찰이 돌아야 판정할 수 있습니다"}
    # 빈자리 후보 = 그 검색어 첫 화면에 '블로그 글이 실리는데' 우리 글은 없는 키워드.
    #   ★ blog_blocks 필드는 '블로그 링크가 2개 이상 실린 블록의 이름' 목록이다.
    #     블록 이름 자체는 귀속이 미검증이라 근거로 쓰지 않는다(2026-08-02 오전 실사고 —
    #     '숏텐츠·클립에 노출 중'이라고 표시했는데 실제로는 타사만 있었다).
    #     우리가 쓰는 신호는 이름이 아니라 '비어 있지 않다' 하나뿐이다 = 블로그 글이 실린다.
    cands = [r for r in rows if (r.get("blog_blocks") or "").strip() and not r.get("mine")]
    skipped_mine = sum(1 for r in rows if r.get("mine"))
    if not cands:
        return {"ok": True, "gaps": [], "skipped": skipped_mine,
                "note": "지면이 열린 키워드에는 이미 우리 글이 있거나, 열린 지면이 없습니다"}

    kws = [r["keyword"] for r in cands][:limit]
    vols: dict = {}
    try:
        from app.services import searchad as _sa
        if _sa.configured():
            vols = _sa.volume_map(kws) or {}
    except Exception:
        _log.exception("[gapscout] 검색량 조회 실패")

    dom, axes = owner_domain(tenant_id), _axis_tokens(t)
    excluded = excluded_tokens(tenant_id)
    out = []
    for r in cands[:limit]:
        kw = r["keyword"]
        vol = int(vols.get(kw.replace(" ", ""), 0) or 0)
        dc, age = -1, -1
        if with_competition and vol >= MIN_VOLUME:      # 관문 통과분만 경쟁 조회(호출 절약)
            try:
                from app.services import blogrank as _br
                dc = int(_br.doc_count(kw) or -1)
                age = int(_br.top_staleness_days(kw) or -1)
            except Exception:
                _log.warning("[gapscout] 경쟁 조회 실패 kw=%s", kw)
        domain, why = classify(kw, dom, axes, excluded)
        out.append({"keyword": kw, "has_surface": True, "mine": 0, "volume": vol,
                    "doc_count": dc, "top_age_days": age,
                    "score": _score(vol, True, dc, age),
                    "domain": domain, "domain_why": why,
                    # 블록 이름은 내보내지 않는다 — 미검증 귀속을 근거처럼 보이게 하면 안 된다.
                    "surface_note": "첫 화면에 블로그 글이 실림",
                    "checked_at": r.get("checked_at") or ""})
    out.sort(key=lambda x: -x["score"])
    _save(tenant_id, out)
    return {"ok": True, "gaps": out, "skipped": skipped_mine,
            "map_rows": len(rows), "domain_tokens": len(dom["tokens"]),
            "axes": [a["axis"] for a in axes]}


def _save(tenant_id: str, gaps: list) -> None:
    try:
        with db._conn() as c:
            _ensure(c)
            now = datetime.utcnow().isoformat()
            for g in gaps:
                c.execute("INSERT OR REPLACE INTO kw_gaps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (tenant_id, g["keyword"], 1, 0, g["volume"], g["doc_count"],
                           g["top_age_days"], g["score"], g["domain"], g["domain_why"][:200],
                           "new", now))
    except Exception:
        _log.exception("[gapscout] 저장 실패 t=%s", tenant_id)


def list_gaps(tenant_id: str, domain: str = "", limit: int = 30) -> list[dict]:
    """저장된 판정 결과 조회(점수순). 화면·진단 공용."""
    try:
        with db._conn() as c:
            _ensure(c)
            q = "SELECT * FROM kw_gaps WHERE tenant_id=?"
            a = [tenant_id]
            if domain:
                q += " AND domain=?"
                a.append(domain)
            rows = c.execute(q + " ORDER BY score DESC LIMIT ?", a + [limit]).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── 영역 프로필(2·3단계에서 채워진다 — 여기서는 읽기만) ──────────
def _ensure_domain(c) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS tenant_domain("
              "tenant_id TEXT, token TEXT, axis TEXT, verdict TEXT, source TEXT, "
              "created_at TEXT, PRIMARY KEY(tenant_id, token))")


# 글감 큐 적재(2단계). source_type은 정렬상 기존 전 소스(P1~P4·R1·inflow) 뒤에 온다 —
#   빈자리는 '추정'이고 실유입·경쟁격차는 '실측'이다. 실측이 먼저 쓰인다.
QUEUE_SOURCE = "vacant"
WEEKLY_CAP = 2            # 주 N건 상한 — 빈자리가 큐를 삼키면 사장님의 실제 소재가 밀린다


def _materials(tenant_id: str) -> dict:
    """이 글을 쓰려면 무엇이 더 필요한가 — 사장님께 그대로 보여줄 말로 만든다."""
    photos, exp = 0, 0
    try:
        from app.services.autoqueue import photo_pool
        photos = len(photo_pool(db.get_tenant(tenant_id)) or [])
    except Exception:
        pass
    try:
        exp = len(db.list_owner_experience(tenant_id, limit=5) or [])
    except Exception:
        pass
    need = []
    if photos < 3:
        need.append("사진")
    if not exp:
        need.append("경험 한 줄")
    return {"photos": photos, "experience": exp,
            "need": need, "ready": not need}


def feed(tenant_id: str, limit: int = 3, dry: bool = True) -> dict:
    """확실 영역의 빈자리를 글감 큐에 편입(2단계).

    ★ '확실'만 넣는다. 인접은 편승(3단계), 미지는 확인 질문 — 여기서는 다루지 않는다.
    ★ 주 N건 상한 — 빈자리 글감이 큐를 삼키면 사장님의 실제 소재(매물·시공)가 밀린다.
    ★ dry=True가 기본이다. 무엇이 들어갈지 먼저 보여주고, 승인된 실행만 실제로 넣는다.
    """
    rows = [g for g in list_gaps(tenant_id, domain="확실", limit=50) if (g.get("score") or 0) > 0]
    if not rows:
        return {"ok": True, "added": 0, "items": [],
                "note": "점수가 0보다 큰 '확실' 빈자리가 없습니다(검색량 관문·지면 조건 확인)"}
    # 이미 큐에 있거나 이미 발행한 키워드는 뺀다(중복 글 방지 — 유사문서는 저품질이다)
    have = set()
    try:
        for q in db.writing_queue_rows(tenant_id, limit=60) or []:
            have.add(" ".join((q.get("target_keyword") or "").split()))
        for p in db.list_blog_publishes(tenant_id, limit=40):
            have.add(" ".join((p.get("target_kw") or "").split()))
    except Exception:
        pass
    used = 0
    try:                                              # 주간 상한 — 최근 7일 적재분
        from datetime import timedelta as _td
        since = (datetime.utcnow() - _td(days=7)).isoformat()
        with db._conn() as c:
            r = c.execute("SELECT COUNT(*) FROM writing_queue WHERE tenant_id=? AND source_type=? "
                          "AND created_at>=?", (tenant_id, QUEUE_SOURCE, since)).fetchone()
        used = int(r[0]) if r else 0
    except Exception:
        pass
    room = max(0, WEEKLY_CAP - used)
    if room <= 0:
        return {"ok": True, "added": 0, "items": [],
                "note": f"이번 주 빈자리 글감 상한({WEEKLY_CAP}건)을 이미 채웠습니다"}

    mats = _materials(tenant_id)
    # ★ 재료 게이트(2026-08-03 사장님 정정) — 경험이 없다고 글을 멈추지 않는다.
    #   검색으로 확인한 사실을 3인칭으로 쓰는 것은 취재이고 정상 재료다.
    #   보류는 '1인칭 서사가 형식상 필수인 글'에만 — 후기(review) 각도가 그렇다.
    #   가격·방법 글은 사실 서술로 먼저 나가고, 답변이 들어오면 경험 보강판으로 올린다.
    _needs_first_person = all(_angle(g["keyword"]) == "review" for g in rows[:limit])
    if not mats["ready"] and not dry and _needs_first_person:
        return {"ok": True, "added": 0, "items": [], "materials": mats,
                "blocked_by": mats["need"],
                "questions": questions(tenant_id, limit=limit),
                "note": f"후기형 글은 1인칭 경험이 형식상 필수라 보류했습니다({', '.join(mats['need'])}). "
                        "아래 질문에 한 줄만 답해 주시면 바로 진행합니다"}
    out, added = [], 0
    for g in rows:
        if added >= min(limit, room):
            break
        kw = g["keyword"]
        if kw in have:
            continue
        why = _why_line(g)
        item = {"keyword": kw, "why": why, "track": "A(매물·시공)",
                "angle": _angle(kw), "need": mats["need"], "score": g["score"]}
        if not dry:
            ok = db.enqueue_writing(tenant_id, QUEUE_SOURCE, kw, item["angle"],
                                    f"빈자리 선점 — {why}", content_type="sell")
            item["enqueued"] = bool(ok)
            added += 1 if ok else 0
        out.append(item)
    return {"ok": True, "dry": dry, "added": added, "items": out,
            "weekly_used": used, "weekly_cap": WEEKLY_CAP, "materials": mats,
            "questions": questions(tenant_id, limit=limit) if not mats["ready"] else []}


# 각도별 경험 질문 틀 — 업종·지명 하드코딩 0(키워드와 검색 의도만 들어간다).
#   왜 질문이 필요한가: 사진은 결과만 보여준다. 왜 그렇게 했는지·무엇이 갈리는지는
#   사장님만 안다. 그 한 줄이 없으면 사진 설명만 있는 글이 된다(영상 자막에서 겪은 것과 같다).
# 손님 문의 프레임만 쓴다(2026-08-03 사장님 지시) — 검색량·자리·키워드·상위글 같은
#   주방 용어는 사장님 화면에 한 글자도 나가지 않는다. 사장님은 결과만 보신다.
# '작업'처럼 특정 업종 말투도 쓰지 않는다 — 빵집·카페에도 그대로 통해야 한다.
_Q_BY_ANGLE = {
    "price": "손님들이 값 차이를 자주 물으시죠. 뭐에 따라 달라지나요? 한 줄이면 됩니다",
    "howto": "이거 하실 때 사장님이 꼭 확인하시는 것 하나만 알려주세요",
    "review": "이번에 하고 나서 손님이 뭐라고 하셨어요? 한 줄이면 글이 더 좋아져요",
}


def inline_question(tenant_id: str, topic: str) -> dict:
    """🗣 생성 시점 인라인 질문(2026-08-03) — 이 세트의 주제에만 딱 하나.

    왜 상시 상자가 아니라 여기인가: 맥락 없는 질문은 답이 안 나온다.
    지금 막 사진을 올리신 그 소재에 대해 물어야 사장님이 답할 말이 있다.

    묻지 않는 경우(둘 다 '이미 안다'):
      ① 수확이 그 주제를 커버한다(과거 글·사진 반복에서 이미 캤다)
      ② 같은 주제로 이미 답하신 적이 있다
    반환 {ask: bool, question, topic, why_skip}
    """
    topic = " ".join((topic or "").split())
    if not topic:
        return {"ask": False, "why_skip": "주제 없음"}
    try:
        from app.services import harvest as _hv
        if _hv.covers(tenant_id, topic):
            return {"ask": False, "why_skip": "이미 아는 내용(과거 글·사진에서 수확)"}
    except Exception:
        pass
    try:
        from app.services.autoqueue import relevant_experience as _rel
        if _rel(tenant_id, topic):
            return {"ask": False, "why_skip": "같은 주제로 이미 답하심"}
    except Exception:
        pass
    return {"ask": True, "topic": topic,
            "question": _Q_BY_ANGLE.get(_angle(topic), _Q_BY_ANGLE["review"])}


def questions(tenant_id: str, limit: int = 3) -> list[dict]:
    """빈자리를 글로 만들려면 사장님께 무엇을 여쭤야 하는가 — 키워드별 한 줄 질문.

    ★ 질문은 '확실' 영역에만 만든다. 미지 영역을 물으면 그건 확인 질문(3단계)이고
      성격이 다르다 — 여기서는 '하시는 일'의 속을 여쭙는다.
    """
    out = []
    for g in list_gaps(tenant_id, domain="확실", limit=30):
        if (g.get("score") or 0) <= 0:
            continue
        kw = g["keyword"]
        tmpl = _Q_BY_ANGLE.get(_angle(kw), _Q_BY_ANGLE["review"])
        out.append({"keyword": kw, "question": tmpl.format(kw=kw),
                    "why": _why_line(g), "score": g["score"]})
        if len(out) >= limit:
            break
    return out


def _why_line(g: dict) -> str:
    """왜 기회인지 한 줄 — 사장님이 읽고 판단하실 말로. 숫자는 실측값만."""
    bits = [f"검색 {g.get('volume', 0):,}회"]
    age = g.get("top_age_days")
    if isinstance(age, int) and age > 0:
        bits.append(f"상위 글 {age}일 전")
    bits.append("첫 화면에 블로그 자리 있음")
    bits.append("우리 글 없음")
    return " · ".join(bits)


def _angle(keyword: str) -> str:
    """검색 의도에 맞는 글 각도 — 기존 판정기를 재사용한다(새 규칙 만들지 않음)."""
    try:
        from app.services import smartblock as _sb
        a = _sb.angle_for(keyword)
        return a if a in ("review", "howto", "price") else "review"
    except Exception:
        return "review"


def answer(tenant_id: str, token: str, verdict: str, axis: str = "", source: str = "사장님 확인") -> dict:
    """사장님 확인 응답을 영역 프로필에 기록(2026-08-02).

    'yes'  — 실제로 하시는 일 → 다음 판정부터 실데이터와 같은 자격으로 본다.
    'no'   — 안 하시는 일 → 영구 제외. 같은 걸 또 물으면 잔소리이자 신뢰 손실이다.
    되돌릴 수 있어야 한다 — 나중에 그 일을 시작하실 수 있다(같은 함수로 덮어쓴다).

    ★ 'yes'가 곧 글감이 되지는 않는다. 분류만 올라간다 — 실사진·실경험이 있어야 글을 쓴다
      (앵커·경험 게이트 그대로). 답변만으로 글을 쓰면 그게 날조다.
    """
    token = " ".join((token or "").split())
    if not token or verdict not in ("yes", "no"):
        return {"ok": False, "error": "token과 verdict(yes/no)가 필요합니다"}
    try:
        with db._conn() as c:
            _ensure_domain(c)
            c.execute("INSERT OR REPLACE INTO tenant_domain VALUES(?,?,?,?,?,?)",
                      (tenant_id, token, axis, verdict, source, datetime.utcnow().isoformat()))
    except Exception:
        _log.exception("[gapscout] 확인 응답 저장 실패 t=%s tok=%s", tenant_id, token)
        return {"ok": False, "error": "저장 실패"}
    return {"ok": True, "token": token, "verdict": verdict}


def confirmed_tokens(tenant_id: str) -> dict:
    """사장님이 '해요'라고 확인하신 것 — 실데이터와 같은 자격으로 영역에 합류한다."""
    try:
        with db._conn() as c:
            _ensure_domain(c)
            rows = c.execute("SELECT token FROM tenant_domain WHERE tenant_id=? AND verdict='yes'",
                             (tenant_id,)).fetchall()
        return {r["token"]: "사장님 확인" for r in rows}
    except Exception:
        return {}


def excluded_tokens(tenant_id: str) -> set:
    """사장님이 '안 해요'라고 답하신 것 — 다시 제안하지 않는다."""
    try:
        with db._conn() as c:
            _ensure_domain(c)
            rows = c.execute("SELECT token FROM tenant_domain WHERE tenant_id=? AND verdict='no'",
                             (tenant_id,)).fetchall()
        return {r["token"] for r in rows}
    except Exception:
        return set()
