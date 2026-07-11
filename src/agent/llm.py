"""agent.llm — ChatOpenAI factory shared by all interpretation-agent nodes."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable
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


def with_structured_output(llm: ChatOpenAI, schema: Any) -> Runnable:
    """Wrap ``llm.with_structured_output(schema)`` honouring STRUCTURED_OUTPUT_METHOD.

    CAVEAT (Ollama / gpt-oss-20b): LangChain's structured-output binding defaults
    to OpenAI function/tool calling. Local models served via Ollama's
    OpenAI-compatible API may not support tool-calling reliably (or at all), so
    typed extraction can fail or return malformed output. Setting
    STRUCTURED_OUTPUT_METHOD="json_schema" switches to JSON-schema-constrained
    decoding, which gpt-oss-20b tends to handle better. Left empty (the default),
    this passes NO method= kwarg so hosted-OpenAI behaviour is unchanged.

    The agent nodes (agent.nodes) go through this helper so the method is
    configurable in one place without rewriting each call site.
    """
    method = get_settings().structured_output_method
    if method:
        return llm.with_structured_output(schema, method=method)
    return llm.with_structured_output(schema)
