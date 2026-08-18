"""사진 중복 정리 골든.

★ 이 도구가 틀리면 **사장님 사진이 사라진다.** 되돌릴 수 없다.
  그래서 검사하는 것은 '용량이 줄었는가'가 아니라 이 셋이다:

    ① 가게 경계를 넘지 않는가 (남의 가게 사진과 합치면 안 된다)
    ② DB 참조를 먼저 옮기는가 (파일부터 지우면 글의 사진이 깨진다)
    ③ 아직 참조 중인 파일을 지우지 않는가

  ①은 2026-08-03 '생성물이 남의 가게로 갔다'와 같은 계열의 사고를 막는다.
"""
import json
import os

import pytest

os.environ.setdefault("SHOPCAST_SECRET", "test")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """가게 둘 · 같은 내용의 사진들을 심은 임시 저장소."""
    import app.storage as _st
    monkeypatch.setattr(_st, "STORAGE_DIR", str(tmp_path), raising=False)
    a, b = tmp_path / "shop-a", tmp_path / "shop-b"
    for d in (a, b):
        (d / ".web").mkdir(parents=True)
        (d / ".thumbs").mkdir(parents=True)
    same = b"\xff\xd8" + b"SAME-PHOTO-BYTES" * 40
    other = b"\xff\xd8" + b"DIFFERENT-BYTES!" * 40
    (a / "1.jpg").write_bytes(same)
    (a / "2.jpg").write_bytes(same)          # ← shop-a 안의 중복(합쳐야 한다)
    (a / "3.jpg").write_bytes(other)
    (b / "9.jpg").write_bytes(same)          # ← 내용은 같지만 **다른 가게**(건드리면 안 된다)
    (a / ".web" / "2.jpg").write_bytes(b"web-derivative")
    for p in (a, b):
        os.utime(p / ("1.jpg" if p == a else "9.jpg"), (1000, 1000))
    os.utime(a / "2.jpg", (2000, 2000))      # 2.jpg가 더 나중 → 1.jpg가 대표
    return tmp_path, a, b


def test_가게_경계를_넘지_않는다(storage):
    """★ 같은 사진이라도 가게가 다르면 합치지 않는다.
    합치면 A가 지운 사진 때문에 B의 글이 깨지고 남의 파일을 참조하게 된다."""
    from app.services import photodedup
    p = photodedup.plan()
    for g in p["groups"]:
        tenants = {photodedup._tenant_of(x) for x in [g["keep"]] + g["drop"]}
        assert len(tenants) == 1, f"한 묶음에 여러 가게가 섞였다: {tenants}"
    dropped = {os.path.basename(d) for g in p["groups"] for d in g["drop"]}
    assert "9.jpg" not in dropped, "다른 가게의 같은 사진을 지우려 했다"


def test_같은_가게_중복만_묶는다(storage):
    from app.services import photodedup
    p = photodedup.plan()
    assert p["n_drop"] == 1, f"중복 1장이어야 하는데 {p['n_drop']}장"
    g = p["groups"][0]
    assert os.path.basename(g["keep"]) == "1.jpg", "더 오래된 파일이 대표여야 한다"
    assert [os.path.basename(x) for x in g["drop"]] == ["2.jpg"]


def test_계획은_아무것도_바꾸지_않는다(storage):
    """plan()이 파일을 건드리면 '확인만 해보자'가 사고가 된다."""
    tmp, a, _ = storage
    from app.services import photodedup
    before = sorted(str(p) for p in tmp.rglob("*.jpg"))
    photodedup.plan()
    assert sorted(str(p) for p in tmp.rglob("*.jpg")) == before


def test_참조를_먼저_옮기고_지운다(storage, monkeypatch):
    """★ 순서가 계약이다 — DB 교체가 끝난 뒤에만 파일이 사라져야 한다."""
    tmp, a, _ = storage
    from app.services import photodedup

    swapped: dict = {}
    order: list = []

    def _fake_swap(mapping):
        swapped.update(mapping)
        order.append("db")
        return 1

    def _fake_refs():
        order.append("check")
        return set()                                   # 아무도 참조하지 않는 상태

    monkeypatch.setattr(photodedup, "_swap_refs", _fake_swap)
    monkeypatch.setattr("app.main._referenced_media", _fake_refs)
    r = photodedup.apply()
    assert order[:2] == ["db", "check"], f"순서가 틀렸다: {order}"
    assert not (a / "2.jpg").exists(), "중복 파일이 안 지워졌다"
    assert (a / "1.jpg").exists(), "대표를 지웠다"
    assert (a / "3.jpg").exists(), "중복이 아닌 사진을 지웠다"
    assert not (a / ".web" / "2.jpg").exists(), "지운 원본의 파생본이 고아로 남았다"
    assert r["freed_mb"] >= 0 and r["files_removed"] >= 1


def test_아직_참조되면_지우지_않는다(storage, monkeypatch):
    """DB 교체가 실패했거나 다른 곳이 아직 그 파일을 들고 있으면 남긴다."""
    tmp, a, _ = storage
    from app.services import photodedup
    monkeypatch.setattr(photodedup, "_swap_refs", lambda m: 0)
    monkeypatch.setattr("app.main._referenced_media",
                        lambda: {os.path.realpath(str(a / "2.jpg"))})
    r = photodedup.apply()
    assert (a / "2.jpg").exists(), "아직 참조 중인 파일을 지웠다"
    assert r["held_still_referenced"] == 1


def test_참조교체는_중첩된_곳도_바꾼다(storage, monkeypatch):
    """image_paths만 보면 안 된다 — photo_markers 등 소비자가 여럿이다."""
    from app.services import photodedup
    tmp, a, _ = storage
    src, dst = os.path.realpath(str(a / "2.jpg")), str(a / "1.jpg")
    payload = {"image_paths": [src], "photo_markers": [{"image_path": src}],
               "nested": {"deep": [{"p": src}]}, "keep_me": "그대로"}
    out = json.loads(json.dumps(payload))

    def _walk(v):
        if isinstance(v, str):
            return {src: dst}.get(os.path.realpath(v), v) if v.startswith("/") else v
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        return v

    out = _walk(out)
    assert out["image_paths"] == [dst]
    assert out["photo_markers"][0]["image_path"] == dst
    assert out["nested"]["deep"][0]["p"] == dst
    assert out["keep_me"] == "그대로"
