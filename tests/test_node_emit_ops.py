"""Tests for the emit_ops node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.agent.schemas import Interpretation


def test_emit_ops_sets_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_program = MagicMock()
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = fake_program
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops
        from tbwc.models.effects import EffectProgram

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="test")
        state = {
            "card_draft": {"title": "Gain 5 Points", "description": "Gain 5 points."},
            "interpretation": interp,
        }
        result = emit_ops(state)
        assert result["program"] is fake_program
        fake_llm.with_structured_output.assert_called_once_with(EffectProgram)


def test_emit_ops_includes_classification_in_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = MagicMock()
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import emit_ops

        interp = Interpretation(placement="self", timing="immediate", mode="immediate", rationale="rx")
        emit_ops({"card_draft": {"title": "Zap", "description": "Lose 5."}, "interpretation": interp})
        human = fake_llm.with_structured_output.return_value.invoke.call_args.args[0][1]["content"]
        assert "Zap" in human
        assert "Classification:" in human
