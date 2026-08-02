"""
운영 계약 박제(부채 청산 3차 — 비용·상태·배포 안전).

발행 산출물 다음 순위. 전부 실사고에서 나왔고, 되돌리면 실패한다.

박제 대상(커밋 cf9a2ea, 0be5cdc, 53a1576, 5d8807e, b0f255d, a377242, cb1b98e):
  A. 데모 tenant 판정 — 모델에 없는 필드를 getattr로 읽어 항상 0이었다
  B. 배포 게이트 — 유령 잡이 영원히 배포를 막지 않는다
  C. 영상 성공 판정 — 만들어진 것을 '실패'로 기록하지 않는다
  D. 렌더 상한 — 쓰지도 않을 사진으로 시간·비용을 태우지 않는다
  E. 게이트 시간 상한 — 품질 루프가 무한히 돌지 않는다
  F. AI 무빙 QC — '불량'과 '검사 못 함'을 구분한다(무빙을 죽이지 않는다)
  G. env 값 공백 — 앞뒤 공백이 키를 400으로 만들지 않는다
"""
from __future__ import annotations

import inspect
import os
import sqlite3
import uuid


# ── A. 데모 tenant 판정 ───────────────────────────────────────────
def test_demo_flag_read_from_table_not_model():
    """A. Tenant 모델에 is_demo 필드가 없어 getattr(t, 'is_demo', 0)은 항상 0이었다.
    그래서 이관·배포게이트·집계가 전부 데모를 실계정으로 취급했다(2026-07-31 실측)."""
    from app import main as _m
    src = inspect.getsource(_m._tenant_is_demo)
    assert "SELECT is_demo FROM tenants" in src, "테이블을 직접 읽지 않음(모델 getattr 재발)"
    from app.domain.models import Tenant
    flds = getattr(Tenant, "model_fields", None) or getattr(Tenant, "__fields__", {})
    assert "is_demo" not in flds, \
        "모델에 is_demo가 생겼다 — 그렇다면 이 테스트와 _tenant_is_demo를 함께 재검토하라"


def test_demo_tenant_is_detected():
    """A2. 실제로 판정되는가 — 테이블에 심고 읽는다."""
    from app import db
    from app import main as _m
    tid = "T_DEMO_" + uuid.uuid4().hex[:8]
    try:
        with db._conn() as c:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(tenants)")]
            assert "is_demo" in cols, "tenants.is_demo 컬럼이 없다"
            c.execute(f"INSERT INTO tenants(id,{'is_demo'}) VALUES(?,1)", (tid,))
        assert _m._tenant_is_demo(tid) is True
        assert _m._tenant_is_demo("T_NOT_EXIST_" + uuid.uuid4().hex[:6]) is False
    except sqlite3.IntegrityError:                 # NOT NULL 컬럼이 더 있으면 스킵하지 않고 명시 실패
        raise
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM tenants WHERE id=?", (tid,))


# ── B. 배포 게이트 ────────────────────────────────────────────────
def test_deploy_gate_ignores_ghost_jobs():
    """B. 'running'인 채 죽은 잡이 남으면 배포가 영원히 막힌다(실측: 7/24 잔존 잡).
    다시쓰기는 10분, 영상 잡은 2시간을 넘으면 죽은 것으로 본다."""
    from app import main as _m
    src = inspect.getsource(_m.admin_busy) if hasattr(_m, "admin_busy") else ""
    if not src:                                     # 함수명이 바뀌면 모듈 전체에서 찾는다
        src = inspect.getsource(_m)
    assert "7200" in src, "영상 잡 유령 필터(2시간)가 없음"
    rw = inspect.getsource(_m._rewrite_running)
    assert "600" in rw or "10" in rw, "다시쓰기 유령 필터가 없음"


def test_deploy_gate_ignores_stale_generation():
    """B3. 생성 잡에는 유령 필터가 아예 없었다 — 2026-08-02 실측: 60%에서 991초 무갱신인데
    status는 running으로 남아 safe_to_deploy가 영원히 false였다.
    다시쓰기(10분)·영상(2시간)에는 있던 장치가 생성에만 빠져 있던 구멍."""
    from app import main as _m
    assert _m.GEN_STALE_SEC > 688, "기준이 실측 정상 소요 최대치보다 짧다(정상 생성을 죽인다)"
    src = inspect.getsource(_m)
    i = src.find('"type": "gen"')
    assert i > 0
    seg = src[max(0, i - 700):i + 700]
    assert "GEN_STALE_SEC" in seg, "생성 잡 유령 필터가 없음(배포 영구 차단 재발)"
    # 값이 없거나 깨진 시각은 '죽은 것'으로 본다 — 판정 불가로 배포가 막히면 안 된다
    assert _m._job_age("") > 1e8
    assert _m._job_age("깨진값") > 1e8
    assert _m._job_age(__import__("datetime").datetime.utcnow().isoformat()) < 5


