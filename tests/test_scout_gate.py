"""🚧 정찰 수집 게이트 골든 — 실패가 데이터로 둔갑하지 않게.

2026-08-05 실물: 네이버가 자동화 브라우저에 결과를 안 줬고(본문 7,102자),
파서는 그 빈 껍데기에서 '추천 검색어·플레이스 MY·숏텐츠 NOW'를 지면 블록으로 보고했다.
수집 실패가 지면 지도가 됐다.
"""
import inspect
import json

from app.services.scout import blocks as B
from app.services.scout import gate as G

# 실측 그대로 — 그날 맥북·서버가 똑같이 받은 껍데기
SHELL = ["최근 검색어", "추천 검색어이 정보가 표시된 이유", "숏텐츠 NOW", "플레이스 MY",
         "네이버 클립", "네이버 가격비교", "네이버플러스 스토어"]


def test_결과_링크가_없으면_수집_실패다():
    """수집 성공의 증거는 결과 링크다."""
    v = G.verdict(SHELL, [], 7102)
    assert v["ok"] is False and v["status"] == "수집 실패"
    assert any("결과 링크 0" in r for r in v["reasons"])


def test_본문_길이로_막지_않는다():
    """★ 처음 만든 게이트가 MIN_TEXT_LEN=20000으로 정상 수집을 막았다(내 오탐).
    실측: 정상 수집되는 키워드도 본문이 6,162~6,871자다(모바일 지면은 원래 짧다).
    되돌리면(길이 문턱 복원) 이 테스트가 실패한다."""
    v = G.verdict(SHELL + ["이미지"], ["hd8788", "no1motorss", "bkjh0412"], 6162)
    assert v["ok"] is True, f"정상 수집을 막는다: {v['reasons']}"
    assert v["n_links"] == 3
    src = inspect.getsource(G.verdict)
    assert "MIN_TEXT_LEN" not in src, "근거 없는 길이 문턱이 남아 있다"


def test_진짜_지면은_통과한다():
    real = ["인기글", "블로그", "플레이스", "웹사이트", "최근 검색어"]
    v = G.verdict(real, ["ksmrnd1", "abc"], 6800)
    assert v["ok"] is True and v["status"] == "수집"
    assert "최근 검색어" not in v["real_blocks"], "UI 껍데기가 결과로 남는다"
    assert v["chrome_dropped"] == 1


def test_노출_판정은_귀속을_쓰지_않는다():
    """실측 사고: 미검증 블록 귀속이 노출 판정에 샜다 —
    my_real_blocks=['숏텐츠 NOW'](UI 껍데기), my_visible=True. 거짓 양성이다.
    노출은 '내 블로그 ID가 결과 링크에 있는가'만으로 판정하고 지면 이름은 말하지 않는다."""
    src = inspect.getsource(B.scan)
    assert 'my_blog in (d.get("allBlogs")' in src, "노출 판정이 아직 귀속에 의존한다"
    assert "bool(mine_real)" not in src, "옛 귀속 기반 판정이 남아 있다"
    assert "is_chrome" in src, "UI 껍데기가 귀속 후보에 남아 있다"
    assert G.verdict(SHELL, ["x"], 6000)["attribution_verified"] is False, \
        "귀속을 검증된 것처럼 말한다"


def test_수집_경로가_게이트를_실제로_쓴다():
    """'존재'가 아니라 '사용' 기준(조항) — 게이트가 있어도 안 부르면 소용없다."""
    src = inspect.getsource(B.scan)
    assert "gate.verdict" in src or "_gate.verdict" in src, "수집이 게이트를 안 탄다"
    assert "collect_failed" in src, "실패를 실패로 기록하지 않는다"
    i_gate = src.index("verdict(")
    i_append = src.index('"blocks": [blk["title"]')
    assert i_gate < i_append, "게이트가 결과 기록보다 뒤에 있다"


def test_이미_쌓인_행의_오염_판정():
    """지우지 않고 표시만 한다 — 삭제하면 오염 규모의 증거가 사라진다."""
    assert G.suspect_row(json.dumps(SHELL)) is True
    assert G.suspect_row(json.dumps(["인기글", "블로그"])) is False
    assert G.suspect_row("[]") is False and G.suspect_row("") is False
    from app import main as m
    src = inspect.getsource(m.admin_scout_contamination)
    assert "DELETE" not in src.upper(), "오염 행을 지운다"
    assert "suspect" in src and "ALTER TABLE" in src, "표시 컬럼이 없다"
