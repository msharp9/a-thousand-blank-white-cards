"""Tests for the sandbox subprocess runner (real subprocess round-trips)."""

from __future__ import annotations

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
