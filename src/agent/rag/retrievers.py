"""agent.rag.retrievers — retriever factories behind a common interface.

dense_retriever() is the real baseline (cosine via agent.rag.store.search).
advanced_retriever() is a multi-query expansion retriever: it paraphrases the
query via an LLM, retrieves each paraphrase through the dense retriever, and
returns the deduplicated union — without touching the agent graph.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from agent.rag.store import search

logger = logging.getLogger(__name__)

# Type alias for a retriever callable: (query, k) -> list of payload dicts.
Retriever = Callable[[str, int], list[dict[str, Any]]]

_PARAPHRASE_SYSTEM = (
    "You are helping retrieve exemplar cards from a card-game database. Given a card "
    "description, generate {n} short, distinct paraphrase queries covering different "
    "aspects of the card's intent. Output ONLY a JSON array of strings, no commentary."
)


def dense_retriever() -> Retriever:
    """Return the baseline dense (cosine) retriever backed by agent.rag.store.search."""

    def _retrieve(query: str, k: int = 4) -> list[dict[str, Any]]:
        return search(query, k=k)

    return _retrieve


class MultiQueryCardRetriever:
    """Multi-query retriever: expand the query into paraphrases via an LLM, retrieve
    each through a base dense retriever, return the deduplicated union (by card_id).

    Interchangeable with a plain Retriever via .retrieve / __call__.
    """

    def __init__(self, base: Retriever | None = None, llm=None, n_queries: int = 3) -> None:
        self._base: Retriever = base or dense_retriever()
        self._llm = llm  # lazily built ChatOpenAI if None
        self._n_queries = n_queries

    def _get_llm(self):
        if self._llm is None:
            from agent.llm import get_chat_model

            self._llm = get_chat_model(temperature=0.3)
        return self._llm

    def _paraphrases(self, query: str) -> list[str]:
        llm = self._get_llm()
        messages = [
            {"role": "system", "content": _PARAPHRASE_SYSTEM.format(n=self._n_queries)},
            {"role": "human", "content": f"Card description: {query}"},
        ]
        try:
            resp = llm.invoke(messages)
            parsed = json.loads(resp.content)
            if isinstance(parsed, list):
                return [str(p) for p in parsed]
        except Exception as exc:
            logger.warning("paraphrase generation failed (non-fatal): %s", exc)
        return []

    def retrieve(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        queries = [query, *[p for p in self._paraphrases(query) if p != query]]
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for q in queries:
            for doc in self._base(q, k):
                doc_id = str(doc.get("card_id") or doc.get("title") or id(doc))
                if doc_id not in seen:
                    seen.add(doc_id)
                    results.append(doc)
        return results

    def __call__(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        return self.retrieve(query, k)


def advanced_retriever() -> Retriever:
    """Multi-query expansion retriever (replaces the phase-2 dense-delegating stub).

    Returns a Retriever callable backed by MultiQueryCardRetriever.
    """
    return MultiQueryCardRetriever()
