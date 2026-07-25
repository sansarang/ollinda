"""
트랙2 대시보드 D1·D2·D3 렌더(사장 언어 — 지표 용어 금지). gowatch 데이터를 HTTP로 받아 HTML 생성.

D1 개선 제안 카드(홈, 이벤트 시에만) / D2 상태 배너(상단, 이상 시에만 — 정상이면 "") /
D3 관측 현황 탭(발행 글별 표, null 지면 = "측정 준비 중" ≠ 미노출).
gowatch 미배포/불통이면 D1·D3는 빈 문자열(무해), D2는 워커 불통 배너.
"""
from __future__ import annotations

import html

from app import db
from app.services import gowatch_client


def _esc(s) -> str:
    return html.escape(str(s or ""))


# ─────────────────────────── D1 개선 제안 카드 ───────────────────────────
def render_d1(tenant_id: str) -> str:
    """이벤트(제안) 있을 때만 카드. 없으면 "". 알림이 아니라 '다음 행동'이 산출물."""
    props = db.list_proposals(tenant_id, status="proposed", limit=5)
    if not props:
        return ""
    cards = []
    for p in props:
        c = p.get("card") or {}
        icon = _esc(c.get("icon") or "✨")
        headline = _esc(c.get("headline") or "개선 제안이 있어요")
        sub = _esc(c.get("sub") or "")
        prev = c.get("preview") or {}
        prev_html = ""
        if prev.get("title") or prev.get("snippet"):
            prev_html = (
                "<div class='mt-2 bg-white/70 rounded-xl p-3 border border-indigo-100'>"
                f"<div class='font-bold text-sm text-slate-800'>{_esc(prev.get('title'))}</div>"
                f"<div class='text-xs text-slate-500 mt-1 line-clamp-2'>{_esc(prev.get('snippet'))}</div></div>")
        act = c.get("action") or {}
        btn = ""
        if act.get("label"):
            btn = (f"<a href='{_esc(act.get('href') or '/me')}' "
                   "class='inline-block mt-3 bg-indigo-600 text-white font-bold text-sm px-4 py-2 rounded-xl'>"
                   f"{_esc(act.get('label'))} →</a>")
        cards.append(
            "<div class='bg-indigo-50 border border-indigo-100 rounded-2xl p-4'>"
            f"<div class='flex items-start gap-2'><div class='text-2xl'>{icon}</div>"
            f"<div class='flex-1'><div class='font-bold text-[15px] text-slate-900'>{headline}</div>"
            f"<div class='text-sm text-slate-600 mt-0.5'>{sub}</div>{prev_html}{btn}</div></div></div>")
    return ("<div class='mb-4'>"
            "<div class='text-xs font-bold text-indigo-500 mb-2'>오늘의 개선 제안</div>"
            "<div class='space-y-3'>" + "".join(cards) + "</div></div>")


# ─────────────────────────── D2 상태 배너 ───────────────────────────
def render_d2(tenant_id: str) -> str:
    """이상 시에만. 정상이면 "". 원인+다음 행동만. 워커 생존은 gorender·gowatch /health 체크."""
    def banner(color: str, text: str, btn_label: str = "", btn_href: str = "") -> str:
        b = ""
        if btn_label:
            b = (f"<a href='{_esc(btn_href or '/me')}' class='ml-auto whitespace-nowrap bg-white/80 "
                 f"font-bold text-xs px-3 py-1.5 rounded-lg border'>{_esc(btn_label)}</a>")
        return (f"<div class='flex items-center gap-2 {color} p-3 rounded-xl mb-4 text-sm'>"
                f"<span>{text}</span>{b}</div>")

    # 1) 색인 소실 — 사장에게 가장 시급(제안 큐에 index_lost 있으면)
    for p in db.list_proposals(tenant_id, status="proposed", limit=10):
        if p.get("kind") == "index_lost":
            return banner("bg-rose-50 text-rose-700", "글이 검색에서 빠졌어요", "재발행 키트",
                          f"/me/proposal/{_esc(p.get('adaptation_id'))}")

    # 2) 순위 확인 워커(gowatch) 생존/지연
    gh = gowatch_client.health()
    if gowatch_client.configured():
        if gh is None:
            return banner("bg-amber-50 text-amber-700", "순위 확인이 잠시 멈춰 있어요 — 확인 중입니다")
        if gh.get("collect_stale"):
            return banner("bg-amber-50 text-amber-700", "순위 확인이 이틀째 안 되고 있어요 — 확인 중입니다")

    # 3) 영상 워커(gorender) 생존
    try:
        from app.services import render_backend as _rb
        import json as _json
        import urllib.request as _u
        gurl = (_rb.GORENDER_URL or "").rstrip("/")
        if gurl:
            try:
                with _u.urlopen(gurl + "/health", timeout=5) as r:
                    _json.loads(r.read().decode("utf-8"))
            except Exception:
                return banner("bg-amber-50 text-amber-700", "영상 준비가 늦어지고 있어요", "다시 만들기", "/me")
    except Exception:
        pass
    return ""


