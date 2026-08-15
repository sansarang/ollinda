"""주방 비공개 골든 (2026-08-16 사장님 지시).

사고 실물:
  사장님 홈에 이런 카드가 5장 떠 있었다 —
    "'부산광역시 동구 썬팅' 글이 검색에서 밀렸어요 (1위→검색 밖)"
  같은 화면 아래에서는 같은 키워드가 "첫 화면에 보이는 중"이었다(실측 blog_search 6위).
  ① 순위·키워드는 사장님이 보실 것이 아니다(헌법: 사장님 화면에 주방 용어 금지)
  ② 표기가 두 갈래(행정 풀네임 vs 구어형)라 같은 키워드가 상반된 두 판정으로 동시에 떴다
  ③ rank_after=None(조회 실패)을 '검색 밖'이라 단정했다 — 못 잰 것을 밀렸다고 보고

원칙: 사장님은 사진만 올리신다. 노출되게 만드는 것은 우리 역할이다.
감지 결과는 무음 교훈으로 적재되어 다음 글에 저절로 반영된다(화면 0).
"""
import os

from app.services import adapt_consume as ac

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── ① 사장님 화면에 카드를 만들지 않는다 ──────────────────────────────────

def test_no_handler_returns_a_card(monkeypatch):
    """어떤 감지든 사장님 카드를 만들면 안 된다."""
    made = []
    monkeypatch.setattr(ac.db, "add_lesson", lambda *a, **k: made.append(a) or "id")
    monkeypatch.setattr(ac.db, "add_notice", lambda *a, **k: None)
    ev = {"keyword": "부산광역시 동구 썬팅", "rank_before": 1, "rank_after": None}
    for fn in (ac._handle_rank_drop, ac._handle_briefing_lost, ac._handle_index_lost):
        card, piece_id = fn({"tenant_id": "t1", "evidence": ev, "id": "a1"})
        assert card is None, f"{fn.__name__}이 사장님 카드를 만들었다: {card}"
        assert piece_id == "", f"{fn.__name__}이 요청 없는 산출물을 만들었다"
    assert made, "감지했는데 교훈도 안 남겼다 — 배운 게 사라진다"


def test_cross_signal_goes_to_operator_only(monkeypatch):
    """가게 무관 정책 신호는 운영자에게만. 사장님 화면 아님."""
    seen = []
    monkeypatch.setattr(ac.db, "add_notice", lambda tid, kind, msg: seen.append((tid, kind)))
    card, _ = ac._handle_cross_signal({"evidence": {"keyword_group": "썬팅", "n_tenants": 3}})
    assert card is None
    assert seen and seen[0][0] == "", f"운영자 공지가 아니라 가게로 갔다: {seen}"


def test_home_does_not_mount_the_proposal_cards():
    """옛 카드 7건이 DB에 남아 있어도 사장님 홈에 뜨면 안 된다 — 마운트 자체를 뺀다."""
    src = _src("app/main.py")
    i = src.find("render_d2(t.id)")
    assert i > 0, "D2 배너 마운트를 못 찾았다"
    seg = src[i - 200:i + 200]
    assert "render_d1" not in seg, "사장님 홈에서 D1 개선 제안 카드가 아직 마운트된다"


def _code_only(rel: str) -> str:
    """독스트링을 걷어낸 '실행되는 코드'만. 사고 기록(독스트링)은 남겨야 하고,
    검사 대상은 실제로 사장님께 나가는 문자열이다."""
    import ast
    tree = ast.parse(_src(rel))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = ""
    return ast.unparse(tree)


def test_no_kitchen_words_in_owner_facing_strings():
    """사장님이 읽는 문자열에 주방 용어가 들어가면 안 된다."""
    code = _code_only("app/services/adapt_consume.py")
    # 사용자에게 보이는 문자열 = card dict의 headline/sub. 그것 자체가 없어야 한다.
    assert "'headline'" not in code and '"headline"' not in code, "카드 headline이 다시 생겼다"
    assert "검색에서 밀렸어요" not in code, "'검색에서 밀렸어요' 문구가 살아 있다"
    assert "검색 밖" not in code, "'검색 밖' 단정이 코드에 살아 있다"


# ── ② 키워드 표기 단일 관문 ──────────────────────────────────────────────

def test_keyword_notation_is_normalized():
    """행정 풀네임과 구어형이 갈라지면 같은 키워드가 상반된 판정으로 동시에 뜬다."""
    assert ac._kw({"keyword": "부산광역시 동구 썬팅"}) == "부산 동구 썬팅"
    assert ac._kw({}) == ""


def test_keyword_gateway_is_the_shared_one():
    """표기 규칙이 두 곳에 살면 갈라진다 — seo._kw_shorten 하나만 쓴다."""
    src = _src("app/services/adapt_consume.py")
    assert "_kw_shorten" in src, "공용 축약 관문을 안 쓴다"


# ── ③ 미측정을 미노출이라 하지 않는다 ────────────────────────────────────

def test_none_rank_is_not_called_out_of_range():
    """rank_after=None은 조회 실패다. '검색 밖'이라 하면 못 잰 것을 밀렸다고 보고하는 것."""
    assert ac._move_text(1, None) == "1위→확인 못 함"
    assert "검색 밖" not in ac._move_text(1, None)


def test_real_measurements_are_reported_as_is():
    """실제로 잰 값은 그대로 말한다 — 과소보고도 정직 위반이다."""
    assert ac._move_text(1, 5) == "1위→5위"
    assert ac._move_text(1, 0) == "1위→상위 밖"      # 0 = 조회됨 · 상위 밖(place.py:245)
    assert ac._move_text(None, None) == ""
