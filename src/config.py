"""config — Application settings loaded from environment / .env file."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import ClassVar

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM gateway (generic, OpenAI-compatible) ---
    # ONE base_url / api_key / model set drives BOTH chat and embeddings. Point it
    # at any OpenAI-compatible endpoint: hosted OpenAI (leave LLM_BASE_URL blank +
    # set a real LLM_API_KEY), a company gateway such as bifrost
    # (LLM_BASE_URL=https://.../v1 + key), or a local server like Ollama
    # (LLM_BASE_URL=http://localhost:11434/v1, LLM_API_KEY=anything).
    #
    # The two url/key fields carry the raw env value; the same-named accessors
    # below normalise them for the OpenAI client (blank base_url -> None, blank
    # api_key -> a harmless placeholder). We alias so the raw fields keep clean
    # env names (LLM_BASE_URL / LLM_API_KEY) while the properties shadow the
    # public names consumers read.
    llm_base_url_raw: str = Field(default="", validation_alias="llm_base_url")
    llm_api_key_raw: str = Field(default="", validation_alias="llm_api_key")

    llm_chat_model: str = "gpt-5.4-mini"
    llm_embedding_model: str = "text-embedding-3-small"
    # Vision: when True AND the played/authored card has art, run_agent attaches
    # the card's PNG data-URL to the model input as an image block. Default OFF —
    # the agent stays byte-identical to text-only until explicitly enabled, and
    # LLM_CHAT_MODEL must actually accept image input for it to matter.
    vision_enabled: bool = False
    # Vector size of the embedding model; feeds the Qdrant collection dimension and
    # MUST match the model. OpenAI text-embedding-3-small is 1536-dim; other
    # servers differ (e.g. Ollama nomic-embed-text is 768) — override to match.
    llm_embedding_dimensions: int = 1536

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

    # Append-only capability telemetry emitted by the agent's ``wish`` tool.
    # Keep it outside tracked source data; production can point this at a
    # persistent volume and export the JSONL for human triage.
    capability_wish_path: str = ".devstate/capability_wishes.jsonl"
    capability_wish_max_bytes: int = Field(default=1_048_576, ge=1024)

    # --- Sandbox ---
    snippet_execution_enabled: bool = True

    # --- Developer experience ---
    dev_mode: bool = False

    # --- Server / CORS ---
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Logging ---
    # Root logger level applied by logging_config.configure_logging().
    # Standard names (DEBUG/INFO/WARNING/ERROR/CRITICAL); override via LOG_LEVEL.
    log_level: str = "INFO"

    # --- Gateway accessors ----------------------------------------------------
    # Normalise the raw LLM_BASE_URL / LLM_API_KEY values for the OpenAI client so
    # callers (agent.llm, rag.embeddings, rag.store) never special-case a provider.

    # Placeholder handed to the OpenAI client when no key is configured. Keyless
    # local gateways / servers (e.g. Ollama) ignore it, but the client library
    # still requires a NON-EMPTY string, so we supply one.
    API_KEY_PLACEHOLDER: ClassVar[str] = "not-needed"

    @property
    def llm_base_url(self) -> str | None:
        """OpenAI-compatible base_url (None = the OpenAI library default endpoint)."""
        return self.llm_base_url_raw or None

    @property
    def llm_api_key(self) -> str:
        """API key for the gateway, or a placeholder when none is configured.

        Blank is allowed (keyless local gateways), but the OpenAI client requires
        a non-empty string, so we substitute ``API_KEY_PLACEHOLDER``.
        """
        return self.llm_api_key_raw or self.API_KEY_PLACEHOLDER

    @property
    def llm_default_headers(self) -> dict[str, str] | None:
        """Mirror LLM_API_KEY into bifrost's x-bf-vk header when a gateway is set."""
        if self.llm_api_key_raw and self.llm_base_url_raw:
            return {"x-bf-vk": self.llm_api_key_raw}
        return None

    @property
    def chat_model(self) -> str:
        return self.llm_chat_model

    @property
    def embedding_model(self) -> str:
        return self.llm_embedding_model

    @property
    def embedding_dimensions(self) -> int:
        """Vector size of the embedding model; threaded into Qdrant collection sizing."""
        return self.llm_embedding_dimensions


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton. Call get_settings.cache_clear() in tests."""
    return Settings()


# --- Eval model pricing --------------------------------------------------- #
# USD per 1,000,000 tokens, keyed by chat-model name. Consumed only by the eval
# harness (evals.instrumentation.cost_usd) to turn token counts into a dollar
# figure — never read by the production agent. Keep these current with the
# gateway's actual billing; the eval notebook may override this dict at runtime.
# Unknown models fall back to "default". Prices are estimates for planning, not
# billing truth.
# Bedrock on-demand US-region rates (via the bifrost gateway), July 2026. Each
# model is keyed under every name the gateway serves it as. Sonnet 5 has promo
# pricing of 2.00/10.00 through 2026-08-31; we carry the standard rate so
# projections don't understate post-promo cost.
_PRICE_HAIKU = {"input": 1.00, "output": 5.00}
_PRICE_SONNET = {"input": 3.00, "output": 15.00}
_PRICE_OPUS = {"input": 5.00, "output": 25.00}
_PRICE_GEMMA_4_31B = {"input": 0.14, "output": 0.40}
_PRICE_GPT_5_4 = {"input": 2.75, "output": 16.50}

EVAL_MODEL_PRICES: dict[str, dict[str, float]] = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": _PRICE_HAIKU,
    "claude-haiku-4-5": _PRICE_HAIKU,
    "claude-haiku-4-5-20251001": _PRICE_HAIKU,
    "us.anthropic.claude-sonnet-5": _PRICE_SONNET,
    "claude-sonnet-5": _PRICE_SONNET,
    "us.anthropic.claude-opus-4-8": _PRICE_OPUS,
    "claude-opus-4-8": _PRICE_OPUS,
    "google.gemma-4-31b": _PRICE_GEMMA_4_31B,
    "openai.gpt-5.4": _PRICE_GPT_5_4,
    "default": {"input": 0.50, "output": 1.50},
}


# --- Eval benchmarks ------------------------------------------------------- #
# The four scored datasets, each a distinct benchmark. ``canonical_key`` names
# the per-card label field (seed cards use "canonical"; eval/real use
# "human_canonical"). ``path`` is repo-root-relative. ``scored`` marks whether
# every card carries a usable label — "real" cards are mostly unlabeled, so
# similarity metrics there are only meaningful on the annotated subset.
EVAL_BENCHMARKS: dict[str, dict[str, object]] = {
    "seed": {"path": "data/seed_cards.json", "canonical_key": "canonical", "scored": True},
    "eval": {"path": "data/eval/eval_cards.json", "canonical_key": "human_canonical", "scored": True},
    "eval_hard": {"path": "data/eval/eval_cards_hard.json", "canonical_key": "human_canonical", "scored": True},
    "real": {"path": "data/eval/real_cards.json", "canonical_key": "human_canonical", "scored": False},
}


def warn_if_no_llm_credentials() -> None:
    """SOFT check: warn (never raise) when the LLM gateway is likely unusable.

    An empty ``LLM_BASE_URL`` targets hosted OpenAI, which needs a real key; if
    ``LLM_API_KEY`` is ALSO empty that combo will fail on the first call. We only
    log a warning — a blank key is legitimate for keyless local gateways, and a
    non-empty ``LLM_BASE_URL`` means some other endpoint is in play — so startup
    never hard-fails on credentials.
    """
    settings = get_settings()
    if not settings.llm_base_url_raw and not settings.llm_api_key_raw:
        logger.warning(
            "No LLM_API_KEY set and LLM_BASE_URL is empty (hosted OpenAI) — "
            "LLM calls will fail. Set LLM_API_KEY, or point LLM_BASE_URL at a gateway."
        )
