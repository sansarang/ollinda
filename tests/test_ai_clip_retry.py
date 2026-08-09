"""Veo 클립 QC 재추첨 골든 — 2026-08-09 '클립 수준' 지시 박제.

사고: QC 탈락 즉시 영구 차단(.bad) — 비결정적 생성기에 1회 추첨이라, 실측에서 한 세트
시도 전건 탈락 → 무빙 0(슬라이드쇼·AI 티). 계약: ① 탈락 시 예산 내 1회 재추첨
② 재추첨 성공분은 정상 캐시 ③ 최종 탈락 후에만 .bad(재과금 방지 불변)
④ 생성 실패(쿼터)는 재추첨하지 않음 ⑤ 기존 .bad는 계속 존중.
"""
import os
import shutil
import uuid

import pytest

from app.media import ai_clip


@pytest.fixture()
def photo(tmp_path, monkeypatch):
    monkeypatch.setenv("VEO_CLIP", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("VEO_QC_RETRY", "1")
    p = tmp_path / "shot.jpg"
    from PIL import Image
    Image.new("RGB", (64, 64), (10, 20, 30)).save(p)
    monkeypatch.setattr(ai_clip, "_text_risk", lambda img: False)   # 필터는 전용 테스트에서만
    return str(p)


def _fake_generate(results):
    """호출마다 결과 지정: 'ok'=파일 생성, None=생성 실패."""
    calls = []
    def gen(img, out):
        calls.append(1)
        r = results[min(len(calls) - 1, len(results) - 1)]
        if r == "ok":
            open(out, "wb").write(b"clip")
            return out
        return None
    return gen, calls


def test_qc_fail_then_pass_on_retry(photo, monkeypatch):
    gen, calls = _fake_generate(["ok", "ok"])
    verdicts = iter([False, True])
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_qc", lambda clip, img: next(verdicts))
    b = ai_clip.ClipBudget(max_new=4)
    out = b.get(photo)
    assert out and out.endswith(".veoclip.mp4") and os.path.exists(out), "재추첨 성공분이 캐시돼야"
    assert len(calls) == 2 and b.stats()["qc_fail"] == 1 and b.stats()["used"] == 1
    assert not os.path.exists(out.replace(".mp4", ".bad")), "성공했는데 차단 마커가 남음"


def test_qc_fail_twice_marks_bad_and_blocks(photo, monkeypatch):
    gen, calls = _fake_generate(["ok", "ok"])
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_qc", lambda clip, img: False)
    b = ai_clip.ClipBudget(max_new=4)
    assert b.get(photo) is None
    assert len(calls) == 2, "재추첨 1회까지만"
    bad = os.path.join(os.path.dirname(photo), "shot.veoclip.bad")
    assert os.path.exists(bad), "최종 탈락 후 영구 차단 마커(재과금 방지)"
    # 이후 호출은 생성 시도 자체가 없어야
    assert b.get(photo) is None and len(calls) == 2


def test_generation_failure_no_retry(photo, monkeypatch):
    gen, calls = _fake_generate([None])
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_qc", lambda clip, img: True)
    b = ai_clip.ClipBudget(max_new=4)
    assert b.get(photo) is None
    assert len(calls) == 1, "쿼터성 생성 실패는 재추첨 금지(비용 보호)"


def test_budget_caps_retries(photo, monkeypatch):
    gen, calls = _fake_generate(["ok", "ok"])
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_qc", lambda clip, img: False)
    b = ai_clip.ClipBudget(max_new=1)          # 예산 1 — 재추첨 불가
    assert b.get(photo) is None
    assert len(calls) == 1, "예산을 넘겨 재추첨하면 안 된다"


def test_text_risk_skips_before_paying(photo, monkeypatch):
    """글자 사진은 생성 시도(과금) 자체가 없어야 — 2026-08-09 비용 절감의 핵심 계약."""
    gen, calls = _fake_generate(["ok"])
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_text_risk", lambda img: True)
    b = ai_clip.ClipBudget(max_new=4)
    assert b.get(photo) is None
    assert len(calls) == 0, "글자 감지됐는데 생성 호출(과금)이 나갔다"
    assert b.stats()["skipped"] == 1
    assert not os.path.exists(os.path.join(os.path.dirname(photo), "shot.veoclip.bad")), \
        "사전 생략은 영구 차단이 아니다(필터 완화 여지)"


def test_veo_cost_metering(photo, monkeypatch):
    """비용 계측(2026-08-09 승인) — 생성 성공=과금(QC 무관), 캐시·생략=0. 세트 api_cost 구멍 봉합."""
    monkeypatch.setenv("VEO_USD_PER_SEC", "0.10")
    gen, calls = _fake_generate(["ok", "ok"])
    verdicts = iter([False, True])                 # 1차 QC 탈락(과금됨) → 2차 통과
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_qc", lambda clip, img: next(verdicts))
    b = ai_clip.ClipBudget(max_new=4)
    assert b.get(photo)
    expected = 2 * ai_clip.DUR_SEC * 0.10          # 두 번 생성 = 두 번 과금(탈락분 포함 — 정직)
    assert abs(b.stats()["usd"] - expected) < 1e-9
    assert b.get(photo) and abs(b.stats()["usd"] - expected) < 1e-9, "캐시 히트는 과금 0"


def test_tts_cost_metering(monkeypatch):
    from app.media import tts
    monkeypatch.setenv("ELEVEN_USD_PER_1K_CHARS", "0.30")
    tts.cost_reset()
    tts._cost_add(500)
    tts._cost_add(500)
    usd, chars = tts.cost_take()
    assert abs(usd - 0.30) < 1e-9 and chars == 1000
    assert tts.cost_take() == (0.0, 0), "take 후 리셋"


def test_render_cost_accumulator():
    from app.generators import video
    video.render_cost_take()                       # 초기화
    video._render_cost_add(0.36, 1, 0.15, 500)
    video._render_cost_add(0.36, 1, 0.0, 0)
    rc = video.render_cost_take()
    assert abs(rc["usd"] - 0.87) < 1e-9 and rc["veo_new"] == 2 and rc["tts_chars"] == 500
    assert video.render_cost_take()["usd"] == 0.0, "번들 단위 리셋"


def test_cache_survives_reedit_and_migrates_legacy(photo, monkeypatch):
    """캐시 키 = 파일명 스템 — 재보정으로 픽셀이 바뀌어도 캐시 히트(재과금 0). 구 해시 키는 이관."""
    monkeypatch.setattr(ai_clip, "_text_risk", lambda img: False)
    gen, calls = _fake_generate(["ok"])
    monkeypatch.setattr(ai_clip, "_generate", gen)
    monkeypatch.setattr(ai_clip, "_qc", lambda clip, img: True)
    b = ai_clip.ClipBudget(max_new=4)
    out = b.get(photo)
    assert out and os.path.basename(out) == "shot.veoclip.mp4"
    # 재보정 시뮬레이션 — 같은 파일명, 다른 픽셀
    from PIL import Image
    Image.new("RGB", (64, 64), (200, 100, 50)).save(photo)
    assert b.get(photo) == out and len(calls) == 1, "재보정 후 캐시 미스(재과금) 회귀"
    # 구 해시 키 캐시 이관
    d = os.path.dirname(photo)
    legacy_photo = os.path.join(d, "old.jpg")
    Image.new("RGB", (64, 64), (1, 2, 3)).save(legacy_photo)
    h = ai_clip._content_hash(legacy_photo)
    open(os.path.join(d, f"{h}.veoclip.mp4"), "wb").write(b"legacy")
    out2 = ai_clip.ClipBudget(max_new=0).get(legacy_photo)   # 예산 0 — 캐시로만 응답 가능
    assert out2 and os.path.basename(out2) == "old.veoclip.mp4", "구 해시 캐시 이관 실패(재과금)"
