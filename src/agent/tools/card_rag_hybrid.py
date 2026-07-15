"""agent.tools.card_rag_hybrid — LangChain tool wrapping the hybrid RAG retriever.

Exposes a single ``card_rag_hybrid`` @tool the agent can call to find precedent
cards matching the given query via semantic similarity AND exact keyword matching.
This tool combines dense (cosine) retrieval with BM25 keyword search via RRF.

The tool uses the hybrid retriever from ``agent.rag.retrievers`` and degrades
gracefully: if the store is empty, uninitialised, or the retriever raises, it
returns a short human-readable string instead of propagating the exception.
"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from agent.rag.retrievers import hybrid_retriever
from agent.tools.card_rag import _format_hits

logger = logging.getLogger(__name__)

_NO_RESULTS = "no similar cards found"
_UNAVAILABLE = "card retrieval unavailable"


@tool
def card_rag_hybrid(query: str, k: int = 4) -> str:
    """Retrieve previously-seen cards similar to the given text, to compare a new card against precedent interpretations. Matches by meaning AND by exact game-mechanic keywords (draw, discard, steal, swap, ...), so it finds precedents plain semantic search misses."""
    try:
        retrieve = hybrid_retriever()
        hits = retrieve(query, k)
    except Exception as exc:
        logger.warning("card_rag_hybrid retrieval unavailable (non-fatal): %s", exc)
        return _UNAVAILABLE
    if not hits:
        return _NO_RESULTS
    return _format_hits(hits)


def get_card_rag_hybrid_tool():
    """Return the ``card_rag_hybrid`` LangChain tool object."""
    return card_rag_hybrid
