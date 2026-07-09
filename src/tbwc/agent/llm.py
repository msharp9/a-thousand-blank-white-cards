"""tbwc.agent.llm — ChatOpenAI factory shared by all interpretation-agent nodes."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

DEFAULT_CHAT_MODEL = "gpt-5.4-mini"


def get_chat_model(model_name: str | None = None, *, temperature: float = 0) -> ChatOpenAI:
    """Return a ChatOpenAI instance.

    Args:
        model_name: Model id. Falls back to OPENAI_CHAT_MODEL env var, then to
            DEFAULT_CHAT_MODEL.
        temperature: Sampling temperature (0 = deterministic).

    The OPENAI_API_KEY environment variable must be set.
    """
    name = model_name or os.environ.get("OPENAI_CHAT_MODEL", DEFAULT_CHAT_MODEL)
    return ChatOpenAI(model=name, temperature=temperature, openai_api_key=os.environ["OPENAI_API_KEY"])
