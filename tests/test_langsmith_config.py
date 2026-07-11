"""Tests for LangSmith Settings fields."""

from __future__ import annotations

import pytest

from config import Settings


def test_langsmith_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.langsmith_tracing is False
    assert s.langsmith_project == "tbwc-dev"
    assert s.langsmith_endpoint == "https://api.smith.langchain.com"


def test_langsmith_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_PROJECT", "tbwc-prod")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.langsmith_tracing is True
    assert s.langsmith_project == "tbwc-prod"


def test_legacy_langchain_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old LANGCHAIN_* env vars still populate the langsmith_* fields."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "legacy-key")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "legacy-proj")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.langsmith_tracing is True
    assert s.langsmith_api_key == "legacy-key"
    assert s.langsmith_project == "legacy-proj"


def test_langsmith_takes_precedence_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both are set, the modern LANGSMITH_* value wins."""
    monkeypatch.setenv("LANGCHAIN_PROJECT", "legacy-proj")
    monkeypatch.setenv("LANGSMITH_PROJECT", "modern-proj")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.langsmith_project == "modern-proj"
