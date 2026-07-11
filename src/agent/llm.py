"""agent.llm — ChatOpenAI factory shared by the interpretation agent."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from config import get_settings

DEFAULT_CHAT_MODEL = "gpt-5.4-mini"


def get_chat_model(model_name: str | None = None, *, temperature: float = 0) -> ChatOpenAI:
    """Return a ChatOpenAI instance for the configured LLM gateway.

    Args:
        model_name: Model id. Falls back to the configured chat model
            (Settings.chat_model — LLM_CHAT_MODEL), then to DEFAULT_CHAT_MODEL.
        temperature: Sampling temperature (0 = deterministic).

    Gateway-aware: ``base_url`` is the configured endpoint (Settings.llm_base_url,
    ``None`` for hosted OpenAI's default) and ``openai_api_key`` is the configured
    key (a placeholder when blank, for keyless local gateways). ChatOpenAI talks to
    any OpenAI-compatible endpoint via ``base_url``.
    """
    settings = get_settings()
    name = model_name or settings.chat_model or DEFAULT_CHAT_MODEL
    return ChatOpenAI(
        model=name,
        temperature=temperature,
        openai_api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        default_headers=settings.llm_default_headers,
    )
