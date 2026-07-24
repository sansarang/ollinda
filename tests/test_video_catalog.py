"""영상 파이프라인 구조 박제 — 사진 복원 or버그·크레딧 고갈 분기·해시 캐시·콘티 계약."""
import json
import pathlib

_BASE = pathlib.Path(__file__).parent.parent


def test_no_disk_or_restore_bug():
    """★ or버그 박제: '[디스크 존재분] or _restore_media'는 로컬 일부 존재 시 R2 잔여를 안 불러온다.
    엔드포인트는 _restore_media(디스크+R2 합침)를 직접 써야 한다 — 버그 패턴 재발 시 실패."""
    src = (_BASE / "app" / "main.py").read_text(encoding="utf-8")
    assert "os.path.exists(x)] or _restore_media" not in src, "or버그 재발 — 로컬 일부 존재 시 R2 누락"
    assert "os.path.exists(x) or _restore_media" not in src


def test_catalog_credit_and_cache_wired():
    """크레딧 고갈 분기 + 사진 해시 캐시가 vision에 배선 — 조용한 실패·재분석 재발 방지."""
    vsrc = (_BASE / "app" / "vision.py").read_text(encoding="utf-8")
    assert "_CATALOG_CREDIT_EXHAUSTED" in vsrc, "크레딧 고갈 분기 없음"
    assert "get_catalog_cache" in vsrc and "save_catalog_cache" in vsrc, "사진 해시 캐시 미배선(재분석 0 안 됨)"
    msrc = (_BASE / "app" / "main.py").read_text(encoding="utf-8")
    assert "vision_credit" in msrc, "엔드포인트 크레딧 안내 미배선(조용한 실패)"


def test_render_contract_valid():
    """콘티 계약(render_v1) 유효 JSON + 필수 구조(scenes·role·shot oneOf)."""
    c = json.loads((_BASE / "contract" / "render_v1.json").read_text(encoding="utf-8"))
    assert c["properties"]["version"]["const"] == "render_v1"
    assert "scenes" in c["properties"] and c["properties"]["scenes"]["maxItems"] == 12


def test_storyboard_adapter_reuses_assets_no_new_render():
    """★ 어댑터 박제: render_storyboard는 기존 렌더 자산만 호출(새 ffmpeg 로직 발명 금지) +
    콘티 crop→zoompan 파라미터화 + 디스크 하한 게이트 경유."""
    vsrc = (_BASE / "app" / "generators" / "video.py").read_text(encoding="utf-8")
    assert "def render_storyboard" in vsrc, "어댑터 메서드 없음"
    seg = vsrc.split("def render_storyboard", 1)[1].split("\n    def _clamp", 1)[0]
    # 기존 자산 재사용 — 어댑터 내부는 이 헬퍼들만 호출(새 렌더 파이프 발명 없음)
    for helper in ("_scene_video", "_scene_card_video", "_data_card_png", "_audio_segment",
                   "_concat_xfade", "_concat", "_post_overlay", "_mux"):
        assert helper in seg, f"어댑터가 기존 자산 {helper} 미사용"
    assert "_RENDER_FLOOR_MB" in seg, "어댑터 디스크 하한 게이트 없음"
    assert "crop=crop" in seg, "콘티 crop 힌트→zoompan 미전달"


def test_render_storyboard_endpoint_queue_and_fallback():
    """어댑터 엔드포인트: 콘티 없으면 blocked(기존 경로 폴백) + 렌더 큐(RENDER_SEM) 경유."""
    msrc = (_BASE / "app" / "main.py").read_text(encoding="utf-8")
    assert "def admin_render_storyboard" in msrc, "render-storyboard 엔드포인트 없음"
    seg = msrc.split("def admin_render_storyboard", 1)[1].split("\n@app.", 1)[0]
    assert "no_storyboard" in seg, "콘티 없을 때 폴백 분기 없음"
    assert "RENDER_SEM" in seg, "렌더 큐 미경유"
    assert "paths[c[\"id\"] - 1]" in seg, "catalog id→사진 경로 매핑 없음"
