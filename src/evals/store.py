"""evals.store — persist and reload eval runs for cross-session comparison.

Each run lands as one JSON file under ``data/eval/runs/`` carrying the config,
the aggregate summary, and every per-card row. The notebook loads any set of
these to chart metric deltas across models / tool sets / caps over time.

The timestamp is supplied by the caller (the notebook stamps ``datetime.now``)
so this module — and the runner it serves — stays free of wall-clock calls.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from evals.paths import find_repo_root
from evals.runner import EvalRunResult


def runs_dir() -> Path:
    """``data/eval/runs/`` under the repo root (created on first save)."""
    return find_repo_root(Path(__file__)) / "data" / "eval" / "runs"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text).strip("-").lower() or "run"


def save_run(run: EvalRunResult, *, timestamp: str = "") -> Path:
    """Write one run to ``data/eval/runs/<ts>_<benchmark>_<model>.json``.

    ``timestamp`` should be filename-safe (e.g. ``20260714-153000``); it defaults
    to the timestamp the run was created with. Returns the path written.
    """
    timestamp = timestamp or run.timestamp
    if not timestamp:
        raise ValueError("save_run needs a timestamp (pass one here or to run_benchmark).")
    directory = runs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    summary = run.aggregate()
    name = f"{timestamp}_{_slug(run.config.benchmark)}_{_slug(str(summary.get('model', 'default')))}.json"
    payload = {
        "timestamp": timestamp,
        "config": run.config.to_dict(),
        "scorer_names": list(run.scorer_names),
        "summary": summary,
        "rows": [asdict(row) for row in run.rows],
    }
    path = directory / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_run(path: Path) -> dict[str, Any]:
    """Load one persisted run's payload (summary + rows) as a plain dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_runs(paths: list[Path] | None = None, *, glob: str = "*.json") -> list[dict[str, Any]]:
    """Load persisted runs — an explicit list, or every file matching ``glob``.

    Returns payload dicts sorted by timestamp so the notebook can chart history
    in chronological order.
    """
    if paths is None:
        directory = runs_dir()
        paths = sorted(directory.glob(glob)) if directory.exists() else []
    payloads = [load_run(p) for p in paths]
    return sorted(payloads, key=lambda p: p.get("timestamp", ""))
