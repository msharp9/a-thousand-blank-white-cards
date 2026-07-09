"""Tests for the gen_snippet node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.agent.schemas import Interpretation, SnippetEffect


def test_gen_snippet_sets_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_snippet = SnippetEffect(code="def apply(state, ctx):\n    pass", explanation="Does nothing.")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = fake_snippet
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import gen_snippet

        interp = Interpretation(
            placement="center", timing="modifier", mode="snippet", trigger_event="on_play", rationale="test"
        )
        state = {
            "card_draft": {"title": "Wild", "description": "Something weird happens."},
            "interpretation": interp,
        }
        result = gen_snippet(state)
        assert isinstance(result["snippet"], SnippetEffect)
        fake_llm.with_structured_output.assert_called_once_with(SnippetEffect)


def test_gen_snippet_handles_missing_interpretation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = SnippetEffect(code="pass", explanation="x")
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import gen_snippet

        # no "interpretation" key -> prompt uses 'unknown', must not raise
        result = gen_snippet({"card_draft": {"title": "X", "description": "Y"}})
        assert isinstance(result["snippet"], SnippetEffect)
        human = fake_llm.with_structured_output.return_value.invoke.call_args.args[0][1]["content"]
        assert "unknown" in human
