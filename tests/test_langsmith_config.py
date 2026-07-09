"""Tests for LangSmith Settings fields."""

from __future__ import annotations

import pytest

from tbwc.config import Settings


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
