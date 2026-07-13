"""models.card — Card data models for seed data and runtime play.

Two card varieties:
  GoldCard   — fully annotated exemplar with structured game-logic ops.
  FillerCard — text-only placeholder for volume in RAG index.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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


# Trigger vocabulary = engine.events.GameEvent values + "on_reaction". Hardcoded
# here (models must not import engine); tests/test_corpus_lint.py asserts the two
# stay in sync.
CardTrigger = Literal[
    "on_play",
    "on_validate_play",
    "on_score_change",
    "on_turn_start",
    "on_turn_end",
    "on_draw_step",
    "on_win_check",
    "on_game_end",
    "on_reaction",
]

# v1 → v2 trigger value remaps (spec appendix, data/eval/CANONICAL_SPEC.md).
_TRIGGER_REMAP = {
    "on_draw": "on_draw_step",
    "on_score": "on_score_change",
    "on_play_card": "on_play",
    "on_empty_hand": "on_win_check",
}
# Table-adjudicated pseudo-events with no engine hook: the rule lives in
# notes/set_rule ops, not a trigger.
_DROPPED_TRIGGERS = {"on_physical_action"}

_V2_PLACEMENTS = {"discard", "center", "player"}
_V2_TARGETS = {"self", "player", "all", "all_others", "card", "all_cards", "none"}


def _looks_like_sandbox_code(text: str) -> bool:
    return text.lstrip().startswith("def apply")


def normalise_canonical(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a v1 canonical dict to the v2 schema (idempotent on v2 input).

    The single legacy shim, shared by CardCanonical's before-validator,
    board.rooms.deck._normalise_card, and scripts/migrate_card_schema.py.
    Permanent, not migration-only: persisted room canonicals, Qdrant payloads,
    and agent memory carry v1 dicts forever. Mapping table lives in
    data/eval/CANONICAL_SPEC.md (appendix).
    """
    data = dict(raw)
    timing = data.pop("timing", None)

    # --- trigger: unify trigger_event → trigger, remap renamed events -------
    trigger = data.pop("trigger_event", None)
    if trigger is None:
        trigger = data.get("trigger")
    trigger = _TRIGGER_REMAP.get(trigger, trigger)
    if trigger in _DROPPED_TRIGGERS:
        trigger = None

    # --- placement: collapse v1 self/destroy, derive when missing -----------
    placement = data.get("placement")
    if placement == "self":
        placement = "player" if timing == "modifier" else "discard"
    elif placement == "destroy":
        placement = "discard"
    elif placement not in _V2_PLACEMENTS:
        if timing == "modifier":
            placement = "player" if data.get("target") in ("self", "player") else "center"
        else:
            placement = "discard"
    data["placement"] = placement

    # "on_play" is meaningless on a one-shot card (it fires when played by
    # definition); only persistent modifiers keep it as a hook event.
    if trigger == "on_play" and placement == "discard":
        trigger = None
    data["trigger"] = trigger

    # --- target: "center" was placement leakage in the v1 model -------------
    if data.get("target") == "center":
        data["target"] = "none"

    # --- snippet → sandbox: code renames; prose degrades to a custom_note ---
    snippet = data.pop("snippet", None)
    if snippet:
        if _looks_like_sandbox_code(snippet):
            data.setdefault("sandbox", snippet)
        elif not data.get("sandbox") and not data.get("steps"):
            ops = list(data.get("ops") or [])
            ops.append({"op": "custom_note", "args": {"note": snippet}})
            data["ops"] = ops

    data.setdefault("venue", "all")
    return data


class CardCanonical(BaseModel):
    """Structured annotation describing how a card behaves in the game engine.

    Schema v2 (data/eval/CANONICAL_SPEC.md). Legacy v1 dicts (timing,
    placement "self"/"destroy", trigger_event, prose snippet) are accepted on
    input and normalised by ``normalise_canonical``.
    """

    target: Literal["self", "player", "all", "all_others", "card", "all_cards", "none"] = Field(
        description="Who or what the card's primary effect targets."
    )
    placement: Literal["discard", "center", "player"] = Field(
        description=(
            "Where the card goes after play: 'discard' = one-shot, resolves and is done; "
            "'center' = stays on the table as a game-wide modifier; 'player' = stays in "
            "front of one player as a modifier attached to them."
        )
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
    trigger: CardTrigger | None = Field(
        default=None,
        description=(
            "None for one-shot cards. For persistent modifiers: the GameEvent that "
            "re-fires the card's hook. 'on_reaction' marks reaction cards, playable "
            "only during another player's play (never on your own play step)."
        ),
    )
    ops: list[CardOp] | None = Field(
        default=None,
        description="Sequence of CardOp to execute. None when the effect is sandbox-only.",
    )
    steps: list[dict] | None = Field(
        default=None,
        description=(
            "Ordered runtime resolution steps. New persisted interpretations use this field; "
            "legacy ops and sandbox fields remain readable."
        ),
    )
    sandbox: str | None = Field(
        default=None,
        description=(
            "Executable `def apply(state, ctx): ...` code (validated by "
            "engine.sandbox.validate). Dataset cards always carry it — even when ops "
            "express the same effect — so the RAG corpus teaches the agent how to "
            "compose sandbox code for effects ops cannot express."
        ),
    )
    magnitude_sign: Literal["positive", "negative", "neutral"] | None = Field(
        default=None,
        description=(
            "Eval-only human label: net effect on the target's standing. Not written "
            "into seed/game data; consumed by eval scorers."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return normalise_canonical(data)
        return data


# ---------------------------------------------------------------------------
# Card variants
# ---------------------------------------------------------------------------


class GoldCard(BaseModel):
    """A fully-annotated exemplar card used to train the AI card generator."""

    id: str | None = Field(
        default=None,
        description=(
            "Stable id, unique within its dataset file. Keeps RAG upserts keyed to "
            "the card rather than its array index across regenerations."
        ),
    )
    title: str = Field(max_length=MAX_CARD_TITLE, description="Short card name.")
    description: str = Field(
        max_length=MAX_CARD_DESCRIPTION,
        description="Card text as written on the physical card. May include flavour text.",
    )
    alt_text: str | None = Field(
        default=None,
        description=(
            "Description of the card's art (what is drawn), None when artless. "
            "First-class so cards can query other cards' art content."
        ),
    )
    canonical: CardCanonical


class FillerCard(BaseModel):
    """A text-only card — title + description only, no structured annotation."""

    id: str | None = None
    title: str = Field(max_length=MAX_CARD_TITLE)
    description: str = Field(max_length=MAX_CARD_DESCRIPTION)
    alt_text: str | None = None


# ---------------------------------------------------------------------------
# Loading mixed arrays from JSON
# ---------------------------------------------------------------------------


def parse_seed_card(data: dict) -> GoldCard | FillerCard:
    """Parse a single seed card dict, returning GoldCard if 'canonical' key present."""
    if "canonical" in data:
        return GoldCard.model_validate(data)
    return FillerCard.model_validate(data)
