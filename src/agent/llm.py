"""agent.llm — ChatOpenAI factory shared by the interpretation agent."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from config import get_settings

DEFAULT_CHAT_MODEL = "gpt-5.4-mini"


def get_chat_model(model_name: str | None = None, *, temperature: float = 0) -> ChatOpenAI:
    """Return a ChatOpenAI instance for the configured provider.

    Args:
        model_name: Model id. Falls back to the active provider's configured chat
            model (Settings.chat_model — OPENAI_CHAT_MODEL or OLLAMA_CHAT_MODEL),
            then to DEFAULT_CHAT_MODEL.
        temperature: Sampling temperature (0 = deterministic).

    Provider-aware: for ``llm_provider == "ollama"`` the client points at the
    local OpenAI-compatible server (Settings.llm_base_url) with a placeholder key;
    for OpenAI it uses the real key (raising a clear error if it is missing).
    ChatOpenAI talks to any OpenAI-compatible endpoint via ``base_url``.
    """
    settings = get_settings()
    name = model_name or settings.chat_model or DEFAULT_CHAT_MODEL
    return ChatOpenAI(
        model=name,
        temperature=temperature,
        openai_api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
