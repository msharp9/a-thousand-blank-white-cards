"""Tests for agent.llm (generic gateway factory)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Settings isolation (hermetic .env) + cache reset is handled globally by the
# autouse ``_hermetic_settings`` fixture in tests/conftest.py.


def test_uses_env_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_CHAT_MODEL", "custom-model")
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model

        get_chat_model()
        MockLLM.assert_called_once_with(
            model="custom-model", openai_api_key="test-key", base_url=None, default_headers=None
        )


def test_temperature_omitted_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """No temperature is sent unless explicitly asked for — some gateway models reject it."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert "temperature" not in kwargs


def test_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_CHAT_MODEL", raising=False)
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import DEFAULT_CHAT_MODEL, get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["model"] == DEFAULT_CHAT_MODEL


def test_explicit_model_and_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model

        get_chat_model("gpt-x", temperature=0.7)
        MockLLM.assert_called_once_with(
            model="gpt-x", temperature=0.7, openai_api_key="test-key", base_url=None, default_headers=None
        )


def test_gateway_key_mirrored_to_bifrost_header(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """With a gateway base_url + key, LLM_API_KEY is mirrored into the x-bf-vk header."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example.com/openai")
    monkeypatch.setenv("LLM_API_KEY", "vk-123")
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["default_headers"] == {"x-bf-vk": "vk-123"}


def test_no_bifrost_header_for_hosted_openai(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Hosted OpenAI (blank base_url) sends no x-bf-vk header — default_headers is None."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-real")
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["default_headers"] is None


def test_empty_key_uses_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A blank LLM_API_KEY passes the non-empty placeholder to the client (no raise)."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model
        from config import Settings

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["openai_api_key"] == Settings.API_KEY_PLACEHOLDER
        assert kwargs["openai_api_key"]  # non-empty


def test_gateway_base_url_and_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A configured base_url + key (e.g. a local Ollama gateway) flows through."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_CHAT_MODEL", "gpt-oss-20b")
    with patch("agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from agent.llm import get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["model"] == "gpt-oss-20b"
        assert kwargs["base_url"] == "http://localhost:11434/v1"
        assert kwargs["openai_api_key"] == "ollama"
