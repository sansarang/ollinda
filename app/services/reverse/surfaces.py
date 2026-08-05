"""🧭 지면 식별 — 무엇이 어느 지면인가.

★ 2026-08-06 실측으로 확정: 네이버 모바일 통합검색은 **data-template-id**로 지면을 나눈다.
  h2/h3 제목이 아니다 — 그걸로 긁었더니 '추천 검색어·플레이스 MY·숏텐츠 NOW' 같은
  UI 껍데기가 검색 블록으로 잡혔다(2026-08-05 사고). 정답지를 그 위에 쌓으면 통째로 오염된다(R3).

실측 근거(2026-08-06, '자동차 썬팅 농도 기준'):
  aipickItem×10   ← AI 브리핑이 인용하는 **채널**(블로그·카페) + 공개 인용수
  articleSource×13 ← 브리핑 출처로 표시된 **글**
  ugcItem×13      ← 블로그·카페 글
  clipItem, imageItem, reply, contentList, header, footer
  '부산 동구 썬팅업체'(상업성)에는 aipickItem이 없다 — 정보성 질의에만 브리핑이 뜬다.

★ 인용수는 **채널 단위 누적**이지 그 글이 인용됐다는 뜻이 아니다(R6).
  '이 채널이 159만 번 인용됨'과 '이 글이 브리핑에 인용됨'은 다른 주장이다. 섞지 않는다.
"""
from __future__ import annotations

# 지면 이름 ← data-template-id. 값은 실측에서 나온 것이고 추측이 아니다.
SURFACE_BY_TPL = {
    # ★ aipickItem만이 브리핑 인용의 증거다. 브리핑이 없는 질의에는 아예 나오지 않는다(실측).
    "aipickItem": "ai_brief_channel",    # 브리핑이 인용하는 채널(+공개 누적 인용수)
    # ★ 2026-08-06 실측 반증: articleSource는 브리핑 전용이 아니다.
    #   브리핑 표지가 없는 '부산 동구 썬팅업체'에도 8건 나왔다.
    #   이걸 '브리핑 인용'으로 라벨하면 거짓 라벨이다(R6) — 일반 출처 표시로 부른다.
    "articleSource": "article_source",   # 결과 글의 출처 표시(브리핑 여부와 무관)
    "ugcItem": "ugc",                    # 블로그·카페 글
    "clipItem": "clip",
    "imageItem": "image",
    "videoItem": "video",
    "kinItem": "kin",                    # 지식iN
}

# 브리핑 인용 라벨을 붙일 수 있는 지면 — 이것만이 R6의 '실제 브리핑 출처 표시'다.
CITED_SURFACES = ("ai_brief_channel",)
# 지면이 아닌 것 — 수집 대상에서 뺀다(R3: UI_CHROME 제외)
NOT_SURFACE = ("layout", "header", "footer", "reply", "contentList", "sdsVerticalLayout")

