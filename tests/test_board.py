"""판 스캔표 골든 (2026-08-17 사장님 지시 — UFO ①).

무엇을 지키나: **글을 쓰기 전에 이길 자리부터 고른다.**
근거가 된 실측:
  · 상위글 나이 중간값이 검색어마다 7일 ~ 3,599일로 갈렸다(kw_anatomy 29개)
  · 통합검색 첫 화면에 블로그 지면이 없는 검색어가 있다('부산 썬팅', 08-01)
  10년 된 글이 지키는 자리는 글을 잘 써서 뚫는 게 아니라 안 가는 게 맞다.

여기서 막는 재발:
  ① 지면 없음보다 순위·나이를 먼저 보는 것 — 지면이 없으면 나머지는 의미가 없다
  ② 안 재본 것을 추측으로 채우는 것 — 빈칸은 '모름'으로 남는다(정직 게이트)
  ③ 뚫리는 자리가 없는데 아무거나 고르는 것 — 없으면 없다고 말해야 한다
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import board


def test_지면이_없으면_다른_지표보다_먼저_걸린다():
    """블로그탭 6위인데 손님 눈엔 0이었던 실측 — 지면 판정이 최우선이다."""
    v, why = board.judge(rank=3, age_days=10, has_surface=False)
    assert v == board.NO_SURFACE, "지면이 없는데 순위·나이로 판정했다"
    assert "블로그 자리" in why


def test_어린_판은_뚫림_오래된_판은_버팀():
    assert board.judge(None, 7, True)[0] == board.OPEN       # 실측 최소 7일
    assert board.judge(None, 3599, True)[0] == board.HARD     # 실측 최대 3,599일
    v, why = board.judge(None, 3599, True)
    assert "9년" in why or "년" in why, "오래된 정도를 사람 말로 알려주지 않는다"


def test_이미_1페이지면_확보다():
    assert board.judge(3, 3599, True)[0] == board.WIN, "이미 상위인데 판정이 뒤집혔다"


def test_안_재본_것은_모름으로_남는다():
    """추측으로 채우면 그게 곧 허위 판정이다."""
    v, why = board.judge(None, None, True)
    assert v == board.UNKNOWN
    assert "안 재" in why


def test_뚫리는_자리가_없으면_빈값을_준다(monkeypatch):
    """아무 키워드나 고르면 그게 침묵 폴백이다 — 없으면 없다고 해야 한다."""
    monkeypatch.setattr(board, "scan",
                        lambda *a, **k: {"attack": [], "counts": {}, "rows": []})
    assert board.next_target("t", ["아무거나"]) == ""


def test_정렬은_공략_가능한_것부터():
    rows = [{"verdict": board.HARD, "age_days": 900}, {"verdict": board.OPEN, "age_days": 30},
            {"verdict": board.WIN, "age_days": 10}]
    rows.sort(key=lambda r: (board.ORDER.index(r["verdict"]),
                             r["age_days"] if r["age_days"] is not None else 10**6))
    assert [r["verdict"] for r in rows] == [board.WIN, board.OPEN, board.HARD]


def test_요약문에_주방용어가_없다():
    """헌법: 사장님 화면에 검색량·문서수·키워드 같은 만드는 사람 말은 쓰지 않는다."""
    for c in ({board.WIN: 2, board.OPEN: 3}, {board.OPEN: 1}, {board.WIN: 1},
              {board.UNKNOWN: 5}, {}):
        line = board.summary_line({"counts": c})
        assert line
        for w in ("검색량", "문서수", "키워드", "롱테일", "C-Rank", "판정"):
            assert w not in line, f"주방 용어가 샜다: {w} / {line}"


def test_스캔은_기본적으로_네트워크를_치지_않는다(monkeypatch):
    """화면에서 즉시 떠야 한다 — 저장된 실측만 읽는다."""
    called = []
    import app.services.blogrank as br
    monkeypatch.setattr(br, "blog_rank", lambda *a, **k: called.append(1) or {})
    board.scan("tid", ["부산 동구 썬팅"], blog_id="x")     # live=False 기본
    assert not called, "기본 스캔이 순위 API를 쳤다(화면이 느려진다)"


def test_리포트가_안뜬_이유를_말한다(monkeypatch):
    """★ UFO ③ — 대행사는 'N편 올렸습니다'로 끝낸다. 우리는 '왜 안 떴는지'를 말한다.
    그 이유는 코드가 판정한 것이어야 한다(LLM이 지어낸 이유를 말하면 그게 날조다)."""
    from app.services import weekly_report as wr

    fake = {"rows": [{"keyword": "부산 중고차", "verdict": board.HARD,
                      "why": "상위글이 3599일(9년) 버팀", "rank": None,
                      "age_days": 3599, "top_n": 5, "has_surface": True}],
            "counts": {board.HARD: 1}, "attack": ["초량 테슬라 썬팅"], "avoid": ["부산 중고차"],
            "unmeasured": 0}
    monkeypatch.setattr(board, "scan", lambda *a, **k: fake)
    monkeypatch.setattr(wr.db, "tracked_keywords", lambda *a, **k: ["부산 중고차"])

    class _T:
        id = "t1"; name = "테스트"; blog_id = ""; publish_schedule = 2
    monkeypatch.setattr(wr, "_rank_change_7d", lambda *a, **k: None)
    monkeypatch.setattr(wr.blogsync if hasattr(wr, "blogsync") else wr, "fetch_feed",
                        lambda *a, **k: {"ok": False, "posts": []}, raising=False)
    rep = wr.build_report(_T())
    assert rep.get("blocked_reason"), "안 뜬 이유를 말하지 않는다"
    assert "9년" in rep["blocked_reason"] or "버팀" in rep["blocked_reason"]
    assert rep.get("next_target") == "초량 테슬라 썬팅", "다음에 노릴 자리를 제시하지 않는다"
    body = wr._email_body(rep)
    assert "🚧" in body and "🎯" in body, "메일 본문에 판정이 안 실린다"


def test_live_조회에_상한이_있다(monkeypatch):
    """★ 2026-08-17 실측 — 12개 검색어를 실조회하다 요청 전체가 502로 죽었다.
    화면이 빈손으로 돌아오느니 몇 개만 재고 나머지는 '모름'으로 남기는 게 낫다."""
    calls = []
    import app.services.blogrank as br
    monkeypatch.setattr(br, "blog_rank", lambda kw, bid: calls.append(kw) or {"rank": 5})
    kws = [f"검색어{i}" for i in range(20)]
    board.scan("tid", kws, blog_id="x", live=True)
    # ★ 2026-08-17 — 여기서 board.LIVE_MAX를 기준으로 쓰면 상수를 키울 때 같이 움직여
    #   아무것도 못 막는다(사령관 실측에서 photocap이 그렇게 뚫렸다). 값을 직접 박는다.
    assert board.LIVE_MAX == 6, "live 조회 상한이 바뀌었다(12개에서 502 실측)"
    assert len(calls) <= 6, f"상한 없이 {len(calls)}번 조회했다(타임아웃 재발)"
