"""롱테일 조합기 — 업종 스키마 문법 × '증명된 속성값'으로 검색어를 만든다.

왜 따로 있는가(2026-08-13):
  빈자리 정찰의 후보가 [이미 쓴 키워드]에서만 나오는 닫힌 루프였다. 그래서 지도에는
  '지역+업종'(부산 중고차·썬팅 가격) 같은 대형 판만 쌓이고, 정작 이길 수 있는
  소재 롱테일('EV5 썬팅')은 후보에 들어오지도 못했다. 점수 산식을 아무리 고쳐도
  재료가 대형 키워드뿐이면 그중 덜 나쁜 것을 고를 뿐이다.

canonical 단일 관문:
  치환 규칙(문법 템플릿 → 검색어)은 이 파일에만 산다. 큐 적재(autoqueue)와
  지면 정찰(blogreach)이 같은 함수를 쓴다 — 규칙이 두 곳에 살면 그 자체가 결함이다.

정직 게이트:
  속성값(attrs)은 **호출부가 증명한 것만** 넘긴다. 이 파일은 스키마 예시 토큰을
  스스로 끌어다 쓰지 않는다. 스키마의 예시 차종은 '업로드 인식'용이지 '없는 매물
  키워드 생성'용이 아니다(딜러에게 없는 캐스퍼로 유령 글이 나갔던 계열).
"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger("shopcast.longtail")

# 문법이 없을 때의 최소 틀 — 업종 어휘 0(플레이스홀더뿐)
_FALLBACK_GRAMMAR = ["{속성} {업종}", "{지역} {업종}"]


def wide_region(region: str) -> str:
    """검색어에 쓸 광역 지명 — '부산광역시'는 아무도 안 친다(실측: 검색량 0)."""
    for tk in (region or "").split():
        if re.search(r"(특별시|광역시|특별자치시|특별자치도|도)$", tk):
            return re.sub(r"(특별시|광역시|특별자치시|특별자치도|자치도|도)$", "", tk)
    return ""


def grammars_for(t) -> list[str]:
    """이 가게 업종의 검색 문법. 실패해도 파이프라인을 세우지 않는다(최소 틀로)."""
    try:
        from app.services import indschema as _isc
        g = (_isc.get_schema(getattr(t, "industry", "") or "",
                             getattr(t, "biz_type", "") or "local").get("search_grammar") or [])
        return [x for x in g if isinstance(x, str) and x.strip()] or list(_FALLBACK_GRAMMAR)
    except Exception:
        _log.exception("[longtail] 스키마 문법 조회 실패 — 최소 틀로 진행")
        return list(_FALLBACK_GRAMMAR)


def _dedupe_adjacent(kw: str) -> str:
    """겹말 정리 — '젤네일 네일'처럼 한 낱말이 옆 낱말에 이미 들어 있으면 짧은 쪽을 뺀다.

    속성값이 업종어를 품는 업종에서 생긴다(네일의 '젤네일', 썬팅의 '신차썬팅').
    사람은 이렇게 검색하지 않는다. 언어 규칙만 쓴다 — 업종어 목록 0.
    """
    toks = kw.split()
    outt: list[str] = []
    for w in toks:
        if outt and (outt[-1] in w or w in outt[-1]):
            if len(w) > len(outt[-1]):
                outt[-1] = w              # 더 구체적인 쪽을 남긴다
            continue
        outt.append(w)
    return " ".join(outt)


def combos(t, attrs: list, *, years: list | None = None, grammars: list | None = None,
           extra_tail: bool = True) -> list[str]:
    """문법 × 증명된 속성값 → 검색어 후보(긴 것 먼저, 중복 제거).

    attrs: 호출부가 '실재한다'고 증명한 속성값만(재고 모델·사장님 실데이터 낱말 등).
           비어 있으면 속성 조합은 만들지 않는다 — 빈칸으로 두는 것이 폴백이다.
    """
    ind0 = ((getattr(t, "industry", "") or "").replace("/", ",").split(",")[0] or "").strip()
    wide = wide_region(getattr(t, "region", "") or "")
    gs = grammars if grammars is not None else grammars_for(t)
    yrs = [y for y in (years or []) if y]
    out: list[str] = []

    def _emit(g: str, subs: dict, attr: str = ""):
        """플레이스홀더를 채운다.

        ★ 2026-08-13 사장님 지적으로 발견: 예전엔 치환 이름을 {속성}·{차종}으로만 알아들었다.
          그래서 스키마가 {향}(캔들)·{디자인}(네일)처럼 자기 업종 말을 쓰면 속성값이 통째로
          사라지고 '부산 캔들' 같은 제네릭만 남았다 — 소재 롱테일이 차량계 업종에만 생겼다.
          구조 자리(지역·업종·의도·연식)를 뺀 **나머지 이름은 전부 속성 자리로 본다.**
          스키마가 어떤 말을 쓰든 동작한다(업종 중립).
        """
        def _sub(m):
            name = m.group(1)
            if name in subs:
                return str(subs[name] or "")
            return str(attr or "")           # 모르는 이름 = 그 업종의 속성 자리
        kw = " ".join(re.sub(r"\{([^}]*)\}", _sub, g).split())
        kw = _dedupe_adjacent(kw)
        if kw and len(kw) >= 3:
            out.append(kw)

    _struct = {"지역": wide, "업종": ind0, "의도": "추천", "연식": (yrs[0] if yrs else "")}
    for a in attrs or []:
        for g in gs:
            _emit(g, _struct, attr=a)
    if extra_tail:                       # 광역+업종 폴백은 항상 포함(기존 동작 보존)
        # 속성값 없이 부르는 자리라 '모르는 이름'이 빈칸이 되도록 attr을 주지 않는다
        _emit("{지역} {업종} 추천", _struct)
        _emit("{업종} 추천", _struct)

    seen, uniq = set(), []
    for kw in out:
        k = " ".join(kw.split())
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def proven_axis_values(tenant_id: str, t, limit: int = 12) -> list[str]:
    """사장님 실데이터에 **실제로 있는** 속성축 값만 골라낸다(추측 0).

    owner_domain = 발행 이력·과거 세트·실경험 Q&A·재고 맥락에서 모은 실낱말.
    여기에 속하지 않는 스키마 예시 토큰은 절대 돌려주지 않는다 — 그것이 유령 키워드의 입구다.
    """
    try:
        from app.services import gapscout as _gs
        dom = (_gs.owner_domain(tenant_id) or {}).get("tokens") or set()
        if not dom:
            return []
        vals: list[str] = []
        for ax in _gs._axis_tokens(t):
            for tok in ax.get("tokens") or []:
                if len(tok) >= 2 and tok in dom and tok not in vals:
                    vals.append(tok)
        return vals[:max(1, limit)]
    except Exception:
        _log.exception("[longtail] 증명된 속성값 수집 실패 t=%s", tenant_id)
        return []
