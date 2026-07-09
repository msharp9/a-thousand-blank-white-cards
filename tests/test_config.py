"""Tests for tbwc.config.Settings."""

from __future__ import annotations

import pytest

from pathlib import Path

from tbwc.config import OPENAI_API_KEY_ERROR, Settings, get_settings, require_openai_api_key


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


def test_key_from_env_file_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A key defined only in a .env file (not the process env) is honoured."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-from-dotenv\n")
    s = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert s.openai_api_key == "sk-from-dotenv"
