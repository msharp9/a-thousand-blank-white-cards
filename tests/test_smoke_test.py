"""Tests for scripts/smoke_test.py (importability + structure; no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_test.py"


def _load():
    spec = importlib.util.spec_from_file_location("smoke_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_module_has_entrypoints() -> None:
    mod = _load()
    assert callable(mod.main)
    assert callable(mod.run)
    assert callable(mod.check_health)
    assert callable(mod.check_ws)
