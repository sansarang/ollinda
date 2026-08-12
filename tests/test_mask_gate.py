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


def test_doc_id_patterns_strict():
    """strict 문서 식별번호 정규식 — 번호판(한글 필수)·VIN·문서번호 잡고, 주행거리·날짜·제원 과매칭 0."""
    import re
    from app import vision as v
    pats = v._DOC_ID_PATTERNS
    def hits(s): return [n for n, p in pats for _ in re.finditer(p, s)]
    assert "plate" in hits("370다4358")               # 한글 중간자
    assert "vin" in hits("KMHF141DBNA491923")
    assert "docno" in hits("98-90-061766")
    # ★ 과매칭 0: 한글 없는 숫자열(주행거리·날짜·제원)은 번호판으로 안 잡힘(트러스트 보존)
    assert "plate" not in hits("3704358")             # 한글 없으면 번호판 아님
    assert hits("12269") == [] and hits("12,269") == []
    assert hits("2022-04-11") == [] and hits("2497") == []


def test_detect_document_pii_graceful_without_tesseract(monkeypatch):
    """tesseract 없으면 [](배포 전 graceful — 크래시 금지)."""
    from app import vision as v
    monkeypatch.setattr("shutil.which", lambda x: None)
    assert v.detect_document_pii("/nonexistent.jpg") == []


def test_second_pass_log_levels(monkeypatch, caplog):
    """2차 PII 패스 로그 레벨 계약(2026-08-12 Sentry 오탐 사고 박제):
    ① 성공 보정(n>0)은 warning — error로 찍으면 Sentry가 장애로 승격해 진짜 장애가 묻힌다.
    ② 2차 마스킹 '실패'는 error — 개인정보가 안 가려졌을 수 있는 진짜 위험(침묵 금지)."""
    import logging
    import inspect
    from app.services import ingest

    src = inspect.getsource(ingest._spawn_photo_edit) if hasattr(ingest, "_spawn_photo_edit") else ""
    if not src:                       # 함수명이 바뀌면 모듈 전체에서 확인
        src = inspect.getsource(ingest)
    # 성공 경로는 warning
    assert "2차 패스에서 PII %d건 추가 마스킹" in src
    i_ok = src.index("2차 패스에서 PII %d건 추가 마스킹")
    assert ".warning(" in src[max(0, i_ok - 300):i_ok], "성공 보정이 error로 찍힘 — Sentry 오탐 회귀"
    # 실패 경로는 error + 침묵(pass) 금지
    assert "2차 PII 마스킹 실패" in src, "2차 마스킹 실패를 삼키고 있음(개인정보 미가림 침묵)"
    i_fail = src.index("2차 PII 마스킹 실패")
    assert ".error(" in src[max(0, i_fail - 300):i_fail], "마스킹 실패가 error로 안 올라감"
