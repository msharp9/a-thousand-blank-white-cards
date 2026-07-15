"""Tests for agent.tools.card_rag_hybrid (retriever mocked; no live embeddings/BM25/Qdrant)."""

from __future__ import annotations

import json
from unittest.mock import patch


def _invoke(query: str, k: int = 4) -> str:
    from agent.tools.card_rag_hybrid import card_rag_hybrid

    return card_rag_hybrid.invoke({"query": query, "k": k})


def test_tool_metadata() -> None:
    from agent.tools.card_rag_hybrid import card_rag_hybrid, get_card_rag_hybrid_tool

    assert card_rag_hybrid.name == "card_rag_hybrid"
    assert card_rag_hybrid.description
    assert get_card_rag_hybrid_tool() is card_rag_hybrid


def test_returns_formatted_hits_with_titles() -> None:
    fake_hits = [
        {"title": "Extra Turn", "description": "Take an extra turn.", "score": 0.91},
        {"title": "Draw Two", "description": "Draw two cards.", "score": 0.72},
    ]
    with patch("agent.tools.card_rag_hybrid.hybrid_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("some new card", k=2)

    assert isinstance(result, str)
    assert "Extra Turn" in result
    assert "Draw Two" in result
    assert "0.910" in result


def test_empty_results_returns_no_similar_cards() -> None:
    with patch("agent.tools.card_rag_hybrid.hybrid_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: []
        result = _invoke("nothing matches")

    assert isinstance(result, str)
    assert result == "no similar cards found"


def test_retriever_raising_degrades_gracefully() -> None:
    def _boom(query, k):
        raise RuntimeError("hybrid retriever error")

    with patch("agent.tools.card_rag_hybrid.hybrid_retriever") as mock_factory:
        mock_factory.return_value = _boom
        result = _invoke("anything")

    assert isinstance(result, str)
    assert result == "card retrieval unavailable"


def test_factory_error_degrades_gracefully() -> None:
    with patch("agent.tools.card_rag_hybrid.hybrid_retriever", side_effect=RuntimeError("boom")):
        result = _invoke("anything")

    assert isinstance(result, str)
    assert result == "card retrieval unavailable"


def test_missing_fields_do_not_raise() -> None:
    fake_hits = [{"title": "Only Title"}]
    with patch("agent.tools.card_rag_hybrid.hybrid_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    assert "Only Title" in result
    assert "n/a" in result


def test_canonical_ops_are_included() -> None:
    canonical = {"ops": [{"type": "steal_points", "amount": 3}]}
    fake_hits = [
        {
            "title": "Steal 3 Points",
            "description": "Steal 3 points from an opponent.",
            "score": 0.88,
            "canonical": json.dumps(canonical),
        }
    ]
    with patch("agent.tools.card_rag_hybrid.hybrid_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("steal points")

    assert "canonical=" in result
    assert '"steal_points"' in result


def test_tool_name_consistency() -> None:
    from agent.tools.card_rag_hybrid import card_rag_hybrid

    assert card_rag_hybrid.name == "card_rag_hybrid"
    assert (
        "semantic similarity" in card_rag_hybrid.description.lower() or "keyword" in card_rag_hybrid.description.lower()
    )
