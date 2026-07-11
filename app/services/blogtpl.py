"""
업종별 블로그 템플릿(블로그템플릿 PHASE 2) — 매장형/셀러형 구조 + 고정정보 블록.

네이버 스마트에디터3.0은 텍스트·사진·지도를 '컴포넌트'로 다룬다:
- 지도·장소는 텍스트로 박지 않고 네이버 '장소' 컴포넌트로 넣어야 플레이스 연결·지역SEO에 유리
  → 본문에는 [여기 네이버 지도 넣기] 마커만 넣고, 발행 화면에서 삽입 가이드 제공(PHASE 3).
- 연락처·영업시간·주차는 텍스트로 충분(네이버가 전화번호를 자동 링크).
- 이미지는 붙여넣기 대신 파일 업로드가 안전(기존 [사진N] 마커 방식 유지).
"""
from __future__ import annotations

MAP_MARKER = "[여기 네이버 지도 넣기]"

# 템플릿 시퀀스 — 생성 프롬프트에 주입해 글 구조를 고정(기존 D.I.A.+ 지시문과 결합)
LOCAL_SEQUENCE = (
    "[글 구조 템플릿 — 매장형(순서 고정)] "
    "① 제목(핵심키워드 맨앞) ② 도입: PAS(문제 공감→불편 확대→해결 예고) 3~4문장 "
    "③ [사진1] ④ 본문: 1인칭 실경험(과정·디테일·수치) + ## 소제목 "
    "⑤ 중간 [사진N] 배치 ⑥ ## 자주 묻는 질문(Q&A 3쌍) "
    "⑦ 마무리 직전: 가게 고정정보 블록(주소·전화·영업시간·주차)은 시스템이 자동 삽입하니 "
    "본문에 따로 쓰지 마라(중복 금지) ⑧ 해시태그. "
    "지도·위치 링크도 본문에 쓰지 마라 — 발행 시 네이버 장소 컴포넌트로 넣는다.")

SELLER_SEQUENCE = (
    "[글 구조 템플릿 — 셀러형(순서 고정)] "
    "① 제목(상품 핵심키워드 맨앞) ② 도입: 구매 전 고민 공감 3~4문장 "
    "③ [사진1](상품) ④ 후기 본문: 직접 써본 경험(장점+아쉬운 점 솔직하게) + ## 소제목 "
    "⑤ 중간 [사진N] ⑥ ## 자주 묻는 질문(Q&A 3쌍) "
    "⑦ 마무리 직전: 구매 안내 블록은 시스템이 자동 삽입하니 본문에 링크를 따로 쓰지 마라 "
    "⑧ 해시태그.")


def sequence_directive(biz_type: str) -> str:
    """사업형태 → 템플릿 시퀀스 지시문(기존 자동분기 biz_type 재사용)."""
    return SELLER_SEQUENCE if (biz_type or "local") == "seller" else LOCAL_SEQUENCE


def fixed_info_block(tenant) -> str:
    """매장 고정정보 블록(글 마무리 자동 삽입) — PHASE 1 매장정보 재사용.
    지도는 텍스트 URL 대신 MAP_MARKER(발행 시 장소 컴포넌트로 교체 안내)."""
    name = getattr(tenant, "name", "") or ""
    lines = ["📍 찾아오는 길 · 이용 안내"]
    if (getattr(tenant, "address", "") or "").strip():
        lines.append(f"주소: {tenant.address.strip()}")
    if (getattr(tenant, "phone", "") or "").strip():
        lines.append(f"전화: {tenant.phone.strip()}")           # 네이버가 자동으로 tel: 링크 처리
    if (getattr(tenant, "hours", "") or "").strip():
        lines.append(f"영업시간: {tenant.hours.strip()}")
    if (getattr(tenant, "parking", "") or "").strip():
        lines.append(f"주차: {tenant.parking.strip()}")
    lines.append("")
    lines.append(MAP_MARKER)
    lines.append("")
    if name:
        lines.append(f"네이버에서 '{name}' 검색 → 플레이스에서 저장·리뷰·예약·전화 ⭐")
    return "\n".join(lines)


def seller_buy_block(tenant) -> str:
    """셀러 구매 블록 — strategies.buy_block 재사용(없으면 자체 조립)."""
    try:
        from app.strategies import buy_block
        b = buy_block(tenant)
        if b:
            return "🛒 구매 안내\n" + b
    except Exception:
        pass
    parts = ["🛒 구매 안내"]
    if (getattr(tenant, "buy_url", "") or "").strip():
        parts.append(f"구매 링크: {tenant.buy_url.strip()}")
    if (getattr(tenant, "search_kw", "") or "").strip():
        mk = {"coupang": "쿠팡", "smartstore": "스마트스토어", "11st": "11번가",
              "gmarket": "지마켓"}.get(getattr(tenant, "marketplace", "") or "", "마켓")
        parts.append(f"{mk}에서 '{tenant.search_kw.strip()}' 검색")
    return "\n".join(parts) if len(parts) > 1 else ""


def closing_block(tenant) -> str:
    """사업형태별 마무리 블록 — 매장형=고정정보 / 셀러형=구매 / 하이브리드=둘 다."""
    bt = getattr(tenant, "biz_type", "local") or "local"
    if bt == "seller":
        return seller_buy_block(tenant)
    if bt == "hybrid":
        buy = seller_buy_block(tenant)
        return fixed_info_block(tenant) + (("\n\n" + buy) if buy else "")
    return fixed_info_block(tenant)
