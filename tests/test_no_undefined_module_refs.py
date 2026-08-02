"""
전역 미정의 모듈 참조 박제(2026-08-02 실사고).

사고: autoqueue.consume()이 `seo.mobile_spec_gate(...)`를 부르는데 그 모듈은 seo를
전역 import하지 않았다(다른 곳은 전부 지역 import). NameError로 큐 생성이 통째로 죽었고,
실패가 조용해서(큐 행이 pending으로 되돌아갈 뿐) 오래 안 보였다.
같은 종류를 훑어보니 main.py에도 3건이 더 있었다 — 그중 하나는 사용자 재생성 경로였다.

이 테스트는 import 하나 빠뜨린 실수를 문법 수준에서 잡는다. 실행해봐야 아는 버그를
실행 전에 잡는다 — 백그라운드 스레드에서 죽으면 아무도 모른다.
"""
from __future__ import annotations

import ast
import pathlib

# ★ 처음엔 몇 개만 지켜봤다(seo·db·vision…). 그러다 같은 실수를 're'로 또 냈다 —
#   목록을 늘리는 방식은 다음 실수를 또 놓친다. 이름 목록을 버리고 '어디에도 안 묶인 이름'을
#   전부 잡는다(모듈 전역 · 함수 안 바인딩 · 파이썬 내장 어디에도 없는 것).
import builtins

_BUILTIN = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "self", "cls"}


def _module_globals(tree: ast.Module) -> set:
    """모듈 전역에 묶이는 이름 전부 — 함수 본문 안으로는 내려가지 않는다(그건 지역이다).
    try/if 블록 안의 import, 타입 주석 대입(x: dict = {}), for·with 대상까지 센다."""
    out = set()

    def walk(nodes):
        for n in nodes:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    out.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(n.name)                       # 본문은 안 본다(지역 영역)
            elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                for t in tgts:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name):
                            out.add(sub.id)
            elif isinstance(n, (ast.For, ast.AsyncFor)):
                for sub in ast.walk(n.target):
                    if isinstance(sub, ast.Name):
                        out.add(sub.id)
                walk(n.body + n.orelse)
            elif isinstance(n, ast.With):
                for item in n.items:
                    if item.optional_vars is not None:
                        for sub in ast.walk(item.optional_vars):
                            if isinstance(sub, ast.Name):
                                out.add(sub.id)
                walk(n.body)
            elif isinstance(n, ast.Try):
                walk(n.body + n.orelse + n.finalbody)
                for h in n.handlers:
                    if h.name:
                        out.add(h.name)
                    walk(h.body)
            elif isinstance(n, ast.If):
                walk(n.body + n.orelse)
            elif isinstance(n, ast.While):
                walk(n.body + n.orelse)
    walk(tree.body)
    return out


def _bound_in(fn) -> set:
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
    return out


def _top_functions(tree):
    """중첩 함수는 바깥 함수의 바인딩을 물려받는다 — 최상위 함수 단위로만 본다(오탐 방지)."""
    out, nested = [], set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(fn):
                if sub is not fn and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    nested.add(id(sub))
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(fn) not in nested:
            out.append(fn)
    return out


def test_no_undefined_module_references():
    """모듈·이름을 쓰면서 import를 빠뜨린 곳이 없어야 한다.
    실사고 2건(2026-08-02): seo.mobile_spec_gate → 큐 생성 전멸 / re.findall → 실경험 주입 불능.
    둘 다 백그라운드 스레드에서 죽어 화면엔 아무것도 안 보였다."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    bad = []
    for f in sorted(root.rglob("*.py")):
        tree = ast.parse(f.read_text())
        glob = _module_globals(tree)
        for fn in _top_functions(tree):
            local = _bound_in(fn)
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                    continue
                nm = n.value.id
                if nm in glob or nm in local or nm in _BUILTIN:
                    continue
                bad.append(f"{f.relative_to(root.parent)}:{n.lineno} "
                           f"{fn.name}() → {nm}.{n.attr} (어디에도 안 묶인 이름)")
    assert not bad, "미정의 이름 참조 — 실행하면 NameError로 죽는다:\n  " + "\n  ".join(bad)
