"""
🧱 스마트블록 정찰 (맥북 로컬) — 네이버 통합검색이 그 키워드를 '어떤 블록들'로 구성하는지 읽는다.

왜: 블로그탭 순위만 보면 착시가 생긴다(실측: 루마가 블로그탭 8위인데 통합검색엔 미노출).
실제 유입은 통합검색 상단 블록(플레이스·클립·인기글·숏텐츠…)에서 나온다. 공개 검색 API로는
블록 구성을 알 수 없어(지연 로딩) 로컬 브라우저 렌더로 읽는다.

원칙: 로그인 0 · 읽기 전용 · 저빈도(키워드당 하루 1회 이하) · 사람 속도.
      결과는 '대략 신호'로 다룬다(스마트블록은 개인화·기기별로 달라진다).
사용:  python3 blocks.py "부산 썬팅" "썬팅업체"        # 블록 구성 스캔
       python3 blocks.py --blog ksmrnd1 "부산 썬팅"    # 내 블로그 노출까지 확인
"""
from __future__ import annotations

import json
import os
import sys
import time

# ★ 서버 이관(2026-08-05): 맥북 홈 경로는 서버에 없다. 볼륨을 쓰되 없으면 상대경로.
#   경로 규칙은 immune.data_root() 하나를 쓴다 — 새 규칙을 만들면 그게 경로 이원화다.
try:
    from app.services.immune import data_root as _dr
    OUT = _dr()
except Exception:
    OUT = os.path.expanduser("~/business/insight")

_JS = """() => {
  const norm = t => (t||'').replace(/\\s+/g,' ').trim().slice(0,40);
  // ★ 2026-08-01 실측 결함 수정 2건:
  //   ① 모바일 통합검색의 인기글·블로그 링크는 blog.naver.com 직링크가 아니라 리다이렉트
  //      (cr/crd/rd?...&url=...%2Fblog.naver.com%2F...)다 → href를 디코드해서 blogId를 캐낸다.
  //   ② 'h.closest(section)'은 블록 제목과 결과 목록이 다른 컨테이너라 빗나갔다 → 제목을 문서
  //      순서로 늘어놓고, 각 링크가 '어느 제목 뒤에 오는가'로 귀속한다(구조 변화에 강함).
  const blogId = a => {
    let h = a.getAttribute('href') || a.href || '';
    try { h = decodeURIComponent(h); } catch (e) {}
    const m = h.match(/blog\\.naver\\.com\\/([A-Za-z0-9_-]+)/)
           || h.match(/[?&](?:blogId|blogid)=([A-Za-z0-9_-]+)/);
    return m ? m[1] : null;
  };
  const heads = [...document.querySelectorAll('h2,h3')]
      .map(h => ({el: h, title: norm(h.innerText)}))
      .filter(h => h.title && h.title.length >= 2);
  const blocks = heads.map(h => ({title: h.title, blogs: []}));
  const allBlogs = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const id = blogId(a);
    if (!id || id === 'PostView' || id === 'PostList') continue;
    allBlogs.push(id);
    let idx = -1;                       // 이 링크 '앞'에 있는 마지막 제목 = 소속 블록
    for (let i = 0; i < heads.length; i++) {
      const pos = heads[i].el.compareDocumentPosition(a);
      if (pos & Node.DOCUMENT_POSITION_FOLLOWING) idx = i; else break;
    }
    if (idx >= 0 && !blocks[idx].blogs.includes(id)) blocks[idx].blogs.push(id);
  }
  blocks.forEach(b => { b.blogs = b.blogs.slice(0, 8); });
  return {blocks, allBlogs: [...new Set(allBlogs)], textLen: document.body.innerText.length};
}"""