def test_deploy_gate_skips_demo_tenant():
    """B2. 랜딩 데모는 티저가 진행률을 안 닫아 유령행이 남는다 — 배포를 막으면 안 된다."""
    from app import main as _m
    src = inspect.getsource(_m)
    i = src.find('"type": "gen"')
    assert i > 0, "생성 busy 행을 찾지 못함"
    assert "_tenant_is_demo" in src[max(0, i - 900):i], "데모 예외가 빠짐"


def test_safe_push_gate_exists_and_fails_closed():
    """B4. 같은 사고를 세 번 냈다(07-24, 07-30, 08-02) — 세 번 다 '확인하겠다'는 규율은
    있었고 세 번 다 확인하지 않았다. 규율이 세 번 실패했으면 장치가 필요하다.
    ★ fail-closed: busy 확인 자체가 실패하면 push하지 않는다(모르는 상태 = 위험한 상태)."""
    import pathlib
    sh = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "safe-push.sh"
    assert sh.exists(), "배포 전 게이트 스크립트가 없음"
    assert os.access(sh, os.X_OK), "실행 권한이 없음(장치가 아니라 문서가 된다)"
    body = sh.read_text()
    assert "/admin/busy" in body, "busy를 확인하지 않음"
    assert "curl -sf" in body and "exit 3" in body, "확인 실패 시 그대로 push하는 구조"
    assert "SHOPCAST_FORCE_PUSH" in body, "사장님 승인 시 통과할 경로가 없다(장치가 일을 막는다)"
    assert "git push" in body


def test_busy_payload_has_fields_the_gate_reads():
    """B5. 게이트가 읽는 필드가 사라지면 게이트는 조용히 '항상 통과'가 된다."""
    from app import main as _m
    src = inspect.getsource(_m)
    for f in ('"busy": busy', '"ghosts": ghosts', '"safe_to_deploy"'):
        assert f in src, f"busy 응답에서 {f} 누락 — 게이트가 눈이 먼다"


# ── C. 영상 성공 판정 ─────────────────────────────────────────────
def test_video_success_judged_per_requested_platform():
    """C. 네이버 영상이 실제로 만들어졌는데 '실패'로 기록되던 버그 —
    성공 여부를 쇼츠 파일(video_path) 하나로 판정했기 때문이다.
    요청한 플랫폼별로 각각 판정해야 한다."""
    from app.services import ingest as _ing
    src = inspect.getsource(_ing)
    i = src.find("_made = {")
    assert i > 0, "플랫폼별 성공 판정(_made)이 없음"
    seg = src[i:i + 1200]
    for ch in ("shorts", "naver", "clip"):
        assert ch in seg, f"{ch} 판정 누락"
    assert "for ch in want" in seg or "for _ch in want" in seg or "any(_made[ch] for ch in want)" in seg, \
        "요청한 플랫폼 기준으로 판정하지 않음"


# ── D. 렌더 상한 ──────────────────────────────────────────────────
def test_photo_cap_matches_scene_cap():
    """D. 씬 상한을 넘는 사진은 어차피 안 쓰인다 — 비전 호출만 태웠다(중복 조각 누적 실사고)."""
    from app.services import ingest as _ing
    assert _ing._VIDEO_MAX_PHOTOS == 9, f"사진 상한이 바뀜: {_ing._VIDEO_MAX_PHOTOS}"
    src = inspect.getsource(_ing)
    assert "len(paths) > _VIDEO_MAX_PHOTOS" in src, "상한이 실제로 적용되지 않음"


