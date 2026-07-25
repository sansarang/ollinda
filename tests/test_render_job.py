"""렌더-잡 직렬화 박제 — 계약 존재·직렬화 함수 배선·게이트 호출(로직 수정 0 증명 보조)."""
import json, pathlib, os
os.environ.setdefault("SHOPCAST_SECRET", "test")
_BASE = pathlib.Path(__file__).parent.parent


def test_render_job_contract_present():
    c = json.loads((_BASE / "contract" / "render_job_v1.json").read_text(encoding="utf-8"))
    assert c["properties"]["version"]["const"] == "render_job_v1"
    assert "ass_r2_key" in c["required"]
    sc = c["properties"]["scenes"]["items"]["required"]
    assert "duration_sec" in sc and "tts_audio_r2_key" in sc and "speech_text" in sc


def test_serializer_calls_existing_gates_only():
    """직렬화가 기존 게이트/해석 함수를 '호출'하는지 — 로직 재구현이 아님(참조 존재 + video 미변경 보조)."""
    from app.services import render_job as rj
    from app.generators import video as v
    src = (_BASE / "app" / "services" / "render_job.py").read_text(encoding="utf-8")
    # 기존 게이트 함수 호출 흔적(재구현 아님)
    for fn in ("_normalize_mileage", "_speechify", "_speech_number_left",
               "_price_semantics_violation", "_build_ass", "_data_card_png", "_EVIDENCE_REF"):
        assert fn in src, f"{fn} 호출 없음"
    assert hasattr(rj, "build_render_job")
    # render_storyboard는 이 모듈에서 건드리지 않는다(직렬화는 독립)
    assert "def render_storyboard" not in src


def test_render_storyboard_unchanged_signature():
    """render_storyboard 시그니처 불변(어댑터가 로직 수정 0)."""
    import inspect
    from app.generators import video as v
    p = list(inspect.signature(v.ShortVideoGenerator.render_storyboard).parameters)
    assert p[:6] == ["self", "sb", "img_by_id", "kws", "tenant", "strat"]
    assert "sale_price" in p and "mileage" in p
