"""sandbox.validate — AST allowlist static safety checker for generated snippets.

Generated `def apply(state, ctx)` snippets must pass this static check before they are
ever stored or executed: no imports, no exec/eval/open/compile/__import__/breakpoint
calls, no dunder attribute access, and exactly one top-level function named `apply`.
The subprocess RUNNER is a later phase; this is the static validator only.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# Names forbidden as standalone calls or attributes.
_FORBIDDEN_CALLS: frozenset[str] = frozenset({"exec", "eval", "open", "compile", "__import__", "breakpoint"})


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    error: str | None = None


class _SafetyVisitor(ast.NodeVisitor):
    """Walk an AST and collect violations."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.errors.append(f"Line {node.lineno}: import statements are not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.errors.append(f"Line {node.lineno}: from-import statements are not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        name: str | None = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in _FORBIDDEN_CALLS:
            self.errors.append(f"Line {node.lineno}: call to '{name}' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.errors.append(f"Line {node.lineno}: dunder attribute access '{node.attr}' is not allowed")
        self.generic_visit(node)


def validate_snippet(code: str) -> ValidationResult:
    """Parse `code` with ast and run allowlist checks.

    PASS requires: parses without SyntaxError; no import/from-import; no calls to
    exec/eval/open/compile/__import__/breakpoint; no dunder attribute access; exactly
    one top-level function named 'apply'.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return ValidationResult(ok=False, error=f"SyntaxError: {exc}")

    top_level_funcs = [node for node in ast.iter_child_nodes(tree) if isinstance(node, ast.FunctionDef)]
    apply_funcs = [f for f in top_level_funcs if f.name == "apply"]
    if len(top_level_funcs) != 1 or len(apply_funcs) != 1:
        return ValidationResult(
            ok=False,
            error=f"Expected exactly one top-level function named 'apply', found: {[f.name for f in top_level_funcs]}",
        )

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    if visitor.errors:
        return ValidationResult(ok=False, error="; ".join(visitor.errors))

    return ValidationResult(ok=True)
