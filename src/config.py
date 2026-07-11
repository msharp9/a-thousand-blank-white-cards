"""config — Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM provider ---
    # "openai" (default, hosted) or "ollama" (local OpenAI-compatible server, e.g.
    # gpt-oss-20b). Ollama lets you run everything locally to save tokens. The
    # provider gates the OpenAI-key requirement (see require_openai_api_key /
    # llm_api_key) and selects which model + base_url + embedding-dim defaults apply.
    llm_provider: str = "openai"

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    # text-embedding-3-small is 1536-dim; this feeds the Qdrant collection size.
    openai_embedding_dimensions: int = 1536

    # --- Ollama (local, OpenAI-compatible API) ---
    # Only consulted when llm_provider == "ollama". The server exposes an
    # OpenAI-compatible endpoint at /v1 and accepts any (dummy) api key.
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_chat_model: str = "gpt-oss-20b"
    ollama_embedding_model: str = "nomic-embed-text"
    # IMPORTANT: Ollama embedding models have DIFFERENT dimensionality than
    # OpenAI's — nomic-embed-text is 768-dim, not 1536. The Qdrant collection must
    # be created with the matching size (see rag.store.init_store) or upserts fail.
    ollama_embedding_dimensions: int = 768

    # Optional override for LangChain's `.with_structured_output(...)` method
    # (used in agent/nodes.py). Empty = library default. gpt-oss-20b may need
    # "json_schema" for reliable structured output — see agent/nodes.py note.
    structured_output_method: str = ""

    # --- LangSmith observability ---
    # Canonical config uses the modern LANGSMITH_* env var convention. The legacy
    # LANGCHAIN_* names are the *old* names for the same LangSmith settings, kept
    # here only as back-compat aliases so pre-existing .env files keep working:
    # if the LANGSMITH_* value is unset, the LANGCHAIN_* value populates it.
    # App code (see board.app) reads only the langsmith_* fields.
    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices("langsmith_tracing", "langchain_tracing_v2"),
    )
    langsmith_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("langsmith_api_key", "langchain_api_key"),
    )
    langsmith_project: str = Field(
        default="tbwc-dev",
        validation_alias=AliasChoices("langsmith_project", "langchain_project"),
    )
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # --- Tavily ---
    tavily_api_key: str = ""

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "tbwc_cards"

    # --- Agent memory (sqlite) ---
    # Backing store for the interpretation agent's own prior card-interpretation
    # decisions (see agent.tools.agent_memory). Defaults to a repo-relative file
    # that .gitignore already ignores (*.db); override via AGENT_MEMORY_DB, or set
    # ":memory:" for an ephemeral in-process store.
    agent_memory_db: str = "agent_memory.db"

    # --- Sandbox ---
    snippet_execution_enabled: bool = True

    # --- Server / CORS ---
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Logging ---
    # Root/"tbwc" logger level applied by logging_config.configure_logging().
    # Standard names (DEBUG/INFO/WARNING/ERROR/CRITICAL); override via LOG_LEVEL.
    log_level: str = "INFO"

    # --- Provider-aware accessors --------------------------------------------
    # These resolve the correct model / base_url / api key / embedding dimension
    # for the active llm_provider, so callers (agent.llm, rag.embeddings,
    # rag.store) don't branch on the provider themselves.

    @property
    def is_ollama(self) -> bool:
        return self.llm_provider.lower() == "ollama"

    @property
    def chat_model(self) -> str:
        return self.ollama_chat_model if self.is_ollama else self.openai_chat_model

    @property
    def embedding_model(self) -> str:
        return self.ollama_embedding_model if self.is_ollama else self.openai_embedding_model

    @property
    def llm_base_url(self) -> str | None:
        """OpenAI-compatible base_url for the active provider (None = library default)."""
        return self.ollama_base_url if self.is_ollama else None

    @property
    def llm_api_key(self) -> str:
        """API key for the active provider.

        Ollama's OpenAI-compatible server ignores the key but the OpenAI client
        still requires a non-empty string, so we hand it a harmless placeholder.
        For OpenAI we require a real key via require_openai_api_key().
        """
        if self.is_ollama:
            return self.openai_api_key or "ollama"
        return require_openai_api_key()

    @property
    def embedding_dimensions(self) -> int:
        """Vector size for the active provider's embedding model.

        Threaded into the Qdrant collection creation. text-embedding-3-small is
        1536-dim; nomic-embed-text (Ollama) is 768-dim — the collection MUST match.
        """
        return self.ollama_embedding_dimensions if self.is_ollama else self.openai_embedding_dimensions


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton. Call get_settings.cache_clear() in tests."""
    return Settings()


# Actionable message surfaced at startup / on first LLM call when the key is absent.
OPENAI_API_KEY_ERROR = "OPENAI_API_KEY is not set. Set it in your environment or .env file."


def require_openai_api_key() -> str:
    """Return the configured OpenAI API key, or raise a clear, actionable error.

    ``Settings`` (via pydantic-settings ``env_file=".env"``) is the single source
    of truth, so a key set only in ``.env`` is honoured without a manual
    ``load_dotenv`` bridge.

    The key is only required for the ``openai`` provider. When ``llm_provider ==
    "ollama"`` all traffic goes to a local OpenAI-compatible server that ignores
    the key, so this gate is a no-op (returns the placeholder key) and the
    startup check in board.app does not fire.
    """
    settings = get_settings()
    # Skip the OpenAI-key requirement entirely for the local Ollama backend.
    if settings.is_ollama:
        return settings.openai_api_key or "ollama"
    key = settings.openai_api_key
    if not key:
        raise RuntimeError(OPENAI_API_KEY_ERROR)
    return key
