"""Tests for agent.tools.card_rag (retriever mocked; no live embeddings/Qdrant)."""

from __future__ import annotations

import json
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
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("steal points")

    assert "canonical=" in result
    assert '"steal_points"' in result


def test_missing_canonical_omits_ops_segment() -> None:
    fake_hits = [{"title": "No Ops", "description": "desc", "score": 0.5}]
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    assert "canonical=" not in result


def test_unparseable_canonical_omits_ops_segment() -> None:
    fake_hits = [{"title": "Bad Ops", "description": "desc", "score": 0.5, "canonical": "{not json"}]
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    assert "canonical=" not in result


def test_empty_canonical_omits_ops_segment() -> None:
    fake_hits = [{"title": "Empty Ops", "description": "desc", "score": 0.5, "canonical": ""}]
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    assert "canonical=" not in result


def test_top_hit_long_canonical_stays_complete_and_parseable() -> None:
    canonical = {"steps": [{"kind": "snippet", "code": "def apply(state, ctx):\n    " + "x = 1\n    " * 300}]}
    fake_hits = [
        {
            "title": "Verbose Card",
            "description": "desc",
            "score": 0.5,
            "canonical": json.dumps(canonical),
        }
    ]
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    canonical_segment = result.split("canonical=", 1)[1]
    assert json.loads(canonical_segment) == canonical
    assert "…" not in canonical_segment


def test_lower_ranked_canonical_is_omitted_whole_when_budget_is_spent() -> None:
    huge = json.dumps({"steps": [{"kind": "snippet", "code": "x" * 7_900}]})
    fake_hits = [
        {"title": "Top", "description": "desc", "score": 0.9, "canonical": huge},
        {"title": "Second", "description": "desc", "score": 0.8, "canonical": huge},
    ]
    with patch("agent.tools.card_rag.dense_retriever") as mock_factory:
        mock_factory.return_value = lambda query, k: fake_hits
        result = _invoke("q")

    assert result.count("canonical=") == 1
    assert "canonical omitted to preserve the result budget" in result
