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


def test_visit_type_industry_not_filtered():
    """속성 앵커 축이 없는(또는 무의미한) 업종은 필터 무적용 — 업종 중립(방문형 기존 흐름 유지)."""
    # 존재하지 않는 업종 → 스키마 기본(속성 예시 토큰 비어있음) → 전부 통과
    kept, dropped = seo.drop_phantom_attr_kws(
        ["부산 카페 추천", "아무거나 키워드"], "존재안함업종xyz", "local", context_text="", inventory_models=[])
    assert dropped == []
    assert len(kept) == 2
