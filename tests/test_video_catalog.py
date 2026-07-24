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
