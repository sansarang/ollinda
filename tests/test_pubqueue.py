"""📤 발행 큐 골든 — 서버가 정하고 로컬이 올린다.

사장님 지시(2026-08-17):
  "미리보기 창에서 내가 확인하고 발행을 누르면 네이버에서 실제로 작성하듯이
   작성되어야 한다. 사진 배치 및 글. 그리고 각 가게마다 아이디와 비밀번호가
   틀리다는거를 정확히 인지해야 한다."

★ 이 골든이 지키는 것 셋:
  ① **이중 발행 금지** — 같은 글이 두 번 올라가면 저품질 판정을 부른다
  ② **가게가 섞이지 않는다** — 큐 항목은 tenant_id를 들고 다닌다.
     이걸 놓치면 남의 블로그에 글이 올라간다(되돌릴 수 없다)
  ③ **성공에는 URL이 있다** — 주소 없는 '성공'은 성공이 아니다
"""
import os

import pytest

os.environ.setdefault("SHOPCAST_SECRET", "test")
os.environ.setdefault("SHOPCAST_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SHOPCAST_DB", "/tmp/test_pubqueue.sqlite")

from app import db  # noqa: E402
from app.services import pubqueue  # noqa: E402

db.init_db()


class _Piece:
    def __init__(self, pid, tenant, title="테스트 글", body="본문 [사진1] 이어짐"):
        self.id = pid
        self.tenant_id = tenant
        self.asset_id = "asset-" + pid
        self.payload = {"title": title, "body": body, "tags": ["썬팅"],
                        "image_paths": [f"/data/storage/{tenant}/a.jpeg",
                                        f"/data/storage/{tenant}/b.jpeg"]}


@pytest.fixture(autouse=True)
def clean():
    with db._conn() as c:
        pubqueue._ensure(c)
        c.execute("DELETE FROM publish_queue")
    yield


def test_같은_글은_두_번_줄서지_않는다():
    """★ 이중 발행은 되돌릴 수 없다. 새로고침·연타로 두 번 눌러도 한 건이어야 한다."""
    p = _Piece("p1", "shop-a")
    a = pubqueue.enqueue("shop-a", p)
    b = pubqueue.enqueue("shop-a", p)
    assert a["ok"] and b["ok"]
    assert b.get("dup") is True, "같은 글이 두 번 등록됐다"
    assert a["id"] == b["id"]


def test_한_번에_하나만_집어간다():
    """에이전트가 둘 돌아도 같은 작업을 두 번 가져가면 안 된다."""
    pubqueue.enqueue("shop-a", _Piece("p1", "shop-a"))
    pubqueue.enqueue("shop-a", _Piece("p2", "shop-a"))
    j1 = pubqueue.claim()
    j2 = pubqueue.claim()
    assert j1 and j2 and j1["id"] != j2["id"], "같은 작업을 두 번 집어갔다"
    assert pubqueue.claim() is None, "없는 작업을 만들어냈다"


def test_가게를_지정해_집어갈_수_있다():
    """★ 가게마다 계정이 다르다 — 에이전트는 자기가 로그인한 가게 것만 가져가야 한다."""
    pubqueue.enqueue("shop-a", _Piece("p1", "shop-a"))
    pubqueue.enqueue("shop-b", _Piece("p2", "shop-b"))
    j = pubqueue.claim("shop-b")
    assert j and j["tenant_id"] == "shop-b", "다른 가게 작업을 가져갔다"
    assert j["piece_id"] == "p2"


def test_사진은_에이전트가_받을_수_있는_경로로_온다():
    """★ /dl/ 은 로그인 세션을 요구한다 — 에이전트는 세션이 없어 404를 받는다.
    이걸 놓치면 사진 없는 글이 올라간다."""
    pubqueue.enqueue("shop-a", _Piece("p1", "shop-a"))
    j = pubqueue.claim()
    photos = j["payload"]["photos"]
    assert len(photos) == 2
    for ph in photos:
        assert ph["url"].startswith("/admin/publish/media/shop-a/"), ph
        assert "/dl/" not in ph["url"], "세션이 필요한 경로를 에이전트에게 줬다"


def test_주소_없는_성공은_성공이_아니다():
    """★ '올렸다'는데 주소가 없으면 확인할 방법이 없다 — 발행 확인·순위 추적이 못 산다."""
    pubqueue.enqueue("shop-a", _Piece("p1", "shop-a"))
    j = pubqueue.claim()
    r = pubqueue.finish(j["id"], True, url="")
    assert r["ok"] and r["published"] is False, "URL 없이 성공 처리됐다"
    assert pubqueue.status_of("p1") == "failed"


def test_성공하면_상태가_남는다():
    pubqueue.enqueue("shop-a", _Piece("p1", "shop-a"))
    j = pubqueue.claim()
    r = pubqueue.finish(j["id"], True, url="https://blog.naver.com/x/123")
    assert r["published"] is True
    assert pubqueue.status_of("p1") == "done"


def test_죽은_작업은_스스로_풀린다():
    """★ 맥북이 꺼지면 큐가 영원히 잠긴다 — 죽은 잡은 스스로 말하지 못한다(헌법 5)."""
    from datetime import datetime, timedelta
    pubqueue.enqueue("shop-a", _Piece("p1", "shop-a"))
    j = pubqueue.claim()
    old = (datetime.utcnow() - timedelta(minutes=pubqueue.STALE_MIN + 5)).isoformat(timespec="seconds")
    with db._conn() as c:
        c.execute("UPDATE publish_queue SET claimed_at=? WHERE id=?", (old, j["id"]))
    again = pubqueue.claim()
    assert again and again["id"] == j["id"], "죽은 작업이 영원히 잠겼다"


def test_본문이_비면_받지_않는다():
    """빈 글을 올리면 그 자체가 저품질이다."""
    p = _Piece("p9", "shop-a", body="   ")
    r = pubqueue.enqueue("shop-a", p)
    assert r["ok"] is False and "본문" in r["error"]


def test_STALE_시간이_임의로_늘지_않는다():
    """값을 상수 참조로 검사하면 상수가 바뀔 때 테스트가 따라가 아무것도 못 잡는다."""
    assert pubqueue.STALE_MIN == 30
