"""agent.rag.retrievers — retriever factories behind a common interface.

dense_retriever() is the baseline (cosine via agent.rag.store.search).
hybrid_retriever() fuses dense search with a per-call BM25 pass over the full
card corpus via Reciprocal Rank Fusion, so exact keyword matches the embedding
misses still surface.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from rank_bm25 import BM25Okapi

from agent.rag import store
from agent.rag.store import search

logger = logging.getLogger(__name__)

# Type alias for a retriever callable: (query, k) -> list of payload dicts.
Retriever = Callable[[str, int], list[dict[str, Any]]]

# First-stage recall depth for each leg of the hybrid retriever, before RRF
# trims to the caller's requested k.
_FIRST_STAGE_K = 10


def dense_retriever() -> Retriever:
    """Return the baseline dense (cosine) retriever backed by agent.rag.store.search."""

    def _retrieve(query: str, k: int = 4) -> list[dict[str, Any]]:
        return search(query, k=k)

    return _retrieve


def _tokenize(text: str) -> list[str]:
    """Lowercase and split text into alphanumeric tokens for BM25 indexing."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _rrf(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    limit: int,
    rrf_constant: int = 60,
) -> list[dict[str, Any]]:
    """Fuse ranked lists of payload dicts via Reciprocal Rank Fusion.

    Docs are keyed by card_id (falling back to title) so the same card ranked
    in multiple lists accumulates 1 / (rrf_constant + rank) once per list,
    with rank starting at 1. The first-seen dict for each key is kept (copied,
    never mutated) and its 'score' field is replaced with the fused score.
    Returns the top-limit dicts sorted by fused score descending.
    """
    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}
    for ranked_list in ranked_lists:
        for rank, doc in enumerate(ranked_list, start=1):
            key = str(doc.get("card_id") or doc.get("title"))
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_constant + rank)
            if key not in docs:
                docs[key] = doc

    fused = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    results: list[dict[str, Any]] = []
    for key, fused_score in fused:
        doc = dict(docs[key])
        doc["score"] = fused_score
        results.append(doc)
    return results


def hybrid_retriever() -> Retriever:
    """Return a retriever that fuses dense (cosine) and BM25 keyword search via RRF.

    Each call builds a fresh BM25Okapi index over agent.rag.store.list_all_cards()
    (the in-memory store mutates during games and the corpus is small, so no
    caching is worthwhile). If the corpus is empty or the query has no
    tokenizable terms, the dense results are returned, trimmed to k.
    """

    def _retrieve(query: str, k: int = 4) -> list[dict[str, Any]]:
        dense_hits = store.search(query, k=_FIRST_STAGE_K)

        cards = store.list_all_cards()
        query_tokens = _tokenize(query)
        if not cards or not query_tokens:
            return dense_hits[:k]

        tokenized_corpus = [_tokenize(f"{card.get('title', '')}\n{card.get('description', '')}") for card in cards]
        bm25 = BM25Okapi(tokenized_corpus)
        bm25_scores = bm25.get_scores(query_tokens)
        ranked = sorted(zip(cards, bm25_scores, strict=True), key=lambda pair: pair[1], reverse=True)
        bm25_hits = [card for card, score in ranked if score > 0][:_FIRST_STAGE_K]

        return _rrf([dense_hits, bm25_hits], limit=k)

    return _retrieve
