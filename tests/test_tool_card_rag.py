"""Tests for agent.tools.card_rag (retriever mocked; no live embeddings/Qdrant)."""

from __future__ import annotations

from unittest.mock import patch


def _invoke(query: str, k: int = 4) -> str:
    from agent.tools.card_rag import card_rag

    return card_rag.invoke({"query": query, "k": k})


def test_tool_metadata() -> None:
    from agent.tools.card_rag import card_rag, get_card_rag_tool

    assert card_rag.name == "card_rag"
    assert card_rag.description
    assert get_card_rag_tool() is card_rag


def test_returns_formatted_hits_with_titles() -> None:
    fake_hits = [
        {"title": "Extra Turn", "description": "Take an extra turn.", "score": 0.91},
        {"title": "Draw Two", "description": "Draw two cards.", "score": 0.72},
    ]
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("some new card", k=2)

    assert isinstance(result, str)
    assert "Extra Turn" in result
    assert "Draw Two" in result
    assert "0.910" in result


def test_empty_results_returns_no_similar_cards() -> None:
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: []
        result = _invoke("nothing matches")

    assert isinstance(result, str)
    assert result == "no similar cards found"


def test_retriever_raising_degrades_gracefully() -> None:
    def _boom(query, k):
        raise RuntimeError("rag.store not initialised")

    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = _boom
        result = _invoke("anything")

    assert isinstance(result, str)
    assert result == "card retrieval unavailable"


def test_factory_error_degrades_gracefully() -> None:
    # Even the factory itself raising (e.g. import-time / config failure) must not escape.
    with patch("agent.tools.card_rag.dense_retriever", side_effect=RuntimeError("boom")):
        result = _invoke("anything")

    assert isinstance(result, str)
    assert result == "card retrieval unavailable"


def test_missing_fields_do_not_raise() -> None:
    fake_hits = [{"title": "Only Title"}]  # no description, no score
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    assert "Only Title" in result
    assert "n/a" in result
