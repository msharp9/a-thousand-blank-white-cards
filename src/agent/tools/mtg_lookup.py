"""mtg_lookup — a Scryfall-backed Magic: The Gathering card lookup tool.

Many players who author cards come from Magic: The Gathering and reference MTG
cards or mechanics by name. This tool lets the agent resolve such a reference by
querying the public Scryfall API (https://scryfall.com/docs/api). Scryfall
requires no API key; it only asks that clients send a descriptive ``User-Agent``
and ``Accept: application/json`` header and rate-limit themselves (a small sleep
between requests).

Layering: this module may import ``config`` / ``logging_config`` / ``models`` /
``engine`` but never ``board``. It only depends on ``httpx`` (a repo dep) and the
langchain ``@tool`` decorator, mirroring the other agent tools.

Graceful degradation is the contract: the tool NEVER raises. A missing card
(404) returns a friendly "not found" message; any network/parse failure returns
a generic "unavailable" message so the agent can keep going.
"""

from __future__ import annotations

import logging
import time

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

    Returns a summary string on success, the not-found string on a 404, and the
    generic unavailable string on any other error. Never raises.
    """
    name = (card_name or "").strip()
    if not name:
        return _NOT_FOUND_TEMPLATE.format(name=name)

    try:
        card = _fetch_card(name)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            logger.info("Scryfall: no MTG card found for %r", name)
            return _NOT_FOUND_TEMPLATE.format(name=name)
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
    """Look up a Magic: The Gathering card by name (via Scryfall) to understand an MTG mechanic or card a player's card references. Returns the card's type line and rules (oracle) text."""
    return _lookup(card_name)


def get_mtg_lookup_tool():
    """Factory returning the ``mtg_lookup`` tool object (parallels the other tools)."""
    return mtg_lookup
