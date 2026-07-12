"""models.cards — the runtime Card model.

Each card has an id, title, description, creator, and an open-ended
`properties` dict that agents set at runtime (e.g. 'indestructible',
'uncounterable', 'playable_out_of_turn').
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from models.effects import Op


class Card(BaseModel):
    """A single card in the game.

    `properties` is open-ended: agents set flags like 'indestructible',
    'uncounterable', 'playable_out_of_turn' here.
    """

    id: str
    title: str
    description: str
    creator_id: str  # player id who created/wrote this card

    # Provenance: "authored" (this game, or a RAG-kept re-entry from a previous
    # game), "seed" (shipped exemplar), or "blank" (un-authored, playable
    # blank). Distinct from `creator_id`, which for seed/blank cards holds a
    # source label rather than a real player id. Drives the epilogue vote pool
    # (only "authored" cards get a vote) — see Room._is_authored_card.
    origin: Literal["authored", "seed", "blank"] | None = None

    # Open-ended flags set by the LLM agent or hand-authored tests.
    # Keys are arbitrary strings; values are Any (bool, int, str…).
    properties: dict[str, Any] = Field(default_factory=dict)

    # Immediate ops resolved against the effects Op discriminated union.
    immediate_ops: list[Op] = Field(default_factory=list)

    # Hook ids (str uuids) pointing into the global HookRegistry.
    hook_ids: list[str] = Field(default_factory=list)
