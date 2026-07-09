"""Tests for tbwc.rag.retrievers."""

from __future__ import annotations

from unittest.mock import patch


def test_dense_retriever_calls_search() -> None:
    fake_results = [{"card_id": "c1", "score": 0.9}]
    with patch("tbwc.rag.retrievers.search", return_value=fake_results) as mock_search:
        from tbwc.rag.retrievers import dense_retriever

        retrieve = dense_retriever()
        results = retrieve("test query", k=2)
        mock_search.assert_called_once_with("test query", k=2)
        assert results == fake_results


def test_dense_retriever_default_k() -> None:
    with patch("tbwc.rag.retrievers.search", return_value=[]) as mock_search:
        from tbwc.rag.retrievers import dense_retriever

        dense_retriever()("q")
        mock_search.assert_called_once_with("q", k=4)


def test_advanced_retriever_is_callable_and_delegates() -> None:
    fake_results = [{"card_id": "x"}]
    with patch("tbwc.rag.retrievers.search", return_value=fake_results) as mock_search:
        from tbwc.rag.retrievers import advanced_retriever

        retrieve = advanced_retriever()
        assert callable(retrieve)
        assert retrieve("q", k=1) == fake_results
        mock_search.assert_called_once_with("q", k=1)
