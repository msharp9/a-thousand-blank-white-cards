"""tbwc.sandbox._child_runner — executed as __main__ inside the isolated subprocess.

Protocol:
  stdin:  one JSON line {"state": {...}, "ctx": {...}, "code": "<snippet src>"}
  stdout: one JSON line {"ops": [...]} on success, or {"error": ...} on failure.

Resource limits are applied BEFORE any user code is exec'd.
"""

from __future__ import annotations

import json
import sys
import traceback


def _apply_rlimits() -> None:
    """Apply OS resource limits (Unix only; no-op elsewhere)."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    except ImportError, ValueError:
        pass


def main() -> None:
    _apply_rlimits()

    payload = json.loads(sys.stdin.readline())
    state_dict = payload["state"]
    ctx_dict = payload["ctx"]
    code = payload["code"]

    from tbwc.sandbox.api_surface import SandboxGame

    sandbox = SandboxGame(state_dict, ctx_dict)

    ns: dict = {}
    exec(compile(code, "<snippet>", "exec"), ns)

    apply_fn = ns.get("apply")
    if apply_fn is None:
        raise RuntimeError("snippet must define apply(state, ctx)")

    apply_fn(sandbox, ctx_dict)
    print(json.dumps({"ops": sandbox.ops()}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc), "traceback": traceback.format_exc()}), flush=True)
        sys.exit(1)
