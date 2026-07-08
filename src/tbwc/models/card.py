"""tbwc.models.card — Card data models for seed data and runtime play.

Two card varieties:
  GoldCard   — fully annotated exemplar with structured game-logic ops.
  FillerCard — text-only placeholder for volume in RAG index.

Both share a SeedCard union type used for JSON loading.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Canonical annotation — describes HOW a card is executed by the engine
# ---------------------------------------------------------------------------


class CardOp(BaseModel):
    """A single discrete game operation expressed as structured data."""

    op: str = Field(
        description=(
            "Operation name, e.g. 'add_points', 'skip_turn', 'reverse_order', "
            "'draw_cards', 'set_win_condition', 'transfer_cards', 'no_op'."
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

    title: str = Field(description="Short card name, ≤60 chars.")
    description: str = Field(description="Card text as written on the physical card. May include flavour text.")
    canonical: CardCanonical


class FillerCard(BaseModel):
    """A text-only card — title + description only, no structured annotation."""

    title: str
    description: str


# ---------------------------------------------------------------------------
# Union type for loading mixed arrays from JSON
# ---------------------------------------------------------------------------

SeedCard = Annotated[
    Union[GoldCard, FillerCard],
    Field(discriminator=None),  # not using discriminator — GoldCard detected by 'canonical' key
]


def parse_seed_card(data: dict) -> GoldCard | FillerCard:
    """Parse a single seed card dict, returning GoldCard if 'canonical' key present."""
    if "canonical" in data:
        return GoldCard.model_validate(data)
    return FillerCard.model_validate(data)
