"""Tests for the retriever_mode config toggle in the retrieve node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tbwc.agent.nodes import _clear_retriever_cache, retrieve


def _state() -> dict:
    return {"card_draft": {"title": "T", "description": "D"}, "search_notes": "intent"}


def test_advanced_mode_uses_advanced_retriever() -> None:
    _clear_retriever_cache()
    fake_adv = MagicMock(return_value=[{"card_id": "adv"}])
    with patch("tbwc.agent.nodes.advanced_retriever", return_value=fake_adv):
        out = retrieve(_state(), {"configurable": {"retriever_mode": "advanced"}})
    assert out["retrieved"] == [{"card_id": "adv"}]
    fake_adv.assert_called_once()
    _clear_retriever_cache()


def test_default_mode_uses_dense() -> None:
    _clear_retriever_cache()
    fake_dense = MagicMock(return_value=[{"card_id": "dense"}])
    # dense path uses the module-level _retriever (patch it)
    with patch("tbwc.agent.nodes._retriever", fake_dense):
        out = retrieve(_state())
    assert out["retrieved"] == [{"card_id": "dense"}]
    _clear_retriever_cache()


def test_no_config_is_backward_compatible() -> None:
    _clear_retriever_cache()
    fake_dense = MagicMock(return_value=[])
    with patch("tbwc.agent.nodes._retriever", fake_dense):
        out = retrieve(_state())  # single-arg call, as the existing graph/tests do
    assert "retrieved" in out
    _clear_retriever_cache()
