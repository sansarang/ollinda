"""
Ingest 서비스 — 사장님 업로드(사진+메모) → Asset 저장 → 캡션 초안 생성 → DRAFT 저장.
'1소스 → 멀티채널'의 진입점. MVP에선 인스타 캡션 1종만 생성(Phase 2에서 확장).
"""
from __future__ import annotations

import os
import uuid

from app import db, storage, seo, vision, reach
from app.domain.models import (AssetType, Channel, ContentKind, ContentPiece,
                               ContentStatus, Tenant)
from app.services.generate import generate_for
from app.strategies import resolve_strategy, ordered_kinds, kind_rank


def ingest_upload(tenant: Tenant, files: list[tuple[bytes, str]], note: str,
                  kinds: list[ContentKind] | None = None,
                  target_kw: str = "", angle: str = "",
                  intake: dict | None = None) -> list[ContentPiece]:
    """files: [(bytes, filename), ...] 여러 장. 1소스(여러 사진) → 멀티채널 생성.
    target_kw: 진단에서 고른 미노출 키워드 — 블로그 제목·본문이 이 키워드를 겨냥(상위노출 PHASE 1).
    angle: 후기형(review)/방법형(howto)/가격형(price) 앵글 — 스마트블록 다중진입용.
    intake(스마트 입력, 콘텐츠생성 PHASE 4): {confirmed, analysis, answers, experience} —
    확인된 사진내용·질문답·경험을 note에 구조 주입(D.I.A.+ 재료), analysis 있으면 vision 생략."""
    intake = intake or {}
    try:
        from app.services import smart_intake
        _block = smart_intake.build_intake_note(tenant.industry, intake.get("confirmed", ""),
                                                intake.get("answers"), intake.get("experience", ""))
        _block += smart_intake.intent_directive(intake.get("intent", ""))   # 확정 의도 → 소재 유형(3-1)
        if _block:
            note = (_block + "\n" + (note or "")).strip()
        smart_intake.record_insight(tenant.industry, intake.get("answers"), intake.get("experience", ""))
    except Exception:
        pass
    # 텍스트는 즉시(빠름), 영상(SHORT)+릴스+캐러셀은 비동기 → 요청이 타임아웃 없이 바로 끝남
    kinds = kinds or list(CORE_KINDS)
    # 사업형태 전략에 따라 생성 순서 정렬 (셀러=영상 우선, 소상공인=블로그 우선)
    strat = resolve_strategy(tenant)
    # 셀러 → 판매 플랫폼 콘텐츠(상품명·상세페이지·태그) 추가 생성
    if strat.key == "seller" and ContentKind.MARKETPLACE not in kinds:
        kinds = kinds + [ContentKind.MARKETPLACE]
    kinds = ordered_kinds(strat, kinds)
    if not files:
        return []
    paths: list[str] = []
    for data, fname in files:
        paths.append(storage.save_upload(data, fname or "photo.jpg", tenant.id))
    # ✨ 사진 자동 보정(전문가 톤) + 검색노출용 EXIF·GPS 메타 삽입. 보정본을 R2에도 재미러.
    try:
        from app.media import photo_boost
        _kws = seo.target_keywords(tenant.industry, tenant.region, note, limit=6)
        _meta = {
            "description": (f"{tenant.region} {tenant.industry} - {tenant.name}").strip(" -"),
            "keywords": ", ".join(_kws) if _kws else f"{tenant.region} {tenant.industry}".strip(),
            "artist": tenant.name,
            "lat": getattr(tenant, "lat", None), "lon": getattr(tenant, "lon", None),
        }
        photo_boost.enhance_all(paths, tenant.industry, _meta)
        for _p in paths:
            storage.mirror_to_r2(_p)
    except Exception:
        pass
    # 대표 Asset(첫 장)에 메모 기록 — 나머지 장은 images로 전달
    asset = db.create_asset(tenant.id, AssetType.IMAGE, paths[0], note)
    # 🎯 진단→생성 연결(상위노출 PHASE 1): 타겟 키워드·앵글을 asset에 실어 생성기로 전달
    target_kw = (target_kw or "").strip()
    if target_kw:
        asset.target_kw = target_kw
        asset.note = (f"[타겟 키워드 — 순위 진단에서 선택된 미노출 키워드] '{target_kw}' "
                      "이 키워드로 검색하는 사람이 찾는 답을 주는 글로 작성하라. "
                      "제목·첫문장·소제목에 자연스럽게 반영(같은 단어 도배 금지, 유의어로 확장).\n"
                      + asset.note)
    if angle in ("review", "howto", "price"):
        asset.angle = angle
    # 👁 비전: 대표 사진을 실제 분석해 생성 프롬프트에 반영(키 없으면 ""). DB엔 원본 메모 유지.
    # 선추측(intake.analysis) 있으면 재호출 생략 — 같은 사진을 이미 분석함(비용 1콜 유지, PHASE 4)
    analysis = (intake.get("analysis") or "").strip() or vision.analyze_all(paths, tenant.industry)
    if analysis:
        # 확인 절차(SEO_CURRENT §5-3): 사용자 확인 없인 '추측' 라벨 + 단정 금지 — 사실로 각인 방지
        from app.services import smart_intake as _si2
        asset.note = f"{note}" + _si2.analysis_block(analysis, intake.get("confirmed", ""))
    # 🎯 마케팅 전략가 — 전 채널이 공유할 크리에이티브 브리프(1콜). 프롬프트에 주입 → 채널 일관성.
    from app.generators.strategist import build_brief, brief_to_directive
    from app.generators.editor import polish
    brief = build_brief(tenant, asset)
    asset.note = asset.note + brief_to_directive(brief)
    # 🔎 업종 상위 패턴 학습(셀러형만 — 이번 범위) : 검색 API 상위 제목·요약의 '구조 경향'만 참고 주입.
    # 기존 프롬프트 불변 — 별도 블록으로 뒤에 붙임. payload에 사용 여부 기록(효과 비교용).
    _pattern_used = None
    try:
        _bt = (getattr(tenant, "biz_type", "local") or "local")
        if _bt in ("seller", "hybrid") and "중고차" in ((tenant.industry or "") + (asset.note or "")):
            from app.services import kwpattern as _kwp
            _tk = (getattr(asset, "target_kw", "") or "").strip() or (
                seo.target_keywords(tenant.industry, tenant.region, asset.note,
                                    axis=resolve_strategy(tenant).keyword_axis, brand=tenant.brand_name) or [""])[0]
            _pat = _kwp.analyze(_tk)
            if _pat:
                _blk = _kwp.directive_block(_pat)
                if _blk:
                    asset.note = asset.note + "\n" + _blk
                    _pattern_used = {"keyword": _tk, "cached": _pat.get("_cached", False),
                                     "title_types": _pat.get("title_types"), "intro": _pat.get("intro")}
    except Exception:
        import logging as _lg3
        _lg3.getLogger("shopcast.kwpattern").exception("[kwpattern] 주입 실패(무시)")
    # 🚗 매물 컨텍스트 저장(셀러·병행 중고차) — 오토큐 차종 롱테일 재료. 폼·vision 확정값에서만(날조 금지).
    try:
        _bt2 = (getattr(tenant, "biz_type", "local") or "local")
        if _bt2 in ("seller", "hybrid") and "중고차" in ((tenant.industry or "") + (asset.note or "")):
            import re as _rc
            _src = (asset.note or "")
            _MODELS = ("모닝", "레이", "스파크", "캐스퍼", "아반떼", "쏘나타", "그랜저", "K3", "K5", "K7", "K8",
                       "코나", "티볼리", "셀토스", "투싼", "쏘렌토", "싼타페", "카니발", "스포티지", "포터", "봉고",
                       "제네시스", "G80", "GV70", "GV80", "말리부", "트랙스", "베뉴", "팰리세이드")
            _model = next((m for m in _MODELS if m in _src), "")
            _year = next(iter(_rc.findall(r"(20[0-2]\d|19[89]\d)", _src)), "")
            _CLASS = [("경차", ("경차", "모닝", "레이", "스파크", "캐스퍼")),
                      ("SUV", ("SUV", "코나", "티볼리", "셀토스", "투싼", "쏘렌토", "싼타페", "스포티지", "팰리세이드", "GV70", "GV80")),
                      ("준중형", ("아반떼", "K3")), ("중형", ("쏘나타", "K5", "말리부")),
                      ("대형", ("그랜저", "K7", "K8", "제네시스", "G80")), ("승합", ("카니발", "스타리아", "포터", "봉고"))]
            _cls = next((c for c, ks in _CLASS if any(k in _src for k in ks)), "")
            if _model or _cls:
                db.save_inventory_context(tenant.id, _model, _year, _cls)
                import logging as _lg4
                _lg4.getLogger("shopcast.ingest").info("[inventory] 컨텍스트 저장 t=%s model=%s year=%s class=%s",
                                                       tenant.id, _model, _year, _cls)
    except Exception:
        pass
    # 📈 성과 학습 루프 — 지난 콘텐츠로 순위가 오른 키워드를 다음 생성에 강화 반영(쓸수록 똑똑해짐)
    try:
        learn = db.improving_keywords(tenant.id)
        if learn:
            kwlist = ", ".join(f"'{x['keyword']}'" for x in learn[:4])
            asset.note += (f"\n[성과 학습 — 효과 검증된 키워드] 아래 키워드는 실제로 순위가 오른 키워드다. "
                           f"제목·첫문장·본문에 자연스럽게 더 강하게 반영하라: {kwlist}")
    except Exception:
        pass
    # 📊 클릭 실측 학습(추적 P3) — 손님을 실제로 데려온 콘텐츠의 키워드·앵글을 다음 생성에 강화
    try:
        top = db.content_click_ranking(tenant.id, days=30, limit=1)
        if top and top[0]["n"] >= 3:                    # 우연 클릭(1~2회)으로 방향 왜곡 방지
            b = db.find_piece_brief(tenant.id, top[0]["content_id"]) or {}
            kw = (b.get("keywords") or [""])[0]
            ang = {"review": "후기형", "howto": "방법형", "price": "가격형"}.get(b.get("angle") or "", "")
            if kw:
                asset.note += (f"\n[성과 학습 — 클릭 실측] '{kw}'{(' · ' + ang) if ang else ''} 콘텐츠가 "
                               f"추적링크 클릭 {top[0]['n']}회로 가장 반응이 좋았다. "
                               "이 키워드·앵글 방향을 참고해 더 강화하라(그대로 복제 금지).")
    except Exception:
        pass
    brief_public = {k: v for k, v in brief.items() if not k.startswith("_")}
    pieces = generate_for(tenant, asset, kinds, images=paths)   # ✍️ 카피라이터·🎬 영상감독
    # 블로그(=상태 저장소)가 실패하면 channel_status·video_job·워치독 전부 실명 → 즉시 1회 재시도(단일점 봉합)
    if ContentKind.BLOG in kinds and not any(p.kind == ContentKind.BLOG for p in pieces):
        import logging as _lg2
        from app.services.generate import LAST_ERRORS as _LEb
        _lg2.getLogger("shopcast.ingest").error(
            "[ingest] BLOG 생성 실패 — 즉시 재시도 1회 (사유: %s)", _LEb.get(str(ContentKind.BLOG), "?"))
        retry = generate_for(tenant, asset, [ContentKind.BLOG], images=paths)
        pieces.extend(retry)
        if not retry and pieces:                       # 재시도도 실패 → 첫 피스에 사유 각인(워치독이 읽음)
            pieces[0].payload["_missing_blog"] = _LEb.get(str(ContentKind.BLOG), "생성 실패(로그 참조)")
    _exp = (intake.get("experience") or "").strip()[:200]       # 사장님 경험담 — 결과 하이라이트용(A2)
    for p in pieces:
        p.payload.setdefault("image_path", paths[0])
        p.payload.setdefault("biz_type", getattr(tenant, "biz_type", "local") or "local")
        if _exp and p.kind in (ContentKind.BLOG, ContentKind.CAPTION, ContentKind.X_POST):
            p.payload["owner_story"] = _exp                     # '내 말이 글이 됐네' 실감 재료
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload, source=asset.note)
        if p.kind == ContentKind.BLOG:
            if _pattern_used:
                p.payload["pattern_learning"] = _pattern_used   # 패턴 사용 여부·신호(효과 비교용 P4)
            from app import llm as _llm
            if _llm.LAST_ROUTE.get("vision"):
                p.payload["vision_route"] = dict(_llm.LAST_ROUTE["vision"])   # 폴백 기록(원가 추적)                          # GEO(AI검색 준비) 점수 — 블로그만(B2)
            p.payload["geo_audit"] = seo.geo_audit(
                "blog", p.payload, name=tenant.name, industry=tenant.industry,
                region=tenant.region or "", biz_type=getattr(tenant, "biz_type", "local") or "local")
        polish(tenant, p)                                       # 🔍 SEO 편집장(저점수만 리라이트)
        try:                                                    # 콘텐츠별 추적링크 자동 포함(추적 P1)
            from app.services import tracklinks                 # polish 뒤 — 리라이트로 링크 유실 방지
            tracklinks.inject(tenant, p)
        except Exception:
            pass
        p.payload["reach"] = reach.estimate(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_public
        ex = ["🎯 전략가", "✍️ 카피라이터"]
        if p.kind == ContentKind.SHORT:
            ex.append("🎬 영상감독")
        if p.payload.get("edited_by_seo"):
            ex.append("🔍 SEO편집장")
        p.payload["experts"] = ex
        db.save_piece(p)
    # 🔗 내부링크 제안(상위노출 PHASE 4) — 같은 주제 축의 발행 확인된 내 글(주제 응집도 = C-Rank 신호)
    try:
        blog_piece0 = next((p for p in pieces if p.kind == ContentKind.BLOG), None)
        if blog_piece0:
            from app.services import blogsync
            rel = blogsync.related_published(tenant.id, blog_piece0.payload.get("target_keywords") or [])
            if rel:
                blog_piece0.payload["related_posts"] = rel
                db.save_piece(blog_piece0)
    except Exception:
        pass
    # 네이버 플레이스 연동 — 매장(local/hybrid)이면 블로그에 플레이스 키워드 + 리뷰요청 문구 첨부 (#플레이스전략)
    try:
        if (getattr(tenant, "biz_type", "local") or "local") in ("local", "hybrid"):
            blog_piece = next((p for p in pieces if p.kind == ContentKind.BLOG), None)
            if blog_piece:
                blog_piece.payload["place_keywords"] = seo.place_keywords(tenant.industry, tenant.region)
                blog_piece.payload["review_request"] = seo.review_request(
                    tenant.name, tenant.region, tenant.industry)
                blog_piece.payload["place_search"] = f'네이버에서 "{tenant.name}" 검색 → 플레이스 찜·예약'
                db.save_piece(blog_piece)
    except Exception:
        pass
    # 🎬 영상 잡 등록·채널 상태 기록·스폰 — 반드시 블로그 payload 재저장(내부링크·플레이스) 뒤에.
    # 앞에 두면 재저장이 메모리의 옛 blog 객체로 DB의 video_job/channel_status를 덮어씀(V1 실측 결함).
    _set_video_job(asset.id, "registered")             # 잡 상태 기록(영상 증발 재발 방지) — 조용한 실종 금지
    from app.services.generate import LAST_ERRORS as _LE
    _cs = {"naver": {"status": "generating"}, "shorts": {"status": "generating"}, "reels": {"status": "generating"}}
    for _k, _ch in KIND_TO_CHANNEL.items():
        if any(p.kind == _k for p in pieces):
            _cs[_ch] = {"status": "done"}
        else:
            _cs[_ch] = {"status": "failed", "error": _LE.get(str(_k), "생성 실패(로그 참조)")}
    _set_channel_status(asset.id, _cs)
    _spawn_video_bundle(tenant, asset, paths, brief_public)
    _autopilot(tenant, pieces)
    return pieces


def _autopilot(tenant: Tenant, pieces: list[ContentPiece]) -> None:
    """가게 신뢰레벨(autonomy)에 따라 자동 발행 — 운영자 검수 최소화.
    0=수동(전부 검수) / 1=점수게이트(85+ 자동, 미달만 검수) / 2=완전자동(검증 통과 전부)."""
    level = getattr(tenant, "autonomy", 0) or 0
    if level < 1:
        return
    from app.registry import get_publisher
    from app.services.publish import publish_and_record
    # 전략 우선순위대로 발행(셀러=영상 먼저)
    strat = resolve_strategy(tenant)
    for p in sorted(pieces, key=lambda x: kind_rank(strat, x.kind.value)):
        pub = get_publisher(p.channel)
        if not pub.supports_auto_publish:        # 네이버 등 반자동은 자동 발행 제외
            continue
        score = (p.payload.get("ranking_audit") or {}).get("score") or 0
        min_auto = 85 if level == 1 else 70      # 완전자동(2)도 최소 점수 게이트(C4)
        if score < min_auto:                     # 점수 미달 → 예외(검수 큐로 남김)
            continue
        # 표시광고법 위험표현이 감지되면 완전자동이라도 사람 검수로 보류(C4)
        _txt = " ".join(str(p.payload.get(k, "")) for k in
                        ("text", "body", "title", "subtitle", "narration", "hook_strategy"))
        if any(r in _txt for r in seo.RISKY_EXPRESSIONS):
            continue
        if seo.hard_block_hits(_txt):            # 의료광고법·자동차관리법 위반 단정 표현 → 자동발행 절대 금지(PHASE 7)
            continue
        if pub.validate(p):                      # 채널 규칙 위반 → 예외
            continue
        db.set_piece_status(p.id, ContentStatus.APPROVED)
        p.status = ContentStatus.APPROVED
        publish_and_record(p)


def _restore_media(tenant_id: str, paths: list) -> list:
    """(근본수정) 로컬에서 사라진 사진을 R2 미러에서 복원 — 저장소 정리로 로컬만 지워진 세트도
    영상 재생성 가능. 복원 실패 파일은 제외하고 존재분만 반환."""
    import os as _os
    out = []
    for p in paths or []:
        if not p:
            continue
        if _os.path.exists(p):
            out.append(p)
            continue
        try:
            from app import storage as _st
            import requests as _rq
            url = _st.r2_media_url(tenant_id, _os.path.basename(p))
            if not url:
                continue
            r = _rq.get(url, timeout=60)
            if r.status_code == 200 and r.content:
                _os.makedirs(_os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(r.content)
                out.append(p)
        except Exception:
            continue
    return out


def video_watchdog() -> None:
    """(영상 증발 재발 방지) 죽은 영상 잡 감지·1회 자동 재시도 — 기존 30분 크론(fresh_index)에 얹힘.
    판정: 최근 24h 세트에 블로그는 있는데 SHORT가 없고, video_job이 done/failed 어느 쪽도 아니며
    30분 이상 경과 → 죽은 잡(스레드 사망·배포 킬·기록 이전 구건). retried 1회 제한(폭주 금지)."""
    import logging
    import os as _os
    from datetime import datetime, timedelta
    log = logging.getLogger("shopcast.video")
    try:
        for row in db.recent_blog_piece_rows(hours=24, limit=50):
            try:
                pieces = db.get_set_pieces(row["asset_id"])
                if any(p.kind == ContentKind.SHORT for p in pieces):
                    continue
                blog = next((p for p in pieces if p.kind == ContentKind.BLOG), None)
                if not blog:
                    continue
                vj = blog.payload.get("video_job") or {}
                _rc = int(vj.get("retry_count") or (1 if vj.get("retried") else 0))
                if vj.get("status") == "done" or _rc >= 2:
                    continue
                if vj.get("status") == "failed" and _rc >= 2:
                    continue
                ref = (vj.get("ts") or row.get("created_at") or "")[:19]
                try:
                    if datetime.utcnow() - datetime.fromisoformat(ref) < timedelta(minutes=30):
                        continue
                except Exception:
                    pass
                tenant = db.get_tenant(row["tenant_id"])
                asset = db.get_asset(row["asset_id"])
                paths = _restore_media(row["tenant_id"], blog.payload.get("image_paths") or [])
                if not (tenant and asset and paths):
                    _set_video_job(row["asset_id"], "failed", error="재시도 불가(가게/사진 소실 — R2 복원도 실패)", retried=True)
                    continue
                from app import llm as _llm
                # 캡션이 Anthropic 라우팅일 때만 크레딧 핑(이원화 후 Gemini 라우팅이면 Anthropic 불필요)
                if _llm.route("caption")[0] == "anthropic" and not _llm.ping():
                    log.info("[video-watchdog] 크레딧 없음 — 재시도 보류 asset=%s", row["asset_id"])
                    continue
                _set_video_job(row["asset_id"], "retrying", retried=True)   # 상한 선기록(폭주 방지)
                _bump_retry(row["asset_id"])
                log.info("[video-watchdog] 죽은 영상 잡 재시도 asset=%s t=%s", row["asset_id"], row["tenant_id"])
                _spawn_video_bundle(tenant, asset, paths, blog.payload.get("brief") or {})
            except Exception:
                log.exception("[video-watchdog] 처리 실패 asset=%s", row.get("asset_id"))
    except Exception:
        log.exception("[video-watchdog] 스캔 실패")
    _text_channel_watchdog(log)


def _bump_retry(asset_id: str) -> None:
    try:
        blog = next((p for p in db.get_set_pieces(asset_id) if p.kind == ContentKind.BLOG), None)
        if blog:
            vj = dict(blog.payload.get("video_job") or {})
            vj["retry_count"] = int(vj.get("retry_count") or (1 if vj.get("retried") else 0)) + 1
            blog.payload["video_job"] = vj
            db.save_piece(blog)
    except Exception:
        pass


def _text_channel_watchdog(log) -> None:
    """(5채널 완전성) 캡션/X/블로그 피스가 누락·실패인 세트를 자동 재생성 — 최대 2회, 사유 기록.
    asset 기반 스캔: 블로그 자체가 실패한 세트(상태 저장소 부재 — 완전 침묵 사각)도 잡는다.
    글·기존 정상 피스 불변. 크레딧 없으면 보류(헛 재시도로 상한 소진 금지)."""
    from datetime import datetime, timedelta
    try:
        from app import llm as _llm
        _pinged = None                                   # 크레딧 핑은 스캔당 최대 1회
        for row in db.recent_asset_rows(hours=24, limit=50):
            try:
                pieces = db.get_set_pieces(row["asset_id"])
                if not pieces:
                    continue
                blog = next((p for p in pieces if p.kind == ContentKind.BLOG), None)
                if not blog:
                    # 블로그 부재 세트 — 재시도 카운트는 첫 피스 payload(_blog_retries)에
                    ref = pieces[0]
                    _rn = int(ref.payload.get("_blog_retries") or 0)
                    if _rn >= 2:
                        continue
                    if _pinged is None:
                        _pinged = _llm.ping()
                    if not _pinged:
                        log.info("[text-watchdog] 크레딧 없음 — BLOG 보류 asset=%s", row["asset_id"])
                        return
                    tenant = db.get_tenant(row["tenant_id"])
                    asset = db.get_asset(row["asset_id"])
                    if not (tenant and asset):
                        continue
                    log.warning("[text-watchdog] BLOG 부재 세트 재생성 asset=%s (retries=%d)",
                                row["asset_id"], _rn)
                    ref.payload["_blog_retries"] = _rn + 1
                    db.save_piece(ref)
                    if _regen_text_piece(tenant, asset, ContentKind.BLOG, ref):
                        # 성공 → 실재 기반 상태 구성(naver 영상은 아직 없으니 failed로 두면 다음 감시가…
                        # 영상은 regen-naver 경로 대상) + 나머지 채널 백필
                        _cs2 = {}
                        for _k2, _c2 in KIND_TO_CHANNEL.items():
                            _cs2[_c2] = {"status": "done" if any(p.kind == _k2 for p in pieces) else "failed",
                                         "error": "" if any(p.kind == _k2 for p in pieces) else "생성 누락"}
                        _has_short = any(p.kind == ContentKind.SHORT for p in pieces)
                        _nv_ok = any((p.payload or {}).get("naver_video", {}).get("path") for p in pieces
                                     if p.kind == ContentKind.SHORT)
                        _cs2["shorts"] = {"status": "done" if _has_short else "failed"}
                        _cs2["reels"] = {"status": "done" if _has_short else "failed"}
                        _cs2["naver"] = ({"status": "done"} if _nv_ok else
                                         {"status": "failed", "error": "네이버 영상 미생성(블로그 소급 후 재생성 필요)"})
                        _set_channel_status(row["asset_id"], _cs2)
                    continue
                cs = dict(blog.payload.get("channel_status") or {})
                # 등록 누락 감시: 5키 미만이면 경고 + 실재 기반 보충(부분 소실·구건 백필)
                if cs and len(cs) < len(CHANNELS):
                    log.warning("[text-watchdog] channel_status %d/5키 — 실재 기반 보충 asset=%s",
                                len(cs), row["asset_id"])
                    _has_s = any(p.kind == ContentKind.SHORT for p in pieces)
                    _nv = any((p.payload or {}).get("naver_video", {}).get("path") for p in pieces
                              if p.kind == ContentKind.SHORT)
                    _fill = {"insta": any(p.kind == ContentKind.CAPTION for p in pieces),
                             "x": any(p.kind == ContentKind.X_POST for p in pieces),
                             "shorts": _has_s, "reels": _has_s, "naver": _nv}
                    _set_channel_status(row["asset_id"], {
                        ch2: {"status": "done" if ok2 else "failed"}
                        for ch2, ok2 in _fill.items() if ch2 not in cs})
                    cs = dict((next((p.payload.get("channel_status") for p in db.get_set_pieces(row["asset_id"])
                                     if p.kind == ContentKind.BLOG), None)) or cs)
                for kind, ch in KIND_TO_CHANNEL.items():
                    have = any(p.kind == kind for p in pieces)
                    st = dict(cs.get(ch) or {})
                    if have and st.get("status") != "failed":
                        continue
                    retries = int(st.get("retries") or 0)
                    if have is False and not st:
                        st = {"status": "failed", "error": "생성 누락(상태 기록 이전 구건)"}
                    if st.get("status") != "failed" or retries >= 2:
                        continue
                    if _pinged is None:
                        _pinged = _llm.ping()            # 텍스트 생성은 Anthropic 경로
                    if not _pinged:
                        log.info("[text-watchdog] 크레딧 없음 — 보류 asset=%s", row["asset_id"])
                        return
                    tenant = db.get_tenant(row["tenant_id"])
                    asset = db.get_asset(row["asset_id"])
                    if not (tenant and asset):
                        continue
                    log.info("[text-watchdog] %s 재생성 asset=%s (retries=%d)", ch, row["asset_id"], retries)
                    from app.services.generate import LAST_ERRORS as _LE2
                    ok = _regen_text_piece(tenant, asset, kind, blog)
                    _set_channel_status(row["asset_id"], {ch: (
                        {"status": "done", "retries": retries + 1} if ok else
                        {"status": "failed", "retries": retries + 1,
                         "error": _LE2.get(str(kind), "재생성 실패")})})
            except Exception:
                log.exception("[text-watchdog] 처리 실패 asset=%s", row.get("asset_id"))
    except Exception:
        log.exception("[text-watchdog] 스캔 실패")


CHANNELS = ("naver", "shorts", "reels", "insta", "x")   # 5채널 완전성 기준(전 채널 보장)
# 동기 생성 채널의 단일 정의 — 업종·biz_type 무관 전 세트 공통(분기별로 달라질 수 없음).
# biz_type은 글 구조·CTA·앵글에만 영향. SHORT(쇼츠·릴스·네이버 영상)는 비동기 번들, MARKETPLACE는 셀러 부가.
CORE_KINDS = (ContentKind.CAPTION, ContentKind.BLOG, ContentKind.X_POST)
KIND_TO_CHANNEL = {ContentKind.CAPTION: "insta", ContentKind.X_POST: "x"}   # 텍스트 채널(동기 생성)


def _set_channel_status(asset_id: str, updates: dict) -> None:
    """세트의 채널별 생성 상태를 블로그 피스 payload.channel_status에 기록(merge).
    updates = {"x": {"status": "failed", "error": "..."}, ...} — '시도조차 안 함'이 침묵하는 구조 금지."""
    try:
        from datetime import datetime
        blog = next((p for p in db.get_set_pieces(asset_id) if p.kind == ContentKind.BLOG), None)
        if not blog:
            return
        cs = dict(blog.payload.get("channel_status") or {})
        for ch, info in updates.items():
            cur = dict(cs.get(ch) or {})
            cur.update(info)
            cur["ts"] = datetime.utcnow().isoformat()
            cs[ch] = cur
        blog.payload["channel_status"] = cs
        db.save_piece(blog)
    except Exception:
        import logging
        logging.exception("[ingest] channel_status 기록 실패 asset=%s", asset_id)


def _regen_text_piece(tenant: Tenant, asset, kind: ContentKind, ref) -> bool:
    """누락·실패한 텍스트 채널(캡션/X/블로그) 단건 재생성 — 표준 generate 경로 재사용(전 게이트 경유).
    ref = 같은 세트의 아무 기존 피스(사진·brief 참조용). 다른 피스 불변. 성공 시 저장+True."""
    paths = _restore_media(tenant.id, (ref.payload.get("image_paths") or [asset.path]))
    if not paths:
        return False
    made = generate_for(tenant, asset, [kind], images=paths)
    if not made:
        return False
    p = made[0]
    p.payload.setdefault("image_path", paths[0])
    p.payload.setdefault("biz_type", getattr(tenant, "biz_type", "local") or "local")
    p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload, source=asset.note)
    p.payload["reach"] = reach.estimate(p.channel.value, p.kind.value, p.payload)
    p.payload["brief"] = ref.payload.get("brief") or {}
    p.payload["experts"] = ["🎯 전략가", "✍️ 카피라이터"]
    if kind == ContentKind.BLOG:                       # 블로그 전용 부가(표준 ingest 루프와 동일 취지)
        try:
            p.payload["geo_audit"] = seo.geo_audit(
                "blog", p.payload, name=tenant.name, industry=tenant.industry,
                region=tenant.region or "", biz_type=getattr(tenant, "biz_type", "local") or "local")
        except Exception:
            pass
    if kind == ContentKind.X_POST:                     # 번들과 동일 설계 — 세트에 쇼츠가 있으면 X에도 영상 첨부
        short = next((q for q in db.get_set_pieces(asset.id)
                      if q.kind == ContentKind.SHORT and (q.payload or {}).get("video_path")), None)
        if short:
            p.payload["video_path"] = short.payload.get("video_path")
    db.save_piece(p)
    return True


def _set_video_job(asset_id: str, status: str, error: str = "", retried: bool | None = None) -> None:
    """영상 잡 상태를 블로그 피스 payload.video_job에 기록(영상 증발 재발 방지).
    registered→running→done/failed(+사유). 실패·미실행이 조용히 사라지는 구조 금지."""
    try:
        from datetime import datetime
        blog = next((p for p in db.get_set_pieces(asset_id) if p.kind == ContentKind.BLOG), None)
        if not blog:
            return
        vj = dict(blog.payload.get("video_job") or {})
        vj.update({"status": status, "ts": datetime.utcnow().isoformat()})
        if error:
            vj["error"] = error[:200]
        if retried is not None:
            vj["retried"] = retried
        blog.payload["video_job"] = vj
        db.save_piece(blog)
    except Exception:
        import logging
        logging.exception("[ingest] video_job 기록 실패 asset=%s", asset_id)


def _spawn_video_bundle(tenant: Tenant, asset, paths: list[str], brief_public: dict) -> None:
    """영상(SHORT)+릴스+캐러셀을 별도 스레드에서 생성·저장 — 요청을 막지 않음(폴링/새로고침으로 표시)."""
    import threading

    def _run():
        from app.generators.video import RENDER_SEM   # 동시 렌더 상한(ffmpeg 폭주 방지, PHASE 12)
        with RENDER_SEM:
            try:
                _set_video_job(asset.id, "running")
                _make_video_bundle(tenant, asset, paths, brief_public)
            except Exception as e:
                import logging
                logging.exception("[ingest] 비동기 영상 번들 실패 tenant=%s", tenant.id)
                _set_video_job(asset.id, "failed", error=repr(e))
                _set_channel_status(asset.id, {ch: {"status": "failed", "error": repr(e)[:150]}
                                               for ch in ("shorts", "reels", "naver")})
    threading.Thread(target=_run, daemon=True).start()


def _make_video_bundle(tenant: Tenant, asset, paths: list[str], brief_public: dict) -> None:
    shorts = generate_for(tenant, asset, [ContentKind.SHORT], images=paths)   # 🎬 영상감독
    for p in shorts:
        p.payload.setdefault("image_path", paths[0])
        p.payload.setdefault("biz_type", getattr(tenant, "biz_type", "local") or "local")
        p.payload["ranking_audit"] = seo.quality_audit(p.channel.value, p.kind.value, p.payload, source=asset.note)
        p.payload["reach"] = reach.estimate(p.channel.value, p.kind.value, p.payload)
        p.payload["brief"] = brief_public
        p.payload["experts"] = ["🎯 전략가", "✍️ 카피라이터", "🎬 영상감독"]
        db.save_piece(p)
    short = next((p for p in shorts if p.kind == ContentKind.SHORT
                  and p.channel == Channel.YOUTUBE and p.payload.get("video_path")), None)
    if not short:
        from app.services.generate import LAST_ERRORS
        _err = LAST_ERRORS.get("ContentKind.SHORT", "영상 미생성(로그 참조)")
        _set_video_job(asset.id, "failed", error=_err)
        _set_channel_status(asset.id, {"shorts": {"status": "failed", "error": _err},
                                       "reels": {"status": "failed", "error": _err},
                                       "naver": {"status": "failed", "error": _err}})
        return
    _set_video_job(asset.id, "done")
    _set_channel_status(asset.id, {
        "shorts": {"status": "done"},
        "naver": ({"status": "done"} if (short.payload.get("naver_video") or {}).get("path")
                  else {"status": "failed", "error": "네이버 영상 미생성(로그 참조)"})})
    saved = db.get_set_pieces(asset.id)
    caption = next((p for p in saved if p.kind == ContentKind.CAPTION), None)
    reel = ContentPiece(
        id=str(uuid.uuid4()), tenant_id=tenant.id, asset_id=asset.id,
        channel=Channel.INSTAGRAM, kind=ContentKind.SHORT,
        payload={"text": (caption.payload.get("text") if caption else short.payload.get("title", "")),
                 "title": short.payload.get("title", ""),
                 "video_path": short.payload.get("video_path"),
                 "image_path": short.payload.get("image_path"),
                 "image_paths": short.payload.get("image_paths", []),
                 "duration_sec": short.payload.get("duration_sec", 0), "is_reel": True,
                 "target_keywords": short.payload.get("target_keywords", [])},
        status=ContentStatus.DRAFT)
    reel.payload["ranking_audit"] = seo.quality_audit(reel.channel.value, reel.kind.value, reel.payload, source=asset.note)
    reel.payload["reach"] = reach.estimate(reel.channel.value, reel.kind.value, reel.payload)
    db.save_piece(reel)
    _set_channel_status(asset.id, {"reels": {"status": "done"}})
    # 𝕏 X에도 같은 숏폼 영상 첨부(글 + 영상)
    xp = next((p for p in saved if p.kind == ContentKind.X_POST), None)
    if xp:
        xp.payload["video_path"] = short.payload.get("video_path")
        xp.payload["image_paths"] = short.payload.get("image_paths", [])
        db.save_piece(xp)
    try:
        from app.generators.carousel import build_carousel
        blog = next((p for p in saved if p.kind == ContentKind.BLOG), None)
        pts = short.payload.get("scene_texts") or []
        title = ((blog.payload.get("title") if blog else None) or short.payload.get("title") or tenant.name)
        if caption and pts:
            cdir = os.path.join(os.environ.get("SHOPCAST_STORAGE", "storage"), tenant.id, "carousel")
            caption.payload["carousel_paths"] = build_carousel(
                tenant, title, pts, getattr(tenant, "biz_type", "local") or "local", cdir)
            db.save_piece(caption)
    except Exception:
        pass
    # ☁ 생성물을 R2에 미러 + 로컬 미디어 삭제(볼륨 확보). R2 설정 시에만 삭제, 미설정이면 미러만(no-op).
    try:
        from app import storage as _st
        _st.mirror_to_r2(short.payload.get("video_path"))
        _st.mirror_to_r2(short.payload.get("cover_path"))
        for _v in (short.payload.get("video_variants") or {}).values():   # 정사각·4:5 변형도 R2로
            _st.mirror_to_r2(_v)
        for cp in (caption.payload.get("carousel_paths") or []) if caption else []:
            _st.mirror_to_r2(cp)
        if _st.r2_configured():
            paths = set()
            for pc in db.get_set_pieces(asset.id):     # 사진은 save_upload가 이미 R2 미러함
                # 로컬 삭제 전 발행용 R2 공개 URL을 payload에 각인(인스타 URL 발행이 삭제 후에도 동작, B5)
                _vu = _st.public_url_for(pc.payload.get("video_path"))
                _iu = _st.public_url_for(pc.payload.get("image_path"))
                if _vu or _iu:
                    if _vu:
                        pc.payload["video_url"] = _vu
                    if _iu:
                        pc.payload["image_url"] = _iu
                    db.save_piece(pc)
                paths.add(pc.payload.get("video_path"))
                paths.add(pc.payload.get("image_path"))
                paths.add(pc.payload.get("cover_path"))
                for _v in (pc.payload.get("video_variants") or {}).values():   # 변형 mp4(디스크 누수 주범)
                    paths.add(_v)
                for ip in (pc.payload.get("image_paths") or []):
                    paths.add(ip)
                for cp2 in (pc.payload.get("carousel_paths") or []):
                    paths.add(cp2)
            for fp in paths:
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)              # 서빙은 R2 리다이렉트로 계속 됨
                    except Exception:
                        pass
    except Exception:
        pass
