"""
전 업종 상위노출 극대화 — 승률 키워드 대량 발굴(대량 P1) → 배치 생성(P3) → 스케줄 배분(P4)
→ 증거 집계(P6). 하드코딩 금지: 업종 프로필(industries) 기반 자동 적응, 프리셋 없는
업종은 ensure_profile 자동 생성으로 대응.

합법 소스만: searchad(실검색량·경쟁도) + 블로그검색 API(상위 글 나이) + 공개 RSS. 크롤링 없음.
정직성: '승률'은 예상치(산식 명시)이며 보장이 아님 — UI에 명시. 조회 실패는 실패로 표기.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from app import db

_log = logging.getLogger("shopcast.mass")

MAX_CANDIDATES = 30
BATCH_GEN_CAP = 5        # 1회 배치 생성 상한(LLM 비용 가드 — 여러 번 나눠 실행 가능)
SIM_THRESHOLD = 0.45     # 유사문서 판정(3-gram Jaccard) — 초과 시 재작성 1회


# ── P1. 후보 생성(업종 프로필 자동 적응 — 하드코딩 없음) ─────────────
def _region_variants(region: str) -> list[str]:
    toks = [w for w in (region or "").split() if w]
    out = []
    if toks:
        out.append(toks[0])                          # 시/도
        if len(toks) >= 2:
            out.append(" ".join(toks[:2]))           # 시+구
        if len(toks) >= 2 and toks[-1] != toks[0]:
            out.append(toks[-1])                     # 동/구 단독
    return list(dict.fromkeys(out)) or [""]


def candidates(t, prof) -> list[str]:
    """업종 프로필 기반 롱테일 후보 — 매장형=[지역×주제×의도], 셀러형=[상품×구매의도]."""
    from app import seo
    biz = (getattr(t, "biz_type", "local") or "local")
    inds = [w.strip() for w in (t.industry or "").replace(",", " ").split() if len(w.strip()) >= 2][:2] \
        or [prof.name]
    axis = (getattr(t, "topic_axis", "") or "").strip()
    heads = list(dict.fromkeys(inds + ([axis] if axis else [])))
    out: list[str] = []
    if biz == "seller":
        prods = list(dict.fromkeys(
            [x for x in (t.industry, getattr(t, "search_kw", ""), getattr(t, "brand_name", "")) if (x or "").strip()]))
        heads2 = []
        for p in prods[:2]:
            p = " ".join(p.split())
            heads2.append(p)
            toks = p.split()
            if len(toks) >= 2:                       # 종류어 축(예: '블루투스 이어폰' → '이어폰')
                heads2.append(toks[-1])
        extra_intents = ["장단점", "한달 사용", "구매 가이드"]
        for p in list(dict.fromkeys(heads2))[:3]:
            for it in list(seo._PRODUCT_INTENTS) + extra_intents:
                out.append(f"{p} {it}")
    else:
        for rv in _region_variants(t.region or "")[:3]:
            for h in heads[:2]:
                for it in seo._INTENTS:
                    out.append(" ".join(f"{rv} {h} {it}".split()))
        # 업종 프로필 앵글 토큰(예: 열차단·펌)도 롱테일 조합에 — 프로필 자동 적응
        angle_toks = []
        for a in (getattr(prof, "content_angles", None) or [])[:3]:
            w = str(a).split()[0].strip("·,")
            if 2 <= len(w) <= 8 and w not in heads:
                angle_toks.append(w)
        for rv in _region_variants(t.region or "")[:2]:
            for w in angle_toks[:2]:
                out.append(" ".join(f"{rv} {heads[0]} {w}".split()))
    seen, uniq = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq[:MAX_CANDIDATES * 2]


# ── P1. 스코어링 ────────────────────────────────────────────
def _win_probability(vol: int, comp: str, top_ages: list) -> tuple:
    """승률(0~100, '예상'임 — 보장 아님) 산식:
      base 50
      + 검색량: 100~1,000(롱테일 스윗스팟) +20 / 10~99 또는 1,001~3,000 +10 / 3,000 초과 -10
      + 경쟁도(searchad compIdx): 낮음 +15 / 높음 -20
      + 상위 글 나이: 상위 평균 180일+ (오래된 강자 = 신선도로 진입 여지) +10,
        상위 5개 중 최근 30일 글 3개+ (지금 경쟁 활발) -15
    난이도: 65+ '하' / 45~64 '중' / 45 미만 '상'."""
    w = 50
    if 100 <= vol <= 1000:
        w += 20
    elif 10 <= vol < 100 or 1000 < vol <= 3000:
        w += 10
    elif vol > 3000:
        w -= 10
    if comp == "낮음":
        w += 15
    elif comp == "높음":
        w -= 20
    ages = [a for a in top_ages if a is not None]
    if ages:
        if sum(ages) / len(ages) >= 180:
            w += 10
        if sum(1 for a in ages if a <= 30) >= 3:
            w -= 15
    w = max(5, min(95, w))
    diff = "하" if w >= 65 else ("중" if w >= 45 else "상")
    return w, diff


def mine(t, industry: str = "") -> dict:
    """P1 — 대량 발굴+랭킹. 반환 {ok, batch_id, items, note} / 실패 {ok:False, error}.
    비용: searchad 후보/5회 + 블로그검색 상위조회(볼륨 상위 20개만)."""
    from app.industries import resolve_industry, ensure_profile
    from app.services import blogrank, searchad
    industry = (industry or t.industry or "").strip()
    if not industry:
        return {"ok": False, "error": "업종을 먼저 설정해주세요."}
    prof = resolve_industry(industry)
    if getattr(prof, "key", "generic") == "generic":
        try:
            prof = ensure_profile(industry)          # 프리셋 없는 업종 자동 생성(꽃집·공방 등)
        except Exception:
            pass
    cands = candidates(t, prof)
    if not cands:
        return {"ok": False, "error": "키워드 후보를 만들 수 없어요 — 지역/업종을 확인해주세요."}
    # 실검색량(합법 searchad) — 5개씩 배치. 실패 시 임의 숫자 금지: 조회 실패 반환.
    vols: dict = {}
    got_any = False
    for i in range(0, len(cands), 5):
        try:
            for r in searchad.keyword_volumes(cands[i:i + 5]):
                vols[(r.get("keyword") or "").replace(" ", "")] = (r.get("total") or 0, r.get("comp") or "")
                got_any = True
        except Exception:
            pass
    if not got_any:
        return {"ok": False, "error": "검색량 조회 실패(searchad) — 잠시 후 다시 시도해주세요. 임의 추정치는 쓰지 않아요."}
    rows = []
    for kw in cands:
        v, comp = vols.get(kw.replace(" ", ""), (0, ""))
        if v < 10:                                   # 월 10 미만 제외(스펙)
            continue
        rows.append({"keyword": kw, "volume": v, "comp": comp})
    rows.sort(key=lambda r: -r["volume"])
    rows = rows[:MAX_CANDIDATES]
    # 상위 글 나이(경쟁 신선도) — 볼륨 상위 20개만 실조회(비용 가드), 나머지는 나이 미반영
    for r in rows[:20]:
        try:
            tops = blogrank.scout_top(r["keyword"], 5)
            r["top_ages"] = [x.get("age_days") for x in tops]
        except Exception:
            r["top_ages"] = []
    for r in rows:
        w, diff = _win_probability(r["volume"], r.get("comp") or "", r.get("top_ages") or [])
        r["win"] = w
        r["difficulty"] = diff
        r["note"] = ("3주 내 1페이지 가능성 높은 편" if w >= 65 else
                     ("해볼 만함 — 4주+ 잡고 꾸준히" if w >= 45 else "강자 많음 — 나중에(롱테일 먼저)"))
        r.pop("top_ages", None)
    rows.sort(key=lambda r: (-r["win"], -r["volume"]))
    for i, r in enumerate(rows):
        r["top10"] = i < 10
        r["status"] = "candidate"
    bid = uuid.uuid4().hex[:10]
    db.save_keyword_batch(bid, t.id, industry, rows)
    return {"ok": True, "batch_id": bid, "items": rows,
            "note": "승률은 실검색량·경쟁도·상위 글 나이 기반 '예상'이에요 — 보장이 아니고, 보통 2~4주 이상 걸려요."}


# ── P3. 배치 생성(유사문서 회피 + 업종별 품질 게이트) ─────────────
_VISUAL_KEYS = {"cafe", "florist", "nail", "photostudio", "interior", "clothing", "pension"}
_EXPERIENCE_KEYS = {"tinting", "hair", "skincare", "academy", "autorepair", "usedcar", "clinic", "dental", "gym", "pilates"}


def _trigrams(s: str) -> set:
    toks = [w for w in (s or "").split() if w]
    return {" ".join(toks[i:i + 3]) for i in range(max(0, len(toks) - 2))}


def similarity(a: str, b: str) -> float:
    """유사문서 판정 — 어절 3-gram Jaccard(네이버 저품질 필터 회피용 내부 기준)."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def industry_gate(prof, payload: dict, biz_type: str = "local") -> dict:
    """업종별 상위노출 통과 기준(P3) — quality_audit 위에 업종 가중 감점.
    시각 업종=이미지 배점↑, 경험 업종=경험서술 배점↑, 셀러=비교·스펙. 통과선 75점."""
    from app import seo
    au = seo.quality_audit("naver_blog", "blog", payload)
    score = au["score"]
    needs = []
    key = getattr(prof, "key", "generic")
    warns = au.get("warnings") or []
    for w in warns:
        if "이미지" in w and (key in _VISUAL_KEYS):
            score -= 6
            needs.append("사진을 3~4장 이상 넣으세요(시각 업종은 사진이 체류를 좌우)")
        if ("경험" in w or "D.I.A" in w) and (key in _EXPERIENCE_KEYS or biz_type == "seller"):
            score -= 6
            needs.append("실제 경험 문장 1개 더(이 업종은 후기·경험이 순위를 좌우)")
    body = payload.get("body") or ""
    kw0 = ((payload.get("target_keywords") or [""])[0] or "")
    if kw0 and body.count(kw0) > 6:
        score -= 10
        needs.append(f"'{kw0}' 반복 줄이기(도배는 저품질)")
    score = max(0, min(100, score))
    return {"score": score, "passed": score >= 75, "needs": needs[:3], "base": au["score"],
            "warnings": warns[:3]}


