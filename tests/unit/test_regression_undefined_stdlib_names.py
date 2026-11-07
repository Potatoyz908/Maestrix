"""Regression: a stdlib module used inside a function that never imported it.

`py_compile` cannot see this, and a broad `except Exception: pass` hides the NameError at runtime. It already
happened: gtk4-ui/src/ui/window.py used `json.load` in switch_worker with no `import json` in scope (other functions
imported it locally), so with no server running a click on a workspace did nothing, and rope persistence broke too.

The check is scope-aware on purpose: an import inside ANOTHER function does not make the name visible here.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOTS = ("gtk4-ui/src", "canvas-cairo/maestri_canvas", "maestri_core")
STDLIB = {
    "json", "os", "sys", "re", "time", "math", "subprocess", "threading", "uuid", "shutil", "tempfile", "datetime",
    "pathlib", "socket", "struct", "base64", "hashlib", "random", "itertools", "functools", "collections", "logging",
    "traceback", "urllib", "signal", "select", "glob", "fnmatch", "stat", "errno", "io", "copy", "weakref", "queue",
    "asyncio", "contextlib", "webbrowser", "shlex", "textwrap", "html", "zlib", "binascii", "platform",
}
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class _Scope:
    def __init__(self, parent: "_Scope | None") -> None:
        self.parent = parent
        self.names: set[str] = set()

    def visible(self, name: str) -> bool:
        s: _Scope | None = self
        while s is not None:
            if name in s.names:
                return True
            s = s.parent
        return False


def undefined_stdlib_uses(source: str) -> list[tuple[str, int]]:
    """[(module, line)] for every `module.attr` where module is a stdlib name not visible from that scope."""
    uses: list[tuple[_Scope, str, int]] = []

    def args_of(node: ast.AST) -> list[str]:
        a = node.args  # type: ignore[attr-defined]
        names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
        return names + [x.arg for x in (a.vararg, a.kwarg) if x is not None]

    def visit(node: ast.AST, scope: _Scope) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNCS):
                if not isinstance(child, ast.Lambda):
                    scope.names.add(child.name)
                    for d in child.decorator_list:
                        visit_expr(d, scope)
                for d in list(child.args.defaults) + [x for x in child.args.kw_defaults if x is not None]:
                    visit_expr(d, scope)
                inner = _Scope(scope)
                inner.names.update(args_of(child))
                body = [child.body] if isinstance(child, ast.Lambda) else child.body
                for stmt in body:
                    visit_expr(stmt, inner)
            elif isinstance(child, ast.ClassDef):
                scope.names.add(child.name)
                visit_expr(child, scope, class_body=True)
            else:
                visit_expr(child, scope)

    def visit_expr(node: ast.AST, scope: _Scope, class_body: bool = False) -> None:
        if isinstance(node, _FUNCS) or (isinstance(node, ast.ClassDef) and not class_body):
            visit(ast.Module(body=[node], type_ignores=[]), scope)
            return
        if isinstance(node, ast.Import):
            scope.names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            scope.names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            scope.names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            scope.names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            scope.names.update(node.names)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in STDLIB:
            uses.append((scope, node.value.id, node.lineno))
        visit(node, scope)

    module = _Scope(None)
    visit(ast.parse(source), module)
    return sorted({(mod, line) for scope, mod, line in uses if not scope.visible(mod)}, key=lambda x: x[1])


def test_scanner_catches_a_module_imported_only_in_a_sibling_function() -> None:
    """The exact shape of the window.py bug: `json` imported in one function, used in another."""
    src = (
        "def a():\n    import json\n    return json.dumps({})\n"
        "def b():\n    return json.loads('{}')\n"          # NameError at runtime
    )
    assert undefined_stdlib_uses(src) == [("json", 5)]


def test_scanner_accepts_module_level_nested_and_aliased_imports() -> None:
    src = (
        "import os\nimport json as js\n"
        "def f():\n    return os.getcwd(), js.dumps({})\n"
        "def outer():\n    import re\n    def inner():\n        return re.compile('x')\n    return inner\n"
    )
    assert undefined_stdlib_uses(src) == []


def test_no_stdlib_module_is_used_without_an_import_in_scope() -> None:
    problems: list[str] = []
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            for mod, line in undefined_stdlib_uses(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(REPO)}:{line}: '{mod}' is used but not imported in scope")
    assert not problems, "\n".join(problems)
