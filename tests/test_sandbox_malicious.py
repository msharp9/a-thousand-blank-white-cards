"""Security tests: malicious snippets MUST be blocked (AST reject or runtime kill).

A snippet is 'blocked' if execute_snippet raises SnippetExecutionError — this covers
both AST-allowlist rejections (before any subprocess) and runtime timeout/rlimit kills.
"""

from __future__ import annotations

import pytest

from engine.sandbox.runner import SnippetExecutionError, execute_snippet

STATE = {
    "players": [{"id": "p1", "name": "A", "score": 0, "hand": [], "connected": True}],
    "turn_index": 0,
    "draw_count": 1,
    "turn_order": ["p1"],
}
CTX = {"actor_id": "p1"}


def assert_blocked(code: str, timeout: float = 5.0) -> None:
    """Assert that execute_snippet refuses the snippet by raising SnippetExecutionError."""
    with pytest.raises(SnippetExecutionError):
        ops = execute_snippet(code, STATE, CTX, timeout=timeout)
        pytest.fail(f"Expected snippet to be blocked, got ops: {ops}")


# ---------------------------------------------------------------------------
# Imports of dangerous modules (AST-blocked, instant — no subprocess)
# ---------------------------------------------------------------------------


def test_import_os_blocked() -> None:
    assert_blocked("import os\ndef apply(s, c):\n    pass\n")


def test_import_subprocess_blocked() -> None:
    assert_blocked("import subprocess\ndef apply(s, c):\n    pass\n")


def test_import_sys_blocked() -> None:
    assert_blocked("import sys\ndef apply(s, c):\n    pass\n")


def test_import_socket_blocked() -> None:
    assert_blocked("import socket\ndef apply(s, c):\n    pass\n")


def test_import_threading_blocked() -> None:
    assert_blocked("import threading\ndef apply(s, c):\n    pass\n")


def test_from_import_blocked() -> None:
    assert_blocked("from os import system\ndef apply(s, c):\n    pass\n")


# ---------------------------------------------------------------------------
# Dunder access (sandbox-escape via the object/class hierarchy) — AST-blocked
# ---------------------------------------------------------------------------


def test_class_bases_subclasses_blocked() -> None:
    assert_blocked("def apply(s, c):\n    return s.__class__.__bases__[0].__subclasses__()\n")


def test_globals_escape_blocked() -> None:
    assert_blocked("def apply(s, c):\n    return apply.__globals__['__builtins__']\n")


# ---------------------------------------------------------------------------
# Forbidden builtins (open / exec / __import__) — AST-blocked
# ---------------------------------------------------------------------------


def test_open_file_blocked() -> None:
    assert_blocked("def apply(s, c):\n    return open('/etc/passwd').read()\n")


def test_exec_blocked() -> None:
    assert_blocked("def apply(s, c):\n    exec('import os')\n")


def test_dunder_import_blocked() -> None:
    assert_blocked("def apply(s, c):\n    __import__('os')\n")


# ---------------------------------------------------------------------------
# Command execution via an imported module — AST-blocked (via the import)
# ---------------------------------------------------------------------------


def test_os_system_blocked() -> None:
    assert_blocked("import os\ndef apply(s, c):\n    os.system('echo pwned')\n")


# ---------------------------------------------------------------------------
# Runtime resource exhaustion — killed by wall-clock timeout / rlimit
# ---------------------------------------------------------------------------


def test_infinite_loop_killed() -> None:
    with pytest.raises(SnippetExecutionError, match="timed out"):
        execute_snippet(
            "def apply(s, c):\n    while True:\n        pass\n",
            STATE,
            CTX,
            timeout=3.0,
        )


def test_fork_bomb_blocked() -> None:
    # `import os` is rejected by the AST validator, so this never spawns anything.
    assert_blocked(
        "import os\ndef apply(s, c):\n    while True:\n        os.fork()\n",
        timeout=3.0,
    )


# ---------------------------------------------------------------------------
# Sanity: a benign snippet is NOT blocked and produces the expected ops
# ---------------------------------------------------------------------------


def test_benign_snippet_allowed() -> None:
    code = "def apply(s, c):\n    s.add_points(c['actor_id'], 5)\n    s.note(\"hello\")\n"
    ops = execute_snippet(code, STATE, CTX, timeout=5.0)
    op_names = [op["op"] for op in ops]
    assert "add_points" in op_names
    assert "custom_note" in op_names
    add_op = next(op for op in ops if op["op"] == "add_points")
    assert add_op["target"] == "p1"
    assert add_op["amount"] == 5
