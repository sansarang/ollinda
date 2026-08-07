"""키워드 지목(override) 고정 골든 — 2026-08-07 실측 박제.

빈자리 큐가 지목한 '차량 썬팅 가격'을 관문 끝의 빈자리 승격 블록이 '썬팅 가격'으로
갈아치웠다(프로덕션 로그: [resolve-kw] 빈자리 승격). 지목 글감은 그 질문에 답하려고
큐에 올라간 것이다 — 키워드가 바뀌면 글이 큐의 질문에 답하지 않게 된다.
승격은 자동 선정 경로 전용. 세트=한 소재=한 키워드.
"""
from app import seo


def test_지목_키워드는_빈자리_승격이_갈아치우지_않는다(monkeypatch):
    monkeypatch.setattr(seo, "_gap_first", lambda cands, tid, note: ["딴 키워드"])
    kw0, kws = seo.resolve_target_keyword(
        industry="테스트업", region="", note="", biz="local", content_type="info",
        target_kw_override="지목 키워드", tenant_id="t-kwpin", verify_volume=False)
    assert kw0 == "지목 키워드", f"지목이 승격에 밀렸다: {kw0!r}"


def test_자동_선정_경로는_빈자리_승격이_그대로_돈다(monkeypatch):
    """지목 고정이 2026-08-02의 원래 기능(자동 경로 승격)을 죽이면 안 된다."""
    monkeypatch.setattr(seo, "_gap_first", lambda cands, tid, note: ["딴 키워드"])
    kw0, kws = seo.resolve_target_keyword(
        industry="테스트업", region="", note="", biz="local", content_type="info",
        target_kw_override="", tenant_id="t-kwpin", verify_volume=False)
    assert kw0 == "딴 키워드", f"자동 경로 승격이 죽었다: {kw0!r}"
