"""Tests for the multi-query advanced retriever (LLM + store mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

from tbwc.rag.retrievers import MultiQueryCardRetriever, advanced_retriever


def _base_returning(docs_by_query: dict[str, list[dict]]):
    def _base(q: str, k: int = 4) -> list[dict]:
        return docs_by_query.get(q, [])

    return _base


def test_multiquery_dedups_union() -> None:
    base = _base_returning(
        {
            "orig": [{"card_id": "a", "title": "A"}, {"card_id": "b", "title": "B"}],
            "para1": [{"card_id": "b", "title": "B"}, {"card_id": "c", "title": "C"}],
        }
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content='["para1"]')
    r = MultiQueryCardRetriever(base=base, llm=llm, n_queries=1)
    out = r.retrieve("orig", k=4)
    ids = [d["card_id"] for d in out]
    assert ids == ["a", "b", "c"]  # union, deduped, first-occurrence order


def test_multiquery_falls_back_on_bad_llm_json() -> None:
    base = _base_returning({"orig": [{"card_id": "a", "title": "A"}]})
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="not json")
    r = MultiQueryCardRetriever(base=base, llm=llm)
    out = r.retrieve("orig")
    assert [d["card_id"] for d in out] == ["a"]  # only original query used


def test_advanced_retriever_is_callable() -> None:
    r = advanced_retriever()
    assert callable(r)


def test_multiquery_callable_interface() -> None:
    base = _base_returning({"q": [{"card_id": "x", "title": "X"}]})
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="[]")
    r = MultiQueryCardRetriever(base=base, llm=llm)
    assert r("q", 4) == [{"card_id": "x", "title": "X"}]
