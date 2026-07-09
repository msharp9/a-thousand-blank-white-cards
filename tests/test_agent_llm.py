"""Tests for tbwc.agent.llm."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tbwc.config import OPENAI_API_KEY_ERROR, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Settings is the single source for the OpenAI key; reset the cache per test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_uses_env_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_CHAT_MODEL", "custom-model")
    with patch("tbwc.agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from tbwc.agent.llm import get_chat_model

        get_chat_model()
        MockLLM.assert_called_once_with(model="custom-model", temperature=0, openai_api_key="test-key")


def test_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
    with patch("tbwc.agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from tbwc.agent.llm import DEFAULT_CHAT_MODEL, get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["model"] == DEFAULT_CHAT_MODEL


def test_explicit_model_and_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with patch("tbwc.agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from tbwc.agent.llm import get_chat_model

        get_chat_model("gpt-x", temperature=0.7)
        MockLLM.assert_called_once_with(model="gpt-x", temperature=0.7, openai_api_key="test-key")


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("tbwc.agent.llm.ChatOpenAI") as MockLLM:
        from tbwc.agent.llm import get_chat_model

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            get_chat_model()
        assert OPENAI_API_KEY_ERROR
        MockLLM.assert_not_called()
