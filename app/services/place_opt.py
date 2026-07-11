"""
네이버 플레이스 최적화(상위노출 PHASE 5) — 동네매장은 플레이스(지도) 상위노출이 방문에 직결.
① 플레이스 순위 분리 추적(ranktrack이 kind='place'로 기록 — 여기선 요약)
② 리뷰 요청 키트(QR/링크/문구) — 리뷰 수·최신성이 플레이스 순위 핵심
③ 정보 완성도 체크리스트 — 영업시간·사진·소식·메뉴 등 빠진 항목 점검

정직성: 가짜 리뷰 유도 금지 — '실제 방문 손님'에게 정당하게 요청하는 문구만 제공.
"""
from __future__ import annotations

from app import db


def review_request_texts(tenant) -> list[dict]:
    """실제 방문 손님 대상 리뷰 요청 문구 3종(카운터/영수증·포장/단골 문자).
    대가성 표현(리뷰 쓰면 서비스 등) 없이 정당한 요청만."""
    name = getattr(tenant, "name", "") or "저희 가게"
    return [
        {"where": "카운터·테이블 (QR과 함께)",
         "text": (f"오늘 {name} 어떠셨나요? 😊\n"
                  f"네이버에서 '{name}' 검색 → 방문자 리뷰 한 줄이 저희에겐 큰 힘이 됩니다!")},
        {"where": "영수증·포장 스티커",
         "text": (f"맛있게 드셨다면 네이버 리뷰 부탁드려요 🙏\n'{name}' 검색 → 리뷰 쓰기")},
        {"where": "단골 손님 문자·카톡",
         "text": (f"안녕하세요, {name}입니다. 늘 찾아주셔서 감사해요!\n"
                  "혹시 지난 방문이 좋으셨다면 네이버 방문자 리뷰로 남겨주시면 정말 감사하겠습니다. "
                  "솔직한 후기가 가장 큰 도움이 됩니다 😊")},
    ]


def place_checklist(tenant) -> list[dict]:
    """플레이스 정보 완성도 — 올린다가 아는 정보(tenant) 기반 점검 + 직접 확인 항목.
    [{key, label, done(True|False|None=직접확인), why, how}]"""
    t = tenant
    has = lambda v: bool((v or "").strip())
    items = [
        {"key": "address", "label": "주소", "done": has(getattr(t, "address", "")),
         "why": "지도 노출의 기본", "how": "가게 설정에서 주소 입력 → 스마트플레이스와 일치 확인"},
        {"key": "phone", "label": "전화번호", "done": has(getattr(t, "phone", "")),
         "why": "전화 문의 = 플레이스 행동 신호", "how": "가게 설정에서 전화번호 입력"},
        {"key": "hours", "label": "영업시간", "done": has(getattr(t, "hours", "")),
         "why": "'영업 중' 필터에 걸림 — 미입력 시 노출 손해", "how": "스마트플레이스 > 기본정보 > 영업시간"},
        {"key": "map_url", "label": "플레이스 URL 연결", "done": has(getattr(t, "map_url", "")),
         "why": "블로그 글→플레이스 유도(저장·예약·전화)", "how": "가게 설정에 네이버 플레이스 URL 입력"},
        {"key": "photos", "label": "사진 20장+ (외부·내부·메뉴)", "done": None,
         "why": "사진 수·품질이 클릭률에 직결", "how": "스마트플레이스 > 사진 관리에서 직접 확인"},
        {"key": "menu", "label": "메뉴/가격 등록", "done": None,
         "why": "'가격' 검색 의도에 매칭", "how": "스마트플레이스 > 메뉴 관리"},
        {"key": "news", "label": "소식 주 1회+", "done": bool(db.list_place_news(t.id, 1)),
         "why": "최신 활동 신호(방치된 플레이스와 차별화)", "how": "올린다 '플레이스 소식 생성' 버튼 활용"},
        {"key": "reviews", "label": "방문자 리뷰 꾸준히", "done": None,
         "why": "리뷰 수 + 최신성 = 플레이스 순위 핵심", "how": "아래 리뷰 요청 키트로 실제 손님에게 요청"},
    ]
    return items


def place_summary(tenant) -> dict:
    """플레이스 카드 데이터 — 체크리스트 + 리뷰 키트 + 플레이스 순위 요약."""
    checklist = place_checklist(tenant)
    known = [i for i in checklist if i["done"] is not None]
    done = sum(1 for i in known if i["done"])
    ranks = []
    for kw in db.tracked_keywords(tenant.id, limit=5):
        hist = [h for h in db.rank_history(tenant.id, kw, kind="place") if h.get("rank") is not None]
        if hist:
            ranks.append({"keyword": kw, "rank": hist[-1]["rank"],
                          "prev": (hist[-2]["rank"] if len(hist) >= 2 else None)})
    return {"checklist": checklist, "done": done, "known": len(known),
            "reviews": review_request_texts(tenant), "place_ranks": ranks}
