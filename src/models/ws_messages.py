"""models.ws_messages — typed WebSocket envelopes (client<->server).

Inbound client messages form a discriminated union (ClientMsg) keyed on `type`,
so a single TypeAdapter(ClientMsg).validate_python(...) deserialises any inbound
message. Server messages mirror the frontend lib/types.ts.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# ─── client → server ────────────────────────────────────────────────────────


class JoinMsg(BaseModel):
    type: Literal["join"] = "join"
    player_id: str | None = None  # None = first join; set on reconnect
    name: str


class StartMsg(BaseModel):
    type: Literal["start"] = "start"


class DrawMsg(BaseModel):
    """The active player draws their card(s) for the turn.

    Turn model: draw → play → end turn. Drawing is the FIRST step of a turn and
    is EXPLICIT (no auto-draw). A player draws once per turn; playing or passing
    before drawing is rejected while the deck still has cards. Drawing the last
    card of the deck arms end-of-game: the drawer finishes their turn, then the
    game ends on the next advance.
    """

    type: Literal["draw"] = "draw"


class PassMsg(BaseModel):
    """The active player ends their turn without playing a card.

    A turn ends by playing a card OR by ending the turn (pass). In the
    draw→play→end model the player must have drawn first (when the deck is
    non-empty). ``EndTurnMsg`` ("end_turn") is an accepted alias handled
    identically.
    """

    type: Literal["pass"] = "pass"


class EndTurnMsg(BaseModel):
    """Alias for :class:`PassMsg` — ends the turn. Same handler as ``pass``."""

    type: Literal["end_turn"] = "end_turn"


class Placement(BaseModel):
    zone: Literal["self", "player", "center"]
    target_player_id: str | None = None  # required when zone == "player"


class PlayMsg(BaseModel):
    type: Literal["play"] = "play"
    card_id: str
    # Optional/back-compat: the UI no longer collects a zone/target up front — the
    # player just picks a card and the interpreter decides whether a target is
    # needed (surfaced via a prompt_choice follow-up).
    placement: Placement | None = None
    chosen_player_id: str | None = None  # for prompt_choice cards (player axis)
    chosen_card_id: str | None = None  # for cards that make the actor pick a card
    # Authoring-on-play: the game is *A Thousand Blank White Cards*, so a blank
    # card is played by authoring it. When the played card is blank, the client
    # sends the authored title+description on the FIRST play of that card_id;
    # the room persists them (clearing the blank flag) BEFORE interpreting, so
    # any prompt_choice follow-up play (which omits these) re-interprets the
    # now-real card. Ignored for non-blank cards.
    title: str | None = None
    description: str | None = None


class CreateCardMsg(BaseModel):
    type: Literal["create_card"] = "create_card"
    title: str
    description: str


class PreviewCardMsg(BaseModel):
    type: Literal["preview_card"] = "preview_card"
    title: str
    description: str


class EpilogueVoteMsg(BaseModel):
    type: Literal["epilogue_vote"] = "epilogue_vote"
    card_id: str
    keep: bool


ClientMsg = Annotated[
    Union[JoinMsg, StartMsg, DrawMsg, PassMsg, EndTurnMsg, PlayMsg, CreateCardMsg, PreviewCardMsg, EpilogueVoteMsg],
    Field(discriminator="type"),
]


# ─── server → client ────────────────────────────────────────────────────────


class StateMsg(BaseModel):
    """Full game state snapshot broadcast to all players."""

    type: Literal["state"] = "state"
    state: dict  # serialized GameState snapshot; typed further in frontend


class EffectAppliedMsg(BaseModel):
    type: Literal["effect_applied"] = "effect_applied"
    log_entry: str


class CardInterpretedMsg(BaseModel):
    type: Literal["card_interpreted"] = "card_interpreted"
    card_id: str
    program: str | None = None
    snippet: str | None = None
    verdict: str  # "ok" | "invalid" | "needs_choice"


class PreviewResultMsg(BaseModel):
    type: Literal["preview_result"] = "preview_result"
    program: str | None = None
    snippet: str | None = None
    verdict: str


class PromptChoiceMsg(BaseModel):
    """Server asks the active player to pick a target."""

    type: Literal["prompt_choice"] = "prompt_choice"
    card_id: str
    prompt: str
    choices: list[dict]  # list of {player_id, name}


class EpilogueMsg(BaseModel):
    type: Literal["epilogue"] = "epilogue"
    cards: list[dict]  # card snapshots created this game


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    message: str


class BrewingMsg(BaseModel):
    """Broadcast while the agent is interpreting a card."""

    type: Literal["brewing"] = "brewing"
    card_id: str


ServerMsg = Union[
    StateMsg,
    EffectAppliedMsg,
    CardInterpretedMsg,
    PreviewResultMsg,
    PromptChoiceMsg,
    EpilogueMsg,
    ErrorMsg,
    BrewingMsg,
]
