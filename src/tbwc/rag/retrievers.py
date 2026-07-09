"""tbwc.rag.retrievers — retriever factories behind a common interface.

dense_retriever() is the real baseline (cosine via tbwc.rag.store.search).
advanced_retriever() is a stub that currently delegates to dense; a later phase
swaps in hybrid/reranked retrieval without touching the agent graph.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tbwc.rag.store import search

# Type alias for a retriever callable: (query, k) -> list of payload dicts.
Retriever = Callable[[str, int], list[dict[str, Any]]]


def dense_retriever() -> Retriever:
    """Return the baseline dense (cosine) retriever backed by tbwc.rag.store.search."""

    def _retrieve(query: str, k: int = 4) -> list[dict[str, Any]]:
        return search(query, k=k)

    return _retrieve


def advanced_retriever() -> Retriever:
    """STUB — hybrid/reranked retrieval arrives in a later phase.

    Currently delegates to dense_retriever so the graph stays functional.
    """
    # TODO(phase6): BM25 + dense fusion + cross-encoder reranking
    return dense_retriever()
