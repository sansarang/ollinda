"""저장소 정리가 사진을 지우지 않는다 — 2026-08-12 실사고 박제.

사고: /admin/cleanup의 '최근 40개만 유지'가 파일 종류·나이를 안 봐서, 방금 업로드한 사진이
영상 몇 개에 밀려 즉시 삭제됨. 그 결과 미리보기가 매번 R2에서 원본을 받아와 느려지고
파생본(썸·웹)도 못 만들었다. 사진은 파생본의 원재료 → 정리 대상에서 제외한다.
"""
import inspect


def _src():
    from app import main
    return inspect.getsource(main.admin_cleanup)


def test_cleanup_never_targets_photos():
    src = _src()
    assert "HEAVY" in src, "정리 대상 한정(HEAVY) 사라짐 — 사진 삭제 회귀 위험"
    assert "allf.append" in src
    # 삭제 후보 수집이 '무거운 확장자' 조건 아래에 있어야 한다
    i_heavy = src.index("HEAVY = (")
    i_append = src.index("allf.append")
    assert i_heavy < i_append, "삭제 후보 수집이 종류 필터보다 앞섬"
    guard = src[max(0, i_append - 260):i_append]
    assert "endswith(HEAVY)" in guard, "사진까지 삭제 후보에 담김(사고 재발)"
    assert "KEEP_HOURS" in guard or "KEEP_HOURS" in src[:i_append], "최근 파일 보호(KEEP_HOURS) 없음"


def test_cleanup_keeps_recent_files_regardless_of_type():
    src = _src()
    assert "KEEP_HOURS = 24" in src, "최근 24시간 파일 보호가 사라짐 — 방금 만든 산출물 삭제 위험"
