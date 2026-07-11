"""Tests for the sandbox AST allowlist validator."""

from __future__ import annotations

from engine.sandbox.validate import validate_snippet

VALID_SNIPPET = """
def apply(state, ctx):
    player_id = ctx["player_id"]
    state.scores[player_id] = state.scores.get(player_id, 0) + 3
"""


def test_valid_snippet_passes() -> None:
    result = validate_snippet(VALID_SNIPPET)
    assert result.ok, result.error


def test_import_rejected() -> None:
    result = validate_snippet("import os\ndef apply(state, ctx): pass")
    assert not result.ok
    assert "import" in result.error.lower()


def test_from_import_rejected() -> None:
    result = validate_snippet("from os import path\ndef apply(state, ctx): pass")
    assert not result.ok


def test_eval_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    eval('1+1')")
    assert not result.ok
    assert "eval" in result.error


def test_exec_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    exec('x=1')")
    assert not result.ok


def test_open_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    open('/etc/passwd')")
    assert not result.ok


def test_dunder_access_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    x = state.__class__")
    assert not result.ok
    assert "dunder" in result.error.lower()


def test_no_apply_function_rejected() -> None:
    result = validate_snippet("def helper(state, ctx): pass")
    assert not result.ok
    assert "apply" in result.error


def test_multiple_functions_rejected() -> None:
    result = validate_snippet("def apply(state, ctx): pass\ndef other(): pass")
    assert not result.ok


def test_syntax_error_rejected() -> None:
    result = validate_snippet("def apply(state ctx):  # missing comma\n    pass")
    assert not result.ok
    assert "SyntaxError" in result.error


def test_compile_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    compile('x', '<s>', 'eval')")
    assert not result.ok


def test_validation_result_ok_field() -> None:
    result = validate_snippet(VALID_SNIPPET)
    assert result.ok is True
    assert result.error is None
