"""tbwc.rooms.deck — build and shuffle a starting deck for a new game.

The intended start-game flow is:
  (1) collect existing cards — prior-game kept cards + seed cards from the RAG
      corpus, falling back to the offline seed-data file if RAG is unavailable,
  (2) create those cards into ``state.cards`` (a card_id -> card dict registry),
  (3) shuffle their ids into ``state.deck`` (padded to >= MIN_DECK cards),
  (4) leave play/dealing to the caller (Room._handle_start).

Everything here is pure and dependency-injectable: pass an ``rng`` for
deterministic shuffles and a ``card_source`` to bypass RAG/OpenAI in tests.
No live external service (Qdrant/OpenAI) is required to build a deck.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable

logger = logging.getLogger(__name__)

# A game needs at least this many cards in the deck to start (acceptance: >= 30).
MIN_DECK = 30

# Type alias for a card source: a zero-arg callable returning raw card dicts.
CardSource = Callable[[], list[dict]]


def _normalise_card(raw: dict, index: int) -> dict:
    """Coerce a raw card (RAG payload or seed-file entry) into a game card dict.

    RAG payloads key the id as ``card_id``; seed-file entries key it as ``id``.
    Missing ids get a stable ``deck-NNN`` fallback so nothing collides silently.
    """
    card_id = raw.get("id") or raw.get("card_id") or f"deck-{index:03d}"
    return {
        "id": card_id,
        "title": raw.get("title", ""),
        "description": raw.get("description", ""),
        "creator_id": raw.get("source", "seed"),
    }


def collect_cards(card_source: CardSource | None = None) -> list[dict]:
    """Collect normalised cards from the given source (or the default source).

    The default source tries the RAG corpus first (seed + prior-game kept cards)
    and falls back to reading the offline seed-data file when RAG is unavailable
    (no store initialised / no network / no API key). Duplicate ids are dropped,
    keeping the first occurrence.
    """
    source = card_source or _default_card_source
    raw_cards = source()
    cards: list[dict] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cards):
        card = _normalise_card(raw, index)
        if card["id"] in seen:
            continue
        seen.add(card["id"])
        cards.append(card)
    return cards


def _default_card_source() -> list[dict]:
    """Prefer RAG-stored cards; fall back to the offline seed-data file."""
    try:
        from tbwc.rag.store import list_all_cards

        cards = list_all_cards()
        if cards:
            return cards
    except Exception as exc:  # store not initialised / offline — fall back
        logger.info("RAG card source unavailable, using offline seed file: %s", exc)

    from tbwc.rag.seed import read_seed_cards

    return read_seed_cards()


def build_deck(
    *,
    card_source: CardSource | None = None,
    rng: random.Random | None = None,
    min_deck: int = MIN_DECK,
) -> tuple[dict[str, dict], list[str]]:
    """Build the card registry and a shuffled deck of at least ``min_deck`` ids.

    Returns ``(cards, deck)`` where ``cards`` maps card_id -> card dict and
    ``deck`` is a shuffled list of card ids with ``len(deck) >= min_deck``.

    If fewer than ``min_deck`` unique cards are available, the deck is padded
    with additional copies (each a distinct ``<id>#N`` card added to the
    registry) so the game can always start. Raises ValueError only if the
    source yields no cards at all.
    """
    rng = rng or random.Random()
    collected = collect_cards(card_source)
    if not collected:
        raise ValueError("no cards available to build a deck (empty card source)")

    cards: dict[str, dict] = {c["id"]: c for c in collected}
    deck: list[str] = list(cards.keys())

    # Pad with distinct copies when the corpus is smaller than the minimum.
    copy_index = 2
    while len(deck) < min_deck:
        for base in collected:
            if len(deck) >= min_deck:
                break
            copy_id = f"{base['id']}#{copy_index}"
            cards[copy_id] = {**base, "id": copy_id}
            deck.append(copy_id)
        copy_index += 1

    rng.shuffle(deck)
    logger.info("built deck of %d cards from %d unique source cards", len(deck), len(collected))
    return cards, deck
