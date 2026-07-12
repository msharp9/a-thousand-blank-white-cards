"""Tests for config.Settings (generic OpenAI-compatible LLM gateway)."""

from __future__ import annotations

import logging

import pytest

from pathlib import Path

from config import Settings, get_settings, warn_if_no_llm_credentials


def test_defaults_load_without_env_file() -> None:
    """Settings should load with default values when no .env is present."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_chat_model == "gpt-5.4-mini"
    assert s.llm_embedding_model == "text-embedding-3-small"
    assert s.llm_embedding_dimensions == 1536
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
    monkeypatch.setenv("LLM_CHAT_MODEL", "gpt-4o")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_chat_model == "gpt-4o"
    get_settings.cache_clear()


def test_base_url_empty_resolves_to_none() -> None:
    """Blank LLM_BASE_URL -> None (the OpenAI library default endpoint)."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_base_url_raw == ""
    assert s.llm_base_url is None


def test_base_url_set_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_base_url == "http://localhost:11434/v1"


def test_api_key_empty_uses_placeholder() -> None:
    """Blank LLM_API_KEY -> non-empty placeholder (the OpenAI client requires one)."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_api_key_raw == ""
    assert s.llm_api_key == Settings.API_KEY_PLACEHOLDER
    assert s.llm_api_key  # non-empty


def test_api_key_set_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-live")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_api_key == "sk-live"


def test_accessors_follow_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat/embedding model + dimensions flow through the accessors."""
    monkeypatch.setenv("LLM_CHAT_MODEL", "gpt-oss-20b")
    monkeypatch.setenv("LLM_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("LLM_EMBEDDING_DIMENSIONS", "768")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.chat_model == "gpt-oss-20b"
    assert s.embedding_model == "nomic-embed-text"
    assert s.embedding_dimensions == 768


def test_dev_mode_defaults_false() -> None:
    get_settings.cache_clear()
    assert get_settings().dev_mode is False
    get_settings.cache_clear()


def test_dev_mode_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("DEV_MODE", "true")
    assert get_settings().dev_mode is True
    get_settings.cache_clear()


def test_warn_if_no_llm_credentials_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Empty base_url AND empty api_key -> a soft warning, never a raise."""
    get_settings.cache_clear()
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    with caplog.at_level(logging.WARNING):
        warn_if_no_llm_credentials()  # must NOT raise
    assert any("LLM_API_KEY" in rec.message for rec in caplog.records)
    get_settings.cache_clear()


def test_warn_if_no_llm_credentials_silent_with_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "sk-live")
    with caplog.at_level(logging.WARNING):
        warn_if_no_llm_credentials()
    assert not any("LLM_API_KEY" in rec.message for rec in caplog.records)
    get_settings.cache_clear()


def test_warn_if_no_llm_credentials_silent_with_base_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A gateway base_url with no key is a valid keyless setup — no warning."""
    get_settings.cache_clear()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    with caplog.at_level(logging.WARNING):
        warn_if_no_llm_credentials()
    assert not any("LLM_API_KEY" in rec.message for rec in caplog.records)
    get_settings.cache_clear()


def test_key_from_env_file_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A key defined only in a .env file (not the process env) is honoured."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=sk-from-dotenv\n")
    s = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert s.llm_api_key == "sk-from-dotenv"
