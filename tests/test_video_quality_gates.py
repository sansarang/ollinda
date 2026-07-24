"""영상 품질 게이트 박제 — VG3(가격 의미)·VG4(크롭 증거)·전환 프로파일 통일."""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")
from app.generators import video as v  # noqa: E402


# ── VG3: 가격 날조(서류 출고가 → 판매가 승격) 차단 ──
def test_sale_price_rejects_acquisition_value():
    """등록증 출고가는 판매가로 인식하지 않는다(딜러 명시 판매가만)."""
    assert v._resolve_sale_price("자동차 출고(취득)가격(부가세 제외): 30,401,818 원", "") == ""
    assert v._resolve_sale_price("판매가 2,900만원", "") == "2,900만원"
    # 본문은 판매 문맥에서만
    assert v._resolve_sale_price("", "출고가 30,401,818원") == ""
    assert v._resolve_sale_price("", "판매가 2,900만원에 내놓은 매물") == "2,900만원"


def test_price_semantics_violation():
    """판매가와 다른 라벨없는 가격 = 위반 / 판매가일치·항목라벨 = 허용."""
    assert v._price_semantics_violation("차량 가격 30,401,818원", "2900만원")   # 위반
    assert v._price_semantics_violation("이 차 3,040만원", "")                  # 판매가 미명시+라벨없음=위반
    assert not v._price_semantics_violation("2,900만원 특가", "2900만원")       # 일치
    assert not v._price_semantics_violation("신차 출고가 3,040만원", "2900만원")  # 항목라벨=허용


def test_extract_data_points_price_only_from_sale_price():
    """_extract_data_points는 명시 판매가만 가격으로 — 본문 임의 가격 승격 금지."""
    sch = {"attribute_axes": [{"axis": "주행거리"}, {"axis": "가격"}]}
    body = "자동차 출고(취득)가격: 30,401,818 원, 주행거리 12,272km"
    # 판매가 미제공 → 가격 카드 없음(30,401,818 승격 금지)
    pts = v.ShortVideoGenerator._extract_data_points(body, body, sch, "seller", sale_price="")
    assert all("30,401,818" not in val for val, lab in pts), "출고가가 데이터카드로 승격됨"
    assert not any(lab == "판매가" for _, lab in pts)
    # 판매가 제공 → 그 값만 판매가 카드
    pts2 = v.ShortVideoGenerator._extract_data_points(body, body, sch, "seller", sale_price="2,900만원")
    assert ("2,900만원", "판매가") in pts2
    assert all("30,401,818" not in val for val, lab in pts2)


# ── VG4: 크롭 후 증거 소실 방지 ──
def test_evidence_ref_detects_number_claims():
    assert v._EVIDENCE_REF.search("성능부 12,269km ↔ 계기판 12,272km 일치")
    assert v._EVIDENCE_REF.search("주행거리 12272km 확인해 보세요")
    assert not v._EVIDENCE_REF.search("베이지 가죽이 넓고 밝습니다")


# ── 전환 버퍼링: concat이 입력 프로파일을 통일하는지(settb·fps·CFR) ──
def test_concat_xfade_normalizes_profile():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "generators", "video.py"),
              encoding="utf-8").read()
    seg = src.split("def _concat_xfade", 1)[1].split("def _post_overlay", 1)[0]
    assert "settb=AVTB" in seg and "fps_mode" in seg, "전환 프로파일 통일(타임베이스·CFR) 누락"


def test_render_storyboard_has_sale_price_param():
    import inspect
    sig = inspect.signature(v.ShortVideoGenerator.render_storyboard)
    assert "sale_price" in sig.parameters, "render_storyboard가 VG3 판매가 기준을 안 받음"
