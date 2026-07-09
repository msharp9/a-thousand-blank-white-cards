"""Tests for tbwc.rag.embeddings."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Settings is the single source for the OpenAI key; reset the cache per test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_get_embeddings_uses_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "my-model")
    import tbwc.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("tbwc.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        mod.get_embeddings()
        MockEmb.assert_called_once_with(model="my-model", openai_api_key="test-key", base_url=None)
    mod.get_embeddings.cache_clear()


def test_get_embeddings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import tbwc.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("tbwc.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        a = mod.get_embeddings()
        b = mod.get_embeddings()
        assert a is b
        MockEmb.assert_called_once()
    mod.get_embeddings.cache_clear()


def test_embed_text_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import tbwc.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("tbwc.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        inst = MagicMock()
        inst.embed_query.return_value = [0.1, 0.2, 0.3]
        MockEmb.return_value = inst
        out = mod.embed_text("hello")
        assert out == [0.1, 0.2, 0.3]
        inst.embed_query.assert_called_once_with("hello")
    mod.get_embeddings.cache_clear()


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import tbwc.rag.embeddings as mod

    from tbwc.config import get_settings

    get_settings.cache_clear()
    mod.get_embeddings.cache_clear()
    with patch("tbwc.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            mod.get_embeddings()
        MockEmb.assert_not_called()
    mod.get_embeddings.cache_clear()


def test_constants() -> None:
    from tbwc.rag.embeddings import DEFAULT_EMBEDDING_MODEL, EMBEDDING_DIMENSIONS

    assert EMBEDDING_DIMENSIONS == 1536
    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"


def test_ollama_provider_uses_base_url_and_placeholder_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """provider=ollama builds embeddings against local base_url with a dummy key."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # no OpenAI key needed
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    import tbwc.rag.embeddings as mod

    mod.get_embeddings.cache_clear()
    with patch("tbwc.rag.embeddings.OpenAIEmbeddings") as MockEmb:
        MockEmb.return_value = MagicMock()
        mod.get_embeddings()
        _, kwargs = MockEmb.call_args
        assert kwargs["model"] == "nomic-embed-text"
        assert kwargs["base_url"] == "http://localhost:11434/v1"
        assert kwargs["openai_api_key"] == "ollama"
    mod.get_embeddings.cache_clear()


def test_embedding_dimensions_is_provider_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Qdrant-facing dimension follows the active provider (1536 vs 768)."""
    from tbwc.config import get_settings
    from tbwc.rag.embeddings import embedding_dimensions

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert embedding_dimensions() == 1536

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert embedding_dimensions() == 768
    get_settings.cache_clear()
