"""mtg_lookup — a Scryfall-backed Magic: The Gathering card/rules-term lookup tool.

Many players who author cards come from Magic: The Gathering and reference MTG
cards, or MTG rules terms (keyword abilities like "trample", keyword actions
like "scry", ability words like "landfall"), by name. This tool resolves a
card reference by querying the public Scryfall API
(https://scryfall.com/docs/api). Scryfall requires no API key; it only asks
that clients send a descriptive ``User-Agent`` and ``Accept: application/json``
header and rate-limit themselves (a small sleep between requests).

Rules terms are NOT cards, so Scryfall 404s on them (e.g. "split second"). A
bundled glossary (``data/mtg_glossary.json``, built by
``scripts/build_mtg_glossary.py``) is checked first — exact, then fuzzy match
— so common terms resolve offline and deterministically; only names that miss
the glossary fall through to the Scryfall card lookup.

Layering: this module may import ``config`` / ``logging_config`` / ``models`` /
``engine`` but never ``board``. It only depends on ``httpx`` (a repo dep) and the
langchain ``@tool`` decorator, mirroring the other agent tools.

Graceful degradation is the contract: the tool NEVER raises. A missing card
(404) returns a friendly "not found" message (after a final glossary fuzzy
check); any network/parse failure returns a generic "unavailable" message; a
missing/corrupt glossary file silently disables the glossary check so the
agent can keep going.
"""

from __future__ import annotations

import difflib
import json
import logging
import time
from pathlib import Path

import httpx
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Scryfall fuzzy "named" endpoint: resolves an approximate card name to one card.
_SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"

# Scryfall asks clients to identify themselves and to rate-limit. A descriptive
# User-Agent plus an explicit JSON Accept header satisfies their guidelines.
_HEADERS = {
    "User-Agent": "a-thousand-blank-white-cards/1.0 (MTG reference lookup)",
    "Accept": "application/json",
}

# Politeness delay between calls (Scryfall suggests ~50-100ms). Kept as a module
# constant so tests can monkeypatch ``time.sleep`` to a no-op and stay fast.
_RATE_LIMIT_SECONDS = 0.1

# Cap oracle text so a wall-of-text card never blows up the agent's context.
_MAX_ORACLE_CHARS = 600

_NOT_FOUND_TEMPLATE = "no MTG card found for '{name}'"
_UNAVAILABLE = "MTG lookup unavailable"

# Bundled rules-term glossary (see scripts/build_mtg_glossary.py for provenance).
GLOSSARY_FILENAME = "data/mtg_glossary.json"

# Checked before Scryfall: catches near-misses like "tramplle" -> "trample".
_GLOSSARY_FUZZY_CUTOFF = 0.85
# Checked again after a Scryfall 404, looser since the card lookup already
# ruled out an exact/close card name.
_GLOSSARY_FALLBACK_CUTOFF = 0.75

# Module-level cache for the loaded glossary. ``None`` = not yet loaded; an
# empty dict means loading failed and the glossary check is disabled.
_glossary_cache: dict[str, str] | None = None


def reset_glossary_cache() -> None:
    """Clear the cached glossary (used by tests to avoid leakage across cases)."""
    global _glossary_cache
    _glossary_cache = None


def _glossary_path() -> Path:
    """Return the bundled glossary path, resolved from the project root."""
    return Path(__file__).resolve().parents[3] / GLOSSARY_FILENAME


def _load_glossary() -> dict[str, str]:
    """Load and cache the glossary, degrading to ``{}`` on any failure.

    A missing file, invalid JSON, or a non-object JSON root all disable the
    glossary check rather than raising, matching this module's never-raise
    contract.
    """
    global _glossary_cache
    if _glossary_cache is not None:
        return _glossary_cache
    try:
        raw = _glossary_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("glossary JSON root must be an object")
        glossary = {str(term).lower(): str(definition) for term, definition in data.items()}
    except Exception as exc:  # noqa: BLE001 - graceful degradation: never raise out of the tool
        logger.warning("mtg_lookup: glossary unavailable, card-only lookup (%s)", exc)
        glossary = {}
    _glossary_cache = glossary
    return glossary