def test_photo_cap_is_told_to_the_user_not_applied_silently():
    """D2. 상한은 서버가 이미 걸고 있었는데(제작시간 때문에 9장) 화면은 17장을 고르게 두고
    '17장으로 영상 만들기'라고 적었다(2026-08-02 사장님 지적).
    지키지 않을 약속을 화면에 쓰는 것도, 조용히 잘라내는 것도 정직 게이트 위반이다."""
    from app import main as _m
    from app.services import ingest as _ing

    # ① 화면이 상한을 알 수 있어야 한다 — 서버 단일 소스에서 내려준다
    ep = inspect.getsource(_m.me_video_photos)
    assert "max_photos" in ep, "화면에 상한을 알려주지 않음"
    assert "_VIDEO_MAX_PHOTOS" in ep, "상한을 UI가 따로 정의(단일 소스 이탈 — 값이 갈라진다)"

    # ② 모달이 상한을 지키고, 이유를 말한다
    js = _m._VMPICK_JS
    assert "d.max_photos" in js, "모달이 상한을 읽지 않음"
    assert "n_sel()>=CAP" in js, "상한을 넘겨 고를 수 있음(약속을 못 지킨다)"
    assert "최대 '+CAP+'장" in js, "상한을 사용자에게 알리지 않음"
    assert "(i<CAP)" in js, "기본 선택이 상한을 넘음"
    assert "names.length<=CAP" in js, "'전부'로 보내 서버가 조용히 자르는 경로가 남음"

    # ③ 서버가 자를 때는 사유를 남긴다
    src = inspect.getsource(_ing)
    i = src.find("paths = paths[:_VIDEO_MAX_PHOTOS]")
    assert i > 0, "상한 적용부를 못 찾음"
    seg = src[i:i + 600]
    assert "note=" in seg, "조용히 자른다(사유가 payload에 안 남는다)"


# ── E. 게이트 시간 상한 ───────────────────────────────────────────
def test_quality_gate_has_deadline():
    """E. 품질 루프에 시간 상한이 없으면 영상 버튼이 영원히 안 나온다(채널 상태 고착 실사고).
    상한을 넘으면 남은 라운드를 생략하고 글을 현 상태로 확정한다."""
    from app.services import qualitycheck as _q
    assert _q.GATE_BUDGET_SEC > 0
    src = inspect.getsource(_q)
    assert "_deadline" in src, "마감 시각이 없음"
    assert "monotonic" in src, "벽시계로 재면 시간 변경에 취약하다"
    assert "남은 라운드 생략" in src, "상한 초과 시 탈출 경로가 없음"


# ── F. AI 무빙 QC ─────────────────────────────────────────────────
def test_ai_clip_qc_distinguishes_bad_from_unknown():
    """F. '불량'과 '검사 못 함'을 같이 처리하면, 비전 호출이 실패할 때마다 멀쩡한 무빙이
    불량으로 낙인찍혀 AI 무빙이 통째로 죽는다. 검사 불가는 None이고 .bad를 남기지 않는다."""
    from app.media import ai_clip as _ac
    assert "bool | None" in (inspect.getsource(_ac._qc).split("\n")[0] +
                             inspect.signature(_ac._qc).__str__() +
                             (_ac._qc.__doc__ or "")) or \
        "None" in inspect.getsource(_ac._qc), "검사 불가(None) 경로가 없음"
    src = inspect.getsource(_ac)
    assert "qc_skip" in src, "검사 불가 카운터가 없음(불량과 뭉뚱그려짐)"
    i = src.find("qc_skip += 1")
    assert i > 0
    seg = src[max(0, i - 400):i + 200]
    assert ".bad" not in seg, "검사 불가인데 불량 마커를 남긴다"


# ── G. env 공백 방어 ──────────────────────────────────────────────
def test_env_values_are_stripped(monkeypatch):
    """G. 실사고: ELEVENLABS_API_KEY 앞에 공백 한 칸 → 400 → 조용히 Gemini 폴백으로 떨어져
    목소리가 바뀌었다. env 값은 항상 strip한다."""
    from app.media import tts as _t
    src = inspect.getsource(_t)
    for var in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        for ln in [l for l in src.splitlines() if var in l and "environ" in l]:
            assert ".strip()" in ln, f"공백 방어 없음: {ln.strip()}"
    # 공백만 든 값은 '설정됨'이 아니다 — configured()가 True를 주면 호출이 400으로 죽는다
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "   ")
    assert _t.configured() is False, "공백만 든 키를 '설정됨'으로 읽음"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "  sk-test  ")
    assert _t.configured() is True
