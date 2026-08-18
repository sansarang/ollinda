"""대행 단일 퍼널 골든 (2026-08-17 사장님 결정).

파는 것이 도구에서 대행으로 바뀌었다. 랜딩의 모든 주 행동은 '상담'이고 '가입'이 아니다.

왜 박제하나:
  가입시켜 놓으면 그 사람이 만나는 다음 화면은 빈 대시보드다. 실측이 그렇게 말한다 —
  가입한 3명(사장님 계정 제외) 전원이 tenant 이름 "내 가게" 기본값 그대로,
  사진 0장·생성 0건이었다. 제품 약속이 "사진만 올리면 됩니다"인데 그 사진을 올린 사람이 없었다.

  랜딩은 손이 자주 가는 파일이라 예전 CTA가 조각으로 되살아나기 쉽다.
  여기서 막는 것은 '가입 버튼의 부활'과 '상담 링크가 죽은 버튼이 되는 것' 둘이다.
"""
import os
import re

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import config, landing


def _rendered_no_comments() -> str:
    """HTML 주석 제외 — 설명 주석에 남은 옛 문구가 오탐된다."""
    return re.sub(r"<!--.*?-->", "", landing.render(), flags=re.S)


def test_랜딩에_가입_버튼이_없다():
    h = _rendered_no_comments()
    assert "/login/kakao" not in h and "/login/google" not in h, "가입 퍼널이 되살아났다"
    assert 'href="/login"' in h, "기존 회원 로그인 입구까지 사라졌다"


def test_상담_링크가_죽은_버튼이_아니다():
    """카카오 채널 미개설 상태에서 채널 URL을 그대로 쓰면 빈 링크가 된다.
    2026-08-09에 '없는 상담 버튼을 안내하던 허위 카피'를 봉합한 적이 있다 — 같은 사고."""
    href = landing.consult_href()
    assert href and href != "#", "상담 링크가 비어 있다"
    assert href.startswith("http") or href.startswith("tel:"), f"열 수 없는 링크: {href}"
    assert href in _rendered_no_comments(), "랜딩이 상담 링크를 실제로 쓰지 않는다"


def test_버튼_문구가_실제로_열리는_것과_일치한다(monkeypatch):
    """'카톡으로 상담받기'를 눌렀는데 전화가 걸리면 그 자체가 거짓말이다."""
    monkeypatch.delenv("KAKAO_CHANNEL_URL", raising=False)
    assert landing.consult_href().startswith("tel:")
    assert "전화" in landing.consult_label()

    monkeypatch.setenv("KAKAO_CHANNEL_URL", "https://pf.kakao.com/_test")
    assert landing.consult_href() == "https://pf.kakao.com/_test"
    assert "카톡" in landing.consult_label()


def test_채널이_생기면_코드_수정_없이_켜진다(monkeypatch):
    """env만 넣으면 전 표면이 카톡으로 바뀌어야 한다 — 링크가 여러 곳에 흩어져 있으면 못 켠다."""
    monkeypatch.setenv("KAKAO_CHANNEL_URL", "https://pf.kakao.com/_switch")
    h = _rendered_no_comments()
    assert "https://pf.kakao.com/_switch" in h
    assert "tel:" not in h.replace(config.__name__, ""), "전화 링크가 남아 카톡으로 안 바뀐 표면이 있다"


def test_주_CTA는_상담이고_체험은_보조다():
    hero = landing._hero()
    i_consult, i_trial = hero.find("사진 보내고 상담받기"), hero.find("샘플 받아보기")
    assert 0 < i_consult < i_trial, "체험이 상담보다 먼저 온다(주 행동이 밀렸다)"


def test_요금은_대행_단일이다():
    """★ 2026-08-18 — 'plan=agency' 결제 링크 검사를 상담 링크 검사로 바꿨다.
    카드 결제를 없앴기 때문이다(사장님: 대행 계약은 내가 직접 한다).
    지키려는 것은 그대로다 — 요금이 대행 하나이고, 누를 곳이 살아 있어야 한다."""
    p = landing._pricing()
    assert f"{config.AGENCY_FROM:,}원" in p
    assert "/billing" not in p, "카드 결제 링크가 되살아났다"
    assert landing.consult_href() in p, "요금 카드에서 상담으로 가는 길이 없다"
    for gone in ("라이트", "스탠다드", f"{config.PRICE_BASIC:,}원"):
        assert gone not in p, f"SaaS 플랜 '{gone}'이 요금에 되살아났다"


def test_FAQ가_대행_질문이고_순위를_보장하지_않는다():
    qa = " ".join(q + " " + a for q, a in landing._QA)
    assert "보장하지 않습니다" in qa, "순위 보장 부인이 사라졌다(약관 6조와 어긋난다)"
    assert "비밀번호" in qa and "받지 않습니다" in qa, "계정 비밀번호를 안 받는다는 고지가 없다"
    assert "쿠팡" not in qa, "SaaS 타깃(온라인 셀러) 문항이 남아 있다"


def test_건수를_지어내지_않는다():
    """월 몇 건인지는 아직 확정되지 않았다. 확정 전에 숫자를 적으면 그게 날조다."""
    qa = " ".join(q + " " + a for q, a in landing._QA)
    assert "몇 건" in qa, "건수 질문 자체가 없다(대행에서 가장 많이 묻는 것)"
    hit = [q for q, a in landing._QA if "몇 건" in q]
    ans = dict(landing._QA)[hit[0]]
    assert not re.search(r"(월\s*)?\d+\s*건", ans), f"확정 안 된 건수를 적었다: {ans}"
