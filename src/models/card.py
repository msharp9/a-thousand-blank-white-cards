"""models.card — Card data models for seed data and runtime play.

Two card varieties:
  GoldCard   — fully annotated exemplar with structured game-logic ops.
  FillerCard — text-only placeholder for volume in RAG index.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Card text limits. A card is a card, not a novel — bounding the text keeps every
# card small enough to embed as a single chunk and enforces the game's terse style.
# Single source of truth; imported by models.ws_messages and agent.rag.store.
MAX_CARD_TITLE = 60
MAX_CARD_DESCRIPTION = 500


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
        description="Sequence of CardOp to execute. Use ops OR snippet, not both.",
    )
    snippet: str | None = Field(
        default=None,
        description=(
            "Free-text rule description when ops cannot fully capture the effect. Use ops OR snippet, not both."
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
