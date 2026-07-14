"""Tests for agent.tools.game_rules (bundled snapshot; no network at all)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.tools import game_rules as gr

# A canned rules extract. Multi-paragraph so we can exercise keyword focusing
# and the overview cap.
_CANNED_EXTRACT = (
    "1,000 Blank White Cards is a party game played with a homemade deck.\n"
    "Players draw and play blank cards, writing a title and an effect on each blank card before it is played.\n"
    "Scoring is done with points, and the player with the highest score at the end wins.\n"
    "House Rules can be invented to settle any dispute during play."
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the module-level extract cache before and after each test."""
    gr.reset_cache()
    yield
    gr.reset_cache()


@pytest.fixture
def canned_rules(monkeypatch, tmp_path: Path):
    """Point the tool at a temp snapshot file holding the canned extract."""
    rules = tmp_path / "game_rules.txt"
    rules.write_text(_CANNED_EXTRACT, encoding="utf-8")
    monkeypatch.setattr(gr, "_rules_path", lambda: rules)
    return rules


def _invoke(query: str = "") -> str:
    return gr.game_rules.invoke({"query": query})


def test_tool_metadata() -> None:
    assert gr.game_rules.name == "game_rules"
    assert gr.game_rules.description
    assert gr.get_game_rules_tool() is gr.game_rules


def test_bundled_snapshot_is_served() -> None:
    # No patching: the real data/game_rules.txt must exist and back the tool.
    result = _invoke("")
    assert result != gr.FALLBACK_SUMMARY
    assert "1000 Blank White Cards" in result


def test_query_returns_matching_paragraph(canned_rules) -> None:
    result = _invoke("blank")
    assert isinstance(result, str)
    assert "writing a title and an effect on each blank card" in result


def test_empty_query_returns_trimmed_overview(canned_rules) -> None:
    # A long extract must be capped (not dumped whole) on an empty query.
    canned_rules.write_text(_CANNED_EXTRACT + "\n" + ("padding sentence. " * 500), encoding="utf-8")
    result = _invoke("")
    assert isinstance(result, str)
    assert len(result) <= gr._OVERVIEW_CHARS + len(" ...")
    assert result.startswith("1,000 Blank White Cards is a party game")


def test_read_is_cached_across_calls(monkeypatch, canned_rules) -> None:
    counting_read = MagicMock(wraps=gr._read_extract)
    monkeypatch.setattr(gr, "_read_extract", counting_read)
    _invoke("blank")
    _invoke("points")
    _invoke("")
    assert counting_read.call_count == 1


def test_missing_file_degrades_to_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gr, "_rules_path", lambda: tmp_path / "nope.txt")
    result = _invoke("blank")
    assert isinstance(result, str)
    assert "party game" in result
    assert result == gr.FALLBACK_SUMMARY


def test_empty_file_degrades_to_fallback(canned_rules) -> None:
    canned_rules.write_text("   ", encoding="utf-8")
    result = _invoke("")
    assert result == gr.FALLBACK_SUMMARY


def test_no_keyword_match_returns_overview(canned_rules) -> None:
    result = _invoke("zzz-nonexistent-keyword")
    # Falls back to the overview (the whole short extract) rather than empty.
    assert result.startswith("1,000 Blank White Cards is a party game")