def scan(keywords: list, my_blog: str = "", show: bool = False) -> list:
    from playwright.sync_api import sync_playwright
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not show)
        pg = b.new_page(viewport={"width": 420, "height": 900},
                        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                                    "Mobile/15E148 Safari/604.1"))
        for kw in keywords:
            try:
                pg.goto("https://m.search.naver.com/search.naver?query=" + kw.replace(" ", "+"),
                        wait_until="networkidle", timeout=45000)
                for _ in range(5):                       # 지연 로딩 유도(사람 스크롤 수준)
                    pg.mouse.wheel(0, 2000)
                    pg.wait_for_timeout(900)
                d = pg.evaluate(_JS)
            except Exception as e:
                rows.append({"keyword": kw, "error": repr(e)[:80]})
                continue
            seen, blocks = set(), []
            for blk in d.get("blocks", []):
                t = blk["title"]
                if t in seen or t in ("최근 검색어",):
                    continue
                seen.add(t)
                blocks.append(blk)
            mine = [blk["title"] for blk in blocks if my_blog and my_blog in blk["blogs"]]
            # 노출 판정도 같은 기준: 내 플레이스에 걸린 내 블로그 링크는 '검색 노출'이 아니다.
            mine_real = [blk["title"] for blk in blocks
                         if my_blog and my_blog in blk["blogs"] and len(blk["blogs"]) >= 2]
            # 🚧 수집 게이트 — 결과 지면이 0이면 '수집 실패'다(빈칸+사유). 껍데기를 지도로 쓰지 않는다.
            from app.services.scout import gate as _gate
            _v = _gate.verdict([blk["title"] for blk in blocks],
                               d.get("allBlogs") or [], d.get("textLen") or 0)
            if not _v["ok"]:
                rows.append({"keyword": kw, "collect_failed": True,
                             "reasons": _v["reasons"], "blocks": [], "blog_blocks": [],
                             "blogs_seen": [], "my_blocks": [], "my_real_blocks": [],
                             "my_visible": None})
                time.sleep(2.5)
                continue
            rows.append({"keyword": kw,
                         "blocks": [blk["title"] for blk in blocks],
                         # ★ '블로그 지면'은 블로그 글이 여러 개 나열된 블록만 인정한다.
                         #   실측 2026-08-01: 플레이스 블록에도 업체가 등록한 블로그 링크가 1개
                         #   딸려 있어, 링크 유무로 세면 지면 없는 판이 있는 판으로 뒤집힌다.
                         "blog_blocks": [blk["title"] for blk in blocks if len(blk["blogs"]) >= 2],
                         "blogs_seen": d.get("allBlogs", [])[:10],
                         "my_blocks": mine,
                         "my_real_blocks": mine_real,
                         "my_visible": bool(mine_real)})
            time.sleep(2.5)                              # 저속(사람 속도)
        b.close()
    os.makedirs(OUT, exist_ok=True)
    json.dump(rows, open(f"{OUT}/last_blocks.json", "w"), ensure_ascii=False, indent=1)
    return rows


def push(tenant_id: str, rows: list) -> None:
    """서버 반영 — 가게 진단·작전이 '이 판에 블로그 지면이 있는가'를 참조하게 한다."""
    import base64, urllib.request
    SERVER = os.environ.get("OLLINDA_URL", "https://ollinda.kr")
    AUTH = os.environ.get("OLLINDA_ADMIN", "admin:4f9fa52e7d67")
    body = json.dumps({"tenant": tenant_id, "rows": rows}, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{SERVER}/admin/blocks-ingest", data=body,
                                 headers={"Content-Type": "application/json"})
    req.add_header("Authorization", "Basic " + base64.b64encode(AUTH.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print("서버 반영:", r.read().decode()[:120])
    except Exception as e:
        print("⚠️ 서버 전송 실패:", repr(e)[:90])


if __name__ == "__main__":
    args = sys.argv[1:]
    blog = ""
    if "--blog" in args:
        i = args.index("--blog")
        blog = args[i + 1]
        args = args[:i] + args[i + 2:]
    show = "--show" in args
    args = [a for a in args if a != "--show"]
    tenant = ""
    if "--tenant" in args:
        i = args.index("--tenant")
        tenant = args[i + 1]
        args = args[:i] + args[i + 2:]
    if not args:
        print(__doc__)
        sys.exit(1)
    _rows = scan(args, my_blog=blog, show=show)
    if tenant:
        push(tenant, _rows)
    for r in _rows:
        if r.get("error"):
            print(f"■ {r['keyword']}: 실패 {r['error']}")
            continue
        print(f"\n■ '{r['keyword']}' 통합검색 구성")
        print("   블록:", " / ".join(r["blocks"][:10]) or "(없음)")
        print("   블로그 실린 블록:", " / ".join(r["blog_blocks"]) or "없음 ← 블로그 글이 통합검색에 안 뜸")
        if blog:
            _own = [x for x in (r.get("my_blocks") or []) if x not in (r.get("my_real_blocks") or [])]
            print(f"   내 블로그({blog}) 노출:",
                  ("✅ " + ", ".join(r["my_real_blocks"])) if r["my_visible"] else "❌ 미노출",
                  (f"(참고: {', '.join(_own)}은 내 플레이스에 걸린 링크 — 검색 노출 아님)" if _own else ""))
