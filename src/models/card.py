"""models.card — Card data models for seed data and runtime play.

Two card varieties:
  GoldCard   — fully annotated exemplar with structured game-logic ops.
  FillerCard — text-only placeholder for volume in RAG index.
"""

from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, Field

# Card text limits. A card is a card, not a novel — bounding the text keeps every
# card small enough to embed as a single chunk and enforces the game's terse style.
# Single source of truth; imported by models.ws_messages and agent.rag.store.
MAX_CARD_TITLE = 60
MAX_CARD_DESCRIPTION = 500

# Card art travels as a PNG data-URL, out-of-band from GameState (see
# Room.card_art): the required prefix and the cap on the WHOLE data-URL length.
# 128 KiB keeps a sketch small enough to store/serve without letting a play
# message smuggle in a megapixel image.
CARD_ART_PREFIX = "data:image/png;base64,"
MAX_CARD_ART_BYTES = 131072

# Aggregate cap on all art stored by one room (Room.card_art). Rooms are never
# evicted and mid-game card creation is uncapped, so without a budget a room's
# registry could grow without bound; once the budget is hit new art is dropped
# (cards are still created, just artless).
MAX_ROOM_ART_BYTES = 4 * 1024 * 1024

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def decode_card_art(data_url: str) -> bytes:
    """Decode a ``CARD_ART_PREFIX`` data-URL to PNG bytes.

    Single decode path shared by the inbound validator (models.ws_messages) and
    the REST art endpoint (board.app). Raises ValueError when the base64 payload
    does not decode cleanly or the decoded bytes are not a PNG (magic-byte
    check), so a prefix claim alone never passes off arbitrary content as PNG.
    """
    png = base64.b64decode(data_url[len(CARD_ART_PREFIX) :], validate=True)
    if not png.startswith(_PNG_MAGIC):
        raise ValueError("card art payload is not a PNG")
    return png


# ---------------------------------------------------------------------------
# Canonical annotation — describes HOW a card is executed by the engine
# ---------------------------------------------------------------------------


class CardOp(BaseModel):
    """A single discrete game operation expressed as structured data."""

    op: str = Field(
        description=(
            "Operation name from the seed-data vocabulary, e.g. 'add_points', "
            "'skip_turn', 'reverse_order', 'draw_cards', 'set_win_condition', "
            "'transfer_cards', 'no_op'. This is free-form authoring vocabulary "
            "and is distinct from the runtime Op discriminated union in "
            "models.effects (not every name here maps 1:1 to a reducer)."
        )
    )
    args: dict[str, int | str | bool | None] = Field(
        default_factory=dict,
        description="Op-specific arguments, e.g. {'amount': 5, 'target': 'self'}.",
    )


class CardCanonical(BaseModel):
    """Structured annotation describing how a card behaves in the game engine."""

    timing: Literal["immediate", "modifier"] = Field(
        description=(
            "'immediate' = resolves the moment it is played; 'modifier' = remains in play and modifies future events."
        )
    )
    target: Literal["self", "player", "all", "center"] = Field(
        description="Who or what the card's primary effect targets."
    )
    placement: Literal["self", "player", "center"] = Field(
        description="Where the card is placed after play (self=in front of player, center=table center)."
    )
    venue: Literal["all", "in_person", "online"] = Field(
        default="all",
        description=(
            "Where the card can actually be played: 'all' = works remotely or in person "
            "(the default, most cards); 'in_person' = requires physical presence/contact "
            "(kiss/touch a player, share food, wear or pass an object, move around the room) "
            "so it must be excluded from remote-game decks; 'online' = only makes sense "
            "digitally (rare)."
        ),
    )
    trigger: str | None = Field(
        default=None,
        description="For modifiers: the event string that activates this card, e.g. 'on_draw'.",
    )
    ops: list[CardOp] | None = Field(
        default=None,
        description="Sequence of CardOp to execute.",
    )
    steps: list[dict] | None = Field(
        default=None,
        description=(
            "Ordered runtime resolution steps. New persisted interpretations use this field; "
            "legacy ops and snippet fields remain readable."
        ),
    )
    snippet: str | None = Field(
        default=None,
        description=(
            "Either free-text rule prose (when ops cannot capture the effect at all), or a real "
            "`def apply(state, ctx): ...` snippet (validated by engine.sandbox.validate) when ops "
            "capture a deterministic prefix but the rest needs dynamic state — e.g. draw_cards "
            "followed by a snippet that scores points per card now in hand."
        ),
    )


# ---------------------------------------------------------------------------
# Card variants
# ---------------------------------------------------------------------------


class GoldCard(BaseModel):
    """A fully-annotated exemplar card used to train the AI card generator."""

    title: str = Field(max_length=MAX_CARD_TITLE, description="Short card name.")
    description: str = Field(
        max_length=MAX_CARD_DESCRIPTION,
        description="Card text as written on the physical card. May include flavour text.",
    )
    canonical: CardCanonical


class FillerCard(BaseModel):
    """A text-only card — title + description only, no structured annotation."""

    title: str = Field(max_length=MAX_CARD_TITLE)
    description: str = Field(max_length=MAX_CARD_DESCRIPTION)


# ---------------------------------------------------------------------------
# Loading mixed arrays from JSON
# ---------------------------------------------------------------------------


def parse_seed_card(data: dict) -> GoldCard | FillerCard:
    """Parse a single seed card dict, returning GoldCard if 'canonical' key present."""
    if "canonical" in data:
        return GoldCard.model_validate(data)
    return FillerCard.model_validate(data)
