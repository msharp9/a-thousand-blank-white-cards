"""Versioned, bounded descriptors and replies for generic multiplayer input."""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
MAX_INTERACTION_DESCRIPTOR_BYTES = 131_072
MAX_INTERACTION_VALUE_BYTES = 65_536


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("must be a bounded identifier")
    return value


def _audience(value: str) -> str:
    if value in {"active", "all", "all_others"} or (
        value.startswith("player:") and _ID.fullmatch(value.removeprefix("player:"))
    ):
        return value
    raise ValueError("invalid interaction audience")


Identifier = Annotated[str, AfterValidator(_identifier)]
Audience = Annotated[str, AfterValidator(_audience)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InteractionOption(StrictModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=160)
    payload: Any | None = None

    @model_validator(mode="after")
    def bounded_payload(self):
        if len(json.dumps(self.payload, default=str).encode()) > 32_768:
            raise ValueError("option payload exceeds 32768 bytes")
        return self


class _Descriptor(StrictModel):
    schema_version: Literal[1] = 1
    prompt: str = Field(min_length=1, max_length=500)
    audience: Audience = "active"
    sealed: bool = False
    timeout_seconds: int = Field(default=60, ge=10, le=300)


class ChoiceInteraction(_Descriptor):
    kind: Literal["choice"] = "choice"
    options: list[InteractionOption] = Field(default_factory=list, max_length=100)
    min_selections: int = Field(default=1, ge=1, le=100)
    max_selections: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def valid_selection_range(self):
        if self.min_selections > self.max_selections:
            raise ValueError("min_selections exceeds max_selections")
        if self.options and self.max_selections > len(self.options):
            raise ValueError("max_selections exceeds option count")
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("choice option ids must be unique")
        if len(self.model_dump_json().encode()) > MAX_INTERACTION_DESCRIPTOR_BYTES:
            raise ValueError("choice descriptor exceeds 131072 bytes")
        return self


class NumberInteraction(_Descriptor):
    kind: Literal["number"] = "number"
    minimum: float = Field(default=-1_000_000, allow_inf_nan=False)
    maximum: float = Field(default=1_000_000, allow_inf_nan=False)
    integer: bool = True

    @model_validator(mode="after")
    def valid_range(self):
        if self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if self.integer and math.ceil(self.minimum) > math.floor(self.maximum):
            raise ValueError("integer interaction range contains no integer")
        return self


class TextInteraction(_Descriptor):
    kind: Literal["text"] = "text"
    max_length: int = Field(default=500, ge=1, le=2000)


class CardPickInteraction(_Descriptor):
    kind: Literal["card_pick"] = "card_pick"
    card_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    # When true, ignore the static ``card_ids`` and present EACH audience member
    # their OWN hand to pick from (the room fills the per-player options at send
    # time and validates each response against that player's hand). This is the
    # only way to run a simultaneous "everyone discards a card they choose" —
    # a shared ``card_ids`` list can't, and snippets can't read other hands.
    from_hand: bool = False
    # How many cards each responder must pick. Defaults 1/1 (single pick). With
    # max_picks > 1 the responder picks a SET ("discard 2 cards"); min_picks 0
    # allows "up to N". The stored value follows suit: a single card_id string
    # when max_picks == 1 (back-compat), else a list of card_ids (see the room's
    # _validate_interaction_response). A responder is never forced to pick more
    # cards than they can select — the room clamps the effective range to the
    # number offered.
    min_picks: int = Field(default=1, ge=0, le=200)
    max_picks: int = Field(default=1, ge=1, le=200)

    @model_validator(mode="after")
    def valid_pick_range(self):
        if self.min_picks > self.max_picks:
            raise ValueError("min_picks exceeds max_picks")
        # A static candidate list can't satisfy a floor larger than itself. Skip
        # this when the options are filled elsewhere (from_hand / input_ref).
        if self.card_ids and not self.from_hand and self.min_picks > len(self.card_ids):
            raise ValueError("min_picks exceeds the number of candidate cards")
        return self


class ConfirmInteraction(_Descriptor):
    kind: Literal["confirm"] = "confirm"
    confirm_label: str = Field(default="Yes", min_length=1, max_length=80)
    decline_label: str = Field(default="No", min_length=1, max_length=80)


class DrawingPoint(StrictModel):
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class DrawingStroke(StrictModel):
    color: str = Field(default="#1a1a1a", pattern=r"^#[0-9A-Fa-f]{6}$")
    width: float = Field(default=0.01, gt=0, le=0.1, allow_inf_nan=False)
    points: list[DrawingPoint] = Field(min_length=1, max_length=256)


class DrawingInteraction(_Descriptor):
    kind: Literal["drawing"] = "drawing"
    max_strokes: int = Field(default=64, ge=1, le=64)
    max_points_per_stroke: int = Field(default=256, ge=2, le=256)


InteractionDescriptor = Annotated[
    Union[
        ChoiceInteraction,
        NumberInteraction,
        TextInteraction,
        CardPickInteraction,
        ConfirmInteraction,
        DrawingInteraction,
    ],
    Field(discriminator="kind"),
]


class ChoiceResponse(StrictModel):
    kind: Literal["choice"] = "choice"
    option_ids: list[Identifier] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_selections(self):
        if len(self.option_ids) != len(set(self.option_ids)):
            raise ValueError("choice selections must be unique")
        return self


class NumberResponse(StrictModel):
    kind: Literal["number"] = "number"
    value: float = Field(allow_inf_nan=False)


class TextResponse(StrictModel):
    kind: Literal["text"] = "text"
    value: str = Field(max_length=2000)


class CardPickResponse(StrictModel):
    kind: Literal["card_pick"] = "card_pick"
    # Legacy single-pick field; still accepted from older clients. Exactly one of
    # card_id / card_ids must be present, and ``picks`` exposes the unified list.
    card_id: Identifier | None = None
    # Multi-pick field: the set of chosen cards ("discard 2 cards"). Uniqueness is
    # enforced so a responder can't pad a required count with duplicates.
    card_ids: list[Identifier] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def exactly_one_shape(self):
        if (self.card_id is None) == (self.card_ids is None):
            raise ValueError("card_pick response requires exactly one of card_id or card_ids")
        if self.card_ids is not None and len(self.card_ids) != len(set(self.card_ids)):
            raise ValueError("card_pick selections must be unique")
        return self

    @property
    def picks(self) -> list[str]:
        """The chosen card ids as a list, regardless of which field was sent."""
        return [self.card_id] if self.card_id is not None else list(self.card_ids or [])


class ConfirmResponse(StrictModel):
    kind: Literal["confirm"] = "confirm"
    confirmed: bool


class DrawingResponse(StrictModel):
    kind: Literal["drawing"] = "drawing"
    strokes: list[DrawingStroke] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def bounded_encoding(self):
        if len(self.model_dump_json().encode()) > MAX_INTERACTION_VALUE_BYTES:
            raise ValueError("drawing payload exceeds 65536 bytes")
        return self


InteractionResponsePayload = Annotated[
    Union[ChoiceResponse, NumberResponse, TextResponse, CardPickResponse, ConfirmResponse, DrawingResponse],
    Field(discriminator="kind"),
]


class InteractionProgress(StrictModel):
    expected_count: int = Field(ge=0, le=100)
    received_count: int = Field(ge=0, le=100)
    submitted: bool = False
    complete: bool = False


class InteractionResultRef(StrictModel):
    result_key: Identifier
    path: list[str | int] = Field(default_factory=list, max_length=12)
