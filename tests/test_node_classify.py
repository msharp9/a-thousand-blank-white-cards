"""Tests for the classify node and route_after_classify edge."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.agent.schemas import Interpretation


def _interp(mode: str = "immediate") -> Interpretation:
    return Interpretation(placement="self", timing="immediate", mode=mode, rationale="test")


def test_classify_sets_interpretation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_interp = _interp("immediate")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = fake_interp
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import classify

        state = {
            "card_draft": {"title": "T", "description": "D"},
            "retrieved": [],
            "search_notes": "intent",
            "attempts": 0,
        }
        result = classify(state)
        assert result["interpretation"] == fake_interp
        assert result["attempts"] == 1
        fake_llm.with_structured_output.assert_called_once_with(Interpretation)


def test_classify_formats_exemplars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_llm = MagicMock()
    fake_llm.with_structured_output.return_value.invoke.return_value = _interp()
    with patch("tbwc.agent.nodes.get_chat_model", return_value=fake_llm):
        from tbwc.agent.nodes import classify

        state = {
            "card_draft": {"title": "T", "description": "D"},
            "retrieved": [{"title": "Ex1", "description": "does x", "canonical": "{}", "score": 0.88}],
            "attempts": 0,
        }
        classify(state)
        human_msg = fake_llm.with_structured_output.return_value.invoke.call_args.args[0][1]["content"]
        assert "Ex1" in human_msg


def test_route_after_classify_immediate() -> None:
    from tbwc.agent.nodes import route_after_classify

    assert route_after_classify({"interpretation": _interp("immediate")}) == "emit_ops"


def test_route_after_classify_snippet() -> None:
    from tbwc.agent.nodes import route_after_classify

    assert route_after_classify({"interpretation": _interp("snippet")}) == "gen_snippet"


def test_route_after_classify_none_fallback() -> None:
    from tbwc.agent.nodes import route_after_classify

    assert route_after_classify({}) == "gen_snippet"
