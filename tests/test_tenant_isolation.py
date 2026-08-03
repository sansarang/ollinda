"""
tenant 격리 박제(2026-08-03 사건 — 생성물 오배송).

사건: 오늘 만든 글이 사장님 실계정이 아니라 동명(同名) 검증용 tenant로 들어갔다.
사장님 화면엔 안 보이는데 나는 '만들었다'고 보고했다.
원인: 검증용 tenant가 실계정과 같은 이름으로 공존했고, 대상을 이름으로 잡았다.

교훈: 이름은 식별자가 아니다. ID만이 식별자다.
"""
from __future__ import annotations

import inspect
import uuid

from app import config, db


def test_production_tenants_are_ids_not_names():
    """A. 실계정 목록은 ID다 — 이름으로 등록하면 같은 사건이 반복된다."""
    assert config.PRODUCTION_TENANTS, "실계정이 등록돼 있지 않다"
    for t in config.PRODUCTION_TENANTS:
        assert len(t) >= 32 and "-" in t, f"ID가 아니다: {t}"
    assert not config.is_production_tenant("루마썬팅 현대상사"), "이름으로 실계정을 판정한다"
    assert not config.is_production_tenant(""), "빈 값이 실계정으로 통과"


def test_new_tenant_defaults_to_test():
    """B. 무표기 tenant 금지 — 새로 만든 가게는 기본이 테스트다.
    표기가 없으면 나중에 이름으로 가리게 되고, 그게 이번 사건이다."""
    t = db.create_tenant(f"격리검증-{uuid.uuid4().hex[:6]}", "썬팅")
    try:
        with db._conn() as c:
            row = c.execute("SELECT is_test FROM tenants WHERE id=?", (t.id,)).fetchone()
        assert row is not None and row["is_test"] == 1, "새 가게가 실계정으로 만들어진다"
        assert not config.is_production_tenant(t.id)
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM tenants WHERE id=?", (t.id,))


def test_publish_refuses_non_production_tenant():
    """C. 테스트 tenant 산출물은 발행 경로에 진입할 수 없다 —
    검증용 글이 사장님 블로그로 나가면 되돌릴 수 없다."""
    from app.domain.models import Channel, ContentKind, ContentPiece, ContentStatus
    from app.services import publish
    p = ContentPiece(id=str(uuid.uuid4()), tenant_id="TEST_NOT_PROD", asset_id="a",
                     channel=Channel.NAVER_BLOG, kind=ContentKind.BLOG,
                     payload={"body": "x"}, status=ContentStatus.DRAFT)
    try:
        publish.publish_and_record(p)
        raise AssertionError("테스트 tenant가 발행됐다")
    except PermissionError as e:
        assert "발행할 수 없" in str(e)


def test_publish_allows_production_tenant_path():
    """C-역: 실계정은 막히면 안 된다 — 차단이 사장님 발행을 죽이면 본말전도다."""
    src = inspect.getsource(__import__("app.services.publish", fromlist=["x"]).publish_and_record)
    assert "is_production_tenant" in src
    i = src.find("is_production_tenant")
    assert "not _cfg.is_production_tenant" in src[max(0, i - 40):i + 40], "실계정도 막는 조건"


def test_target_must_be_declared():
    """D. 작업 시작 시 대상을 확인·명시한다 — 조용히 진행하면 오배송을 또 못 본다."""
    r = config.assert_target("TEST_X", "생성")
    assert r["production"] is False and r["label"] == "테스트 tenant"
    r2 = config.assert_target(config.PRODUCTION_TENANTS[0], "생성")
    assert r2["production"] is True and r2["label"] == "실계정"
    assert "tenant_id" in r2, "보고서에 붙일 형태가 아니다"


def test_constitution_has_execution_discipline():
    """F. 무결점 실행 규율(2026-08-03) — 오늘 tenant 이관을 절반만 하고 '완료'라고 보고했다.
    DB만 옮기고 미디어를 안 옮겨 사장님 화면의 사진이 전부 깨졌다.
    계획·영향범위·롤백·완결검증을 먼저 쓰지 않은 것이 원인이고, 그 자체가 실패다."""
    import pathlib
    txt = (pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text()
    assert "무결점 실행 규율" in txt, "규율이 헌법에 없다"
    # ★ 줄바꿈으로 끊길 수 있는 문구는 낱말로 검사한다(오늘 이미 겪은 실수 — 골든은
    #   표현이 아니라 규칙의 실체를 물어야 한다).
    flat = " ".join(txt.split())
    for must in ("실행 계획", "영향 범위", "롤백 방법", "완결 검증 기준",
                 "전 자원 대조표", "함수화", "골든 박제", "2회 재발"):
        assert must in flat, f"규율 조항 누락: {must}"
    # 규율은 절대 원칙 안에 있어야 한다(부록에 두면 안 읽힌다)
    assert txt.index("무결점 실행 규율") < txt.index("업종 중립"), "절대 원칙 최상단이 아니다"


def test_constitution_bans_name_based_targeting():
    """E. 원칙이 헌법에 있어야 다음 세션이 안다 — 코드에만 있으면 잊힌다."""
    import pathlib
    txt = (pathlib.Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text()
    assert "이름으로 tenant를 특정하지 않는다" in txt
    assert "PRODUCTION_TENANTS" in txt
    assert "같은 보고에서 섞지 않는다" in txt
