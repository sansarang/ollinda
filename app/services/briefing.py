"""
매일 아침 브리핑 — AI 사장님 파트너(브리핑 PHASE 1).
수동(사장님이 들어와야 일어남) → 능동(올린다가 먼저 "오늘 상황 + 할 일 딱 하나")으로.

심리 설계:
  ① 할 일은 딱 하나 — 신호들을 점수화해 최고 1개만, 나머지는 버린다(5개 주면 0개 함).
  ② 경쟁불안·손실 프레이밍은 '해결책과 함께'만 — 위협만 주고 끝내지 않는다.
  ③ "나머지는 제가 준비할게요" 파트너 톤 — 짐을 나눠 진다.

정직성: 실측 데이터(순위 스냅샷·경쟁사 스냅샷·발행 이력·실검색량)만 사용.
신호가 없으면 지어내지 않고 "오늘은 특별한 변화 없어요" 기본 브리핑.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from app import config, db

_log = logging.getLogger("shopcast.briefing")

# 신호별 기본 중요도(오늘 가장 임팩트 큰 것 1개 선정) — 높을수록 우선
_W_COMPETITOR = 90     # 경쟁사 역전/추격 = 손실 진행형(가장 급함)
_W_RANK_DOWN = 80      # 내 순위 하락
_W_GAP = 70            # 발행 공백(C-Rank 일관성 경고)
_W_MISSING_KW = 60     # 미노출 고검색량 키워드(선점 기회)
_W_RANK_UP = 50        # 순위 상승(굳히기)
_W_DEFAULT = 10


def _sig_competitor(t) -> dict | None:
    """경쟁사 위협 — 최근 스냅샷에서 경쟁사가 나보다 위거나 역전."""
    try:
        comps = db.list_competitors(t.id)
        for comp in comps:
            snaps = db.competitor_snapshots(comp["id"], limit=2)
            if not snaps:
                continue
            s = snaps[0]
            my, their = s.get("my_rank"), s.get("competitor_rank")
            if their and (not my or their < my):
                kw = s.get("keyword") or (comp.get("keywords") or [""])[0] or \
                     f"{t.region} {t.industry}".strip()
                my_txt = f"{my}위" if my else "미노출"
                return {"score": _W_COMPETITOR, "kind": "competitor",
                        "headline": f"동네 검색에서 {comp['name']}이(가) 사장님보다 위에 있어요"
                                    f" ({their}위 vs 내 가게 {my_txt}).",
                        "task": "추격 글 1편 — 글감은 제가 잡아뒀어요, 사진 3장만 보내주세요",
                        "reason": "경쟁사가 먼저 자리를 잡으면 되찾는 데 몇 배 오래 걸려요. 오늘 한 편이면 추격이 시작돼요.",
                        "kw": kw, "angle": "review"}
    except Exception:
        pass
    return None


def _sig_rank_moves(t) -> list[dict]:
    """내 순위 변화(상승/하락) — rank_snapshots 실측 비교."""
    out = []
    try:
        from app.services import ranktrack
        for d in ranktrack.rank_deltas(t.id, limit=6):
            f, l = d.get("first"), d.get("last")
            if f is None or l is None:
                continue
            fv, lv = (f or 31), (l or 31)
            kw = d["keyword"]
            if lv > fv:                                # 하락
                out.append({"score": _W_RANK_DOWN + min(9, lv - fv), "kind": "rank_down",
                            "headline": f"추적 중인 검색 순위 하나가 {f or '미노출'}위 → {l or '미노출'}위로 밀렸어요.",
                            "task": "회복 글 1편 — 글감은 제가 정해뒀어요, 최근 작업 사진 3장만 보내주세요",
                            "reason": "새 글이 끊기면 네이버가 '활동이 식었다'고 봐요. 오늘 한 편이면 흐름을 되돌릴 수 있어요.",
                            "kw": kw, "angle": "review"})
            elif lv < fv and (l or 0) > 0:             # 상승
                out.append({"score": _W_RANK_UP + min(9, fv - lv), "kind": "rank_up",
                            "headline": f"추적 중인 검색 순위 하나가 {f or '미노출'}위 → {l}위로 오르는 중이에요!",
                            "task": "기세 굳히기 글 1편 — 글감은 제가 이어뒀어요 (사진 3장이면 충분해요)",
                            "reason": "오르는 키워드에 글을 더하면 상위 안착이 훨씬 빨라져요. 지금이 제일 효율 좋은 타이밍이에요.",
                            "kw": kw, "angle": "howto"})
    except Exception:
        pass
    return out


def _sig_publish_gap(t, plan: str) -> dict | None:
    """발행 공백 — C-Rank 일관성 경고(실측: publish_activity)."""
    try:
        from app.services import pubcal
        act = db.publish_activity(t.id)
        gap = act.get("gap_days")
        if act.get("last_at") and gap is not None and gap >= config.REMIND_GAP_DAYS:
            wp = pubcal.week_plan(t, plan)
            topic = (wp["suggestions"][0]["topic"] if wp["suggestions"]
                     else f"{t.region} {t.industry}".strip())
            angle = (wp["suggestions"][0]["angle"] if wp["suggestions"] else "review")
            return {"score": _W_GAP + min(9, gap), "kind": "gap",
                    "headline": f"{gap}일째 새 발행이 없어요 (이번 주 {wp['done']}/{wp['target']}회).",
                    "task": "오늘 1편 — 글감은 준비돼 있어요, 사진 3장만 보내주세요",
                    "reason": "발행 간격이 벌어지면 쌓아둔 C-Rank 꾸준함 신호가 식어요. 한 편이면 페이스가 돌아와요.",
                    "kw": topic, "angle": angle}
    except Exception:
        pass
    return None


def _sig_missing_kw(t) -> dict | None:
    """미노출 고검색량 키워드 — 진단(실측 순위+실검색량) 기반 선점 기회."""
    try:
        from app.services import diagnose
        r = diagnose.diagnose_rank(t.industry, t.region, t.name)
        if r.get("estimated"):
            return None
        miss = sorted(r.get("missing") or [], key=lambda s: -(s.get("volume") or 0))
        if miss:
            kw, vol = miss[0]["keyword"], miss[0].get("volume") or 0
            vol_txt = f"한 달에 {vol:,}번씩 " if vol else ""
            return {"score": _W_MISSING_KW + (5 if vol >= 1000 else 0), "kind": "missing_kw",
                    "headline": f"손님들이 {vol_txt}검색하는 말인데, 사장님 가게가 아직 안 보여요.",
                    "task": "선점 글 1편 — 글감은 제가 정해뒀어요, 사진 3장만 보내주세요",
                    "reason": ("그 검색이 지금은 전부 다른 가게로 가고 있어요. "
                               "먼저 자리 잡은 글이 오래 갑니다."),
                    "kw": kw, "vol": vol, "angle": "review"}
    except Exception:
        pass
    return None


def _analyst_line(pid: str) -> str:
    try:
        from app.services import analyst
        line = analyst.cached_brief_line(pid)
        return (" " + line) if line else ""
    except Exception:
        return ""


def _sig_stuck(t) -> list[dict]:
    """추적 글 정체 진단(rx P4) — 5일+ 같은 자리(11위 밖)면 품질 경고 기반 처방을 브리핑으로.
    '보장' 금지 — '올라갈 가능성' 표현까지만. 실측(post 스냅샷·저장된 audit)만 사용."""
    out = []
    try:
        for pub in db.list_blog_publishes(t.id, limit=5):
            piece = db.get_piece(pub.get("piece_id") or "")
            if not piece:
                continue
            kw = ((piece.payload or {}).get("target_keywords") or [""])[0].strip()
            if not kw:
                continue
            hist = [h for h in db.rank_history(t.id, kw, kind="post", limit=15)
                    if h.get("rank") is not None]
            if len(hist) < 3:
                continue
            first_at, last_at = (hist[0].get("checked_at") or "")[:10], (hist[-1].get("checked_at") or "")[:10]
            ranks = [h["rank"] for h in hist[-5:]]
            cur = ranks[-1]
            span = 0
            try:
                import datetime as _d
                span = (_d.date.fromisoformat(last_at) - _d.date.fromisoformat(first_at)).days
            except Exception:
                pass
            if not (cur and cur > 10 and span >= 5 and max(ranks) - min(ranks) <= 1):
                continue
            au = (piece.payload or {}).get("ranking_audit") or {}
            weak = next((w for w in (au.get("warnings") or []) if "경험" in w or "수치" in w), "")
            fix = "경험 문장을 보강하면" if weak else "같은 주제 글을 하나 더하면"
            out.append({"score": 74, "kind": "stuck",
                        "headline": f"발행하신 글 하나가 {span}일째 {cur}위 부근에서 정체 중이에요.",
                        "task": f"{fix} 올라갈 가능성이 있어요 — 다음 글감에 자동 반영해뒀어요 (자세한 진단은 리포트에서)",
                        "reason": "정체는 신호 부족이라는 뜻이에요. 진단이 부족한 항목을 짚고 바로 보강해드려요. (순위 보장은 아니에요)",
                        "kw": kw, "angle": "review"})
            break
    except Exception:
        pass
    return out


def _sig_race(t) -> list[dict]:
    """추적 중인 발행 글의 순위 이동(생존신고 P5) — rank_snapshots(kind='post') 실측 비교.
    첫 페이지(10위) 진입은 축하(성취=리텐션), 상승은 '가능성' 표현까지만(보장 금지)."""
    out = []
    try:
        for pub in db.list_blog_publishes(t.id, limit=5):
            piece = db.get_piece(pub.get("piece_id") or "")
            if not piece:
                continue
            kw = ((piece.payload or {}).get("target_keywords") or [""])[0].strip()
            if not kw:
                continue
            hist = [h for h in db.rank_history(t.id, kw, kind="post", limit=10)
                    if h.get("rank") is not None]
            if len(hist) < 2:
                continue
            prev, cur = hist[-2]["rank"], hist[-1]["rank"]
            _title = ((pub.get("post_title") or "")[:16] + "…") if pub.get("post_title") else "발행하신 글 하나"
            if cur and cur <= 10 and (not prev or prev > 10):
                out.append({"score": 85, "kind": "race_first_page",
                            "headline": f"발행하신 글('{esc_kw(_title)}')이 {prev or '31위 밖'} → {cur}위, 첫 페이지에 진입했어요!",
                            "task": "굳히기 타이밍 — 이어갈 글감은 제가 잡아뒀어요 (사진 3장이면 충분해요)",
                            "reason": ("첫 페이지에 막 오른 글은 지금 밀어주면 안착 가능성이 커요. (순위 보장은 아니에요)"
                                   + _analyst_line(pub.get("piece_id") or "")),
                            "kw": kw, "angle": "howto"})
            elif cur and prev and cur < prev:
                out.append({"score": 72, "kind": "race_up",
                            "headline": f"발행하신 글('{esc_kw(_title)}')이 어제 {prev}위 → 오늘 {cur}위로 올랐어요.",
                            "task": "이 페이스 유지 — 같은 주제 글 1편, 글감은 준비돼 있어요",
                            "reason": (("이 페이스면 첫 페이지 진입 가능성이 보여요 — 오르는 중 한 편이 제일 효율 좋아요."
                                       if cur > 10 else "상단 유지에는 꾸준함이 답이에요.")
                                       + _analyst_line(pub.get("piece_id") or "")),
                            "kw": kw, "angle": "howto"})
    except Exception:
        pass
    return out


# ── 셀러 신호(셀러 C1) — 지역·플레이스 대신 상품 키워드 쇼핑검색 축 ──
def _sig_shop_market(t) -> list[dict]:
    """셀러: 쇼핑검색 실측 — ① 상위권인데 1위 아님(추격+상위 상품 가격 실측)
    ② 미노출 고검색량 키워드(선점 기회). 공식 shop.json 범위만(리뷰수는 API 미제공 → 안 씀)."""
    out = []
    try:
        from app.services import diagnose, place
        r = diagnose.diagnose_product_rank(
            t.industry, getattr(t, "brand_name", "") or t.name, getattr(t, "brand_name", "") or "")
        if r.get("estimated"):
            return []

        def _top_line(kw):
            tops = place.shop_top(kw, 1)
            if tops and tops[0].get("mall"):
                p = tops[0]
                return f" 지금 1위는 {p['mall']}" + (f"({p['price']:,}원)" if p.get("price") else "") + "이에요."
            return ""

        caught = sorted(r.get("caught") or [], key=lambda s: s["rank"])
        if caught and caught[0]["rank"] > 1:
            s = caught[0]
            kw = s["keyword"]
            out.append({"score": _W_COMPETITOR - 5, "kind": "shop_chase",
                        "headline": f"쇼핑 검색에서 내 상품이 {s['rank']}위 — 위에 {s['rank'] - 1}개 상품이 있어요."
                                    + _top_line(kw),
                        "task": "내돈내산 후기 글 1편 — 글감은 제가 잡아뒀어요, 사진 3장만 보내주세요",
                        "reason": "구매 전 검색은 후기 글에서 갈려요. 후기 콘텐츠가 쌓이면 상품 클릭·전환이 같이 올라요.",
                        "kw": kw, "angle": "review"})
        miss = sorted(r.get("missing") or [], key=lambda s: -(s.get("volume") or 0))
        if miss:
            s = miss[0]
            kw, vol = s["keyword"], s.get("volume") or 0
            vol_txt = f"한 달에 {vol:,}번씩 " if vol else ""
            out.append({"score": _W_MISSING_KW + (5 if vol >= 1000 else 0), "kind": "shop_missing",
                        "headline": f"손님들이 {vol_txt}검색하는 상품인데, 상위 {r.get('scan_depth', 40)}위 안에 "
                                    "내 상품이 안 보여요." + _top_line(kw),
                        "task": "선점 후기 글 1편 — 글감은 제가 정해뒀어요, 사진 3장만 보내주세요",
                        "reason": "그 검색이 지금은 전부 다른 스토어로 가고 있어요. 후기 글이 검색 유입의 지렛대예요.",
                        "kw": kw, "vol": vol, "angle": "review"})
    except Exception:
        pass
    return out


def esc_kw(s: str) -> str:
    return (s or "").replace("<", "").replace(">", "")


def build_briefing(t, plan: str = "free") -> dict:
    """tenant의 '오늘의 브리핑 1건' — 신호 점수화 → 최고 1개만(딱 하나 원칙).
    반환: {kind, headline, task, reason, action_href, action_label, pass_href, score, date}."""
    signals: list[dict] = []
    if (getattr(t, "biz_type", "local") or "local") == "seller":
        # 셀러: 상품 키워드 쇼핑검색 축(추격/선점) + 순위 변화(kind=shop 포함) + 발행 공백.
        # 지역·플레이스 기반 신호(경쟁사·지역 미노출)는 셀러에겐 안 씀(매장 냄새 제거).
        cand = _sig_shop_market(t) + _sig_rank_moves(t) + [_sig_publish_gap(t, plan)] + _sig_race(t) + _sig_stuck(t)
    else:
        cand = ([_sig_competitor(t)] + _sig_rank_moves(t)
                + [_sig_publish_gap(t, plan), _sig_missing_kw(t)] + _sig_race(t) + _sig_stuck(t))
    for s in cand:
        if s:
            signals.append(s)
    if signals:
        best = max(signals, key=lambda x: x["score"])
    else:
        # 정직한 기본 브리핑 — 신호를 지어내지 않는다
        from app.services import pubcal
        wp = pubcal.week_plan(t, plan)
        topic = (wp["suggestions"][0]["topic"] if wp["suggestions"]
                 else f"{t.region} {t.industry}".strip() or "핵심 주제")
        best = {"score": _W_DEFAULT, "kind": "steady",
                "headline": "오늘은 순위·경쟁에 특별한 변화가 없어요 — 좋은 신호예요.",
                "task": "꾸준함이 곧 순위예요 — 오늘 1편, 글감은 제가 골라뒀어요 (사진 3장이면 충분)",
                "reason": "네이버 C-Rank는 '같은 주제 꾸준한 발행'을 가장 오래 기억해요.",
                "kw": topic, "angle": "review"}
    kw = best.get("kw") or ""
    best["action_href"] = (f"/me?target_kw={quote(kw)}&angle={best.get('angle', 'review')}&from=briefing"
                           if kw else "/me")
    best["action_label"] = "사진 보내고 시작하기"
    best["pass_href"] = "/api/briefing/pass"
    best["partner_note"] = "사진만 보내주시면 글·영상·발행 준비는 제가 할게요."
    # 어제 클릭 실측 동기부여(추적 P3) — 있을 때만, 링크 클릭 기준임을 명시(정직)
    try:
        import datetime as _dt
        y = (_dt.datetime.utcnow() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        yc = db.clicks_on_date(t.id, y)
        if yc:
            best["partner_note"] = (f"어제 콘텐츠가 손님 {yc}명을 데려왔어요(추적링크 클릭 기준) — "
                                    "오늘도 하나 어때요? " + best["partner_note"])
    except Exception:
        pass
    # 관심 손님(방문자 B2) — 익명 재방문 3회+ 실측이 있을 때만 타이밍 제안
    try:
        hv = db.visitor_stats(t.id, days=7).get("hot_visitors") or 0
        if hv:
            best["partner_note"] = (f"이번 주 3번 이상 다시 온 관심 손님이 {hv}명 있어요(익명 집계) — "
                                    "이벤트·새 소식을 알릴 타이밍이에요. " + best["partner_note"])
    except Exception:
        pass
    best["date"] = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    return best


# ── PHASE 2: 매일 아침 능동 발송 ─────────────────────────
def _briefing_text(t, b: dict) -> str:
    """발송용 텍스트 — 파트너 톤('나머지는 제가 준비할게요')."""
    return (f"사장님, 오늘 아침 브리핑이에요 ☕\n\n"
            f"■ 오늘 상황\n{b['headline']}\n\n"
            f"■ 오늘 할 일 딱 하나\n{b['task']}\n"
            f"→ {b['reason']}\n\n"
            f"{b['partner_note']}\n"
            f"시작하기: https://ollinda.kr{b['action_href']}\n"
            f"(오늘은 쉬어가도 괜찮아요 — 앱에서 '오늘은 패스'를 눌러주세요)")


def _send_kakao_stub(t, b: dict) -> None:
    # TODO(kakao): 알림톡 템플릿 승인 후 비즈메시지 발송 연결. 현재는 스텁(로그만).
    _log.info("[briefing] 카톡 알림톡(스텁) tenant=%s kind=%s", t.id, b.get("kind"))


def send_morning(now_kst_hour: int) -> dict:
    """현재 KST 시각과 tenant.briefing_hour가 일치하는 가게에 아침 브리핑 생성·발송.
    스케줄러가 매시 정각 호출(인스턴스 1개 전제 + daily_briefings sent 플래그로 1일 1회 보장)."""
    import datetime
    import os
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    sent = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if not t or not (t.industry or "").strip():
            continue
        if not getattr(t, "briefing_on", 1) or int(getattr(t, "briefing_hour", 8) or 8) != now_kst_hour:
            continue
        if db.briefing_sent(tid, today):               # 1일 1회(중복 발송 방지 락)
            continue
        try:
            b = get_or_create_today(t, u.get("plan") or "free")
            text = _briefing_text(t, b)
            db.add_notice(tid, "briefing", f"오늘 아침 브리핑 — {b['headline']} 오늘 할 일: {b['task']}")
            email = (u.get("email") or "")
            if email and not email.endswith((".guest", ".local")) and os.environ.get("SMTP_HOST"):
                try:
                    from app.services.weekly_report import _send_email
                    _send_email(email, "[올린다] 오늘 아침 브리핑", text)
                except Exception:
                    _log.exception("[briefing] 이메일 실패 uid=%s", u.get("id"))
            _send_kakao_stub(t, b)
            db.mark_briefing_sent(tid, today)
            sent += 1
        except Exception:
            _log.exception("[briefing] 발송 실패 tenant=%s", tid)
    if sent:
        _log.info("[briefing] %02d시 브리핑 %d건 발송", now_kst_hour, sent)
    return {"sent": sent, "hour": now_kst_hour}


# ── PHASE 4: 저녁 성과 피드백(하루 루프: 아침 브리핑 → 실행 → 저녁 피드백) ──
def _evening_text(t, st: dict) -> str:
    """저녁 피드백 — 전부 실측. 데이터 없으면 '내일부터 추적' 정직 안내."""
    lines = []
    if st["clicks_today"]:
        lines.append(f"오늘 콘텐츠 링크로 벌써 {st['clicks_today']}명이 들어왔어요.")
    ups = [m for m in st["rank_moves"] if (m["after"] or 31) < (m["before"] or 31)]
    downs = [m for m in st["rank_moves"] if (m["after"] or 31) > (m["before"] or 31)]
    for m in ups[:2]:
        b = f"{m['before']}위" if m["before"] else "미노출"
        lines.append(f"'{m['keyword']}' 순위가 {b} → {m['after']}위로 움직이는 중이에요.")
    if downs and not ups:
        m = downs[0]
        lines.append(f"'{m['keyword']}'가 {m['before']}위 → {m['after'] or '미노출'}로 밀렸어요 — 내일 브리핑에서 대응책 드릴게요.")
    if not lines:
        lines.append("오늘 만든 콘텐츠의 순위 변화는 내일부터 추적해서 알려드릴게요 — 네이버 반영엔 하루 이틀 걸려요.")
    return ("사장님, 오늘 하루 마무리 피드백이에요 🌙\n\n"
            + "\n".join("· " + x for x in lines)
            + f"\n\n오늘 {st['made_today']}건 만드셨어요. 내일 아침 브리핑에서 다음 한 수를 준비해둘게요.")


def send_evening() -> dict:
    """저녁 20시 — '오늘 콘텐츠를 만든' 가게에만 성과 피드백(안 만든 날은 조용히 — 스팸 방지)."""
    import datetime
    import os
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    sent = 0
    for u in db.list_users():
        tid = u.get("tenant_id")
        if not tid:
            continue
        t = db.get_tenant(tid)
        if not t or not (t.industry or "").strip() or not getattr(t, "briefing_on", 1):
            continue
        if db.briefing_sent(tid, today, col="evening_sent"):
            continue
        st = db.today_feedback_stats(tid)
        if not st["made_today"]:                        # 오늘 실행 없음 → 피드백 없음(정직·비스팸)
            continue
        try:
            text = _evening_text(t, st)
            db.add_notice(tid, "evening", text.split("\n\n")[1][:180])
            email = (u.get("email") or "")
            if email and not email.endswith((".guest", ".local")) and os.environ.get("SMTP_HOST"):
                try:
                    from app.services.weekly_report import _send_email
                    _send_email(email, "[올린다] 오늘 하루 피드백", text)
                except Exception:
                    _log.exception("[briefing] 저녁 이메일 실패 uid=%s", u.get("id"))
            _send_kakao_stub(t, {"kind": "evening"})
            # 저녁 락 — 브리핑 행이 없으면 만들어 두고 표시
            if not db.get_briefing(tid, today):
                db.save_briefing(tid, today, {"kind": "evening_only", "date": today})
            db.mark_briefing_sent(tid, today, col="evening_sent")
            sent += 1
        except Exception:
            _log.exception("[briefing] 저녁 피드백 실패 tenant=%s", tid)
    _log.info("[briefing] 저녁 피드백 %d건", sent)
    return {"sent": sent}


def get_or_create_today(t, plan: str = "free") -> dict:
    """오늘 브리핑 조회(있으면 재사용 — 1일 1회) 없으면 생성·저장."""
    today = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
    cached = db.get_briefing(t.id, today)
    if cached:
        return cached
    b = build_briefing(t, plan)
    db.save_briefing(t.id, today, b)
    return b
