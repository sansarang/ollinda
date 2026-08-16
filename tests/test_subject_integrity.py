"""소재 정합 골든 (2026-08-16 실사고).

사고 실물:
  사장님이 **테슬라 모델Y** 사진을 올렸는데 완성된 글이 **GV80** 글로 나왔다.
  제목·첫문장·본문·한눈요약·FAQ까지 전부 GV80. 사진에 없는 차종이다 = 날조.

인과 사슬(전부 실측으로 확인):
  ① '제네시스 GV80 신차패키지' blog_search 궤적:
       07-14:24 → 27 → 28 → 08-07:0(미노출) → 08-10:0     ← 죽어간 키워드
  ② improving_keywords가 미노출(0)을 **6위로 눕혀** gain=18의 '가장 많이 오른 키워드'로 집계
  ③ ingest가 그 키워드를 "제목·첫문장·본문에 더 강하게 반영하라"로 생성 브리프에 주입
  ④ 모델이 지시를 따라 소재를 통째로 갈아치움

여기서 무는 것:
  · 미노출을 실제 순위처럼 다루지 않는다(오늘만 세 번째로 만난 같은 계열 결함)
  · 학습 키워드가 소재를 이길 수 없다 — 소재는 사진·입력이 정한다
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _code(rel: str) -> str:
    """주석 줄을 걷어낸 실행 코드. 사고 기록(주석)은 남겨야 하고,
    검사 대상은 실제로 도는 코드다 — 주석의 'max_tokens=800' 인용에 골든이 걸렸다."""
    return "\n".join(ln for ln in _src(rel).splitlines()
                     if not ln.lstrip().startswith("#"))


# ── ① 죽은 키워드가 '오른 키워드'로 집계되면 안 된다 ──────────────────────

def test_dying_keyword_is_not_counted_as_improving():
    """실사고 궤적 그대로: 24위에서 미노출로 죽은 키워드는 '상승'이 아니다."""
    from app.services import surfaces
    series = [24, 24, 27, 27, 27, 26, 28, 28, 28, 0, 0, 0, 0]     # 실측 값
    first = surfaces.rank_for_compare(series[0])
    last = surfaces.rank_for_compare(series[-1])
    assert first - last <= 0, (
        f"죽은 키워드가 {first - last}계단 '상승'으로 잡힌다 — GV80 사고의 뿌리")


def test_entering_keyword_is_still_counted_as_improving():
    """반대로 미노출 → 진입은 진짜 상승이다. 눈금이 한쪽으로 죽으면 안 된다."""
    from app.services import surfaces
    assert surfaces.rank_for_compare(0) - surfaces.rank_for_compare(5) > 0


def test_improving_keywords_does_not_flatten_miss_to_top():
    """미노출을 상위권 숫자(6 등)로 눕히는 코드가 되살아나면 안 된다."""
    src = _code("app/db.py")
    i = src.find("def improving_keywords")
    seg = src[i:i + 2200]
    assert "miss=6" not in seg, "미노출을 6위로 눕히는 산식이 돌아왔다"
    assert "rank_for_compare" in seg, "순위 비교 단일 관문을 안 쓴다"


# ── ② 학습 키워드는 소재를 이길 수 없다 ──────────────────────────────────

def test_learned_keywords_cannot_override_the_subject():
    """'제목·첫문장·본문에 더 강하게 반영하라'가 소재를 갈아치웠다."""
    src = _code("app/services/ingest.py")
    i = src.find("improving_keywords(tenant.id)")
    assert i > 0, "학습 키워드 주입부를 못 찾았다"
    seg = src[i:i + 1400]
    assert "더 강하게 반영하라" not in seg, "소재를 이기는 지시문이 남아 있다"
    assert "소재는 이번 사진" in seg or "소재를 바꾸는 근거가 아니다" in seg, \
        "소재 우선 원칙이 지시문에 없다"
    assert "무시하라" in seg, "소재와 어긋날 때 무시하라는 지시가 없다"
    assert "날조" in seg, "사진에 없는 것을 쓰면 날조라는 경고가 없다"


# ── ③ 영상 제목은 내용에서 나온다 ────────────────────────────────────────

def test_video_title_is_not_a_fixed_template():
    """전에는 f'{kw0} 핵심만 정리했어요' 고정이라 매번 같은 제목이 나왔다."""
    src = _code("app/generators/video.py")
    i = src.find("blog_title = (pl.get(\"title\")")
    assert i > 0, "영상 제목 조립부를 못 찾았다"
    seg = src[i:i + 900]
    assert "opening" in seg, "영상 제목이 이 영상의 내용(훅)을 안 쓴다"
    assert "kw0} 핵심만" not in seg, "고정 템플릿이 그대로 남아 있다"


def test_display_layer_keeps_content_derived_title():
    """표시 층이 내용 기반 제목을 고정 템플릿으로 덮어쓰면 원위치다."""
    src = _code("app/main.py")
    i = src.find("def _nv_canonical")
    seg = src[i:i + 2000]
    assert "title_src" in seg, "내용 기반 제목을 구분하지 않는다"
    assert "_kept or" in seg, "내용 기반 제목을 살리지 않는다"


# ── ④ 표기 통일 ──────────────────────────────────────────────────────────

def test_video_meta_uses_spoken_region_form():
    """제목만 '부산광역시…', 설명·태그는 '부산 동구' — 한 영상 안에서 표기가 갈라졌다."""
    src = _code("app/main.py")
    i = src.find("def _nv_canonical")
    seg = src[i:i + 1600]
    assert "_kw_shorten" in seg, "영상 메타가 표기 단일 관문을 안 거친다"


def test_generator_title_uses_shortened_keyword():
    src = _code("app/generators/video.py")
    i = src.find("blog_title = (pl.get(\"title\")")
    seg = src[i:i + 900]
    assert "kw_nat" in seg, "생성기 제목이 구어형 키워드를 안 쓴다"


def test_query_plan_core_is_shortened():
    """계획에 남는 핵심 질의도 구어형이어야 나중에 실측 검색어와 대조된다."""
    src = _code("app/generators/text_claude.py")
    i = src.find("_ab_plan = _abm.plan(")
    assert i > 0
    seg = src[max(0, i - 500):i + 200]
    assert "_kw_shorten" in seg, "query_plan 핵심 질의가 행정 풀네임으로 남는다"


# ── ⑤ 대본 생성이 상한에 걸려 죽지 않는다 ────────────────────────────────

def test_script_token_budget_is_not_tight():
    """max_tokens=800에서 stop_reason=max_tokens 빈 응답 → 폴백으로 떨어졌다(실측)."""
    src = _code("app/generators/video.py")
    assert "max_tokens=800" not in src, "대본 생성 상한이 다시 800으로 내려갔다"
    assert "max_tokens=600" not in src, "구어화 상한이 다시 600으로 내려갔다"


# ── ⑥ 제목 표기 — 사람들이 검색하는 말로 (2026-08-16) ────────────────────

def test_title_pipeline_uses_spoken_region_form():
    """사장님 지적: "부산광역시 동구라고 사람들이 검색하지 않는다. 부산동구라고 검색한다."

    실물: 제목이 '부산 동구 …' → '부산광역시 동구 …'로 굳었다.
    원인 두 곳 —
      ① 프롬프트가 제목 후보를 kw0(행정 풀네임)로 만들라고 지시
      ② _pick_title이 kw0 포함 후보만 통과시킴 → 풀네임 제목만 살아남음
      게다가 같은 지시문이 바로 다음 줄에서 "풀네임 대신 구어형"이라고 모순되게 경고했다.
    """
    src = _code("app/generators/text_claude.py")
    for marker, why in (
        ("[제목후보]", "제목 후보 생성"),
        ("[1글 1키워드]", "소제목 지시"),
    ):
        i = src.find(marker)
        assert i > 0, f"{why} 지시문을 못 찾았다"
        seg = src[max(0, i - 200):i + 200]
        assert "_kw_shorten(kw0)" in seg, f"{why}가 아직 행정 풀네임을 쓴다"
    i = src.find("_pick_title(title_cands")
    assert i > 0
    assert "_kw_title" in src[i:i + 120], "제목 게이트가 구어형을 안 쓴다"


def test_keyword_directive_is_not_self_contradictory():
    """제목엔 풀네임을 넣으라 해놓고 본문엔 구어형을 쓰라고 하면 모델이 갈린다."""
    from app.generators.text_claude import _kw_natural_directive
    d = _kw_natural_directive("부산광역시 동구 썬팅업체", "부산광역시 동구")
    assert "'부산 동구 썬팅업체' 원형은 제목에서만" in d, d
    assert "'부산광역시 동구 썬팅업체' 원형" not in d, "제목에 풀네임을 요구하는 문구가 남았다"
