"""유령 키워드 박제 — searchad 주입/스키마 시드 토큰이 '없는 매물' 키워드로 새는 것 차단.
캐스퍼·레이(스키마 시드, 재고 아님)=제거 / 모닝·그랜저(재고)=통과(오탐 0) / 제네릭=통과 / 방문형=미적용."""
import app.seo as seo


def test_phantom_casper_dropped():
    """그랜저·모닝 딜러에 '캐스퍼중고가격'(searchad 주입) — 재고·note에 없으니 제거."""
    kept, dropped = seo.drop_phantom_attr_kws(
        ["캐스퍼중고가격", "중고차판매 추천", "그랜저중고", "모닝중고"],
        "중고차", "seller", context_text="현대 더 뉴 그랜저 IG 매물", inventory_models=["그랜저", "모닝"])
    assert "캐스퍼중고가격" not in kept
    assert any("캐스퍼" in str(d) for d in dropped)
    assert "그랜저중고" in kept and "모닝중고" in kept        # 재고 모델 = 오탐 0 통과
    assert "중고차판매 추천" in kept                          # 제네릭 통과


def test_phantom_rey_dropped_moning_kept():
    """레이(스키마 시드·재고 아님)=제거, 모닝(재고)=통과 — 오탐 0."""
    kept, dropped = seo.drop_phantom_attr_kws(
        ["레이 중고", "모닝 중고", "부산 기장 중고차"],
        "중고차", "seller", context_text="매물 실사진 세트", inventory_models=["모닝"])
    assert "레이 중고" not in kept
    assert "모닝 중고" in kept
    assert "부산 기장 중고차" in kept                         # 기장(지역)은 속성축 아님 → 통과


def test_bare_note_drops_all_phantom_models():
    """앵커 없는 bare note + 재고 없음 → 모든 모델특정 유령 제거, 제네릭만 남음."""
    kept, dropped = seo.drop_phantom_attr_kws(
        ["캐스퍼중고가격", "레이중고", "중고차 추천"],
        "중고차", "seller", context_text="매물 실사진 세트", inventory_models=[])
    assert kept == ["중고차 추천"]
    assert len(dropped) == 2


def test_field_sweep_seeds_removed_reals_kept():
    """씨앗 세척(phantom-sweep) 로직 — target_keywords 필드에서 유령만 제거, 실재고·제네릭·지역 무변경.
    컨텍스트=note+재고(제목/키워드 자신 제외). 4형제 상주 감시(레이·기장·모닝·캐스퍼)."""
    field = ["캐스퍼중고가격", "그랜저중고", "모닝 중고", "부산 기장 중고차판매", "중고차판매 추천", "레이중고"]
    kept, dropped = seo.drop_phantom_attr_kws(
        field, "중고차", "seller",
        context_text="[사진 분석] 현대 더 뉴 그랜저 흰색 세단 매물",   # note=그랜저(재고엔 모닝도)
        inventory_models=["그랜저", "모닝"])
    removed = [d[0] for d in dropped]
    assert "캐스퍼중고가격" in removed and "레이중고" in removed        # 유령(재고·note 없음) 제거
    assert "그랜저중고" in kept and "모닝 중고" in kept                # 실재고 유지(오탐 0)
    assert "부산 기장 중고차판매" in kept and "중고차판매 추천" in kept  # 지역·제네릭 유지(기장=지역, 속성축 아님)


def test_all_piece_types_have_generator():
    """구조 보증(#4): 전 피스 타입에 생성기 존재 → 공통 재생성 경로(_regen_piece_common)가 어떤 타입도
    누락 안 함. 'regen-blog·naver 따로 만들다 SHORT 누락'한 계보의 구조적 재발 방지 — 새 타입 추가 시
    생성기 없으면 이 테스트가 실패해 공통 경로 미지원을 잡는다."""
    from app.registry import GENERATORS
    from app.domain.models import ContentKind
    for k in (ContentKind.BLOG, ContentKind.SHORT, ContentKind.CAPTION,
              ContentKind.X_POST, ContentKind.MARKETPLACE):
        assert k in GENERATORS, f"{k} 생성기 누락 — 공통 재생성 경로가 이 타입을 못 다룸(피스 타입 누락 재발)"


def test_all_generators_route_through_shared_keyword():
    """구조 보증(#4): 키워드를 자체 결정하는 전 생성기가 seo.resolve_target_keyword(공유 관문)를 경유.
    생성기별 raw seo.target_keywords 직결로 키워드를 정하던 계보(SHORT 캐스퍼) 재발 방지 —
    새 생성기가 공유 관문을 안 거치면 이 테스트가 실패한다."""
    import pathlib
    base = pathlib.Path(__file__).parent.parent / "app"
    # 키워드 결정 생성기 파일 → 공유 관문 호출 필수
    for rel in ("generators/text_claude.py", "generators/video.py",
                "generators/x_text.py", "generators/strategist.py"):
        src = (base / rel).read_text(encoding="utf-8")
        assert "resolve_target_keyword" in src, f"{rel}: 공유 키워드 관문(resolve_target_keyword) 미경유"


def test_resolve_keyword_drops_phantom_keeps_anchor():
    """공유 관문 자체가 phantom 차단 + 앵커 확정 — 그랜저 note→그랜저, bare note→제네릭(캐스퍼 미유입)."""
    import app.seo as seo
    kw_g, _ = seo.resolve_target_keyword(industry="중고차", region="부산 기장",
                                         note="현대 더 뉴 그랜저 IG 매물", biz="seller",
                                         content_type="sell", keyword_axis="product",
                                         prof_name="중고차판매", verify_volume=False)
    assert "캐스퍼" not in kw_g and "그랜저" in kw_g
    kw_b, _ = seo.resolve_target_keyword(industry="중고차", region="부산 기장",
                                         note="매물 실사진 세트", biz="seller",
                                         content_type="sell", keyword_axis="product",
                                         prof_name="중고차판매", verify_volume=False)
    assert "캐스퍼" not in kw_b            # 앵커 부재 → 제네릭, 인기 타모델 미납치


def test_visit_type_industry_not_filtered():
    """속성 앵커 축이 없는(또는 무의미한) 업종은 필터 무적용 — 업종 중립(방문형 기존 흐름 유지)."""
    # 존재하지 않는 업종 → 스키마 기본(속성 예시 토큰 비어있음) → 전부 통과
    kept, dropped = seo.drop_phantom_attr_kws(
        ["부산 카페 추천", "아무거나 키워드"], "존재안함업종xyz", "local", context_text="", inventory_models=[])
    assert dropped == []
    assert len(kept) == 2
