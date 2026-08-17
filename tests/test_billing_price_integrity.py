"""결제 금액 정합성 골든 — 표시가와 청구액이 갈리지 않는다.

박제 사유(2026-08-17, 대행 단일 전환 점검 중 발견):
  `pay_paddle.price_id()`가 `... or PADDLE_PRICE_SELF`로 폴백했다.
  `PADDLE_PRICE_AGENCY`가 비어 있으면 **화면엔 39만원이 뜨는데 결제는 스탠다드
  금액으로** 나간다. 기능 결함이 아니라 신뢰 사고이고, 헌법의 침묵 폴백 금지 정면 위반.
  당시 운영 env에는 AGENCY가 설정돼 있어 터지지 않았을 뿐 — 지워지면 첫 고객에서 터진다.

  같이 잡은 것: 랜딩 버튼은 "대행"인데 결제창 상품명이 "프로"였다.
"""
import importlib
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import config
from app.services import pay_paddle


def test_없는_플랜은_다른_플랜_가격으로_대체되지_않는다(monkeypatch):
    """폴백이 살아나면 표시가와 청구액이 갈린다."""
    monkeypatch.setenv("PADDLE_PRICE_SELF", "pri_self_xxx")
    monkeypatch.delenv("PADDLE_PRICE_AGENCY", raising=False)
    assert pay_paddle.price_id("agency") == "", "대행 가격이 없는데 다른 가격으로 떨어졌다"


def test_플랜별_가격ID는_자기_것만_반환한다(monkeypatch):
    monkeypatch.setenv("PADDLE_PRICE_SELF", "pri_self_xxx")
    monkeypatch.setenv("PADDLE_PRICE_AGENCY", "pri_agency_yyy")
    assert pay_paddle.price_id("agency") == "pri_agency_yyy"
    assert pay_paddle.price_id("self") == "pri_self_xxx"


def test_가격ID가_없으면_결제창을_열지_않는다(monkeypatch):
    """빈 값을 받고도 체크아웃을 열면 폴백 제거가 무의미하다."""
    from fastapi.testclient import TestClient

    from app import auth, main
    monkeypatch.setenv("PADDLE_CLIENT_TOKEN", "tok_test")
    monkeypatch.setenv("PADDLE_PRICE_SELF", "pri_self_xxx")
    monkeypatch.delenv("PADDLE_PRICE_AGENCY", raising=False)
    monkeypatch.setattr(auth, "current_user",
                        lambda r: {"id": "u1", "email": "t@t.com", "plan": "free"})
    html = TestClient(main.app).get("/billing?plan=agency").text
    assert "Paddle.Checkout.open" not in html, "가격 ID 없이 결제창을 열었다"
    assert "pri_self_xxx" not in html, "다른 플랜 가격 ID가 결제창에 실렸다"


def test_대행_상품명이_랜딩과_결제창에서_같다():
    """누른 버튼과 결제창의 상품명이 다르면 거기서 결제를 멈춘다."""
    from app import landing
    assert config.PLANS["agency"]["name"] == "대행"
    assert "plan=agency" in landing._pricing()


def test_대행가가_표시가와_같은_소스에서_나온다():
    """랜딩 표시가가 상수에서 오지 않으면 상수를 바꿔도 화면이 안 바뀐다."""
    html = __import__("app.landing", fromlist=["landing"])._pricing()
    assert f"{config.AGENCY_FROM:,}원" in html
    assert config.PLANS["agency"]["price"] == config.AGENCY_FROM
