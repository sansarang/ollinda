"""B: 씬-자막 매칭 — 지시어 강제 대조 + 고아 지시어 씬 삭제(업종 하드코딩 0)."""
import app.generators.video as v


GS = ("[사진1] 레이 차량 전면부와 그릴\n"
      "[사진2] 계기판과 주행거리 표시\n"
      "[사진3] 실내 시트와 대시보드")
IMGS = ["a.jpg", "b.jpg", "c.jpg"]


def test_referent_hard_match():
    """지시어가 vision 묘사에 있으면 그 사진으로 강제 배정(모순 방지)."""
    out = v._match_photos(["주행거리 확인하세요"], IMGS, GS, "t")
    assert out[0] == "b.jpg"  # 주행거리→계기판 사진(#2), 전면샷 아님


def test_used_referent_scene_dropped():
    """지시어(주행거리)가 한 사진에만 있고 그 사진이 앞 씬에 배정되면, 뒤 씬은 일치 사진 없어 삭제."""
    drops = []
    # 주행거리는 사진2에만 → 첫 씬이 사진2 사용, 둘째 주행거리 씬은 남은 일치 사진 없음 → 삭제
    lines = ["주행거리 확인하세요", "주행거리 정보도 정확합니다"]
    out = v._match_photos(lines, IMGS, GS, "t", drops=drops)
    assert 1 in drops
    assert out[1] is None


def test_model_name_not_dropped():
    """모델명(스키마 축 토큰)은 vision 묘사에 없으므로 하드 지시어 아님 → 삭제 안 하고 순차."""
    drops = []
    out = v._match_photos(["그랜저 중고 첫 질문은"], IMGS, GS, "t", drops=drops, axis_vocab={"그랜저"})
    assert drops == []                       # 그랜저는 어느 묘사에도 없음 → 드롭 트리거 아님
    assert out[0] in IMGS


def test_no_referent_falls_back():
    """지시어 없는 일반 문장은 순차 폴백(삭제 아님)."""
    drops = []
    out = v._match_photos(["오늘 방문해 보세요"], IMGS, GS, "t", drops=drops)
    assert drops == []
    assert out[0] in IMGS


def test_drops_optin_only():
    """drops 미전달 시 지시어 불일치도 None 반환하지 않음(쇼츠·폴백 안전)."""
    out = v._match_photos(["주행거리 확인", "주행거리 상태"], IMGS, GS, "t")
    assert all(x is not None for x in out)
