"""Tests for the retrieve node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

FAKE_EXEMPLARS = [{"card_id": "s1", "title": "Extra Turn", "description": "...", "score": 0.9}]


def test_retrieve_uses_search_notes() -> None:
    fake_retriever = MagicMock(return_value=FAKE_EXEMPLARS)
    with patch("tbwc.agent.nodes._retriever", fake_retriever):
        from tbwc.agent.nodes import retrieve

        state = {
            "card_draft": {"title": "Go Again", "description": "Take another turn."},
            "search_notes": "The card grants an extra turn.",
        }
        result = retrieve(state)
        fake_retriever.assert_called_once_with("The card grants an extra turn.", k=4)
        assert result["retrieved"] == FAKE_EXEMPLARS


def test_retrieve_falls_back_to_card_text() -> None:
    fake_retriever = MagicMock(return_value=[])
    with patch("tbwc.agent.nodes._retriever", fake_retriever):
        from tbwc.agent.nodes import retrieve

        state = {"card_draft": {"title": "Zap", "description": "Lose 5 points."}}
        retrieve(state)
        call_query = fake_retriever.call_args[0][0]
        assert "Zap" in call_query
        assert "Lose 5 points" in call_query
