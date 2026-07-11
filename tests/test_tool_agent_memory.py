"""Tests for agent.tools.agent_memory — sqlite-backed decision memory.

Every test uses a ``tmp_path`` DB (or ``:memory:``) so nothing is written into
the repo working tree, and clears the ``get_settings`` cache after pointing
``AGENT_MEMORY_DB`` at the temp file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import get_settings

from agent.tools.agent_memory import (
    AgentMemoryStore,
    get_agent_memory_tools,
    recall_decisions,
    remember_decision,
)


@pytest.fixture
def memory_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the configured agent-memory DB at an isolated temp file."""
    db = tmp_path / "agent_memory.db"
    monkeypatch.setenv("AGENT_MEMORY_DB", str(db))
    get_settings.cache_clear()
    yield db
    get_settings.cache_clear()


def test_write_then_recall(memory_db: Path) -> None:
    out = remember_decision.invoke(
        {
            "card_title": "Time Warp",
            "card_description": "Take an extra turn.",
            "verdict": "allowed",
            "note": "treated as an extra-turn effect",
        }
    )
    assert "Time Warp" in out

    recalled = recall_decisions.invoke({"query": "Time Warp"})
    assert "Time Warp" in recalled
    assert "allowed" in recalled


def test_recall_orders_most_recent_first_and_limits(memory_db: Path) -> None:
    store = AgentMemoryStore()
    for i in range(7):
        store.remember(
            card_title=f"Zap {i}",
            card_description="deal damage",
            verdict=f"verdict-{i}",
        )

    results = store.recall("Zap", limit=5)
    assert len(results) == 5
    # Most recent (highest i) first.
    assert results[0]["card_title"] == "Zap 6"
    assert results[-1]["card_title"] == "Zap 2"


def test_recall_matches_description_and_note(memory_db: Path) -> None:
    store = AgentMemoryStore()
    store.remember(
        card_title="Mystery",
        card_description="a spooky graveyard effect",
        verdict="denied",
        note="reference to a horror movie",
    )
    assert store.recall("graveyard")[0]["card_title"] == "Mystery"
    assert store.recall("horror movie")[0]["card_title"] == "Mystery"


def test_recall_empty_returns_nothing_recalled(memory_db: Path) -> None:
    assert recall_decisions.invoke({"query": "does-not-exist"}) == "nothing recalled"


def test_graceful_degradation_on_sqlite_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unopenable DB path degrades to a string, never raises."""
    bad = tmp_path / "no_such_dir" / "memory.db"  # parent dir does not exist
    monkeypatch.setenv("AGENT_MEMORY_DB", str(bad))
    get_settings.cache_clear()
    try:
        assert (
            remember_decision.invoke({"card_title": "X", "card_description": "y", "verdict": "z"})
            == "memory unavailable"
        )
        assert recall_decisions.invoke({"query": "anything"}) == "memory unavailable"
    finally:
        get_settings.cache_clear()


def test_tools_have_name_and_description() -> None:
    tools = get_agent_memory_tools()
    names = {t.name for t in tools}
    assert names == {"remember_decision", "recall_decisions"}
    for t in tools:
        assert t.description and t.description.strip()