def generate_batch(t, batch_id: str, keywords: list[str], files: list, note: str = "") -> dict:
    """P3+P4 — 선택 키워드로 블로그 글 배치 생성(백그라운드 권장).
    각 글: 서로 다른 롱테일 + 앵글 로테이션 + 유사문서 회피(재작성 1회) + 업종 게이트(미달 자동 보완)
    → P4: 발행 캘린더에 하루 1~2개 자동 배분."""
    from app.domain.models import AssetType, ContentKind
    from app.industries import resolve_industry
    from app.services.generate import generate_for
    from app.services.revise import autofix_instruction, revise_piece
    from app import storage, vision
    batch = db.get_keyword_batch(batch_id)
    if not batch or batch["tenant_id"] != t.id:
        return {"ok": False, "error": "배치를 찾을 수 없어요."}
    items = batch["items"]
    sel = [it for it in items if it["keyword"] in set(keywords)][:BATCH_GEN_CAP]
    if not sel:
        return {"ok": False, "error": "선택된 키워드가 없어요."}
    prof = resolve_industry(batch.get("industry") or t.industry)
    paths = [storage.save_upload(data, name or "p.jpg", t.id) for data, name in (files or [])]
    analysis = vision.analyze_all(paths, t.industry) if paths else ""
    angles = ["review", "howto", "price"]
    prev_bodies: list[str] = []
    made = 0
    for i, it in enumerate(sel):
        kw, angle = it["keyword"], angles[i % 3]
        base_note = ((note or "") + (f"\n[사진 분석] {analysis}" if analysis else "")).strip()
        anti_dup = ("\n[배치 생성 — 유사문서 금지] 같은 가게의 다른 글들과 도입 방식·구성·소제목·사례를 "
                    "완전히 다르게 써라. 이 글은 오직 이 키워드 하나의 검색 의도에만 답한다.")
        asset = db.create_asset(t.id, AssetType.IMAGE, (paths[0] if paths else ""), base_note + anti_dup)
        asset.target_kw = kw
        asset.angle = angle
        try:
            pieces = generate_for(t, asset, [ContentKind.BLOG], images=(paths or None))
        except Exception:
            _log.exception("[mass] 생성 실패 kw=%s", kw)
            it["status"] = "failed"
            db.save_keyword_batch(batch_id, t.id, batch["industry"], items)
            continue
        if not pieces:
            it["status"] = "failed"
            db.save_keyword_batch(batch_id, t.id, batch["industry"], items)
            continue
        p = pieces[0]
        # 유사문서 회피 — 배치 내 앞 글들과 대조, 초과 시 1회 재작성
        body = p.payload.get("body") or ""
        if any(similarity(body, pb) > SIM_THRESHOLD for pb in prev_bodies):
            try:
                revise_piece(p, "앞서 쓴 글과 구성이 비슷하다. 도입·소제목·사례·문단 순서를 전혀 다르게 다시 써라. "
                                f"타겟 키워드 '{kw}'와 그 검색 의도는 유지.")
                body = p.payload.get("body") or body
            except Exception:
                pass
        # 업종별 품질 게이트 — 미달 시 autofix 1회 보완 후 재채점
        gate = industry_gate(prof, p.payload, getattr(t, "biz_type", "local") or "local")
        if not gate["passed"]:
            try:
                revise_piece(p, autofix_instruction(p.payload.get("ranking_audit") or {}, "blog")
                             or "경험 문장과 구체 수치를 보강")
                gate = industry_gate(prof, p.payload, getattr(t, "biz_type", "local") or "local")
            except Exception:
                pass
        try:
            from app.services import tracklinks
            tracklinks.inject(t, p)                  # P5: 업종 CTA·추적링크(매장=지도/셀러=구매) 자동
        except Exception:
            pass
        p.payload["mass_batch"] = batch_id
        p.payload["gate"] = gate
        db.save_piece(p)
        prev_bodies.append(body)
        it.update({"status": "ready" if gate["passed"] else "needs_fix",
                   "piece_id": p.id, "asset_id": asset.id, "angle": angle,
                   "gate_score": gate["score"], "needs": gate["needs"]})
        made += 1
        db.save_keyword_batch(batch_id, t.id, batch["industry"], items)
    # P4 — 발행 스케줄 배분: 하루 1~2개(한 번에 몰아 올리면 어뷰징 의심 → 분산)
    ready = [it for it in items if it.get("status") in ("ready", "needs_fix") and not it.get("scheduled_date")]
    per_day = 2 if len(ready) > 7 else 1
    day = datetime.utcnow().date() + timedelta(days=1)
    slot = 0
    for it in ready:
        it["scheduled_date"] = day.isoformat()
        slot += 1
        if slot >= per_day:
            slot = 0
            day += timedelta(days=1)
    db.save_keyword_batch(batch_id, t.id, batch["industry"], items)
    return {"ok": True, "made": made, "items": items}


