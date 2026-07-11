"""Static architecture-layering guard.

Parses (never imports) every module under ``src/`` and enforces the component
dependency rules for the board/engine/agent/models split. Because it reads the
real files via ``ast``, it stays honest as the code changes and is fast (no
runtime imports, no LLM/network side effects).

Layering rules
--------------
* ``models`` is the foundation: it must not import ``engine``, ``agent``, or ``board``.
* ``engine`` may import ``models`` (+ shared infra); it must NOT import ``agent``,
  ``board``, or ``evals``. ``engine.sandbox`` may import ``engine`` (existing lazy
  coupling used by ``revalidate``).
* ``agent`` may import ``engine`` and ``models`` (+ shared infra); it must NOT
  import ``board`` or ``evals``.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

# Shared infra that any layer is allowed to import.
_INFRA = {"config", "logging_config"}


def _iter_modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _top_level_imports(path: Path) -> set[str]:
    """Return the set of top-level package names imported by ``path``.

    Only the first dotted segment matters for layering (e.g. ``agent.rag.store``
    -> ``agent``). Relative imports are ignored — they can never cross a
    top-level package boundary.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import, stays within the same package
            if node.module:
                tops.add(node.module.split(".")[0])
    return tops


def _modules_under(component: str) -> list[Path]:
    base = SRC / component
    return sorted(base.rglob("*.py"))


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC))


def _assert_no_import(component: str, forbidden: set[str], *, exempt: set[Path] = frozenset()) -> None:
    violations: list[str] = []
    for path in _modules_under(component):
        if path in exempt:
            continue
        bad = _top_level_imports(path) & forbidden
        if bad:
            violations.append(f"{_rel(path)} imports {sorted(bad)}")
    assert not violations, f"{component} layering violations: " + "; ".join(violations)


def test_src_layout_exists() -> None:
    """Guard against silently testing nothing if the tree is restructured again."""
    for component in ("agent", "board", "engine", "evals", "models"):
        assert (SRC / component).is_dir(), f"missing component dir src/{component}"
    assert (SRC / "agent" / "rag").is_dir()
    assert (SRC / "engine" / "sandbox").is_dir()
    assert (SRC / "board" / "rooms").is_dir()


def test_models_imports_no_higher_layer() -> None:
    _assert_no_import("models", {"engine", "agent", "board", "evals"})


def test_engine_imports_no_higher_layer() -> None:
    # engine may import models + infra + its own engine.sandbox; nothing above it.
    _assert_no_import("engine", {"agent", "board", "evals"})


def test_agent_imports_no_higher_layer() -> None:
    # agent may import engine, models, infra; not board or evals.
    _assert_no_import("agent", {"board", "evals"})


def test_engine_sandbox_may_import_engine() -> None:
    """The existing lazy coupling (sandbox.revalidate -> engine.apply) is allowed.

    This test documents that ``engine.sandbox`` importing ``engine`` is
    intentional and not treated as a violation (it lives *under* engine).
    """
    sandbox = SRC / "engine" / "sandbox"
    # Just parse them all to ensure they are valid and importable as a set.
    for path in sorted(sandbox.rglob("*.py")):
        tops = _top_level_imports(path)
        # sandbox must not reach up into agent/board/evals either.
        assert not (tops & {"agent", "board", "evals"}), f"{_rel(path)} reaches above engine"


def test_infra_is_importable_everywhere() -> None:
    """Sanity: infra modules exist at the top of src and are not packages."""
    for name in _INFRA:
        assert (SRC / f"{name}.py").is_file(), f"missing shared infra src/{name}.py"
