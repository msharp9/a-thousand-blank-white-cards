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
        MockLLM.assert_called_once_with(model="custom-model", temperature=0, openai_api_key="test-key", base_url=None)


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
        MockLLM.assert_called_once_with(model="gpt-x", temperature=0.7, openai_api_key="test-key", base_url=None)


def test_missing_key_raises_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("tbwc.agent.llm.ChatOpenAI") as MockLLM:
        from tbwc.agent.llm import get_chat_model

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            get_chat_model()
        assert OPENAI_API_KEY_ERROR
        MockLLM.assert_not_called()


def test_ollama_provider_uses_base_url_and_placeholder_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """provider=ollama builds ChatOpenAI against the local base_url with a dummy key."""
    monkeypatch.chdir(tmp_path)  # isolate from any real .env in the repo root
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # no OpenAI key needed
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    with patch("tbwc.agent.llm.ChatOpenAI") as MockLLM:
        MockLLM.return_value = MagicMock()
        from tbwc.agent.llm import get_chat_model

        get_chat_model()
        _, kwargs = MockLLM.call_args
        assert kwargs["model"] == "gpt-oss-20b"
        assert kwargs["base_url"] == "http://localhost:11434/v1"
        assert kwargs["openai_api_key"] == "ollama"  # placeholder, no real key


def test_with_structured_output_default_passes_no_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (unset STRUCTURED_OUTPUT_METHOD) keeps hosted-OpenAI behaviour."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from tbwc.agent.llm import with_structured_output

    fake_llm = MagicMock()
    with_structured_output(fake_llm, object)
    fake_llm.with_structured_output.assert_called_once_with(object)


def test_with_structured_output_honours_configured_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """STRUCTURED_OUTPUT_METHOD=json_schema is threaded into with_structured_output."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("STRUCTURED_OUTPUT_METHOD", "json_schema")
    from tbwc.agent.llm import with_structured_output

    fake_llm = MagicMock()
    with_structured_output(fake_llm, object)
    fake_llm.with_structured_output.assert_called_once_with(object, method="json_schema")