# ─────────────────────────── D3 관측 현황 탭 ───────────────────────────
def _rank_cell(sr) -> str:
    if sr is None:
        return "<span class='text-slate-400'>미노출</span>"
    return f"<b>{int(sr)}위</b>"


def _surface_cell(v) -> str:
    # null 지면 = '측정 준비 중'(미노출과 구분 — 사장 오독 방지). 값 있으면 노출로.
    if v is None:
        return "<span class='text-slate-400 text-xs'>측정 준비 중</span>"
    return "노출"


def _indexed_cell(ix) -> str:
    if ix is None:
        return "<span class='text-slate-400 text-xs'>확인 중</span>"
    return "○" if ix else "<span class='text-rose-500'>빠짐</span>"


def render_d3(tenant_id: str) -> str:
    """발행 글별 [키워드/순위/스마트블록/브리핑/색인/마지막 확인] 표. 390px 정상(가로 스크롤)."""
    obs = gowatch_client.list_observations(tenant=tenant_id, limit=200)
    if not obs:
        if not gowatch_client.configured():
            return "<div class='text-sm text-slate-400 p-4'>관측 워커 준비 중이에요.</div>"
        return "<div class='text-sm text-slate-400 p-4'>아직 관측 데이터가 없어요 — 첫 수집을 기다리는 중이에요.</div>"
    rows = []
    for o in obs:
        sf = o.get("surfaces") or {}
        kw = _esc(o.get("keyword") or o.get("publish_id"))
        cap = _esc((o.get("captured_at") or "")[:10])
        rows.append(
            "<tr class='border-t border-slate-100'>"
            f"<td class='py-2 pr-3 font-medium'>{kw}</td>"
            f"<td class='py-2 px-2 text-center'>{_rank_cell(sf.get('search_rank'))}</td>"
            f"<td class='py-2 px-2 text-center'>{_surface_cell(sf.get('smartblock'))}</td>"
            f"<td class='py-2 px-2 text-center'>{_surface_cell(sf.get('ai_briefing'))}</td>"
            f"<td class='py-2 px-2 text-center'>{_indexed_cell(sf.get('indexed'))}</td>"
            f"<td class='py-2 pl-2 text-right text-xs text-slate-400'>{cap}</td></tr>")
    return (
        "<div class='overflow-x-auto'><table class='w-full text-sm min-w-[420px]'>"
        "<thead><tr class='text-xs text-slate-400 text-left'>"
        "<th class='py-1 pr-3'>글 키워드</th><th class='py-1 px-2 text-center'>순위</th>"
        "<th class='py-1 px-2 text-center'>스마트블록</th><th class='py-1 px-2 text-center'>AI 브리핑</th>"
        "<th class='py-1 px-2 text-center'>색인</th><th class='py-1 pl-2 text-right'>마지막 확인</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "<div class='text-xs text-slate-400 mt-2'>‘측정 준비 중’은 아직 수집 전이라는 뜻이에요 — 노출이 안 됐다는 뜻이 아니에요.</div></div>")
