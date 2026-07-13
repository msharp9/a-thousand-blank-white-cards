"""Tests for the sandbox AST allowlist validator."""

from __future__ import annotations

from engine.sandbox.validate import validate_snippet

VALID_SNIPPET = """
def apply(state, ctx):
    state.add_points('self', 3)
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


def test_getattr_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    getattr(state, 'x')")
    assert not result.ok


def test_getattr_dunder_bypass_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    getattr(state, '__class__')")
    assert not result.ok


def test_setattr_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    setattr(state, 'x', 1)")
    assert not result.ok


def test_delattr_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    delattr(state, 'x')")
    assert not result.ok


def test_vars_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    vars(state)")
    assert not result.ok


def test_globals_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    globals()")
    assert not result.ok


def test_locals_call_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    locals()")
    assert not result.ok


def test_validation_result_ok_field() -> None:
    result = validate_snippet(VALID_SNIPPET)
    assert result.ok is True
    assert result.error is None


def test_unknown_sandbox_method_rejected_with_recommendation() -> None:
    result = validate_snippet("def apply(state, ctx):\n    state.draw('self', 2)\n")

    assert result.ok is False
    assert "draw_cards" in result.error


def test_builtins_namespace_is_rejected() -> None:
    result = validate_snippet(
        "def apply(state, ctx):\n    state.custom_note(__builtins__['open']('/etc/hosts').read())\n"
    )

    assert result.ok is False
    assert "__builtins__" in result.error


def test_private_sandbox_state_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    state._ops.append({})\n")

    assert result.ok is False
    assert "private attribute" in result.error


def test_invalid_sandbox_method_arguments_rejected() -> None:
    result = validate_snippet("def apply(state, ctx):\n    state.add_points('self')\n")

    assert result.ok is False
    assert "invalid call" in result.error
