"""작업실 모바일 내비 계약 — 로그아웃은 스크롤 스트립 밖 고정(2026-08-11 사장님 발견 박제).

스트립(overflow-x-auto) 끝에 넣으면 메뉴가 넘칠 때 화면 밖으로 밀려나
모바일에서 로그아웃이 '없는 버튼'이 된다.
"""


def _src():
    return open("app/main.py", encoding="utf-8").read()


def test_mobile_logout_outside_scroll_strip():
    src = _src()
    assert "mob-logout" in src, "모바일 고정 로그아웃 앵커 유실"
    # 옛 패턴(스트립 안 ml-auto 로그아웃) 회귀 금지
    assert "ml-auto text-sm text-slate-400 whitespace-nowrap'>로그아웃" not in src, \
        "로그아웃이 스크롤 스트립 안으로 회귀 — 모바일에서 다시 사라진다"
    # 구조: 스트립은 flex-1 내부 컨테이너, 로그아웃은 그 형제(flex-shrink-0)
    i_strip = src.find("overflow-x-auto flex-1 min-w-0")
    i_logout = src.find("mob-logout flex-shrink-0")
    assert 0 < i_strip < i_logout, "스트립-로그아웃 구조 변형 — 고정 배치가 깨졌는지 확인"
