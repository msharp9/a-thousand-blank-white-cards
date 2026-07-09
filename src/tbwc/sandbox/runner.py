"""tbwc.sandbox.runner — execute_snippet: spawn an isolated subprocess, collect the op diff.

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

from tbwc.sandbox.validate import validate_snippet

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
    result_check = validate_snippet(code)
    if not result_check.ok:
        raise SnippetExecutionError(f"Snippet failed validation: {result_check.error}")

    payload = json.dumps({"state": state_dict, "ctx": ctx_dict, "code": code})
    src_dir = str(Path(__file__).parent.parent.parent)  # .../src (parent of tbwc)
    cmd = [sys.executable, "-I", str(_CHILD_RUNNER)]

    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PYTHONPATH": src_dir},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SnippetExecutionError(f"Snippet timed out after {timeout}s") from exc

    stdout = proc.stdout.strip()
    if not stdout:
        stderr_snippet = proc.stderr[:500] if proc.stderr else "(no stderr)"
        raise SnippetExecutionError(f"Child produced no output (exit={proc.returncode}): {stderr_snippet}")

    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SnippetExecutionError(f"Child stdout was not valid JSON: {stdout[:200]}") from exc

    if "error" in response:
        raise SnippetExecutionError(f"Snippet raised: {response['error']}")

    return response["ops"]
