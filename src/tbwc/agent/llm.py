"""tbwc.agent.llm — ChatOpenAI factory shared by all interpretation-agent nodes."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from tbwc.config import get_settings, require_openai_api_key

DEFAULT_CHAT_MODEL = "gpt-5.4-mini"


def get_chat_model(model_name: str | None = None, *, temperature: float = 0) -> ChatOpenAI:
    """Return a ChatOpenAI instance.

    Args:
        model_name: Model id. Falls back to Settings.openai_chat_model
            (OPENAI_CHAT_MODEL / .env), then to DEFAULT_CHAT_MODEL.
        temperature: Sampling temperature (0 = deterministic).

    Reads the OpenAI API key via Settings (the single source of truth) and
    raises a clear error if it is missing.
    """
    name = model_name or get_settings().openai_chat_model or DEFAULT_CHAT_MODEL
    return ChatOpenAI(model=name, temperature=temperature, openai_api_key=require_openai_api_key())
