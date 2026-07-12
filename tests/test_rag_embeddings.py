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


class _CountingEmbeddings:
    """Fake embeddings client that counts live calls and returns deterministic vectors."""

    def __init__(self) -> None:
        self.query_calls = 0
        self.document_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [float(len(text)), 1.0, 2.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [[float(len(t)), 1.0, 2.0] for t in texts]


@pytest.fixture
def _cache_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the disk cache at tmp_path and reset the process-global cache."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    import agent.rag.embeddings as mod

    cache_file = tmp_path / ".embedding_cache.json"
    monkeypatch.setattr(mod, "_cache_path", lambda: cache_file)
    mod._cache = None
    mod.get_embeddings.cache_clear()
    return mod, cache_file


def test_embed_text_cached_hits_on_second_call(_cache_env, monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _ = _cache_env
    fake = _CountingEmbeddings()
    monkeypatch.setattr(mod, "get_embeddings", lambda: fake)

    first = mod.embed_text_cached("hello")
    assert fake.query_calls == 1
    mod._cache = None  # force reload from disk to prove persistence
    second = mod.embed_text_cached("hello")
    assert second == first
    assert fake.query_calls == 1  # zero additional live calls


def test_embed_texts_cached_batches_misses(_cache_env, monkeypatch: pytest.MonkeyPatch) -> None:
    mod, _ = _cache_env
    fake = _CountingEmbeddings()
    monkeypatch.setattr(mod, "get_embeddings", lambda: fake)

    out = mod.embed_texts_cached(["a", "bb", "ccc"])
    assert fake.document_calls == 1  # single round-trip for all misses
    assert [v[0] for v in out] == [1.0, 2.0, 3.0]
    mod._cache = None
    again = mod.embed_texts_cached(["a", "bb", "ccc"])
    assert again == out
    assert fake.document_calls == 1  # all served from cache


def test_embed_texts_cached_falls_back_on_count_mismatch(_cache_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateways that ignore list batching (e.g. some Bedrock routes) return the
    wrong number of vectors; we must fall back to per-text embedding, not crash."""
    mod, _ = _cache_env

    class _ShortBatch(_CountingEmbeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.document_calls += 1
            return [[0.0, 0.0, 0.0]]  # one vector for many inputs

    fake = _ShortBatch()
    monkeypatch.setattr(mod, "get_embeddings", lambda: fake)

    out = mod.embed_texts_cached(["a", "bb", "ccc"])
    assert fake.query_calls == 3  # per-text fallback embedded each input
    assert [v[0] for v in out] == [1.0, 2.0, 3.0]


def test_changing_model_or_dimensions_misses(_cache_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import get_settings

    mod, _ = _cache_env
    fake = _CountingEmbeddings()
    monkeypatch.setattr(mod, "get_embeddings", lambda: fake)

    mod.embed_text_cached("same text")
    assert fake.query_calls == 1

    # Different text -> miss.
    mod.embed_text_cached("other text")
    assert fake.query_calls == 2

    # Different model -> miss even for identical text.
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_EMBEDDING_MODEL", "some-other-model")
    mod.embed_text_cached("same text")
    assert fake.query_calls == 3

    # Different dimensions -> miss even for identical text+model.
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_EMBEDDING_DIMENSIONS", "768")
    mod.embed_text_cached("same text")
    assert fake.query_calls == 4
    get_settings.cache_clear()


def test_corrupt_cache_degrades_gracefully(_cache_env, monkeypatch: pytest.MonkeyPatch) -> None:
    mod, cache_file = _cache_env
    cache_file.write_text("{ not valid json ")
    fake = _CountingEmbeddings()
    monkeypatch.setattr(mod, "get_embeddings", lambda: fake)

    out = mod.embed_text_cached("hello")
    assert out == [5.0, 1.0, 2.0]
    assert fake.query_calls == 1


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
