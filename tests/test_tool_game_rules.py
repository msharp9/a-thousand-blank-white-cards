"""Tests for agent.tools.game_rules (Wikipedia fetch mocked; no live network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.tools import game_rules as gr

# A canned Wikipedia-style plain-text extract. Multi-paragraph so we can exercise
# keyword focusing and the overview cap.
_CANNED_EXTRACT = (
    "1,000 Blank White Cards is a party game played with a homemade deck.\n"
    "Players draw and play blank cards, writing a title and an effect on each blank card before it is played.\n"
    "Scoring is done with points, and the player with the highest score at the end wins.\n"
    "House Rules can be invented to settle any dispute during play."
)


def _make_response(extract: str) -> MagicMock:
    """Build a fake httpx.Response returning a MediaWiki extracts payload."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"query": {"pages": {"12345": {"extract": extract}}}}
    return resp


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the module-level extract cache before and after each test."""
    gr.reset_cache()
    yield
    gr.reset_cache()


def _invoke(query: str = "") -> str:
    return gr.game_rules.invoke({"query": query})


def test_tool_metadata() -> None:
    assert gr.game_rules.name == "game_rules"
    assert gr.game_rules.description
    assert gr.get_game_rules_tool() is gr.game_rules


def test_query_returns_matching_paragraph() -> None:
    with patch("httpx.get", return_value=_make_response(_CANNED_EXTRACT)):
        result = _invoke("blank")

    assert isinstance(result, str)
    assert "writing a title and an effect on each blank card" in result


def test_empty_query_returns_trimmed_overview() -> None:
    # A long extract must be capped (not dumped whole) on an empty query.
    long_extract = _CANNED_EXTRACT + "\n" + ("padding sentence. " * 500)
    with patch("httpx.get", return_value=_make_response(long_extract)):
        result = _invoke("")

    assert isinstance(result, str)
    assert len(result) <= gr._OVERVIEW_CHARS + len(" ...")
    assert result.startswith("1,000 Blank White Cards is a party game")


def test_fetch_is_cached_across_calls() -> None:
    mock_get = MagicMock(return_value=_make_response(_CANNED_EXTRACT))
    with patch("httpx.get", mock_get):
        _invoke("blank")
        _invoke("points")
        _invoke("")

    assert mock_get.call_count == 1


def test_network_error_degrades_to_fallback() -> None:
    with patch("httpx.get", side_effect=RuntimeError("network down")):
        result = _invoke("blank")

    assert isinstance(result, str)
    assert "party game" in result
    assert result == gr.FALLBACK_SUMMARY


def test_empty_extract_degrades_to_fallback() -> None:
    with patch("httpx.get", return_value=_make_response("   ")):
        result = _invoke("")

    assert result == gr.FALLBACK_SUMMARY


def test_no_keyword_match_returns_overview() -> None:
    with patch("httpx.get", return_value=_make_response(_CANNED_EXTRACT)):
        result = _invoke("zzz-nonexistent-keyword")

    # Falls back to the overview (the whole short extract) rather than empty.
    assert result.startswith("1,000 Blank White Cards is a party game")
