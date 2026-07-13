"""models.ws_messages — typed WebSocket envelopes (client<->server).

Inbound client messages form a discriminated union (ClientMsg) keyed on `type`,
so a single TypeAdapter(ClientMsg).validate_python(...) deserialises any inbound
message. Server messages mirror the frontend lib/types.ts.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, Field

from models.card import CARD_ART_PREFIX, MAX_CARD_ART_BYTES, MAX_CARD_DESCRIPTION, MAX_CARD_TITLE, decode_card_art

# Length-bounded card text, enforced on every inbound authoring message via the
# ClientMsg TypeAdapter in board.ws. Limits live in models.card (single source).
CardTitle = Annotated[str, Field(max_length=MAX_CARD_TITLE)]
CardDescription = Annotated[str, Field(max_length=MAX_CARD_DESCRIPTION)]


def _validate_card_art(value: str) -> str:
    """Validate an inbound card-art data-URL: prefix, size cap, real PNG content.

    Enforced at the message boundary so Room and the REST art endpoint can trust
    every stored data-URL to decode to actual PNG bytes (decode + magic-byte
    check live in models.card.decode_card_art, single source).
    """
    if not value.startswith(CARD_ART_PREFIX):
        raise ValueError(f"card art must be a {CARD_ART_PREFIX!r} data-URL")
    if len(value) > MAX_CARD_ART_BYTES:
        raise ValueError(f"card art exceeds {MAX_CARD_ART_BYTES} bytes ({len(value)})")
    try:
        decode_card_art(value)
    except ValueError as exc:  # binascii.Error subclasses ValueError
        raise ValueError(f"card art payload is not a valid PNG: {exc}") from exc
    return value


CardArt = Annotated[str, AfterValidator(_validate_card_art)]

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
    title: CardTitle | None = None
    description: CardDescription | None = None
    # Optional hand-drawn art for the authored blank, as a validated PNG
    # data-URL. Stored out-of-band in Room.card_art (never in GameState).
    art: CardArt | None = None
    # Play this card into the open reaction window (canonical trigger
    # "on_reaction"). Reaction plays come from non-active players and bypass
    # the active-player/has-drawn gates; they are only legal while a window is
    # open. prompt_choice follow-ups for a reaction must re-send this flag.
    as_reaction: bool = False


class PassReactionMsg(BaseModel):
    """Decline to react to the pending play. When every eligible player has
    passed, the window closes early and the pending card resolves."""

    type: Literal["pass_reaction"] = "pass_reaction"
    # Stale-window guard: a pass for an already-closed window is ignored.
    window_id: str | None = None


class CreateCardMsg(BaseModel):
    type: Literal["create_card"] = "create_card"
    title: CardTitle
    description: CardDescription
    # Optional hand-drawn art, as a validated PNG data-URL (see PlayMsg.art).
    art: CardArt | None = None


class PreviewCardMsg(BaseModel):
    type: Literal["preview_card"] = "preview_card"
    title: CardTitle
    description: CardDescription


class EpilogueStartMsg(BaseModel):
    """Host-only: advance from the post-game results screen into the epilogue
    vote. Only valid while ``phase == "results"``."""

    type: Literal["epilogue_start"] = "epilogue_start"


class EpilogueVoteMsg(BaseModel):
    type: Literal["epilogue_vote"] = "epilogue_vote"
    card_id: str
    keep: bool


class EpilogueDoneMsg(BaseModel):
    """A player is done voting. Any card they never voted on abstains — this is
    what makes voting skippable rather than requiring full coverage."""

    type: Literal["epilogue_done"] = "epilogue_done"


class EpilogueFinalizeMsg(BaseModel):
    """Host-only: finalize the epilogue immediately, regardless of who's done."""

    type: Literal["epilogue_finalize"] = "epilogue_finalize"


ClientMsg = Annotated[
    Union[
        JoinMsg,
        StartMsg,
        DrawMsg,
        PassMsg,
        EndTurnMsg,
        PlayMsg,
        PassReactionMsg,
        CreateCardMsg,
        PreviewCardMsg,
        EpilogueStartMsg,
        EpilogueVoteMsg,
        EpilogueDoneMsg,
        EpilogueFinalizeMsg,
    ],
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
    # In-character comment from the agent. Optional so pre-agent callers / older
    # clients stay compatible; D1/D2 consume it (D1 persists it to the game log).
    comment: str = ""


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


class ReactionWindowMsg(BaseModel):
    """Broadcast when a play opens a reaction window. Public info only — each
    client decides its own eligibility from the hand canonicals it already has
    in the state snapshot; the server validates authoritatively. The snapshot's
    ``pending_play`` field is the reconnect-safe source of truth; this message
    is the immediacy push."""

    type: Literal["reaction_window"] = "reaction_window"
    window_id: str
    card_id: str  # the pending card (full body rides the state snapshot)
    actor_id: str
    deadline_epoch_ms: int


class ReactionResultMsg(BaseModel):
    """Broadcast when a reaction window closes, however it closed."""

    type: Literal["reaction_result"] = "reaction_result"
    window_id: str
    # "resolved" = timeout or all passed (original play resolves normally).
    outcome: Literal["resolved", "countered", "stolen", "redirected"]
    reactor_id: str | None = None
    reaction_card_id: str | None = None


ServerMsg = Union[
    StateMsg,
    EffectAppliedMsg,
    CardInterpretedMsg,
    PreviewResultMsg,
    PromptChoiceMsg,
    EpilogueMsg,
    ErrorMsg,
    BrewingMsg,
    ReactionWindowMsg,
    ReactionResultMsg,
]
