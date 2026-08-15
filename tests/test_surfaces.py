"""지면(kind) 단일 관문 골든 (2026-08-16).

무엇을 막나 — 실측으로 드러난 계측 결함 3종의 재발:
  ① 중복 기록: place.rank() 값을 kind='blog'와 kind='place'로 두 번 쓰던 것
  ② 지면 혼합 비교: 한 키워드의 여러 지면을 한 시계열로 묶어 '순위 상승'을 만들어내던 것
     (그 값이 학습 루프 → 생성 브리프로 역주입됐다)
  ③ 라벨 사전 3중 복사 (main.py 2곳 · weekly_report.py 1곳)

왜 골든인가: 규율 2 — 개수·경로가 조용히 늘다가 망가지는 것은 문서가 아니라 테스트로 막는다.
"""
import os
import re

from app.services import surfaces

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── ① 옛 라벨로는 새로 쓰지 않는다 ────────────────────────────────────────

def test_legacy_region_kind_is_not_writable():
    """kind='blog'는 이름과 달리 지역검색이다. 새로 쓰면 kind='place'와 중복이 된다."""
    assert surfaces.REGION_LEGACY == "blog"
    assert not surfaces.writable(surfaces.REGION_LEGACY)
    for k in (surfaces.BLOG_SEARCH, surfaces.PLACE, surfaces.POST, surfaces.SHOP):
        assert surfaces.writable(k), f"쓰기 가능해야 하는 지면이 막혔다: {k}"


def test_save_rank_snapshot_refuses_legacy_kind(monkeypatch):
    """관문에서 막아야 한다 — 조용히 삼키지 말고 거부하고 로그를 남긴다(침묵 폴백 금지)."""
    from app import db
    calls = []
    monkeypatch.setattr(db, "_conn", lambda: (_ for _ in ()).throw(
        AssertionError("옛 라벨 쓰기가 DB까지 내려갔다")))
    monkeypatch.setattr(db.logging.getLogger("shopcast.rank"), "warning",
                        lambda *a, **k: calls.append(a))
    db.save_rank_snapshot("t1", "부산 썬팅", 3, kind=surfaces.REGION_LEGACY)
    assert calls, "거부하면서 로그를 남기지 않았다"


def test_no_source_writes_legacy_kind():
    """어느 코드도 kind='blog'로 새로 기록하지 않는다 — 중복의 뿌리."""
    bad = []
    for rel in ("app/db.py", "app/main.py", "app/services/ranktrack.py",
                "app/services/growth.py", "app/services/race.py",
                "app/services/queryscout.py"):
        for i, line in enumerate(_src(rel).splitlines(), 1):
            if "save_rank_snapshot" not in line:
                continue
            if re.search(r"kind\s*=\s*[\"']blog[\"']", line):
                bad.append(f"{rel}:{i}")
    assert not bad, f"옛 라벨(kind='blog')로 쓰는 곳이 남았다: {bad}"


# ── ② 지면을 섞어 비교하지 않는다 ────────────────────────────────────────

def test_improving_keywords_filters_by_kind():
    """지면별로 걸러 비교해야 한다. 안 걸면 플레이스 3위와 블로그탭 1위를 비교하게 된다."""
    s = _src("app/db.py")
    i = s.find("def improving_keywords")
    assert i > 0
    seg = s[i:i + 1800]
    assert "COALESCE(kind" in seg, "improving_keywords가 지면을 안 걸고 조회한다"
    assert "surfaces.PRIORITY" in seg, "지면 우선순위를 쓰지 않는다"


def test_stagnant_and_deltas_pick_one_surface():
    """정체 판정·성장 그래프도 한 지면만 본다."""
    s = _src("app/services/ranktrack.py")
    for fn in ("def stagnant_keywords", "def rank_deltas"):
        i = s.find(fn)
        assert i > 0, f"{fn} 없음"
        seg = s[i:i + 1400]
        assert "surfaces.PRIORITY" in seg, f"{fn}가 지면을 신뢰도 순으로 고르지 않는다"


def test_rank_deltas_does_not_pick_by_history_length():
    """길이로 고르면 옛 라벨(중복분)이 이긴다 — 실제로 그래서 지역검색이 블로그 순위로 보였다."""
    s = _src("app/services/ranktrack.py")
    i = s.find("def rank_deltas")
    seg = s[i:i + 1400]
    assert "len(best[1])" not in seg, "여전히 기록 길이로 지면을 고르고 있다"


# ── ③ 라벨 사전은 한 곳에만 산다 ──────────────────────────────────────────

def test_surface_label_dict_is_not_copied():
    """'블로그탭' 사전이 두 곳 이상에 살면 한쪽만 고쳐진다(2026-08-14 하루 4회 사고의 모양)."""
    copies = []
    for rel in ("app/main.py", "app/services/weekly_report.py", "app/services/ranktrack.py"):
        for i, line in enumerate(_src(rel).splitlines(), 1):
            if '"블로그탭"' in line and '{' in line and 'blog_search' in line:
                copies.append(f"{rel}:{i}")
    assert not copies, f"지면 라벨 사전이 복사됐다(surfaces.label을 쓸 것): {copies}"


def test_label_covers_every_kind():
    for k in surfaces.PRIORITY:
        assert surfaces.label(k) and surfaces.label(k) != k, f"라벨 누락: {k}"
    assert surfaces.label("듣보") == "듣보", "모르는 값을 빈칸으로 삼키면 안 된다"


# ── 순위 0 판독 (place.py:245) ───────────────────────────────────────────

def test_zero_is_not_a_rank():
    """0은 순위가 아니라 '상위 N위 밖'이다. 정수라 순위처럼 읽히는 함정."""
    assert not surfaces.is_exposed(0)
    assert not surfaces.is_exposed(None)
    assert surfaces.out_of_range(0)
    assert surfaces.is_exposed(1) and surfaces.is_exposed(30)
    assert surfaces.rank_for_compare(0) == surfaces.MISS
    assert surfaces.rank_for_compare(None) == surfaces.MISS
    assert surfaces.rank_for_compare(3) == 3


def test_best_kind_prefers_own_identifier_surfaces():
    """자사 식별자 정확 매칭(blog_search·post)이 가장 신뢰도 높다."""
    assert surfaces.best_kind(["place", "blog_search"]) == surfaces.BLOG_SEARCH
    assert surfaces.best_kind(["blog", "place"]) == surfaces.PLACE
    assert surfaces.best_kind([]) == ""


# ── indexed_at 정직성 ────────────────────────────────────────────────────

def test_index_label_does_not_claim_duration_beyond_polling_window():
    """indexed_at은 우리가 확인한 시각이다. 24시간 넘는 값은 폴링 간격이라 소요시간으로 말하면 거짓."""
    from app.main import _index_label
    near = _index_label({"published_at": "2026-08-12T07:01:00",
                         "indexed_at": "2026-08-12T07:20:00"})
    assert "안에" in near, f"상한 표현이 아니다: {near}"
    assert "만에" not in near, "'만에'는 소요시간 주장 — 우리가 아는 건 상한뿐이다"
    far = _index_label({"published_at": "2026-07-01T01:04:00",
                        "indexed_at": "2026-08-10T22:30:00"})
    assert not re.search(r"\d", far), f"폴링 간격을 소요시간처럼 말했다: {far}"
