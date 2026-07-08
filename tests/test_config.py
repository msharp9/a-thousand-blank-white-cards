"""Tests for tbwc.config.Settings."""

from __future__ import annotations

import pytest

from tbwc.config import Settings, get_settings


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
