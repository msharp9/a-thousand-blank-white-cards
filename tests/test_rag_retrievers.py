"""Tests for agent.rag.retrievers."""

from __future__ import annotations

from unittest.mock import patch


def test_dense_retriever_calls_search() -> None:
    fake_results = [{"card_id": "c1", "score": 0.9}]
    with patch("agent.rag.retrievers.search", return_value=fake_results) as mock_search:
        from agent.rag.retrievers import dense_retriever

        retrieve = dense_retriever()
        results = retrieve("test query", k=2)
        mock_search.assert_called_once_with("test query", k=2)
        assert results == fake_results


def test_dense_retriever_default_k() -> None:
    with patch("agent.rag.retrievers.search", return_value=[]) as mock_search:
        from agent.rag.retrievers import dense_retriever

        dense_retriever()("q")
        mock_search.assert_called_once_with("q", k=4)
