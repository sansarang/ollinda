"""대행 운영판 골든 (2026-08-18 사장님 지시).

지시: "완전 대행사로 바뀌었잖아. 사진 올리는 거, 사장들한테 제시하는 내용,
      시스템 설계에 맞게 바뀌어야 해."

무엇이 어긋났나 — 주체가 바뀌었다:
  SaaS 시절: 고객이 로그인해 **자기 가게 하나**를 본다(/me)
  대행:      사장님이 **여러 가게**를 본다
  /me는 가게가 셋만 돼도 굴릴 수 없다.

여기서 막는 재발:
  ① 가게마다 쿼리를 돌려 느려지는 것 — 어제 '내 콘텐츠'가 그렇게 됐다
     (카드 36개 × 쿼리 3~4회). 대행은 가게가 계속 느니 처음부터 집계로 짠다.
  ② 운영판이 인증 없이 열리는 것 — 전 고객 정보가 한 화면에 있다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import main


def test_집계는_가게수와_무관하게_쿼리_한_번이다():
    """★ 가게마다 쿼리를 돌리면 고객이 늘수록 느려진다(어제 실측한 실패 모드)."""
    import inspect
    src = inspect.getsource(main._ops_rows)
    assert "c.execute(sql" in src, "집계 쿼리를 한 번에 돌리지 않는다"
    # 반복문 안에서 DB를 다시 치면 안 된다
    body = src[src.find("for r in rows"):]
    for bad in ("db.rank_history", "db.get_blog_publish", "db.list_sets", "c.execute"):
        assert bad not in body, f"카드 반복문에서 쿼리를 돈다: {bad}"


def test_데모_가게는_빼고_센다():
    """'카페 미리보기' 같은 데모 tenant가 운영판에 섞이면 숫자가 거짓이 된다."""
    import inspect
    src = inspect.getsource(main._ops_rows)
    assert "is_demo" in src, "데모 가게를 거르지 않는다"


def test_운영판은_인증_뒤에_있다():
    """전 고객 정보가 한 화면에 있다 — 무인증 노출은 사고다."""
    import inspect
    src = inspect.getsource(main)
    assert '@app.get("/admin/ops"' in src, \
        "운영판이 /admin 밖에 있으면 Basic 인증 미들웨어가 안 걸린다"


def test_일감이_한눈에_보인다():
    """대행 운영은 '무엇을 해야 하는가'가 첫 화면에 있어야 한다."""
    import inspect
    src = inspect.getsource(main.admin_ops)
    for k in ("사진 대기", "검토·발행 대기", "이번 주 발행"):
        assert k in src, f"운영 지표가 빠졌다: {k}"


def test_업로드링크가_가게마다_노출된다():
    """대행에서 고객은 로그인하지 않는다 — 링크 하나로 사진만 보낸다."""
    import inspect
    src = inspect.getsource(main.admin_ops)
    assert "/u/" in src and "upload_token" in src, "고객 업로드 링크가 운영판에 없다"


def test_대기_계산이_음수로_가지_않는다():
    """세트를 지우면 made가 assets보다 클 수 있다 — 음수 대기는 거짓 정보다."""
    import inspect
    src = inspect.getsource(main._ops_rows)
    assert "max(0," in src, "대기 수가 음수로 표시될 수 있다"
