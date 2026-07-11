"""Tests for agent.tools.web_search — the standalone Tavily web-search tool.

All tests are hermetic: the Tavily client construction is monkeypatched so no
live network call is ever made.
"""

from __future__ import annotations

import pytest

import config
from agent.tools import web_search as ws


class _FakeTavily:
    """Stand-in for langchain_tavily.TavilySearch returning canned results."""

    def __init__(self, results):
        self._results = results

    def invoke(self, _payload):
        return self._results


def _clear_client_cache() -> None:
    """Clear the lru_cache if still the real function (tests may monkeypatch it)."""
    clear = getattr(ws._get_tavily_client, "cache_clear", None)
    if clear is not None:
        clear()


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """The Tavily client is lru_cached; clear it around every test."""
    _clear_client_cache()
    yield
    _clear_client_cache()


def _set_tavily_key(monkeypatch, key: str) -> None:
    """Point get_settings() at a Settings whose tavily_api_key is `key`."""
    config.get_settings.cache_clear()
    settings = config.Settings(tavily_api_key=key)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    # web_search imports get_settings into its own namespace.
    monkeypatch.setattr(ws, "get_settings", lambda: settings)


def test_tool_metadata():
    """The tool object exposes the expected name and a non-empty description."""
    tool = ws.get_web_search_tool()
    assert tool is ws.web_search
    assert tool.name == "web_search"
    assert tool.description and isinstance(tool.description, str)


def test_returns_summary_with_titles_and_urls(monkeypatch):
    """With a key set and canned results, the summary contains titles and urls."""
    _set_tavily_key(monkeypatch, "sk-test")
    canned = {
        "results": [
            {
                "title": "Rickroll",
                "content": "An internet meme involving Rick Astley.",
                "url": "https://example.com/rickroll",
            },
            {
                "title": "Doge",
                "content": "A Shiba Inu meme.",
                "url": "https://example.com/doge",
            },
        ]
    }
    monkeypatch.setattr(ws, "_get_tavily_client", lambda: _FakeTavily(canned))

    out = ws.web_search.invoke({"query": "what is rickroll"})
    assert isinstance(out, str)
    assert "Rickroll" in out
    assert "https://example.com/rickroll" in out
    assert "Doge" in out
    assert "https://example.com/doge" in out


def test_returns_summary_for_bare_list(monkeypatch):
    """Tavily's bare-list result shape is also summarised."""
    _set_tavily_key(monkeypatch, "sk-test")
    canned = [{"title": "T1", "content": "snippet", "url": "https://ex.com/1"}]
    monkeypatch.setattr(ws, "_get_tavily_client", lambda: _FakeTavily(canned))

    out = ws.web_search.invoke({"query": "q"})
    assert "T1" in out
    assert "https://ex.com/1" in out


def test_graceful_degradation_no_key(monkeypatch):
    """With no tavily_api_key, the tool returns the unavailable string, no raise."""
    _set_tavily_key(monkeypatch, "")

    def _boom():
        raise AssertionError("client must not be constructed without a key")

    monkeypatch.setattr(ws, "_get_tavily_client", _boom)

    out = ws.web_search.invoke({"query": "anything"})
    assert out == "web search unavailable"


def test_graceful_degradation_on_error(monkeypatch):
    """If the underlying search raises, the tool still returns a string."""
    _set_tavily_key(monkeypatch, "sk-test")

    class _Boom:
        def invoke(self, _payload):
            raise RuntimeError("tavily exploded")

    monkeypatch.setattr(ws, "_get_tavily_client", lambda: _Boom())

    out = ws.web_search.invoke({"query": "q"})
    assert isinstance(out, str)
    assert out == "web search unavailable"


def test_empty_results_returns_unavailable(monkeypatch):
    """Empty result set summarises to the unavailable string (no crash)."""
    _set_tavily_key(monkeypatch, "sk-test")
    monkeypatch.setattr(ws, "_get_tavily_client", lambda: _FakeTavily({"results": []}))

    out = ws.web_search.invoke({"query": "q"})
    assert out == "web search unavailable"
