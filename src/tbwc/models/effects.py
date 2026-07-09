"""tbwc.models.effects — immediate Op discriminated union, Target, EffectProgram."""

from __future__ import annotations

from typing import Annotated, Literal, Union, get_args

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Target addresses
# ---------------------------------------------------------------------------
Target = Literal[
    "self",
    "left_neighbor",
    "right_neighbor",
    "all",
    "all_others",
    "chooser",  # actor chooses at play-time (requires_choice=True)
    "target_player",  # pre-resolved by engine from ctx.chosen_player_id
    "player_with_most_points",
    "player_with_least_points",
    "player_with_empty_hand",
]

# The set of valid runtime Target literals (derived from the Literal above so it
# never drifts). Used by map_authoring_target for passthrough detection.
_VALID_TARGETS: frozenset[str] = frozenset(get_args(Target))

# ---------------------------------------------------------------------------
# Authoring vocabulary -> runtime Target mapping
# ---------------------------------------------------------------------------
# The card-authoring layer (models.card.CardCanonical.target and the agent's
# Interpretation.placement) uses a small, human-friendly vocabulary that is an
# ALIAS layer on top of the richer runtime Target set. This table is the ONE
# canonical place that translation lives; see bead rjp for the taxonomy.
#
# Authoring vocab:  self | player | all | center
# Plus defensive synonyms the LLM/authors sometimes emit.
#
# NOTE on "center": center is NOT a player target — it describes WHERE a card
# sits (the shared table area), not WHO it affects. It therefore has no valid
# runtime Target and is deliberately absent from this table. Callers dealing
# with placement must handle "center" separately and must never feed it to
# map_authoring_target as a player target (it will raise / fall back).
_AUTHORING_TARGET_ALIASES: dict[str, Target] = {
    "self": "self",
    # "a player you pick" — the actor chooses at play time.
    "player": "chooser",
    "opponent": "chooser",
    "all": "all",
    "all_players": "all",
    "everyone": "all",
    # everyone except the actor
    "all_others": "all_others",
    "others": "all_others",
}


def map_authoring_target(raw: str, *, default: Target | None = None) -> Target:
    """Map an authoring/synonym target string onto a valid runtime ``Target``.

    Translation precedence:
      1. Already-valid runtime Target -> passed through unchanged.
      2. Known authoring alias / synonym -> its runtime Target (see table above).
      3. Unknown value -> raise ValueError, unless ``default`` is provided, in
         which case ``default`` is returned (documented safe fallback, e.g.
         "chooser" so the actor can still pick a valid player at play time).

    The lookup is case-insensitive and tolerant of surrounding whitespace.

    IMPORTANT: "center" is a *placement* concept, not a player target, so it is
    NOT in the alias table. Passing "center" here is treated as an unknown value
    (raises, or returns ``default``). Callers that need to route placement must
    special-case "center" before calling this function.
    """
    key = raw.strip().lower()
    if key in _VALID_TARGETS:
        return key  # type: ignore[return-value]  # narrowed by membership check
    if key in _AUTHORING_TARGET_ALIASES:
        return _AUTHORING_TARGET_ALIASES[key]
    if default is not None:
        return default
    raise ValueError(
        f"Cannot map authoring target {raw!r} onto a runtime Target. "
        f"Valid runtime targets: {sorted(_VALID_TARGETS)}; "
        f"known aliases: {sorted(_AUTHORING_TARGET_ALIASES)}. "
        "Note: 'center' is a placement, not a player target."
    )


# ---------------------------------------------------------------------------
# Individual op models — discriminated by the `op` literal field
# ---------------------------------------------------------------------------


class AddPointsOp(BaseModel):
    op: Literal["add_points"] = "add_points"
    target: Target = "self"
    amount: int


class SubtractPointsOp(BaseModel):
    op: Literal["subtract_points"] = "subtract_points"
    target: Target = "self"
    amount: int


class SetPointsOp(BaseModel):
    op: Literal["set_points"] = "set_points"
    target: Target = "self"
    amount: int


class SkipTurnOp(BaseModel):
    op: Literal["skip_turn"] = "skip_turn"
    target: Target = "self"


class ExtraTurnOp(BaseModel):
    op: Literal["extra_turn"] = "extra_turn"
    target: Target = "self"


class ReverseOrderOp(BaseModel):
    op: Literal["reverse_order"] = "reverse_order"


class ChangeDrawCountOp(BaseModel):
    op: Literal["change_draw_count"] = "change_draw_count"
    amount: int  # new draw_count value (absolute, not delta)


class StealPointsOp(BaseModel):
    op: Literal["steal_points"] = "steal_points"
    from_target: Target
    to_target: Target = "self"
    amount: int


class DrawCardsOp(BaseModel):
    op: Literal["draw_cards"] = "draw_cards"
    target: Target = "self"
    amount: int = 1


class DestroyCardOp(BaseModel):
    op: Literal["destroy_card"] = "destroy_card"
    card_id: str  # id of card to remove from target's hand


class SetWinConditionOp(BaseModel):
    op: Literal["set_win_condition"] = "set_win_condition"
    kind: Literal["highest_points", "lowest_points", "first_to", "last_standing", "none"]
    threshold: int | None = None


class CustomNoteOp(BaseModel):
    """A no-op that logs a flavour message; useful for cards that only register hooks."""

    op: Literal["custom_note"] = "custom_note"
    note: str


# ---------------------------------------------------------------------------
# Discriminated union — Pydantic v2 uses Annotated + Field(discriminator=...)
# ---------------------------------------------------------------------------
Op = Annotated[
    Union[
        AddPointsOp,
        SubtractPointsOp,
        SetPointsOp,
        SkipTurnOp,
        ExtraTurnOp,
        ReverseOrderOp,
        ChangeDrawCountOp,
        StealPointsOp,
        DrawCardsOp,
        DestroyCardOp,
        SetWinConditionOp,
        CustomNoteOp,
    ],
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# EffectProgram: the full payload attached to a card play
# ---------------------------------------------------------------------------
class EffectProgram(BaseModel):
    ops: list[Op] = Field(default_factory=list)
    requires_choice: bool = False  # True when any op targets "chooser"