def _glossary_match(name: str, cutoff: float) -> str | None:
    """Return a formatted glossary hit for ``name``, or ``None`` if no match.

    Tries an exact case-insensitive match first, then a fuzzy match (via
    :func:`difflib.get_close_matches`) against the glossary's own terms.
    """
    glossary = _load_glossary()
    if not glossary:
        return None
    key = name.strip().lower()
    if not key:
        return None
    term = key if key in glossary else next(iter(difflib.get_close_matches(key, glossary, n=1, cutoff=cutoff)), None)
    if term is None:
        return None
    return f"{term} (MTG keyword): {glossary[term]}"


def _fetch_card(card_name: str) -> dict:
    """Fetch one card's JSON from Scryfall's fuzzy ``cards/named`` endpoint.

    Sleeps briefly first (rate-limit politeness), then issues a single GET.
    Raises :class:`httpx.HTTPStatusError` on a non-2xx response (e.g. 404 when
    no card matches) and lets any transport/parse error propagate; the caller in
    :func:`_lookup` is responsible for turning those into friendly strings.
    """
    time.sleep(_RATE_LIMIT_SECONDS)
    response = httpx.get(
        _SCRYFALL_NAMED_URL,
        params={"fuzzy": card_name},
        headers=_HEADERS,
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def _summarize(card: dict) -> str:
    """Render a Scryfall card JSON as a concise ``name — cost — type\\noracle`` blurb."""
    name = card.get("name") or "Unknown card"
    mana_cost = card.get("mana_cost") or ""
    type_line = card.get("type_line") or ""
    oracle_text = card.get("oracle_text") or ""

    if len(oracle_text) > _MAX_ORACLE_CHARS:
        oracle_text = oracle_text[:_MAX_ORACLE_CHARS].rstrip() + "…"

    # Build the header line, dropping empty segments so we don't emit stray dashes.
    header_parts = [part for part in (name, mana_cost, type_line) if part]
    header = " — ".join(header_parts)

    return f"{header}\n{oracle_text}".rstrip()


def _lookup(card_name: str) -> str:
    """Core lookup logic (pure of the ``@tool`` wrapper, easy to unit test).

    Checks the bundled rules-term glossary (exact, then fuzzy) before ever
    hitting the network, so keywords like "trample" resolve offline. A miss
    falls through to the Scryfall card lookup; a Scryfall 404 gets one more,
    looser glossary fuzzy check before returning the not-found string. Returns
    a summary string on a card hit, and the generic unavailable string on any
    other error. Never raises.
    """
    name = (card_name or "").strip()
    if not name:
        return _NOT_FOUND_TEMPLATE.format(name=name)

    glossary_hit = _glossary_match(name, _GLOSSARY_FUZZY_CUTOFF)
    if glossary_hit is not None:
        return glossary_hit

    try:
        card = _fetch_card(name)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            logger.info("Scryfall: no MTG card found for %r", name)
            fallback_hit = _glossary_match(name, _GLOSSARY_FALLBACK_CUTOFF)
            return fallback_hit if fallback_hit is not None else _NOT_FOUND_TEMPLATE.format(name=name)
        logger.warning("Scryfall HTTP error for %r: %s", name, exc)
        return _UNAVAILABLE
    except Exception as exc:  # noqa: BLE001 - graceful degradation: never raise out of the tool
        logger.warning("Scryfall lookup failed for %r: %s", name, exc)
        return _UNAVAILABLE

    try:
        return _summarize(card)
    except Exception as exc:  # noqa: BLE001 - a malformed payload must not crash the agent
        logger.warning("Scryfall response parse failed for %r: %s", name, exc)
        return _UNAVAILABLE


@tool
def mtg_lookup(card_name: str) -> str:
    """Look up a Magic: The Gathering reference a player's card draws on, by card name, or an MTG rules keyword/term ('trample', 'split second'). Returns the card's type line and rules (oracle) text, or the keyword's definition."""
    return _lookup(card_name)


def get_mtg_lookup_tool():
    """Factory returning the ``mtg_lookup`` tool object (parallels the other tools)."""
    return mtg_lookup
