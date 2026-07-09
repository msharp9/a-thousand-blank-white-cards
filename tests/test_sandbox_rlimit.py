"""Resource-limit / isolation tests for the sandbox runner (real subprocesses)."""

from __future__ import annotations

import sys

import pytest

from tbwc.sandbox.runner import SnippetExecutionError, execute_snippet

STATE = {
    "players": [{"id": "p1", "name": "A", "score": 0, "hand": [], "connected": True}],
    "turn_index": 0,
    "draw_count": 1,
    "direction": 1,
}
CTX = {"actor_id": "p1"}


def test_infinite_loop_times_out() -> None:
    code = "def apply(s, c):\n    while True:\n        pass\n"
    with pytest.raises(SnippetExecutionError, match="timed out"):
        execute_snippet(code, STATE, CTX, timeout=3.0)


def test_file_write_blocked() -> None:
    # RLIMIT_FSIZE=0 (Unix) OR the AST validator/child error — either way no readable file.
    # 'open' is blocked by the AST allowlist, so this is rejected before/at execution.
    code = "def apply(s, c):\n    open('/tmp/tbwc_sandbox_escape.txt', 'w').write('x')\n"
    with pytest.raises(SnippetExecutionError):
        execute_snippet(code, STATE, CTX, timeout=5.0)


@pytest.mark.skipif(sys.platform == "darwin", reason="RLIMIT_AS not enforced by macOS kernel")
def test_memory_bomb_fails() -> None:
    # Allocate far beyond RLIMIT_AS. On Linux this raises MemoryError in the child
    # (-> error JSON -> SnippetExecutionError); on macOS it's skipped.
    code = "def apply(s, c):\n    x = bytearray(500 * 1024 * 1024)\n    s.add_points(c['actor_id'], len(x))\n"
    with pytest.raises(SnippetExecutionError):
        execute_snippet(code, STATE, CTX, timeout=5.0)
