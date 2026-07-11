"""Tests for config.Settings."""

from __future__ import annotations

import pytest

from pathlib import Path

from config import OPENAI_API_KEY_ERROR, Settings, get_settings, require_openai_api_key


def test_defaults_load_without_env_file() -> None:
    """Settings should load with default values when no .env is present."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.openai_chat_model == "gpt-5.4-mini"
    assert s.openai_embedding_model == "text-embedding-3-small"
    assert s.qdrant_collection == "tbwc_cards"
    assert s.port == 8000
    assert "http://localhost:3000" in s.cors_origins


def test_get_settings_returns_singleton() -> None:
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    get_settings.cache_clear()


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "gpt-4o")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.openai_chat_model == "gpt-4o"
    get_settings.cache_clear()


def test_require_openai_api_key_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unset key produces a clear, actionable error."""
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        require_openai_api_key()
    assert str(exc.value) == OPENAI_API_KEY_ERROR
    assert "OPENAI_API_KEY" in str(exc.value)
    get_settings.cache_clear()


def test_require_openai_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live")
    assert require_openai_api_key() == "sk-live"
    get_settings.cache_clear()


def test_ollama_provider_skips_key_requirement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """provider=ollama makes require_openai_api_key a no-op (returns placeholder)."""
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert require_openai_api_key() == "ollama"  # no RuntimeError raised
    get_settings.cache_clear()


def test_provider_aware_accessors(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat/embedding model, base_url and dimensions follow the active provider."""
    get_settings.cache_clear()
    openai_s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not openai_s.is_ollama
    assert openai_s.chat_model == "gpt-5.4-mini"
    assert openai_s.embedding_model == "text-embedding-3-small"
    assert openai_s.llm_base_url is None
    assert openai_s.embedding_dimensions == 1536

    ollama_s = Settings(_env_file=None, llm_provider="ollama")  # type: ignore[call-arg]
    assert ollama_s.is_ollama
    assert ollama_s.chat_model == "gpt-oss-20b"
    assert ollama_s.embedding_model == "nomic-embed-text"
    assert ollama_s.llm_base_url == "http://localhost:11434/v1"
    assert ollama_s.embedding_dimensions == 768
    assert ollama_s.llm_api_key == "ollama"  # placeholder, no real key required
    get_settings.cache_clear()


def test_key_from_env_file_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A key defined only in a .env file (not the process env) is honoured."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-from-dotenv\n")
    s = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert s.openai_api_key == "sk-from-dotenv"
