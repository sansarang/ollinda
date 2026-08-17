"""본문 사진 수 상한 + 배치 검증 골든 (2026-08-17 사장님 지시).

지시: "네이버 상위노출이나 사용자 체류시간에 맞게 사진 수를 제한해야 한다"
      "그 사진에 맞게 글도 작성되고 배치가 되는지… 정반대로 가면 안 된다"

실측 근거 — 같은 재료로 세 번 만들어 잰 값:
    글자    문단   사진   문단당사진   뭉침
    3,547   22     9      0.41        0곳
    3,186   25    16      0.64        1곳
    3,502   19    25      1.32        9곳
  문단당 사진이 1장을 넘으면 마커가 붙는다. 붙으면 그 사이에 읽을 것이 없어
  체류시간이 늘지 않고 오히려 준다.

절대 상한 22장 — 상위글 사진 중간값 21장(kw_anatomy 29키워드) +
Yeti가 이미지를 20초에 1장 가져간다는 자체 로그 실측(22장이면 수집에만 7분).

여기서 막는 재발:
  ① _select_slot_photos가 재정렬만 하고 자르지 않아 25장이 전부 마커가 된 것
  ② 배치가 어긋나도 아무도 모르는 것 — 자리를 정하는 것과 맞는지 보는 것은 다른 일이다
"""
import os

os.environ.setdefault("SHOPCAST_SECRET", "test")

from app.services import photocap as pc


def test_많이_올려도_절대상한을_넘지_않는다():
    # ★ 2026-08-17 사령관 시험에서 드러난 골든 결함 — 여기서 pc.HARD_MAX를 참조했더니
    #   상수를 22→99로 바꿔도 골든이 통과했다(기준이 같이 움직였다).
    #   실측에서 나온 값은 골든에 **직접 박는다** — 그래야 상수를 건드리면 잡힌다.
    #   22의 근거: 상위글 사진 중간값 21장(kw_anatomy 29키워드) + Yeti 20초/장(자체 로그).
    assert pc.HARD_MAX == 22, "절대 상한이 실측 근거(상위글 21장·Yeti 20초/장)에서 벗어났다"
    assert pc.cap_for(50, target_chars=10000) <= 22
    assert pc.cap_for(25, target_chars=3500) < 25, "25장이 그대로 통과했다(뭉침 9곳 재발)"


def test_실측에서_나온_상수는_골든이_직접_지킨다():
    """상수를 참조하는 골든은 상수가 바뀌면 같이 움직여 아무것도 못 막는다.
    실측 근거가 있는 값은 그 값 자체를 박아둔다."""
    assert pc.PER_PARA == 0.7, "문단당 사진 상한이 바뀌었다(실측: 0.41→뭉침0, 1.32→뭉침9)"
    assert pc.MIN_PHOTOS == 3, "최소 사진 수가 바뀌었다(상위글 최소 3장)"


def test_적게_올리면_자르지_않는다():
    assert pc.cap_for(3) == 3
    assert pc.cap_for(1) == 1
    assert pc.cap_for(0) == 0


def test_문단수를_알면_그것을_기준으로_한다():
    """문단당 1장을 넘기면 붙는다 — 문단 수가 있으면 글자수보다 정확하다."""
    assert pc.cap_for(25, n_paragraphs=19) <= int(19 * pc.PER_PARA) + 1
    assert pc.cap_for(25, n_paragraphs=40) > pc.cap_for(25, n_paragraphs=10)


def test_실측_조합이_뭉치지_않는_밀도로_나온다():
    """3,500자·19문단에 25장을 넣어 9곳이 뭉쳤다 — 그 조합이 다시 나오면 안 된다."""
    cap = pc.cap_for(25, target_chars=3500, n_paragraphs=19)
    assert cap / 19 <= 1.0, f"문단당 {cap/19:.2f}장 — 1장을 넘으면 붙는다"


def test_최소장수는_지킨다():
    """사진이 너무 적으면 체류가 안 는다(상위글 최소 3장)."""
    assert pc.cap_for(10, target_chars=100) >= pc.MIN_PHOTOS


def test_자른_이유를_말한다():
    """조용히 버리면 왜 사진이 줄었는지 아무도 모른다."""
    assert pc.reason(25, 17)
    assert "25" in pc.reason(25, 17) and "17" in pc.reason(25, 17)
    assert pc.reason(9, 9) == "", "안 줄였는데 이유를 만들었다"


# ── 배치 검증 ──────────────────────────────────────────────
NOTE = ("[사진1] 후면 유리 열선 위로 썬팅 시공이 완료된 모습\n"
        "[사진2] 도어 트림 화이트 가죽 암레스트에 코팅제를 바르는 장면\n")


def test_맞게_배치되면_통과한다():
    body = ("후면 유리는 열선 손상 없이 시공하는 게 핵심입니다.\n\n[사진1]\n\n"
            "도어 트림과 암레스트는 손이 자주 닿아 가죽 코팅이 필요합니다.\n\n[사진2]")
    r = pc.placement_audit(body, NOTE, 2)
    assert r["ok"] and r["n_miss"] == 0, r


def test_정반대로_가면_잡는다():
    """★ 사장님 지적 — 가죽 코팅 사진 옆에 도장 이야기가 붙은 실물이 있었다."""
    body = ("타이어 공기압은 계절마다 달라집니다.\n\n[사진1]\n\n"
            "세차 주기는 한 달에 두 번이면 충분합니다.\n\n[사진2]")
    r = pc.placement_audit(body, NOTE, 2)
    assert not r["ok"] and r["n_miss"] == 2, "내용과 어긋난 배치를 통과시켰다"
    assert r["rate"] == 0


def test_묘사가_없으면_어긋남으로_세지_않는다():
    """vision 실패(8월 25%)는 배치 잘못이 아니다 — 원인이 다른 것을 섞으면 오진한다."""
    body = "아무 문장.\n\n[사진1]"
    r = pc.placement_audit(body, "", 1)
    assert r["n_checked"] == 0 and r["ok"], "묘사 없음을 배치 실패로 셌다"


def test_생성기가_상한과_검증을_실제로_쓴다():
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc.BlogDraftGenerator.generate)
    assert "cap_for" in src, "사진 상한이 생성 경로에 안 물렸다"
    assert "placement_audit" in src, "배치 검증이 생성 경로에 안 물렸다"
    assert "photo_placement" in src, "검증 결과가 payload에 안 남는다"


def test_배치_단계에서_문단수로_다시_자른다():
    """★ 2026-08-17 실물 — 생성 전 글자수 어림(3,500자 → 17장)이 빗나갔다.
    실제 문단이 19개라 문단당 0.89장이 됐고 5곳이 뭉쳤다.
    글자수는 문단 수를 예측하지 못한다 — 문단을 아는 자리에서 다시 잘라야 한다."""
    import inspect

    from app.generators import text_claude as tc
    src = inspect.getsource(tc._semantic_photo_placement)
    assert "cap_for" in src, "배치 단계에 상한 재조정이 없다(어림값 그대로 쓴다)"
    assert "allowed_idx" in src.split("cap_for")[0], "허용 문단 수를 세기 전에 잘랐다"


def test_문단수_기준이_뭉침없는_밀도를_준다():
    """실측: 0.41 → 뭉침 0곳 · 0.64 → 1곳 · 0.89 → 5곳 · 1.32 → 9곳."""
    for n_para in (10, 19, 25, 40):
        cap = pc.cap_for(25, n_paragraphs=n_para)
        assert cap / n_para <= pc.PER_PARA + 0.01, \
            f"문단 {n_para}개에 {cap}장 = {cap/n_para:.2f} — 뭉친다"
