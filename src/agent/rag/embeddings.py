"""agent.rag.embeddings — cached OpenAI embeddings singleton for card RAG."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from config import get_settings

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
# Default (OpenAI text-embedding-3-small) vector size. NOTE: this is a fallback
# only — the authoritative, provider-aware dimension comes from
# Settings.embedding_dimensions and is what rag.store uses to size the Qdrant
# collection. Ollama models differ (nomic-embed-text = 768), so never hard-code
# 1536 downstream; read embedding_dimensions() / Settings.embedding_dimensions.
EMBEDDING_DIMENSIONS = 1536


def embedding_dimensions() -> int:
    """Return the vector size for the active provider's embedding model.

    Provider-aware (OpenAI 1536 vs Ollama nomic-embed-text 768). rag.store threads
    this into the Qdrant collection creation so its size matches the vectors.
    """
    return get_settings().embedding_dimensions


@lru_cache(maxsize=1)
def get_embeddings() -> OpenAIEmbeddings:
    """Return a cached OpenAIEmbeddings instance for the configured provider.

    Reads the embedding model, API key, and base_url via Settings (the single
    source of truth, backed by env vars / .env). For OpenAI a real key is
    required (clear error if missing); for Ollama the client points at the local
    OpenAI-compatible server with a placeholder key. The lru_cache ensures a
    single instance per process.
    """
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model or DEFAULT_EMBEDDING_MODEL,
        openai_api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        # Ollama's OpenAI-compatible /embeddings endpoint only accepts raw
        # strings. By default OpenAIEmbeddings tokenizes text with tiktoken and
        # POSTs integer token-ID arrays (the "len-safe" path), which Ollama
        # rejects with "400 - invalid input type". Disabling the context-length
        # check sends the raw string instead. Only needed for non-OpenAI
        # (local) providers; the hosted OpenAI API handles token-ID input fine.
        check_embedding_ctx_length=not settings.is_ollama,
    )


def embed_text(text: str) -> list[float]:
    """Embed a single string and return the raw float vector."""
    return get_embeddings().embed_query(text)
