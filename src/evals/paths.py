"""evals.paths — locate the repo root regardless of cwd, worktree, or nesting depth.

``harness.py`` and ``conclusions.py`` both need ``data/eval/`` relative to the repo
root. A fixed ``parents[N]`` walk breaks the moment the file moves (e.g. a git
worktree checkout under ``.claude/worktrees/<id>/``), so we instead walk up from
the caller looking for the repo-root marker (``pyproject.toml``).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT_ENV_VAR = "TBWC_REPO_ROOT"
_REPO_ROOT_MARKER = "pyproject.toml"


def find_repo_root(start: Path) -> Path:
    """Return the repo root containing ``start``.

    Honors ``TBWC_REPO_ROOT`` as an explicit override (set it when the marker
    walk can't apply, e.g. a packaged install). Otherwise walks up from
    ``start`` until a directory containing ``pyproject.toml`` is found.

    Raises FileNotFoundError if no marker is found above ``start``.
    """
    override = os.environ.get(REPO_ROOT_ENV_VAR)
    if override:
        return Path(override).resolve()

    current = start.resolve()
    for directory in (current, *current.parents):
        if (directory / _REPO_ROOT_MARKER).is_file():
            return directory
    raise FileNotFoundError(f"Could not locate {_REPO_ROOT_MARKER} above {current}; set {REPO_ROOT_ENV_VAR}.")
