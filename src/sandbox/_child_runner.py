"""sandbox._child_runner — executed as __main__ inside the isolated subprocess.

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
    """Apply OS resource limits to the child before any user code runs (defense in depth).

    Security model
    --------------
    This is a *hardening* layer, not a real isolation boundary. The limits below cap
    the blast radius of a runaway or malicious snippet on Unix-like systems:

      * ``RLIMIT_CPU = (5, 5)`` — 5s soft/hard CPU-time cap. On reaching the soft limit
        the kernel sends ``SIGXCPU``; at the hard limit it sends ``SIGKILL``. This stops
        CPU-bound busy loops even if the parent's wall-clock timeout somehow misfires.
      * ``RLIMIT_AS = 256MB`` — address-space cap. Over-allocation raises ``MemoryError``
        in the child. NOTE: the macOS kernel does *not* enforce ``RLIMIT_AS``, so memory
        bombs are only contained on Linux. 256MB is deliberately not smaller — a tighter
        limit (e.g. 128MB) can fail to import / crash the interpreter itself on some
        platforms before user code ever runs.
      * ``RLIMIT_FSIZE = 0`` — forbids writing any non-empty file.

    Platform notes
    --------------
      * Windows has no ``resource`` module (``ImportError``) — this is a silent no-op there.
      * Some sandboxed/containerized environments forbid ``setrlimit`` (``OSError``) or
        reject specific values (``ValueError``); we swallow those so execution still runs
        (with weaker limits) rather than failing hard.

    What this does NOT do
    ---------------------
    rlimits do NOT block network access, and this in-process ``exec`` is not a strong
    security boundary. For production use, run untrusted snippets inside a real sandbox
    (gVisor, Firecracker, a container-per-exec, or a hosted code-execution service).
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    except ImportError, ValueError, OSError:
        pass


def main() -> None:
    _apply_rlimits()

    payload = json.loads(sys.stdin.readline())
    state_dict = payload["state"]
    ctx_dict = payload["ctx"]
    code = payload["code"]

    from sandbox.api_surface import SandboxGame

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
