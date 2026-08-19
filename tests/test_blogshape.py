"""글 골격 골든 — 같은 가게에서 같은 뼈대가 반복되면 실패한다.

2026-08-19 사장님: "글쓰기 형식이 전부다 똑같잔항"
  실측 — 루마썬팅 발행글 8편이 전부 같은 뼈대였다:
      본문 2~3덩이 → [FAQ] → [요약] → 함께 보면 좋은 글
  새로 만든 6업종 글 6편도 마찬가지였다.

★ 이건 같은 계열 결함 **2회째**다.
  2026-08-16에 섹션 **이름만** 돌렸다(sections.py: 한눈 요약/요약하면/핵심만 정리).
  옷만 갈아입혀서 뼈대는 그대로 남았고, 8편이 또 같아졌다.
  헌법: 같은 계열 2회째부터는 표면별 수정 금지, 구성 규칙 자체를 바꾼다.

★ 위험한 것은 '보기 싫음'이 아니다. 같은 블로그에 같은 골격이 쌓이면
  유사문서·기계 생성 신호가 된다 — 금지선의 '내용 복제 변주'다.
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import blogshape as bs  # noqa: E402


def test_한_가게에서_8편이_전부_다른_뼈대로_나간다():
    """★ 이 파일의 존재 이유. 실측에서 8편이 같았다."""
    seen = []
    for i in range(8):
        sh = bs.pick(f"asset-{i}", seen)
        seen.insert(0, sh["id"])
    assert len(set(seen)) == 8, f"8편에 같은 골격이 섞였다: {seen}"


def test_직전_글과는_절대_겹치지_않는다():
    """골격을 다 쓴 뒤 재사용할 때도 바로 앞 글과 같으면 안 된다."""
    seen = []
    for i in range(20):
        sh = bs.pick(f"a{i}", seen)
        assert not seen or sh["id"] != seen[0], f"{i}편이 직전과 같다: {sh['id']}"
        seen.insert(0, sh["id"])


def test_같은_글은_항상_같은_골격이다():
    """무작위면 재생성 때마다 달라져 게이트와 어긋난다(sections.py와 같은 원칙)."""
    for _ in range(5):
        assert bs.pick("asset-x", ["record"])["id"] == bs.pick("asset-x", ["record"])["id"]


def test_골격마다_마무리_블록이_다르다():
    """★ 전부 FAQ+요약을 달면 뼈대가 또 같아진다. 그게 원래 문제였다."""
    faqs = {s["id"]: bool(s["faq"]) for s in bs.SHAPES}
    sums = {s["id"]: s["summary"] for s in bs.SHAPES}
    assert len(set(faqs.values())) > 1, "모든 골격이 FAQ를 요구한다 — 뼈대가 같아진다"
    assert len(set(map(str, sums.values()))) > 1, "요약 형태가 전부 같다"
    # 문답 중심 글에 질문 섹션을 또 붙이면 이상하다
    assert bs.needs_faq("qna") is False, "본문이 문답인데 FAQ를 또 요구한다"
    # 기록형에 '자주 묻는 질문'이 붙던 것이 지금 어색함의 정체였다
    assert bs.needs_faq("record") is False


def test_업종어를_넣지_않는다():
    """헌법 업종 중립 — 헬스장·펜션·동물병원에도 그대로 통해야 한다.
    실계정이 둘 다 자동차라 프롬프트에 차량어가 31곳 쌓였다(2026-08-19 실측)."""
    text = " ".join(s["flow"] + s["name"] for s in bs.SHAPES)
    for w in ("시공", "매물", "차종", "차량", "썬팅", "중고차", "입고", "출고", "필름"):
        assert w not in text, f"골격 문구에 업종어가 박혔다: {w}"


def test_게이트가_묻는_것과_같은_답을_준다():
    """골격이 요구하지 않는 섹션을 게이트가 검사하면 점수가 깎여 발행이 막힌다.
    게이트(qualitycheck·geo_audit)는 이 함수만 보고 판단해야 한다."""
    for s in bs.SHAPES:
        assert bs.needs_faq(s["id"]) == bool(s["faq"])
        assert bs.needs_summary(s["id"]) == bool(s["summary"])
    # 모르는 id는 기본 골격으로 — 옛 글(blog_shape 없음)도 판정이 되어야 한다
    assert bs.get("")["id"] == bs.DEFAULT["id"]
    assert bs.needs_faq("") is bool(bs.DEFAULT["faq"])


def test_프롬프트_블록이_고른_섹션만_지시한다():
    no_faq = bs.prompt_block("record", "자주 묻는 질문", "한눈 요약")
    assert "자주 묻는 질문" not in no_faq, "FAQ가 필요 없는 골격에 FAQ를 지시한다"
    assert "붙이지 마라" in no_faq or "한눈 요약" in no_faq
    with_faq = bs.prompt_block("problem", "자주 묻는 질문", "한눈 요약")
    assert "자주 묻는 질문" in with_faq and "한눈 요약" in with_faq


def test_골격_수가_줄지_않는다():
    """종류가 줄면 반복 주기가 짧아진다. 값을 리터럴로 박는다(상수 참조 금지)."""
    assert len(bs.SHAPES) >= 8, f"골격이 {len(bs.SHAPES)}종으로 줄었다"
    assert len({s["id"] for s in bs.SHAPES}) == len(bs.SHAPES), "id가 중복됐다"


def test_FAQ를_요구하지_않는_골격에_코드가_도로_붙이지_않는다():
    """★ 실측으로 잡은 결함(2026-08-19).

    골격을 도입하고 3편을 만들었더니 '붙이지 마라'고 지시한 3편 **전부**에 FAQ가 붙었다.
    FAQ를 다루는 곳이 넷이었는데 둘만 고쳤기 때문이다:
      ① 프롬프트 구성 지시   ② 게이트(qualitycheck)    ← 고침
      ③ 후처리 자동 보강     ④ 출력 형식 지시·감점(seo) ← 안 고침
    하나만 남아도 원위치다. 네 곳이 같은 기준(blogshape.needs_faq)을 봐야 한다.
    """
    import inspect
    from app.generators import text_claude as tc
    from app import seo
    from app.services import qualitycheck as qc

    gen = inspect.getsource(tc)
    i = gen.find("FAQ 섹션 누락 대비")
    assert i > 0, "FAQ 보강 후처리를 못 찾음"
    assert "needs_faq" in gen[i:i + 1400], "후처리가 골격을 안 본다 — 코드가 FAQ를 도로 붙인다"

    # 출력 형식 지시가 FAQ를 또 요구하면 두 지시가 어긋나고 모델은 '있다'는 쪽을 따른다
    fmt = gen[gen.find("📐 형식 개편"):gen.find("📐 형식 개편") + 700]
    assert "_sec_names['faq']" not in fmt, "출력 형식 지시가 FAQ를 강제한다"

    # 감점·채점도 같은 기준
    assert "needs_faq" in inspect.getsource(seo.quality_audit), "감점이 골격을 안 본다"
    assert "needs_faq" in inspect.getsource(seo.geo_audit), "GEO 채점이 골격을 안 본다"
    assert "needs_faq" in inspect.getsource(qc), "품질 게이트가 골격을 안 본다"
