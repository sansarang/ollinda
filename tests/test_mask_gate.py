"""오폭 방지 박제 — 좌표 신뢰도 게이트(미달 스킵+로그) + 길이 예산 콘티 게이트."""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")


def test_confidence_gate_skips_low_conf_pii(tmp_path, monkeypatch):
    """★ 신뢰도 미달 PII 박스는 모자이크 안 함(정상 차체 오폭 방지) + 로그에 processed=False."""
    from PIL import Image
    from app.media import photo_boost as pb
    p = str(tmp_path / "car.jpg")
    Image.new("RGB", (400, 300), (120, 130, 140)).save(p, quality=90)
    # vision이 '정상 차체'를 저신뢰(0.3)로 개인정보라 오탐한 상황
    monkeypatch.setattr("app.vision.detect_personal_info",
                        lambda path: [{"type": "label", "x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.3, "conf": 0.3}])
    pb._MASK_LAST_LOG = []
    cnt = pb.mask_personal_info(p)
    assert cnt == 0, "저신뢰 박스가 처리됨 — 오폭 방지 실패"
    assert pb._MASK_LAST_LOG and pb._MASK_LAST_LOG[-1]["processed"] is False
    assert "conf<" in pb._MASK_LAST_LOG[-1]["reason"]


def test_confidence_gate_processes_high_conf_pii(tmp_path, monkeypatch):
    """고신뢰(확실한 번호판) 박스는 정상 모자이크."""
    from PIL import Image
    from app.media import photo_boost as pb
    p = str(tmp_path / "plate.jpg")
    Image.new("RGB", (400, 300), (120, 130, 140)).save(p, quality=90)
    monkeypatch.setattr("app.vision.detect_personal_info",
                        lambda path: [{"type": "plate", "x0": 0.3, "y0": 0.6, "x1": 0.7, "y1": 0.8, "conf": 0.95}])
    pb._MASK_LAST_LOG = []
    cnt = pb.mask_personal_info(p)
    assert cnt == 1, "고신뢰 번호판이 처리 안 됨"
    assert pb._MASK_LAST_LOG[-1]["processed"] is True


def test_gate_thresholds_exist():
    from app.media import photo_boost as pb
    assert 0 < pb.PII_CONF_MIN <= 1 and 0 < pb.OVERLAY_CONF_MIN <= 1


def test_director_budget_gate():
    """채널 예산이 콘티 검증 조건 — 초과 추정치는 예산 밖(반려 대상)."""
    from app.services import director as d
    for ch in ("naver", "shorts", "reels"):
        sp = d._CHANNEL_SPEC[ch]
        assert "dmin" in sp and "dmax" in sp and sp["dmin"] < sp["dmax"]
    # 10씬×40자 ≈ 74s → 네이버 예산(30~60) 초과여야(반려)
    over = d.estimate_duration([{"line": "가" * 40}] * 10)
    assert over > d._CHANNEL_SPEC["naver"]["dmax"], "과길이 콘티가 예산 내로 오판정"
    # 6씬×35자 ≈ 예산 내
    ok = d.estimate_duration([{"line": "가" * 35}] * 6)
    assert d._CHANNEL_SPEC["naver"]["dmin"] <= ok <= d._CHANNEL_SPEC["naver"]["dmax"]


def test_vision_detectors_emit_conf():
    """검출기 프롬프트에 conf 스키마 명시 — 신뢰도 게이트의 입력원."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "vision.py"), encoding="utf-8").read()
    assert '"conf"' in src and "detect_personal_info" in src


def test_mask_trace_endpoint_wired():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py"), encoding="utf-8").read()
    assert "def admin_mask_trace" in src and "would_process" in src and "attached_warning" in src
