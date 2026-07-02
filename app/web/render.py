"""
서버렌더 HTML 헬퍼 — Tailwind(CDN), 화이트+블루. 반응형(폰/PC).
MVP용 최소 템플릿. 추후 분리/프레임워크화 가능.
"""
from __future__ import annotations

import html

_HEAD = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://cdn.tailwindcss.com"></script>
</head><body class="bg-slate-50 text-slate-800 min-h-screen">
<div class="max-w-3xl mx-auto p-5">"""

_FOOT = "</div></body></html>"


def esc(s: str) -> str:
    return html.escape(s or "")


def page(title: str, body: str) -> str:
    return _HEAD.format(title=esc(title)) + body + _FOOT


def nav(active: str = "") -> str:
    def link(href, label, key):
        cls = "font-bold text-blue-600" if key == active else "text-slate-600 hover:text-slate-900"
        return f'<a href="{href}" class="{cls} text-sm">{label}</a>'
    return ('<div class="flex items-center gap-4 mb-6 pb-3 border-b">'
            '<a href="/admin" class="font-extrabold text-lg text-blue-700">shopcast</a>'
            + link("/admin", "검수", "dash")
            + link("/admin/board", "현황판", "board")
            + link("/admin/shops", "가게", "shops")
            + '</div>')


_ICONS = {
    "review": '<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    "board": '<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>',
    "shops": '<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7l1.5-3h15L21 7M4 7h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V7zm4 5h8"/></svg>',
    "dot": '<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/></svg>',
    "ops": '<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/></svg>',
}


def _si(href: str, label: str, key: str, active: str, icon: str = "dot") -> str:
    on = key == active
    cls = ("bg-indigo-600 text-white shadow-lg shadow-indigo-900/40" if on
           else "text-slate-400 hover:bg-slate-800 hover:text-white")
    return (f'<a href="{href}" class="flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition {cls}">'
            f'{_ICONS.get(icon, _ICONS["dot"])}<span>{label}</span></a>')


def shell(active: str, title: str, body: str, subtitle: str = "", actions: str = "") -> str:
    """VMAS급 운영자 대시보드 셸 — 다크 사이드바 + 상단바 + 콘텐츠."""
    sidebar = (
        '<aside class="hidden md:flex w-60 bg-slate-900 text-white flex-col fixed h-full z-30">'
        '<div class="px-5 h-16 flex items-center gap-2 border-b border-white/5">'
        '<span class="font-extrabold text-lg">올린다</span>'
        '<span class="text-[10px] text-slate-500 font-medium">Ollinda</span></div>'
        '<nav class="flex-1 overflow-y-auto px-3 py-4 space-y-1">'
        '<p class="px-3 text-[11px] font-semibold text-slate-500 mb-1">메인</p>'
        + _si("/admin/ops", "운영 관제탑", "ops", active, "ops")
        + _si("/admin", "검수 대기", "review", active, "review")
        + _si("/admin/board", "포스팅 현황판", "board", active, "board")
        + '<p class="px-3 text-[11px] font-semibold text-slate-500 mt-4 mb-1">채널별</p>'
        + _si("/admin/board?channel=instagram", "인스타그램", "c_ig", active)
        + _si("/admin/board?channel=naver_blog", "네이버 블로그", "c_nv", active)
        + _si("/admin/board?channel=youtube", "유튜브 쇼츠", "c_yt", active)
        + _si("/admin/board?channel=x", "X (트위터)", "c_x", active)
        + '<p class="px-3 text-[11px] font-semibold text-slate-500 mt-4 mb-1">관리</p>'
        + _si("/admin/shops", "가게 관리", "shops", active, "shops")
        + _si("/admin/users", "구독자 관리", "users", active, "shops")
        + _si("/admin/industries", "업종 프로필", "industries", active)
        + '</nav>'
        '<div class="px-3 py-4 border-t border-white/5">'
        '<a href="/" class="flex items-center gap-3 px-3 py-2 rounded-xl text-sm text-slate-400 hover:text-white hover:bg-slate-800 transition">🌐 사이트 보기</a></div>'
        '</aside>')
    sub = f'<p class="text-sm text-slate-400 mt-0.5">{esc(subtitle)}</p>' if subtitle else ""
    header = (
        '<header class="h-16 bg-white/90 backdrop-blur border-b border-slate-200 flex items-center px-6 sm:px-8 sticky top-0 z-20">'
        f'<div><h1 class="text-lg font-bold text-slate-800 leading-tight">{esc(title)}</h1>{sub}</div>'
        f'<div class="ml-auto flex items-center gap-3">{actions}</div></header>')
    head = ('<!doctype html><html lang=ko><head><meta charset=utf-8>'
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} · 올린다</title>'
            '<script src="https://cdn.tailwindcss.com"></script>'
            '<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.min.css" rel="stylesheet">'
            '<style>body{font-family:Pretendard,system-ui,sans-serif}</style>'
            '</head><body class="bg-slate-50 text-slate-800">')
    return (head + '<div class="flex min-h-screen">' + sidebar
            + '<div class="flex-1 md:ml-60 min-w-0">' + header
            + f'<main class="p-6 sm:p-8">{body}</main></div></div></body></html>')


def stat_card(label: str, value, color: str = "slate") -> str:
    return (f'<div class="bg-white rounded-2xl border border-slate-100 p-5 shadow-sm">'
            f'<div class="text-3xl font-extrabold text-{color}-600">{value}</div>'
            f'<div class="text-sm text-slate-500 mt-1">{esc(label)}</div></div>')


def badge(status: str) -> str:
    colors = {"draft": "bg-amber-100 text-amber-700", "approved": "bg-blue-100 text-blue-700",
              "published": "bg-green-100 text-green-700", "rejected": "bg-rose-100 text-rose-600",
              "failed": "bg-rose-100 text-rose-600"}
    c = colors.get(status, "bg-slate-100 text-slate-600")
    return f'<span class="px-2 py-0.5 rounded text-xs font-semibold {c}">{esc(status)}</span>'
