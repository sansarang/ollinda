"""대행 운영판 골든.

★ 2026-08-18 — 나는 이 화면을 **새로 만들었다가 걷어냈다.**
  `/admin/ops`가 이미 있었는데(신호등·주간목표·검수큐·리믹스·소재팩·사진요청 링크까지)
  확인하지 않고 새로 만들어 기존 것을 가렸다(FastAPI는 먼저 등록된 라우트를 쓴다).
  사장님이 어제 지적한 "만들어놓은 걸 하나도 안 쓴다"를 그대로 반복한 것이다.

  그래서 이 골든의 첫 임무는 **중복 라우트 금지**다.
  같은 경로가 두 번 정의되면 뒤엣것은 죽고, 죽은 줄도 모른 채 시간이 간다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app import main


def test_같은_경로가_두_번_정의되지_않는다():
    """★ 오늘 실제로 당했다 — /admin/ops를 중복 정의해 기존 화면을 가렸다.
    FastAPI는 먼저 등록된 것을 쓰므로 나중 것은 조용히 죽는다."""
    seen, dup = {}, []
    for r in main.app.routes:
        path = getattr(r, "path", None)
        if not path:
            continue
        for m in (getattr(r, "methods", None) or {"GET"}):
            key = (m, path)
            if key in seen:
                dup.append(f"{m} {path}")
            seen[key] = True
    assert not dup, "같은 경로가 두 번 정의됐다(뒤엣것은 죽는다):\n  " + "\n  ".join(dup)


def test_운영판이_인증_뒤에_있다():
    """전 고객 정보가 한 화면에 있다 — 무인증 노출은 사고다."""
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert "/admin/ops" in paths, "운영판이 없다"
    # /admin* 은 Basic 인증 미들웨어가 막는다(fail-closed)
    import inspect
    src = inspect.getsource(main.admin_basic_auth)
    assert 'path.startswith("/admin")' in src


def test_운영판이_대행_지표를_보여준다():
    """대행 운영은 '오늘 무엇을 해야 하는가'가 첫 화면에 있어야 한다."""
    import inspect
    src = inspect.getsource(main.ops)
    for k in ("검수 대기", "이번주 발행", "발행 부족", "오늘 할 일"):
        assert k in src, f"운영 지표가 빠졌다: {k}"


def test_가게마다_사진요청_링크가_있다():
    """대행에서 고객은 로그인하지 않는다 — 링크 하나로 사진만 보낸다."""
    import inspect
    src = inspect.getsource(main.ops)
    assert "/u/" in src and "tenant_token" in src, "고객 사진 업로드 링크가 없다"
