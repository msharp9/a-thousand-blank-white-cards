"""tbwc.rag.embeddings — cached OpenAI embeddings singleton for card RAG."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from tbwc.config import get_settings, require_openai_api_key

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    """Return a cached OpenAIEmbeddings instance.

    Reads the OpenAI API key and embedding model via Settings (the single
    source of truth, backed by env vars / .env). Raises a clear error if the
    key is missing. The lru_cache ensures a single instance per process.
    """
    return OpenAIEmbeddings(
        model=get_settings().openai_embedding_model or DEFAULT_EMBEDDING_MODEL,
        openai_api_key=require_openai_api_key(),
    )


def embed_text(text: str) -> list[float]:
    """Embed a single string and return the raw float vector."""
    return get_embeddings().embed_query(text)
