"""Tests for the sandbox subprocess runner (real subprocess round-trips)."""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

from engine.sandbox.runner import SnippetExecutionError, execute_snippet

STATE = {
    "players": [{"id": "p1", "name": "A", "score": 0, "hand": [], "connected": True}],
    "turn_index": 0,
    "draw_count": 1,
    "direction": 1,
}
CTX = {"actor_id": "p1"}


def test_benign_snippet_returns_ops() -> None:
    code = "def apply(s, c):\n    s.add_points(c['actor_id'], 1)\n"
    ops = execute_snippet(code, STATE, CTX)
    assert ops == [{"op": "add_points", "target": "p1", "amount": 1}]


def test_invalid_ast_rejected_before_spawn() -> None:
    # 'import os' fails the AST validator -> SnippetExecutionError, no subprocess
    code = "import os\ndef apply(s, c):\n    pass\n"
    with pytest.raises(SnippetExecutionError):
        execute_snippet(code, STATE, CTX)


def test_runtime_error_becomes_execution_error() -> None:
    code = "def apply(s, c):\n    raise RuntimeError('boom')\n"
    with pytest.raises(SnippetExecutionError):
        execute_snippet(code, STATE, CTX)


def test_timeout_raises() -> None:
    # Busy-loop to exceed the wall timeout. Use a tiny timeout to keep the test fast.
    code = "def apply(s, c):\n    while True:\n        pass\n"
    with pytest.raises(SnippetExecutionError):
        execute_snippet(code, STATE, CTX, timeout=1.0)


def test_child_runner_compiles() -> None:
    child_runner = Path(__file__).parent.parent / "src" / "engine" / "sandbox" / "_child_runner.py"
    py_compile.compile(str(child_runner), doraise=True)


def test_execute_snippet_end_to_end_returns_valid_diff() -> None:
    code = "def apply(s, c):\n    s.add_points(c['actor_id'], 2)\n    s.note('smoke')\n"
    ops = execute_snippet(code, STATE, CTX)
    assert ops == [
        {"op": "add_points", "target": "p1", "amount": 2},
        {"op": "custom_note", "note": "smoke"},
    ]
