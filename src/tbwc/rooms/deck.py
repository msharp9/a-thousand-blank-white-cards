"""tbwc.rooms.deck — build and shuffle a starting deck for a new game.

The intended start-game flow is:
  (1) collect existing cards — prior-game kept cards + seed cards from the RAG
      corpus, falling back to the offline seed-data file if RAG is unavailable,
  (2) seed a fraction of BLANK cards into the deck (see BLANK_CARD_RATIO) — the
      game is literally *A Thousand Blank White Cards*, so blanks are drawable
      and playable: a blank sits in the hand as blank and is AUTHORED ON PLAY,
  (3) create every card (real + blank) into ``state.cards`` (a card_id -> card
      dict registry),
  (4) shuffle their ids into ``state.deck`` (padded to >= MIN_DECK cards),
  (5) leave play/dealing to the caller (Room._handle_start).

Everything here is pure and dependency-injectable: pass an ``rng`` for
deterministic shuffles and a ``card_source`` to bypass RAG/OpenAI in tests.
No live external service (Qdrant/OpenAI) is required to build a deck.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable

logger = logging.getLogger(__name__)

# A game needs at least this many cards in the deck to start (acceptance: >= 30).
MIN_DECK = 30

# Fraction of the target deck size (``min_deck``) that is seeded as BLANK cards.
# Blanks are added ON TOP of the collected real cards and count toward min_deck,
# so a freshly built deck is roughly this fraction blank (a little less once real
# cards push the total above min_deck). ~1/3 keeps blanks common — the game is
# *A Thousand Blank White Cards* — without letting them dominate the deck.
BLANK_CARD_RATIO = 1 / 3

# Type alias for a card source: a zero-arg callable returning raw card dicts.
CardSource = Callable[[], list[dict]]


def _make_blank_card(n: int) -> dict:
    """Return a blank card dict (id ``blank-<n>``).

    A blank enters the hand as blank (empty title/description, ``blank`` flag
    set) and is authored on play: Room._handle_play fills in the title and
    description, sets ``creator_id`` to the player, and clears the ``blank`` flag
    before interpreting. ``creator_id`` starts as ``"blank"`` so an un-played
    blank is attributable to no player.
    """
    return {
        "id": f"blank-{n}",
        "title": "",
        "description": "",
        "blank": True,
        "creator_id": "blank",
    }


def _coerce_canonical(raw_canonical: object) -> dict | None:
    """Return a card's ``canonical`` annotation as a dict, or ``None``.

    The two card sources encode canonical differently: the offline seed file
    (``data/*.json``) carries it as a nested dict, while the RAG store persists
    it as a JSON string payload (see ``rag.store.upsert_card``). Normalise both
    to a dict so downstream (``engine.compile.compile_card``) has one shape to
    read. Empty strings, ``None`` and unparseable/degenerate values yield
    ``None`` (i.e. "no structured annotation").
    """
    if raw_canonical is None or raw_canonical == "":
        return None
    if isinstance(raw_canonical, dict):
        return raw_canonical
    if isinstance(raw_canonical, str):
        try:
            parsed = json.loads(raw_canonical)
        except ValueError:  # JSONDecodeError subclasses ValueError
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _normalise_card(raw: dict, index: int) -> dict:
    """Coerce a raw card (RAG payload or seed-file entry) into a game card dict.

    RAG payloads key the id as ``card_id``; seed-file entries key it as ``id``.
    Missing ids get a stable ``deck-NNN`` fallback so nothing collides silently.

    Crucially this preserves the card's structured game logic: ``canonical``
    (normalised to a dict via :func:`_coerce_canonical`), the ``ops`` and
    ``venue`` lifted out of it for convenient top-level access, and the raw
    ``description`` snippet. Historically this function dropped everything but
    id/title/description/creator_id, which stripped the ops off every card and
    forced every play through the LLM interpreter — the deterministic play path
    depends on these fields surviving into ``state.cards``.
    """
    card_id = raw.get("id") or raw.get("card_id") or f"deck-{index:03d}"
    canonical = _coerce_canonical(raw.get("canonical"))
    card: dict = {
        "id": card_id,
        "title": raw.get("title", ""),
        "description": raw.get("description", ""),
        "creator_id": raw.get("source", "seed"),
    }
    if canonical is not None:
        card["canonical"] = canonical
        # Lift ops/venue to the top level so callers need not re-parse canonical.
        if canonical.get("ops") is not None:
            card["ops"] = canonical["ops"]
        card["venue"] = canonical.get("venue", "all")
    return card


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

    Composition (deterministic given ``rng``):
      1. Collect the real cards from the source.
      2. Seed ``round(min_deck * BLANK_CARD_RATIO)`` BLANK cards (``blank-0`` …)
         ON TOP of the real cards. Blanks are real registry entries (so they
         render and can be looked up) and count toward ``min_deck``; they are
         authored on play (see Room._handle_play).
      3. If real + blank cards still fall short of ``min_deck``, pad with
         distinct copies of the REAL cards (each a ``<id>#N`` entry) — blanks
         are never duplicated.

    Raises ValueError only if the source yields no real cards at all.
    """
    rng = rng or random.Random()
    collected = collect_cards(card_source)
    if not collected:
        raise ValueError("no cards available to build a deck (empty card source)")

    cards: dict[str, dict] = {c["id"]: c for c in collected}
    deck: list[str] = list(cards.keys())

    # Seed blank cards on top of the real cards (they count toward min_deck).
    num_blanks = round(min_deck * BLANK_CARD_RATIO)
    for n in range(num_blanks):
        blank = _make_blank_card(n)
        cards[blank["id"]] = blank
        deck.append(blank["id"])

    # Pad with distinct copies of the REAL cards when still short of the minimum.
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
    logger.info(
        "built deck of %d cards from %d unique source cards (%d blanks)",
        len(deck),
        len(collected),
        num_blanks,
    )
    return cards, deck
