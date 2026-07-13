"""agent.rag.seed — load gold/filler seed cards into the Qdrant RAG store at startup."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.rag.store import init_store, upsert_cards

logger = logging.getLogger(__name__)

# Default path relative to the project root (where uvicorn is launched).
DEFAULT_SEED_PATH = Path("data/seed_cards.json")


def _canonical_to_str(value: object) -> str:
    """Normalise a card's canonical field to a JSON string (empty if missing)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def read_seed_cards(seed_path: Path | None = None) -> list[dict]:
    """Read and parse the seed-cards JSON file (offline — no store, no network).

    Returns the raw list of card dicts, each guaranteed an 'id' (generating a
    'seed-NNN' id when the source omits one). A missing file logs a warning and
    returns an empty list. This is the offline card source used by deck building
    when the RAG store is unavailable.
    """
    path = seed_path or DEFAULT_SEED_PATH
    if not path.exists():
        logger.warning("Seed cards file not found at %s — skipping", path)
        return []

    cards: list[dict] = json.loads(path.read_text())
    for index, card in enumerate(cards):
        card.setdefault("id", f"seed-{index:03d}")
    return cards


def load_seed_cards(seed_path: Path | None = None) -> int:
    """Initialise the RAG store and upsert all seed cards.

    Returns the number of cards upserted. A missing file logs a warning and
    returns 0. Cards without an 'id' get a generated 'seed-NNN' id; a canonical
    dict is JSON-serialised to a string (canonical is stored as payload).
    """
    path = seed_path or DEFAULT_SEED_PATH
    cards = read_seed_cards(path)
    if not cards:
        return 0

    init_store()

    prepared = [
        {
            "card_id": card["id"],
            "title": card["title"],
            "description": card["description"],
            # Art description rides as payload (not embedded) so retrieved
            # exemplars expose what their art depicts — cards can key off it.
            "alt_text": card.get("alt_text"),
            "canonical": _canonical_to_str(card.get("canonical")),
            "source": "seed",
        }
        for card in cards
    ]

    try:
        upsert_cards(prepared)
        count = len(prepared)
    except Exception:
        logger.exception("Failed to upsert seed cards")
        count = 0
    logger.info("Loaded %d seed cards into RAG store", count)
    return count
