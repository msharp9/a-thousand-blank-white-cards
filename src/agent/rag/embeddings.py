"""agent.rag.embeddings — cached OpenAI embeddings singleton for card RAG."""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from config import get_settings

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
