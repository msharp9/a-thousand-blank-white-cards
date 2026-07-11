"""agent.tools.game_rules — consult the real-world rules of '1000 Blank White Cards'.

Exposes a single ``game_rules`` @tool the interpretation agent can call to look
up how the tabletop game "1,000 Blank White Cards" actually works, so it can
resolve edge cases when deciding what a played card should do.

The tool fetches the game's Wikipedia article ONCE (via the MediaWiki API) and
caches the plain-text extract in a module-level variable, so repeated calls in a
single process never re-hit the network. When a ``query`` keyword is supplied it
returns only the paragraphs mentioning that keyword; otherwise it returns a
trimmed overview. It NEVER raises: on any network/parse failure it falls back to
a built-in short summary of the game's core rules so the tool stays useful
offline and in tests.

Layering: this module imports only ``logging`` / stdlib / ``httpx`` — no board.
"""

from __future__ import annotations

import logging

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# The real article title uses a comma; the MediaWiki API normalises redirects,
# but we request the canonical title directly.
_WIKI_API_URL = "https://en.wikipedia.org/w/api.php"
_ARTICLE_TITLE = "1,000 Blank White Cards"
_USER_AGENT = (
    "Mozilla/5.0 (compatible; TBWC-InterpretationAgent/1.0; +https://en.wikipedia.org/wiki/1,000_Blank_White_Cards)"
)
_HTTP_TIMEOUT_SECONDS = 8.0

# Cap on the overview length so an empty-query call never dumps the whole page
# into the LLM context window.
_OVERVIEW_CHARS = 1200

# Built-in, hand-written fallback so the tool is useful with no network. Exposed
# as a module constant so tests can assert on it.
FALLBACK_SUMMARY = (
    "1,000 Blank White Cards is a party game played with a deck the players make "
    "themselves: you start with blank white cards and write a title, artwork, and "
    "an effect on each one. Cards can do almost anything — award or subtract "
    "points, change the rules, or invent new mechanics on the spot — and blank "
    "cards drawn during play are authored by the player before being played. "
    "Players keep score with points, informal House Rules resolve disputes, and "
    "the player with the highest score when the deck runs out wins."
)

# Module-level cache for the fetched extract. ``None`` = not yet fetched.
_extract_cache: str | None = None


def reset_cache() -> None:
    """Clear the cached Wikipedia extract (used by tests to avoid leakage)."""
    global _extract_cache
    _extract_cache = None


def _fetch_extract() -> str:
    """Fetch the plain-text Wikipedia extract for the game, or raise on failure.

    Uses the MediaWiki ``query`` API with ``prop=extracts&explaintext=1`` which
    returns the article body as plain text. ``redirects=1`` normalises the title
    so alternate spellings resolve to the canonical page.
    """
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "format": "json",
        "redirects": "1",
        "titles": _ARTICLE_TITLE,
    }
    resp = httpx.get(
        _WIKI_API_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=_HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    pages = data["query"]["pages"]
    # ``pages`` is a dict keyed by page id; grab the first (only) page's extract.
    page = next(iter(pages.values()))
    extract = page.get("extract")
    if not extract or not extract.strip():
        raise ValueError("empty extract returned from Wikipedia API")
    return extract.strip()


def _get_extract() -> str:
    """Return the cached extract, fetching (and caching) it on first call.

    Raises whatever :func:`_fetch_extract` raises on failure; the caller decides
    how to degrade. Only a *successful, non-empty* extract is cached.
    """
    global _extract_cache
    if _extract_cache is None:
        _extract_cache = _fetch_extract()
    return _extract_cache


def _overview(extract: str) -> str:
    """Return a trimmed overview of the extract, capped at ``_OVERVIEW_CHARS``."""
    if len(extract) <= _OVERVIEW_CHARS:
        return extract
    return extract[:_OVERVIEW_CHARS].rstrip() + " ..."


def _focus(extract: str, query: str) -> str:
    """Return paragraphs of ``extract`` mentioning ``query`` (case-insensitive).

    Prepends a short lead (the first paragraph) for context. If nothing matches,
    fall back to the trimmed overview so the agent always gets something useful.
    """
    needle = query.strip().lower()
    paragraphs = [p.strip() for p in extract.split("\n") if p.strip()]
    lead = paragraphs[0] if paragraphs else ""
    matches = [p for p in paragraphs if needle in p.lower()]
    if not matches:
        return _overview(extract)
    body = "\n\n".join(matches)
    if lead and lead not in matches:
        return f"{lead}\n\n{body}"
    return body


@tool
def game_rules(query: str = "") -> str:
    """Look up the official rules of the tabletop game '1000 Blank White Cards' (from Wikipedia) to clarify how a card or edge case should work. Optionally pass a keyword to focus on a section/sentence."""
    try:
        extract = _get_extract()
    except Exception as exc:
        logger.warning("game_rules: Wikipedia lookup unavailable, using fallback (%s)", exc)
        return FALLBACK_SUMMARY
    if query and query.strip():
        return _focus(extract, query)
    return _overview(extract)


def get_game_rules_tool():
    """Return the ``game_rules`` LangChain tool object."""
    return game_rules
