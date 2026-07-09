"""Tests for ops/verify_langsmith.py env-checking (no graph/LLM run)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "ops" / "verify_langsmith.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_langsmith", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_check_env_reports_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENAI_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        monkeypatch.delenv(var, raising=False)
    mod = _load()
    problems = mod.check_env()
    assert any("OPENAI_API_KEY" in p for p in problems)
    assert any("LANGSMITH_API_KEY" in p for p in problems)
    assert any("tracing not enabled" in p for p in problems)


def test_check_env_all_good(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("LANGSMITH_API_KEY", "y")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    mod = _load()
    assert mod.check_env() == []


def test_sample_card_shape() -> None:
    mod = _load()
    assert set(mod.SAMPLE_CARD.keys()) == {"title", "description"}
