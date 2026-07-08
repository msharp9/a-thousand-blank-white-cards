"""tbwc.models.effects — immediate Op discriminated union, Target, EffectProgram."""

from __future__ import annotations

from typing import Annotated, Literal, Union

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
