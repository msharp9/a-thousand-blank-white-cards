"""engine.sandbox.runner — execute_snippet: spawn an isolated subprocess, collect the op diff.

Security boundary = subprocess + rlimit (set in _child_runner). In-process exec is NOT
a boundary. For production, replace with gVisor/Firecracker/container-per-exec or a hosted
code-exec service.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from engine.sandbox.validate import validate_snippet

_WALL_TIMEOUT: float = 10.0
_CHILD_RUNNER = Path(__file__).parent / "_child_runner.py"


class SnippetExecutionError(Exception):
    """Raised when the child process fails, times out, or the snippet is invalid."""


def execute_snippet(
    code: str,
    state_dict: dict[str, Any],
    ctx_dict: dict[str, Any],
    *,
    timeout: float = _WALL_TIMEOUT,
) -> list[dict[str, Any]]:
    """Execute `code` in an isolated subprocess and return the recorded op diff.

    Raises SnippetExecutionError on AST-validation failure, timeout, crash, or
    error JSON from the child.
    """
    from config import get_settings

    if not get_settings().snippet_execution_enabled:
        return [{"op": "custom_note", "note": "snippet execution disabled by feature flag"}]

    result_check = validate_snippet(code)
    if not result_check.ok:
        raise SnippetExecutionError(f"Snippet failed validation: {result_check.error}")

    payload = json.dumps({"state": state_dict, "ctx": ctx_dict, "code": code})
    src_dir = str(Path(__file__).parent.parent.parent)  # .../src (engine/sandbox/runner.py -> src)
    cmd = [sys.executable, "-I", str(_CHILD_RUNNER)]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PYTHONPATH": src_dir},
    )
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.wait()
        raise SnippetExecutionError(f"Snippet timed out after {timeout}s (wall-clock)") from exc
    returncode = proc.returncode

    stdout = stdout.strip()
    if not stdout:
        stderr_snippet = stderr[:500] if stderr else "(no stderr)"
        raise SnippetExecutionError(f"Child produced no output (exit={returncode}): {stderr_snippet}")

    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SnippetExecutionError(f"Child stdout was not valid JSON: {stdout[:200]}") from exc

    if "error" in response:
        raise SnippetExecutionError(f"Snippet raised: {response['error']}")

    return response["ops"]
