"""agent.rag.embeddings — cached OpenAI embeddings singleton for card RAG."""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

from config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
# Default (OpenAI text-embedding-3-small) vector size. NOTE: this is a fallback
# only — the authoritative dimension comes from Settings.embedding_dimensions
# (LLM_EMBEDDING_DIMENSIONS) and is what rag.store uses to size the Qdrant
# collection. Other embedding models differ (e.g. Ollama nomic-embed-text = 768),
# so never hard-code 1536 downstream; read embedding_dimensions().
EMBEDDING_DIMENSIONS = 1536


def embedding_dimensions() -> int:
    """Return the configured embedding vector size (Settings.embedding_dimensions).

    rag.store threads this into the Qdrant collection creation so its size matches
    the vectors the configured model emits.
    """
    return get_settings().embedding_dimensions


@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    """Return a cached OpenAIEmbeddings instance for the configured LLM gateway.

    Reads the embedding model, API key, and base_url via Settings (the single
    source of truth, backed by env vars / .env). ``base_url`` is the configured
    endpoint (None = hosted OpenAI) and the key is a placeholder when blank. The
    lru_cache ensures a single instance per process.
    """
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model or DEFAULT_EMBEDDING_MODEL,
        openai_api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        default_headers=settings.llm_default_headers,
        # False = send raw strings, not tiktoken token-ID arrays. Gateways (bifrost/
        # Bedrock) and local servers reject token arrays; cards are length-bounded so
        # the tiktoken chunking that True would add is never needed.
        check_embedding_ctx_length=False,
    )


def embed_text(text: str) -> list[float]:
    """Embed a single string and return the raw float vector."""
    return get_embeddings().embed_query(text)


CACHE_FILENAME = ".embedding_cache.json"

_cache: dict[str, list[float]] | None = None


def _cache_path() -> Path:
    """Return the disk cache path at the project root (four levels up from this file)."""
    return Path(__file__).resolve().parents[3] / CACHE_FILENAME


def _load_cache() -> dict[str, list[float]]:
    """Return the process-wide vector cache, loading it from disk once.

    A missing or corrupt cache file degrades to an empty cache — the cache is a
    pure optimization and must never break embedding.
    """
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_cache_path().read_text())
        except Exception:
            logger.debug("embedding cache unreadable — starting empty", exc_info=True)
            _cache = {}
    return _cache


def _save_cache(cache: dict[str, list[float]]) -> None:
    """Persist the cache to disk, swallowing any error (optimization only)."""
    try:
        _cache_path().write_text(json.dumps(cache))
    except Exception:
        logger.debug("failed to persist embedding cache", exc_info=True)


def _cache_key(text: str) -> str:
    """Hash model + dimensions + text.

    Model and dimensions are part of the key so switching embedding model/provider
    invalidates automatically: a 1536-dim vector must never be served for a 768-dim
    model.
    """
    model = get_settings().embedding_model
    payload = f"{model}\n{embedding_dimensions()}\n{text}"
    return hashlib.blake2b(payload.encode()).hexdigest()


def embed_text_cached(text: str) -> list[float]:
    """Embed a single string, returning a cached vector when the key hits."""
    cache = _load_cache()
    key = _cache_key(text)
    if key in cache:
        return cache[key]
    vector = embed_text(text)
    cache[key] = vector
    _save_cache(cache)
    return vector


def embed_texts_cached(texts: list[str]) -> list[list[float]]:
    """Embed many strings, calling the live API only for cache misses.

    Returns vectors in input order; duplicate misses within a batch are embedded
    once. Misses go through one batched ``embed_documents`` round-trip, falling
    back to per-text embedding when a gateway returns a mismatched vector count
    (some Bedrock-routed gateways do not honour list batching).
    """
    cache = _load_cache()
    keys = [_cache_key(text) for text in texts]
    missing = {key: text for key, text in zip(keys, texts) if key not in cache}
    if missing:
        miss_keys = list(missing.keys())
        vectors = get_embeddings().embed_documents(list(missing.values()))
        if len(vectors) == len(miss_keys):
            cache.update(zip(miss_keys, vectors))
        else:
            for key, text in missing.items():
                cache[key] = embed_text(text)
        _save_cache(cache)
    return [cache[key] for key in keys]