def due_today(t) -> list[dict]:
    """P4 — 오늘 발행 예정(복붙 안내용). 배치 생성분 + 자동 글감 큐 생성분(payload.scheduled_date).
    [{keyword, piece_id, asset_id}]."""
    today = datetime.utcnow().date().isoformat()
    out = []
    for b in db.list_keyword_batches(t.id, limit=5):
        for it in b["items"]:
            if it.get("scheduled_date") == today and it.get("status") in ("ready", "needs_fix") \
                    and it.get("piece_id") and not db.get_blog_publish(it["piece_id"]):
                out.append({"keyword": it["keyword"], "piece_id": it["piece_id"],
                            "asset_id": it.get("asset_id", "")})
    for row in db.writing_queue_rows(t.id, status="done", limit=20):
        pid = row.get("piece_id") or ""
        if not pid or db.get_blog_publish(pid):
            continue
        piece = db.get_piece(pid)
        if not piece:
            continue
        sd = (piece.payload or {}).get("scheduled_date") or ""
        if sd and sd <= today:                        # 밀린 예정분도 오늘 카드로(방치 방지)
            out.append({"keyword": row.get("target_keyword") or "", "piece_id": pid,
                        "asset_id": piece.asset_id})
    return out[:2]


# ── P2. 전문 주제 축 자동 제안(C-Rank '한 주제 꾸준히') ─────────────
def suggest_axis(t, prof, items: list | None = None) -> str:
    """주제 축 제안 — 발굴 키워드 상위권의 최빈 토큰(지역·의도어 제외) + 업종.
    데이터 없으면 프로필 첫 앵글 토큰. 하드코딩 없음(프로필·실키워드 유래)."""
    from app import seo
    from collections import Counter
    stop = set(seo._INTENTS) | set(seo._PRODUCT_INTENTS) | set((t.region or "").split()) \
        | set((t.industry or "").replace(",", " ").split())
    toks = Counter()
    for it in (items or [])[:10]:
        for w in it["keyword"].split():
            if w not in stop and len(w) >= 2:
                toks[w] += 1
    ind0 = (t.industry or prof.name or "").split(",")[0].split()[0] if (t.industry or prof.name) else ""
    if toks:
        return f"{ind0} {toks.most_common(1)[0][0]}".strip()
    for a in (getattr(prof, "content_angles", None) or []):
        w = str(a).split()[0].strip("·,")
        if 2 <= len(w) <= 8:
            return f"{ind0} {w}".strip()
    return ind0


