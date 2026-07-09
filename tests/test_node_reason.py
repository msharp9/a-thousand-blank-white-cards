"""Tests for the reason node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_reason_node_returns_search_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="The card lets a player skip their turn.")
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import reason

        state = {"card_draft": {"title": "Skip Turn", "description": "Skip your turn."}}
        result = reason(state)
        assert "search_notes" in result
        assert isinstance(result["search_notes"], str)
        assert len(result["search_notes"]) > 0


def test_reason_node_passes_card_text_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content="summary")
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import reason

        reason({"card_draft": {"title": "Fireball", "description": "Deal 3 damage."}})
        messages = fake_llm.invoke.call_args.args[0]
        human = messages[-1]["content"]
        assert "Fireball" in human
        assert "Deal 3 damage" in human
