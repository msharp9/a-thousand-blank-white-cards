"""Tests for agent.rag.embeddings (generic gateway)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Settings isolation (hermetic .env) + Settings-cache reset is handled globally
# by the autouse ``_hermetic_settings`` fixture in tests/conftest.py. The
# ``get_embeddings`` LRU cache cleared below is a *separate* cache.


def test_get_embeddings_uses_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_EMBEDDING_MODEL", "my-model")
    import agent.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("agent.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        mod.get_embeddings()
        # check_embedding_ctx_length is hardcoded False: send raw strings, not token
        # arrays (gateways/Bedrock reject arrays; cards are length-bounded).
        MockEmb.assert_called_once_with(
            model="my-model",
            openai_api_key="test-key",
            base_url=None,
            check_embedding_ctx_length=False,
            default_headers=None,
        )
    mod.get_embeddings.cache_clear()


def test_get_embeddings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    import agent.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("agent.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        a = mod.get_embeddings()
        b = mod.get_embeddings()
        assert a is b
        MockEmb.assert_called_once()
    mod.get_embeddings.cache_clear()


def test_embed_text_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    import agent.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("agent.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        inst = MagicMock()
        inst.embed_query.return_value = [0.1, 0.2, 0.3]
        MockEmb.return_value = inst
        out = mod.embed_text("hello")
        assert out == [0.1, 0.2, 0.3]
        inst.embed_query.assert_called_once_with("hello")
    mod.get_embeddings.cache_clear()


def test_empty_key_uses_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A blank LLM_API_KEY passes the non-empty placeholder (no raise)."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    import agent.rag.embeddings as mod

    from config import Settings, get_settings

    get_settings.cache_clear()
    mod.get_embeddings.cache_clear()
    with patch("agent.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        mod.get_embeddings()
        _, kwargs = MockEmb.call_args
        assert kwargs["openai_api_key"] == Settings.API_KEY_PLACEHOLDER
    mod.get_embeddings.cache_clear()


def test_constants() -> None:
    from agent.rag.embeddings import DEFAULT_EMBEDDING_MODEL, EMBEDDING_DIMENSIONS

    assert EMBEDDING_DIMENSIONS == 1536
    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"


def test_gateway_base_url_and_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A gateway config (base_url + key) flows through; ctx-length is always False."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_EMBEDDING_MODEL", "nomic-embed-text")
    import agent.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("agent.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        mod.get_embeddings()
        _, kwargs = MockEmb.call_args
        assert kwargs["model"] == "nomic-embed-text"
        assert kwargs["base_url"] == "http://localhost:11434/v1"
        assert kwargs["openai_api_key"] == "ollama"
        # Hardcoded: raw strings, never tiktoken token-id arrays.
        assert kwargs["check_embedding_ctx_length"] is False
    mod.get_embeddings.cache_clear()


def test_embedding_dimensions_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Qdrant-facing dimension follows LLM_EMBEDDING_DIMENSIONS."""
    from config import get_settings
    from agent.rag.embeddings import embedding_dimensions

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_EMBEDDING_DIMENSIONS", raising=False)
    assert embedding_dimensions() == 1536

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_EMBEDDING_DIMENSIONS", "768")
    assert embedding_dimensions() == 768
    get_settings.cache_clear()
