"""Typed host-admin actions and the persisted unanimous-consent proposal."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.game_state import normalize_condition_key


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetScoreAdminAction(_StrictModel):
    kind: Literal["set_score"] = "set_score"
    player_id: str = Field(min_length=1, max_length=80)
    score: int = Field(ge=-1_000_000, le=1_000_000)


class MoveCardAdminAction(_StrictModel):
    kind: Literal["move_card"] = "move_card"
    source_zone: Literal["deck", "discard", "center", "exile", "in_play"]
    card_id: str | None = Field(default=None, max_length=80)
    source_player_id: str | None = Field(default=None, max_length=80)
    selector: Literal["top", "bottom"] | None = None
    to_zone: Literal["deck", "discard", "center", "exile", "in_play", "hand"]
    to_player_id: str | None = Field(default=None, max_length=80)
    deck_position: Literal["top", "bottom", "shuffle"] | None = None

    @model_validator(mode="after")
    def validate_zones(self) -> MoveCardAdminAction:
        if self.source_zone == "deck":
            if self.selector is None or self.card_id is not None or self.source_player_id is not None:
                raise ValueError("deck source requires selector and forbids card_id/source_player_id")
        elif self.source_zone == "in_play":
            if self.card_id is None or self.source_player_id is None or self.selector is not None:
                raise ValueError("in_play source requires card_id and source_player_id")
        elif self.card_id is None or self.source_player_id is not None or self.selector is not None:
            raise ValueError("public global source requires card_id only")

        if self.to_zone in {"hand", "in_play"}:
            if self.to_player_id is None:
                raise ValueError(f"{self.to_zone} destination requires to_player_id")
        elif self.to_player_id is not None:
            raise ValueError("to_player_id is only valid for hand/in_play destinations")

        if self.to_zone == "deck":
            if self.deck_position is None:
                raise ValueError("deck destination requires deck_position")
        elif self.deck_position is not None:
            raise ValueError("deck_position is only valid for deck destinations")
        return self


class ShuffleDeckAdminAction(_StrictModel):
    kind: Literal["shuffle_deck"] = "shuffle_deck"
    include_discard: bool = False


class SetConditionAdminAction(_StrictModel):
    kind: Literal["set_condition"] = "set_condition"
    player_id: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")
    value: str | int | float | bool
    duration_turns: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def bound_text_value(self) -> SetConditionAdminAction:
        self.key = normalize_condition_key(self.key)
        if isinstance(self.value, str) and len(self.value) > 500:
            raise ValueError("condition text exceeds 500 characters")
        return self


class RemoveConditionAdminAction(_StrictModel):
    kind: Literal["remove_condition"] = "remove_condition"
    player_id: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def normalize_key(self) -> RemoveConditionAdminAction:
        self.key = normalize_condition_key(self.key)
        return self


class RemoveHookAdminAction(_StrictModel):
    kind: Literal["remove_hook"] = "remove_hook"
    hook_id: str = Field(min_length=1, max_length=160)


class EliminatePlayersAdminAction(_StrictModel):
    kind: Literal["eliminate_players"] = "eliminate_players"
    player_ids: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_players(self) -> EliminatePlayersAdminAction:
        if len(self.player_ids) != len(set(self.player_ids)):
            raise ValueError("player_ids must be unique")
        return self


class EndGameAdminAction(_StrictModel):
    kind: Literal["end_game"] = "end_game"
    winner_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_winners(self) -> EndGameAdminAction:
        if self.winner_ids is not None and len(self.winner_ids) != len(set(self.winner_ids)):
            raise ValueError("winner_ids must be unique")
        return self


class SetResultWinnersAdminAction(_StrictModel):
    kind: Literal["set_result_winners"] = "set_result_winners"
    winner_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_winners(self) -> SetResultWinnersAdminAction:
        if len(self.winner_ids) != len(set(self.winner_ids)):
            raise ValueError("winner_ids must be unique")
        return self


AdminAction = Annotated[
    Union[
        SetScoreAdminAction,
        MoveCardAdminAction,
        ShuffleDeckAdminAction,
        SetConditionAdminAction,
        RemoveConditionAdminAction,
        RemoveHookAdminAction,
        EliminatePlayersAdminAction,
        EndGameAdminAction,
        SetResultWinnersAdminAction,
    ],
    Field(discriminator="kind"),
]


class AdminProposalPreviewItem(_StrictModel):
    kind: str
    title: str = Field(max_length=160)
    detail: str = Field(max_length=1000)


class PendingAdminProposal(_StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: str
    proposer_id: str
    phase: Literal["playing", "results"]
    actions: list[AdminAction] = Field(min_length=1, max_length=20)
    required_voter_ids: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    deadline_at: datetime
    rng_seed: int
    preview: list[AdminProposalPreviewItem]
    warnings: list[str] = Field(default_factory=list)
