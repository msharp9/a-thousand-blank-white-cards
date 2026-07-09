"""tbwc.config — Application settings loaded from environment / .env file."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-5.4-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # --- LangSmith ---
    langchain_api_key: str = ""
    langchain_project: str = "tbwc"
    langchain_tracing_v2: bool = False

    # LangSmith observability (newer LANGSMITH_* env var convention)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "tbwc-dev"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # --- Tavily ---
    tavily_api_key: str = ""

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "tbwc_cards"

    # --- Sandbox ---
    snippet_execution_enabled: bool = True

    # --- Server / CORS ---
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    host: str = "0.0.0.0"
    port: int = 8000


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
    ``load_dotenv`` bridge. OpenAI is currently required; if a local/Ollama
    backend is added later, this gate should be skipped in that mode.
    """
    key = get_settings().openai_api_key
    if not key:
        raise RuntimeError(OPENAI_API_KEY_ERROR)
    return key
