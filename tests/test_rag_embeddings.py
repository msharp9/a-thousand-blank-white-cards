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
        MockEmb.assert_called_once_with(model="my-model", openai_api_key="test-key")
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


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import tbwc.rag.embeddings as mod

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