# ── P6. 증거 집계(실측만 — 생존신고 스냅샷 기준) ─────────────────
def evidence(t) -> dict:
    """상위노출 증거 — {published, first_page, avg_days, cases:[...], clicks}.
    '1페이지'는 rank_snapshots(kind=post) ≤10 실측. 추정치 없음."""
    pubs = db.list_blog_publishes(t.id, limit=30)
    published = len(pubs)
    first_page, days_list, cases = 0, [], []
    for pub in pubs:
        piece = db.get_piece(pub.get("piece_id") or "")
        kw = ""
        if piece:
            kw = ((piece.payload or {}).get("target_keywords") or [""])[0].strip()
        kw = kw or (pub.get("target_kw") or "").strip()
        if not kw:
            continue
        hist = [h for h in db.rank_history(t.id, kw, kind="post", limit=60) if h.get("rank")]
        top = min((h["rank"] for h in hist), default=None)
        if top and top <= 10:
            first_page += 1
            first_hit = next(h for h in hist if h["rank"] and h["rank"] <= 10)
            try:
                d0 = datetime.fromisoformat((pub.get("published_at") or "")[:19])
                d1 = datetime.fromisoformat((first_hit.get("checked_at") or "")[:19])
                dd = max(0, (d1 - d0).days)
                days_list.append(dd)
                cases.append({"keyword": kw, "days": dd, "best": top,
                              "title": (pub.get("post_title") or "")[:40]})
            except Exception:
                cases.append({"keyword": kw, "days": None, "best": top,
                              "title": (pub.get("post_title") or "")[:40]})
    try:
        clicks = sum(int(l.get("clicks") or 0) for l in db.list_links(t.id))
    except Exception:
        clicks = 0
    cases.sort(key=lambda c: (c["best"], c["days"] if c["days"] is not None else 999))
    return {"published": published, "first_page": first_page,
            "avg_days": (round(sum(days_list) / len(days_list)) if days_list else None),
            "cases": cases[:6], "clicks": clicks}
