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


def test_orphan_referent_dropped():
    """지시어가 어느 사진에도 없으면 씬 삭제(drops 기록·해당 위치 None)."""
    drops = []
    lines = ["깨끗한 전면", "주행거리 확인", "엔진룸 상태 최상"]
    out = v._match_photos(lines, IMGS, GS, "t", drops=drops, axis_vocab={"엔진룸"})
    assert drops == [2]
    assert out[2] is None


def test_no_referent_falls_back():
    """지시어 없는 일반 문장은 순차 폴백(삭제 아님)."""
    drops = []
    out = v._match_photos(["오늘 방문해 보세요"], IMGS, GS, "t", drops=drops)
    assert drops == []
    assert out[0] in IMGS


def test_drops_optin_only():
    """drops 미전달 시 고아 지시어도 None 반환하지 않음(쇼츠·폴백 안전)."""
    out = v._match_photos(["엔진룸 상태"], IMGS, GS, "t", axis_vocab={"엔진룸"})
    assert all(x is not None for x in out)
