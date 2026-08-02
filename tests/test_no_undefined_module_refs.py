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

# 지역 import로 쓰는 관례가 있는 무거운 모듈들(전역 import를 강제하지 않는다)
WATCHED = ("seo", "db", "vision", "storage", "llm", "auth", "oauth")


def _module_globals(tree: ast.Module) -> set:
    out = set()
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(n, (ast.Try, ast.If)):          # try/except import 블록
            for sub in ast.walk(n):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        out.add(a.asname or a.name.split(".")[0])
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


def test_no_undefined_module_references():
    """모듈을 쓰면서 import를 빠뜨린 곳이 없어야 한다.
    실사고: seo.mobile_spec_gate → NameError → 큐 생성 전멸(조용히)."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    bad = []
    for f in sorted(root.rglob("*.py")):
        tree = ast.parse(f.read_text())
        glob = _module_globals(tree)
        for fn in [x for x in ast.walk(tree)
                   if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            local = _bound_in(fn)
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                    continue
                nm = n.value.id
                if nm in WATCHED and nm not in glob and nm not in local:
                    bad.append(f"{f.relative_to(root.parent)}:{n.lineno} "
                               f"{fn.name}() → {nm}.{n.attr} (import 없음)")
    assert not bad, "미정의 모듈 참조 — 실행하면 NameError로 죽는다:\n  " + "\n  ".join(bad)
