"""agent.rag.store — in-memory Qdrant vector store for card exemplars.

Manages the single global 'cards' collection in :memory: mode (prototype).
Exposes init_store(), upsert_card(), search(). Embedded text is title+description;
canonical effect and provenance travel as payload, not embedded.
"""

from __future__ import annotations

import hashlib
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from agent.rag.embeddings import embedding_dimensions, embed_text, embed_text_cached, embed_texts_cached
from models.card import MAX_CARD_DESCRIPTION, MAX_CARD_TITLE

COLLECTION_NAME = "cards"


def _stable_point_id(card_id: str) -> int:
    """Derive a deterministic uint64 Qdrant point id from a card_id.

    Python's built-in str hash is per-process randomized (PYTHONHASHSEED), so it
    would produce a different id each run and re-seeding the same card_id would
    create duplicate points. blake2b is stable across processes.
    """
    return int.from_bytes(hashlib.blake2b(card_id.encode(), digest_size=8).digest(), "big") % (2**63)


# Module-level singleton — init_store() must be called before any other function.
_client: QdrantClient | None = None


def init_store() -> QdrantClient:
    """Create (or recreate) the in-memory Qdrant client and 'cards' collection.

    Safe to call multiple times; recreates the collection each call so tests can
    start clean. Returns the client for inspection.
    """
    global _client
    _client = QdrantClient(location=":memory:")
    # Size the collection to the configured embedding dimension. OpenAI
    # text-embedding-3-small is 1536-dim, Ollama nomic-embed-text is 768-dim — a
    # mismatch here makes every upsert fail, so derive it rather than hard-coding.
    _client.recreate_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=embedding_dimensions(), distance=Distance.COSINE),
    )
    return _client


def _require_client() -> QdrantClient:
    if _client is None:
        raise RuntimeError("rag.store not initialised — call init_store() first")
    return _client


def _card_text(title: str, description: str) -> str:
    return f"{title}\n{description}"


def _validate_card_lengths(card_id: str, title: str, description: str) -> None:
    if len(title) > MAX_CARD_TITLE or len(description) > MAX_CARD_DESCRIPTION:
        raise ValueError(
            f"card {card_id!r} exceeds text limits "
            f"(title {len(title)}/{MAX_CARD_TITLE}, description {len(description)}/{MAX_CARD_DESCRIPTION})"
        )


def _card_point(
    card_id: str,
    title: str,
    description: str,
    canonical: str,
    source: str,
    vector: list[float],
    keep_votes: int = 0,
    destroy_votes: int = 0,
) -> PointStruct:
    return PointStruct(
        id=_stable_point_id(card_id),  # Qdrant needs a stable uint64 id
        vector=vector,
        payload={
            "card_id": card_id,
            "title": title,
            "description": description,
            "canonical": canonical,
            "source": source,
            "keep_votes": keep_votes,
            "destroy_votes": destroy_votes,
        },
    )


def upsert_card(
    card_id: str,
    title: str,
    description: str,
    canonical: str,
    source: str = "seed",
    keep_votes: int = 0,
    destroy_votes: int = 0,
) -> None:
    """Embed title+description and upsert a point into the cards collection.

    canonical is stored as payload (not embedded). source is a provenance label
    ("seed" | "player"), also payload. Embedding goes through the content-hash cache
    so unchanged cards are never re-embedded across reloads.

    keep_votes/destroy_votes are the CUMULATIVE epilogue vote totals across every
    game this card has survived — Qdrant's upsert replaces the whole payload, so
    callers carrying forward vote history must pass the running totals back in
    (see :func:`get_card_totals`), not just this game's counts.
    """
    _validate_card_lengths(card_id, title, description)
    client = _require_client()
    vector = embed_text_cached(_card_text(title, description))
    point = _card_point(card_id, title, description, canonical, source, vector, keep_votes, destroy_votes)
    client.upsert(collection_name=COLLECTION_NAME, points=[point])


def upsert_cards(cards: list[dict[str, Any]]) -> None:
    """Embed and upsert many cards in one batch (one embedding round-trip for misses).

    Each dict needs card_id, title, description, canonical, and source. Length limits
    are validated per card before any embedding.
    """
    if not cards:
        return
    for card in cards:
        _validate_card_lengths(card["card_id"], card["title"], card["description"])
    client = _require_client()
    vectors = embed_texts_cached([_card_text(c["title"], c["description"]) for c in cards])
    points = [
        _card_point(c["card_id"], c["title"], c["description"], c["canonical"], c["source"], vector)
        for c, vector in zip(cards, vectors)
    ]
    client.upsert(collection_name=COLLECTION_NAME, points=points)


def list_all_cards(limit: int = 10_000) -> list[dict[str, Any]]:
    """Return every stored card payload (seed + prior-game kept cards).

    Uses Qdrant's scroll API, which reads stored payloads directly — no query
    embedding is computed, so this works offline (no OpenAI call). Raises
    RuntimeError if the store was never initialised, letting callers fall back
    to another card source.
    """
    client = _require_client()
    records, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return [dict(record.payload or {}) for record in records]


def get_card_totals(card_id: str) -> tuple[int, int] | None:
    """Return (keep_votes, destroy_votes) currently stored for card_id.

    Returns None if the card isn't in the corpus (e.g. authored this game and
    never previously kept) — callers should treat that as a 0-0 starting point.
    """
    client = _require_client()
    records = client.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[_stable_point_id(card_id)],
        with_payload=True,
    )
    if not records:
        return None
    payload = records[0].payload or {}
    return (payload.get("keep_votes", 0), payload.get("destroy_votes", 0))


def delete_card(card_id: str) -> None:
    """Remove a card from the corpus so it stops re-entering future decks.

    Used to retire cards the cumulative epilogue vote decides to destroy.
    Deleting an id that isn't present is a no-op.
    """
    client = _require_client()
    client.delete(collection_name=COLLECTION_NAME, points_selector=[_stable_point_id(card_id)])


def search(query_text: str, k: int = 4) -> list[dict[str, Any]]:
    """Embed query_text and return the top-k most similar card payloads.

    Each dict has: card_id, title, description, canonical, source, plus a 'score'
    float (cosine similarity).
    """
    client = _require_client()
    vector = embed_text(query_text)
    response = client.query_points(collection_name=COLLECTION_NAME, query=vector, limit=k)
    exemplars: list[dict[str, Any]] = []
    for hit in response.points:
        payload = dict(hit.payload)
        payload["score"] = hit.score
        exemplars.append(payload)
    return exemplars
