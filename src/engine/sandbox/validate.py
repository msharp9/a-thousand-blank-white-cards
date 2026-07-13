"""engine.sandbox.validate — AST allowlist static safety checker for generated snippets.

Generated `def apply(state, ctx)` snippets must pass this static check before they are
ever stored or executed: no imports, no exec/eval/open/compile/__import__/breakpoint/
getattr/setattr/delattr/vars/globals/locals calls, no dunder attribute access, and
exactly one top-level function named `apply`.
The subprocess RUNNER is a later phase; this is the static validator only.
"""

from __future__ import annotations

import ast
import difflib
import inspect
from dataclasses import dataclass

# Names forbidden as standalone calls or attributes.
_FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "open",
        "compile",
        "__import__",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "vars",
        "globals",
        "locals",
    }
)
_FORBIDDEN_NAMES: frozenset[str] = frozenset({"__builtins__", "__loader__", "__spec__"})


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    error: str | None = None


class _SafetyVisitor(ast.NodeVisitor):
    """Walk an AST and collect violations."""

    def __init__(self, state_name: str, api: dict[str, object]) -> None:
        self.errors: list[str] = []
        self.state_name = state_name
        self.api = api

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.errors.append(f"Line {node.lineno}: import statements are not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.errors.append(f"Line {node.lineno}: from-import statements are not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in _FORBIDDEN_NAMES:
            self.errors.append(f"Line {node.lineno}: access to '{node.id}' is not allowed")
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
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == self.state_name:
            self._validate_state_call(node, func.attr)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.errors.append(f"Line {node.lineno}: dunder attribute access '{node.attr}' is not allowed")
        elif node.attr.startswith("_"):
            self.errors.append(f"Line {node.lineno}: private attribute access '{node.attr}' is not allowed")
        if isinstance(node.value, ast.Name) and node.value.id == self.state_name and node.attr not in self.api:
            suggestion = self._suggest(node.attr)
            self.errors.append(
                f"Line {node.lineno}: SandboxGame has no member '{node.attr}'"
                + (f"; use '{suggestion}'" if suggestion else "")
            )
        self.generic_visit(node)

    def _validate_state_call(self, node: ast.Call, name: str) -> None:
        member = self.api.get(name)
        if member is None or not callable(member):
            return
        signature = inspect.signature(member)
        args = [object()] * (len(node.args) + 1)
        kwargs = {keyword.arg: object() for keyword in node.keywords if keyword.arg is not None}
        try:
            signature.bind(*args, **kwargs)
        except TypeError as exc:
            self.errors.append(f"Line {node.lineno}: invalid call to SandboxGame.{name}{signature}: {exc}")

    def _suggest(self, name: str) -> str | None:
        aliases = {"draw": "draw_cards", "play": "play_card"}
        if name in aliases and aliases[name] in self.api:
            return aliases[name]
        matches = difflib.get_close_matches(name, self.api, n=1, cutoff=0.45)
        return matches[0] if matches else None


def _sandbox_api() -> dict[str, object]:
    from engine.sandbox.api_surface import SandboxGame

    return {
        name: member for name, member in inspect.getmembers(SandboxGame) if not name.startswith("_") and name != "ops"
    }


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

    apply_fn = apply_funcs[0]
    if len(apply_fn.args.args) < 2:
        return ValidationResult(ok=False, error="apply must accept state and ctx parameters")

    visitor = _SafetyVisitor(apply_fn.args.args[0].arg, _sandbox_api())
    visitor.visit(tree)
    if visitor.errors:
        return ValidationResult(ok=False, error="; ".join(visitor.errors))

    return ValidationResult(ok=True)
