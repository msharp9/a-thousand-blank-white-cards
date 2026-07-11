"""agent.tools.web_search — a standalone Tavily-backed web-search tool.

Exposes a single LangChain ``@tool`` the interpretation agent can call on demand
to resolve meme / pop-culture / game-terminology references found in a card's
text. The tool is defensively non-fatal: if no Tavily key is configured or the
Tavily call raises, it returns a short "web search unavailable" string rather
than propagating an exception (a tool that raises can break the agent loop).

Layering: this module imports only ``config`` (plus stdlib / LangChain) — never
``board``.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from langchain_core.tools import tool

from config import get_settings

logger = logging.getLogger(__name__)

# Per-result snippet truncation and overall cap on how many hits we summarise.
_SNIPPET_MAX = 300
_MAX_HITS = 5
_UNAVAILABLE = "web search unavailable"


@functools.lru_cache(maxsize=1)
def _get_tavily_client() -> Any:
    """Lazily construct (and cache) a TavilySearch client.

    Mirrors agent.nodes: pydantic-settings loads the key into Settings only (not
    os.environ), while the Tavily wrapper reads os.environ["TAVILY_API_KEY"] by
    default. So we thread the key from Settings, passing it only when non-empty
    (an empty string would otherwise clobber the wrapper's own env lookup).
    """
    from langchain_tavily import TavilySearch

    key = get_settings().tavily_api_key
    kwargs: dict[str, Any] = {"max_results": _MAX_HITS}
    if key:
        kwargs["tavily_api_key"] = key
    return TavilySearch(**kwargs)


def _summarise(results: Any) -> str:
    """Reduce Tavily's response into a concise "title — snippet — url" text block.

    Tavily may return a dict with a "results" list, a bare list of hit dicts, or
    a plain string. We handle each shape and truncate snippets so the summary
    stays small enough to hand back to the LLM.
    """
    hits: list[dict[str, Any]] = []
    if isinstance(results, dict) and isinstance(results.get("results"), list):
        hits = [r for r in results["results"] if isinstance(r, dict)]
    elif isinstance(results, list):
        hits = [r for r in results if isinstance(r, dict)]
    elif isinstance(results, str):
        return results.strip()[:2000] or _UNAVAILABLE

    lines: list[str] = []
    for r in hits[:_MAX_HITS]:
        title = str(r.get("title", "")).strip()
        content = str(r.get("content", "")).strip()[:_SNIPPET_MAX]
        url = str(r.get("url", "")).strip()
        parts = [p for p in (title, content, url) if p]
        if parts:
            lines.append(" — ".join(parts))
    return "\n".join(lines) if lines else _UNAVAILABLE


@tool
def web_search(query: str) -> str:
    """Search the web to understand memes, pop-culture references, or game terminology mentioned in a card.

    Use this when a card's title or description references something external —
    a meme, a game name, a pop-culture phrase — that you need to look up to
    interpret the card faithfully. Returns a short text summary of the top web
    results (title, snippet, and url per hit). If web search is not available it
    returns "web search unavailable".
    """
    if not get_settings().tavily_api_key:
        logger.info("web_search: no tavily_api_key configured; returning unavailable")
        return _UNAVAILABLE
    try:
        client = _get_tavily_client()
        results = client.invoke({"query": query})
    except Exception as exc:  # noqa: BLE001 — a tool must never raise into the agent loop
        logger.info("web_search failed (non-fatal): %s", exc)
        return _UNAVAILABLE
    return _summarise(results)


def get_web_search_tool() -> Any:
    """Return the ``web_search`` LangChain tool object.

    Factory kept for symmetry with the other tool modules; the tool is also
    importable directly as ``web_search``.
    """
    return web_search
