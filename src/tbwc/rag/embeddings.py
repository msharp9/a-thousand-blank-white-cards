"""tbwc.rag.embeddings — cached OpenAI embeddings singleton for card RAG."""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    """Return a cached OpenAIEmbeddings instance.

    Reads OPENAI_API_KEY and optionally OPENAI_EMBEDDING_MODEL from the
    environment. The lru_cache ensures a single instance per process.
    """
    return OpenAIEmbeddings(
        model=os.environ.get("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        openai_api_key=os.environ["OPENAI_API_KEY"],
    )


def embed_text(text: str) -> list[float]:
    """Embed a single string and return the raw float vector."""
    return get_embeddings().embed_query(text)