# 페이지에서 지면별 항목을 뽑는 JS. 파싱 규칙은 여기 하나뿐이다(R4).
EXTRACT_JS = """() => {
  const norm = t => (t || '').replace(/\\s+/g, ' ').trim();
  // ★ 2026-08-06 실측: 항목 안 첫 번째 a는 '채널 홈'(m.blog.naver.com/id)이고
  //   글 URL은 그 뒤에 온다. 첫 링크에서 멈추면 글을 하나도 못 캔다(13건 중 1건).
  //   카페 글도 정답지다 — blog만 보면 표본의 절반을 버린다.
  const refOf = href => {
    let h = href || '';
    try { h = decodeURIComponent(h); } catch (e) {}
    const m = h.match(/(blog|cafe)\\.naver\\.com\\/([A-Za-z0-9_-]+)\\/(\\d{6,})/);
    if (m) return {kind: m[1], blog: m[2], post: m[3]};
    const c = h.match(/(blog|cafe)\\.naver\\.com\\/([A-Za-z0-9_-]+)/);
    return c ? {kind: c[1], blog: c[2], post: null} : null;
  };
  const items = [];
  for (const el of document.querySelectorAll('[data-template-id]')) {
    const tpl = el.getAttribute('data-template-id');
    // 글 URL이 있는 링크를 우선한다 — 채널 홈에서 멈추지 않는다
    let ref = null, href = '';
    for (const a of el.querySelectorAll('a[href]')) {
      const raw = a.getAttribute('href') || a.href || '';
      const got = refOf(raw);
      if (!got) continue;
      if (got.post) { ref = got; href = raw; break; }
      if (!ref) { ref = got; href = raw; }
    }
    const txt = norm(el.innerText);
    if (!txt) continue;
    items.push({tpl: tpl, text: txt.slice(0, 300), blog: ref ? ref.blog : null,
                post: ref ? ref.post : null, kind: ref ? ref.kind : null,
                href: (href || '').slice(0, 300),
                cites: (txt.match(/([\\d,]+)만?\\s*인용/) || [])[1] || null,
                // 채널 ID를 못 캐는 항목이 있다 — 이름이라도 남겨 인용 라벨을 잃지 않는다.
                // 다만 이름 매칭은 동명이인 위험이 있으므로 ID가 있을 때만 라벨로 쓴다(R6).
                name: (txt.split(/\\s+/).slice(0, 6).join(' ') || '').slice(0, 40)});
  }
  const t = document.body.innerText || '';
  return {items: items, textLen: t.length,
          hasBrief: t.includes('AI 브리핑') || t.includes('AI브리핑')};
}"""


def classify(items: list) -> dict:
    """template-id → 지면별로 나눈다. 지면이 아닌 것은 버린다."""
    out = {}
    for it in (items or []):
        tpl = it.get("tpl") or ""
        if tpl in NOT_SURFACE:
            continue
        name = SURFACE_BY_TPL.get(tpl)
        if not name:
            continue                      # 모르는 template은 넣지 않는다(짐작 금지)
        out.setdefault(name, []).append(it)
    return out


def unknown_templates(items: list) -> list:
    """분류표에 없는 template — 네이버가 구조를 바꾸면 여기로 드러난다(조용히 넘기지 않는다)."""
    seen = {}
    for it in (items or []):
        tpl = it.get("tpl") or ""
        if tpl and tpl not in SURFACE_BY_TPL and tpl not in NOT_SURFACE:
            seen[tpl] = seen.get(tpl, 0) + 1
    return sorted(seen.items(), key=lambda x: -x[1])


def verify(items: list, has_brief: bool) -> dict:
    """R3 선행 검증 — 지면이 실제로 갈렸는가. 이게 통과해야 정답지를 쌓을 수 있다.

    ★ 2026-08-06 정정: 처음엔 'AI 브리핑' 표지어와 aipickItem 유무가 일치해야 통과로 봤다.
      그런데 표지어는 있는데 지면이 없는 판이 실측으로 나왔다('썬팅 필름 종류 차이').
      **표지어 존재 ≠ 브리핑 지면 존재**다 — 텍스트에 낱말이 있다고 지면이 있는 게 아니다.
      브리핑 유무의 사실은 aipickItem이고, 표지어는 참고 신호로만 남긴다.
    통과 조건: 지면이 하나라도 갈렸고, 모르는 template이 없을 것(구조가 바뀌면 드러나야 한다).
    """
    by = classify(items)
    unknown = unknown_templates(items)
    return {
        "surfaces": {k: len(v) for k, v in by.items()},
        "has_brief_marker": has_brief,          # 참고용 — 판정 근거가 아니다
        "brief_surface": bool(by.get("ai_brief_channel")),   # 사실은 이쪽이다
        "marker_vs_surface": ("표지어만 있고 지면 없음"
                              if has_brief and not by.get("ai_brief_channel") else ""),
        "unknown_templates": unknown[:6],
        "ok": bool(by) and not unknown,
    }
