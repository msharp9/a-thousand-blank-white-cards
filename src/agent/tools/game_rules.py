"""agent.tools.game_rules — consult the house rules of '1000 Blank White Cards'.

Exposes a single ``game_rules`` @tool the interpretation agent can call to look
up how THIS digital edition works — scoring (negative scores are legal), effect
timing (everything is instantaneous), zones, turn/end-of-game shape, what the
engine supports and what it does not yet — so it can resolve edge cases without
inventing constraints.

The rules text is a hand-maintained mechanics reference (``data/game_rules.txt``),
read once and cached in a module-level variable. It is written FOR this lookup
tool: one self-contained, keyword-rich fact per line (the empty-query overview
returns the first ``_OVERVIEW_CHARS`` chars — the "Core facts" block is sized to
fit it), with "NOT YET SUPPORTED" lines kept in sync with the engine-gap beads.

When a ``query`` keyword is supplied the tool returns only the lines mentioning
that keyword; otherwise it returns the trimmed overview. It NEVER raises: if
the rules file is missing or empty it falls back to a built-in short summary
of the game's core rules.

Layering: this module imports only ``logging`` / stdlib — no board, no network.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Bundled rules reference, resolved from the project root (like the seed-cards
# and embedding-cache paths) so the tool works regardless of CWD.
RULES_FILENAME = "data/game_rules.txt"

# Cap on the overview length so an empty-query call never dumps the whole page
# into the LLM context window.
_OVERVIEW_CHARS = 1200

# Built-in, hand-written fallback so the tool is useful even without the
# rules file. Exposed as a module constant so tests can assert on it.
FALLBACK_SUMMARY = (
    "1,000 Blank White Cards is a party game played with a deck the players make "
    "themselves: you start with blank white cards and write a title, artwork, and "
    "an effect on each one. Cards can do almost anything — award or subtract "
    "points, change the rules, or invent new mechanics on the spot — and blank "
    "cards drawn during play are authored by the player before being played. "
    "Apply card text literally and immediately: scores are unbounded integers "
    "(negative scores are legal), all effects resolve instantly, and the player "
    "with the highest score when the deck runs out wins."
)

# Module-level cache for the loaded snapshot. ``None`` = not yet read.
_extract_cache: str | None = None


def reset_cache() -> None:
    """Clear the cached rules text (used by tests to avoid leakage)."""
    global _extract_cache
    _extract_cache = None


def _rules_path() -> Path:
    """Return the rules-file path at the project root (four levels up from this file)."""
    return Path(__file__).resolve().parents[3] / RULES_FILENAME


def _read_extract() -> str:
    """Read the bundled rules reference, or raise on a missing/empty file."""
    extract = _rules_path().read_text(encoding="utf-8")
    if not extract.strip():
        raise ValueError(f"rules file at {RULES_FILENAME} is empty")
    return extract.strip()


def _get_extract() -> str:
    """Return the cached rules text, reading (and caching) it on first call.

    Raises whatever :func:`_read_extract` raises on failure; the caller decides
    how to degrade. Only a *successful, non-empty* read is cached.
    """
    global _extract_cache
    if _extract_cache is None:
        _extract_cache = _read_extract()
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
    """Look up the house rules of this game of '1000 Blank White Cards' — scoring (negative scores are legal), effect timing (everything is instant), zones, game end, and which mechanics the engine does or does not support — to clarify how a card or edge case should work. Optionally pass a keyword (e.g. 'discard', 'dice', 'negative') to focus on the matching rules."""
    try:
        extract = _get_extract()
    except Exception as exc:
        logger.warning("game_rules: rules snapshot unavailable, using fallback (%s)", exc)
        return FALLBACK_SUMMARY
    if query and query.strip():
        return _focus(extract, query)
    return _overview(extract)


def get_game_rules_tool():
    """Return the ``game_rules`` LangChain tool object."""
    return game_rules
