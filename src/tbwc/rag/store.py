"""tbwc.rag.store — in-memory Qdrant vector store for card exemplars.

Manages the single global 'cards' collection in :memory: mode (prototype).
Exposes init_store(), upsert_card(), search(). Embedded text is title+description;
canonical effect and provenance travel as payload, not embedded.
"""

from __future__ import annotations

import hashlib
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from tbwc.rag.embeddings import EMBEDDING_DIMENSIONS, embed_text

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
    _client.recreate_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
    )
    return _client


def _require_client() -> QdrantClient:
    if _client is None:
        raise RuntimeError("rag.store not initialised — call init_store() first")
    return _client


def upsert_card(
    card_id: str,
    title: str,
    description: str,
    canonical: str,
    source: str = "seed",
) -> None:
    """Embed title+description and upsert a point into the cards collection.

    canonical is stored as payload (not embedded). source is a provenance label
    ("seed" | "player"), also payload.
    """
    client = _require_client()
    text = f"{title}\n{description}"
    vector = embed_text(text)
    point_id = _stable_point_id(card_id)  # Qdrant needs a stable uint64 id
    point = PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "card_id": card_id,
            "title": title,
            "description": description,
            "canonical": canonical,
            "source": source,
        },
    )
    client.upsert(collection_name=COLLECTION_NAME, points=[point])


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
