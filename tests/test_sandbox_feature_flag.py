"""Tests for the snippet_execution_enabled feature flag."""

from __future__ import annotations

from unittest.mock import patch

from config import Settings
from engine.sandbox.runner import execute_snippet

STATE = {
    "players": [{"id": "p1", "name": "A", "score": 0, "hand": [], "connected": True}],
    "turn_index": 0,
    "draw_count": 1,
    "turn_order": ["p1"],
}
CTX = {"actor_id": "p1"}
CODE = "def apply(s, c):\n    s.add_points(c['actor_id'], 1)\n"


def test_flag_can_be_set_false() -> None:
    s = Settings(_env_file=None, snippet_execution_enabled=False)  # type: ignore[call-arg]
    assert s.snippet_execution_enabled is False


def test_flag_default_true() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.snippet_execution_enabled is True


def test_disabled_returns_note_without_subprocess() -> None:
    disabled = Settings(_env_file=None, snippet_execution_enabled=False)  # type: ignore[call-arg]
    with patch("config.get_settings", return_value=disabled):
        ops = execute_snippet(CODE, STATE, CTX)
    assert ops == [{"op": "custom_note", "note": "snippet execution disabled by feature flag"}]


def test_enabled_executes_normally() -> None:
    enabled = Settings(_env_file=None, snippet_execution_enabled=True)  # type: ignore[call-arg]
    with patch("config.get_settings", return_value=enabled):
        ops = execute_snippet(CODE, STATE, CTX)
    assert {"op": "add_points", "target": "p1", "amount": 1} in ops
