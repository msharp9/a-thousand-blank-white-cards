"""models.effects — immediate Op discriminated union, Target, EffectProgram."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from models.interactions import (
    CardPickInteraction,
    ChoiceInteraction,
    InteractionDescriptor,
    InteractionResultRef,
)
from models.game_state import normalize_condition_key

MAX_RESOLUTION_STEPS = 8
MAX_INTERACTION_STEPS = 4
MAX_INTERACTION_PLAN_BYTES = 262_144
MAX_RESOLUTION_PLAN_BYTES = 524_288

# ---------------------------------------------------------------------------
# Target addresses
# ---------------------------------------------------------------------------
# The closed set of well-known player addresses. Beyond these, two OPEN,
# validated prefix forms keep the vocabulary extensible without a frozen enum
# (docs/state-example.jsonc's "everything is dynamic" mandate):
#   "id:<player_id>"      — one specific player (missing id resolves to nobody)
#   "has:<condition_key>" — every player whose conditions bag has a truthy key
_VALID_TARGETS: frozenset[str] = frozenset(
    {
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
    }
)

_TARGET_PREFIXES: tuple[str, ...] = ("id:", "has:")


def _validate_target(value: str) -> str:
    if value in _VALID_TARGETS:
        return value
    for prefix in _TARGET_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix):
            if prefix == "has:":
                key = normalize_condition_key(value[len(prefix) :])
                if not key:
                    break
                return f"{prefix}{key}"
            return value
    raise ValueError(
        f"invalid Target {value!r}: expected one of {sorted(_VALID_TARGETS)} "
        f"or a prefixed form ({', '.join(p + '…' for p in _TARGET_PREFIXES)})"
    )


Target = Annotated[str, AfterValidator(_validate_target)]

# ---------------------------------------------------------------------------
# Card-target addresses
# ---------------------------------------------------------------------------
# A CardTarget is a SEPARATE axis from the player ``Target`` above: it addresses
# CARDS (by zone), not players. Effects that manipulate cards (e.g. destroy a
# card) resolve a CardTarget into a concrete list of card ids via
# ``engine.reducers._resolve_card_targets``.
#
#   "this"        — the card currently being played (ctx.card_id). Guarded: if
#                   there is no card in context this resolves to nothing.
#   "chosen_card" — the actor picks a card at play time (requires
#                   ctx.chosen_card_id; flips EffectProgram.requires_choice,
#                   mirroring the player "chooser" convention).
#   "all_in_play" — every card in every player's in-play zone
#                   (state.cards_in_play()).
#   "all_in_hand" — cards in a hand. FIRST-CUT DECISION: this resolves to the
#                   ACTOR's own hand (state.get_player(ctx.actor_id).hand).
#                   Whose-hand composition (e.g. "all cards in a chosen player's
#                   hand") is a documented future extension — it would pair a
#                   CardTarget with a companion player Target rather than
#                   overloading this literal.
#   "all_in_center" — every card in the shared center zone
#                   (state.center_cards(), i.e. the house-rules area).
#   "last_played" — the card of the most recent completed "play" history
#                   event. The acting card's own play is recorded only after
#                   its effects finish, so it is never the match during its
#                   own resolution — but an earlier, genuinely completed play
#                   of the same card (returned to hand, then replayed) still
#                   counts. Plays whose card has since left the card registry
#                   are skipped; no surviving prior play resolves to nothing.
# Open, validated prefix forms (mirroring the player Target grammar):
#   "id:<card_id>"   — one specific card (missing id resolves to nothing)
#   "attr:<k>=<v>"   — every card whose attributes bag has key k stringifying to v
_VALID_CARD_TARGETS: frozenset[str] = frozenset(
    {
        "this",
        "chosen_card",
        "all_in_play",
        "all_in_hand",
        "all_in_center",
        "last_played",
    }
)


def _validate_card_target(value: str) -> str:
    if value in _VALID_CARD_TARGETS:
        return value
    if value.startswith("id:") and len(value) > 3:
        return value
    if value.startswith("attr:") and "=" in value[5:] and value[5:].split("=", 1)[0]:
        return value
    raise ValueError(
        f"invalid CardTarget {value!r}: expected one of {sorted(_VALID_CARD_TARGETS)}, "
        "'id:<card_id>', or 'attr:<key>=<value>'"
    )


CardTarget = Annotated[str, AfterValidator(_validate_card_target)]

# ---------------------------------------------------------------------------
# Card-flow destinations
# ---------------------------------------------------------------------------
# "card_owner" is a PER-CARD destination, valid only where cards flow INTO a
# player zone (TransferCardOp.to_target, MoveCardsOp.to_player) — never as a
# general player Target, because it is meaningless without a card to own.
# The reducer (engine.reducers._resolve_card_owner) resolves it per moved card:
#   1. the player whose hand/in_play zone currently holds the card;
#   2. else the actor of the card's most recent "play" history event (the
#      hand the card was played FROM — a played-then-discarded card still
#      belongs to whoever played it);
#   3. else the card dict's recorded ``creator_id`` when it names a live
#      player (seed/blank cards carry a source label there instead).
# A card with no resolvable owner is a logged per-card no-op.
CARD_OWNER = "card_owner"


def _validate_card_flow_target(value: str) -> str:
    if value == CARD_OWNER:
        return value
    return _validate_target(value)


CardFlowTarget = Annotated[str, AfterValidator(_validate_card_flow_target)]

# CardTargets that mean "the actor picks a card at play time" — their presence
# flips EffectProgram.requires_choice, mirroring the player _CHOICE_TARGETS.
_CHOICE_CARD_TARGETS: frozenset[str] = frozenset({"chosen_card"})

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
    "chosen_player": "chooser",
    "all": "all",
    "all_players": "all",
    "everyone": "all",
    # everyone except the actor
    "all_others": "all_others",
    "others": "all_others",
    # the player who acts immediately after the actor in turn_order — see
    # engine.loop._next_in_order / advance_turn, which both step +1 through
    # state.effective_turn_order(); that is exactly left_neighbor's formula.
    "next_player": "left_neighbor",
    # the player who acted immediately before the actor (turn-order predecessor).
    "previous_player": "right_neighbor",
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
    stripped = raw.strip()
    if stripped.startswith(_TARGET_PREFIXES):
        return stripped  # ids/condition keys are case-sensitive — no lowering
    key = stripped.lower()
    if key in _VALID_TARGETS:
        return key
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


def is_known_target(raw: str) -> bool:
    """True if ``raw`` normalizes to a valid runtime Target or a known authoring alias.

    Used by callers (see ``engine.compile``) to distinguish an explicit-but-unknown
    target — which ``map_authoring_target(..., default=...)`` would silently paper
    over — from an omitted one, so drift can be logged instead of swallowed.
    """
    stripped = raw.strip()
    if stripped.startswith(_TARGET_PREFIXES):
        return True
    key = stripped.lower()
    return key in _VALID_TARGETS or key in _AUTHORING_TARGET_ALIASES


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


class ScrambleOrderOp(BaseModel):
    """Randomize the turn rotation order (state.turn_order)."""

    op: Literal["scramble_order"] = "scramble_order"


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


class RollDieOp(BaseModel):
    """Roll ``count`` dice of ``sides`` sides with the engine's injected rng.

    The roll TOTAL feeds ``outcome`` (points/draws applied to the resolved
    ``target``); ``outcome="none"`` is a bare roll — pure theater, or a value
    later steps read back from the recorded dice_roll history event.

    ``result`` pre-resolves the roll: when set, the reducer uses these values
    instead of rolling. The sandbox/replay path fills it in so revalidation
    replays the SAME roll deterministically instead of re-rolling.
    """

    op: Literal["roll_die"] = "roll_die"
    sides: int = Field(default=6, ge=2, le=1000)
    count: int = Field(default=1, ge=1, le=10)
    target: Target = "self"
    outcome: Literal["add_points", "subtract_points", "draw_cards", "none"] = "none"
    result: list[int] | None = None

    @model_validator(mode="after")
    def _result_matches_roll(self) -> RollDieOp:
        if self.result is None:
            return self
        if len(self.result) != self.count:
            raise ValueError(f"roll_die result has {len(self.result)} values but count={self.count}")
        for value in self.result:
            if not 1 <= value <= self.sides:
                raise ValueError(f"roll_die result value {value} outside 1..{self.sides}")
        return self


class DiscardRandomOp(BaseModel):
    """Discard ``count`` random cards from each resolved target's hand.

    Deliberately NOT pre-resolved by the sandbox (unlike ``roll_die``):
    snippets cannot read other players' hands or observe the picks, so the
    reducer draws them at reduce time from the injected rng — no branch/desync
    risk. A target holding fewer than ``count`` cards discards their whole
    hand.
    """

    op: Literal["discard_random"] = "discard_random"
    target: Target = "self"
    count: int = Field(default=1, ge=1, le=10)


# Zones move_cards can address. "exile" is the op-level spelling of the
# GameState "exiled" pile (the reducer maps it); every other name matches the
# GameState zone taxonomy (see models.game_state.GameState docstring).
Zone = Literal["deck", "discard", "hand", "in_play", "center", "exile"]

_PLAYER_ZONES: frozenset[str] = frozenset({"hand", "in_play"})


def validate_move_cards_source(
    card_target: str | None,
    from_zone: str | None,
    selector: str,
    count: int,
) -> None:
    """Enforce the move_cards source contract shared by the op model and the
    sandbox API: at least one of card_target/from_zone, and selector/count
    stay at their defaults in addressed+zone mode (they are not applied)."""
    if card_target is None and from_zone is None:
        raise ValueError("move_cards requires card_target or from_zone (or both)")
    if card_target is not None and from_zone is not None:
        if selector != "top" or count != 1:
            raise ValueError("move_cards with card_target and from_zone does not apply selector/count")


class MoveCardsOp(BaseModel):
    """Move cards between zones without playing them (mill, tuck, unbury…).

    Source is an explicit ``card_target`` (cards move from wherever they
    currently live), a ``from_zone`` with a ``selector``/``count``
    (``selector="random"`` picks with the engine's injected rng at reduce
    time — never pre-resolved, so snippets learn nothing about hidden cards),
    or BOTH: the addressed card(s) move only if they actually sit in the
    declared zone ("exile the chosen card, but only from the center") —
    ``selector``/``count`` are not applied in that mode and must stay at
    their defaults. "top" of the deck is the next card drawn; "top" of any
    other zone is its most recently added card. ``from_player``/``to_player``
    are required exactly when the corresponding zone is per-player
    (hand/in_play). ``to_position`` applies only when the destination is the
    deck: "top", "bottom", or "shuffle" (a random position per card).
    ``to_player`` also accepts "card_owner" — each moved card routes to its
    own owner (see the CardFlowTarget notes above).
    """

    op: Literal["move_cards"] = "move_cards"
    card_target: CardTarget | None = None
    from_zone: Zone | None = None
    selector: Literal["top", "bottom", "all", "random"] = "top"
    count: int = Field(default=1, ge=1, le=50)
    from_player: Target | None = None
    to_zone: Zone
    to_position: Literal["top", "bottom", "shuffle"] = "top"
    to_player: CardFlowTarget | None = None

    @model_validator(mode="after")
    def _source_shape_and_player_zones(self) -> MoveCardsOp:
        validate_move_cards_source(self.card_target, self.from_zone, self.selector, self.count)
        if self.from_zone in _PLAYER_ZONES and self.from_player is None:
            raise ValueError(f"move_cards from_zone {self.from_zone!r} requires from_player")
        if self.from_player is not None and self.from_zone not in _PLAYER_ZONES:
            raise ValueError("move_cards from_player is only valid with from_zone 'hand' or 'in_play'")
        if self.to_zone in _PLAYER_ZONES and self.to_player is None:
            raise ValueError(f"move_cards to_zone {self.to_zone!r} requires to_player")
        if self.to_player is not None and self.to_zone not in _PLAYER_ZONES:
            raise ValueError("move_cards to_player is only valid with to_zone 'hand' or 'in_play'")
        return self


class ShuffleDeckOp(BaseModel):
    """Shuffle the draw pile in place with the engine's injected rng.

    ``include_discard=True`` is the classic reshuffle: the discard pile is
    folded into the deck before shuffling and left empty.
    """

    op: Literal["shuffle_deck"] = "shuffle_deck"
    include_discard: bool = False


class DestroyCardOp(BaseModel):
    op: Literal["destroy_card"] = "destroy_card"
    # Back-compat: the raw single card id to remove (from hand / in_play /
    # center). Still honoured when ``card_target`` is not set.
    card_id: str | None = None
    # Preferred: a CardTarget axis resolved by the reducer. When set, it takes
    # precedence over ``card_id`` and may resolve to MANY cards.
    card_target: CardTarget | None = None


class TransferCardOp(BaseModel):
    """Move selected cards from their current zone into a player's hand.

    ``to_target`` names ONE player — or "card_owner", which routes each
    resolved card to its own owner (see the CardFlowTarget notes above), so
    "return the last card played to its owner's hand" is
    ``transfer_card(card_target="last_played", to_target="card_owner")``.
    """

    op: Literal["transfer_card"] = "transfer_card"
    card_target: CardTarget = "this"
    to_target: CardFlowTarget = "self"


class RevealHandOp(BaseModel):
    """Reveal (or conceal) a hand: ``target`` = whose hand, ``to`` = who may see it.

    ``persistent=False`` is a one-shot peek: no state change — the Room pushes
    the hand's contents to the resolved audience once (lost on reconnect).
    ``persistent=True`` writes the structural Player visibility fields:
    ``to="all"`` sets ``hand_public`` (face-up play); any other audience adds
    the resolved viewers to ``hand_revealed_to``. ``mode="conceal"`` reverses a
    persistent reveal: ``to="all"`` clears both fields, a narrower audience is
    removed from ``hand_revealed_to``.
    """

    op: Literal["reveal_hand"] = "reveal_hand"
    target: Target = "self"  # whose hand
    to: Target = "all"  # who may see it
    persistent: bool = False  # False = one-shot reveal
    mode: Literal["reveal", "conceal"] = "reveal"  # conceal = un-reveal


class EliminatePlayerOp(BaseModel):
    """Knock the targeted player(s) out of the game; everyone else plays on.

    The reducer sets the structural ``Player.eliminated`` flag and discards the
    player's hand; their ``in_play`` cards (and any hooks/rules those set)
    stay in effect. The turn loop skips eliminated players and win scoring
    ignores them — with win_condition "last_standing", the last non-eliminated
    player wins. The last active player can never be eliminated (guarded
    no-op), so the game always has someone standing.
    """

    op: Literal["eliminate_player"] = "eliminate_player"
    target: Target


class SetWinConditionOp(BaseModel):
    op: Literal["set_win_condition"] = "set_win_condition"
    kind: Literal["highest_points", "lowest_points", "first_to", "empty_hand", "last_standing", "none"]
    threshold: int | None = None


class SetConditionOp(BaseModel):
    """Write one key in each targeted player's open ``conditions`` bag.

    The generic writer behind card-invented statuses ("poisoned", "confused",
    …): reserved keys skip_next/extra_turn keep their loop semantics; any
    other key is free-form state the UI/agent/hooks can read. ``value=None``
    removes the key (and any TTL it had).

    ``duration_turns`` makes the status expire on its own: the TTL ticks down
    at the start of each targeted player's turn and the condition is removed
    when it reaches 0. Re-setting a key with a new duration restarts the
    clock; re-setting it WITHOUT one clears any previous TTL, making the
    condition persistent again.
    """

    op: Literal["set_condition"] = "set_condition"
    target: Target = "self"
    key: str
    value: Any = None
    duration_turns: int | None = Field(default=None, ge=1)

    @field_validator("key")
    @classmethod
    def _normalize_key(cls, value: str) -> str:
        key = normalize_condition_key(value)
        if not key:
            raise ValueError("condition key cannot be blank")
        return key


class SetCardAttributeOp(BaseModel):
    """Write one key in each targeted card's open ``attributes`` bag.

    Attributes are card-invented metadata (e.g. a color assigned to every
    card for an Uno variant) addressable later via the "attr:<k>=<v>"
    CardTarget form. ``value=None`` removes the key.
    """

    op: Literal["set_card_attribute"] = "set_card_attribute"
    card_target: CardTarget = "this"
    key: str
    value: Any = None


class CreateCardOp(BaseModel):
    """Register ``count`` copies of a new card and route them somewhere.

    Created cards carry structured authoring ``ops`` (compiled deterministically
    when later drawn/played — no LLM round-trip) plus optional ``attributes``.
    ``destination``: "deck_shuffle" (random deck positions), "deck_top",
    "deck_bottom", "hand", "discard", or "center". When "hand", copies go to
    the resolved ``target`` player(s) — defaulting to "self" (the actor), but
    any Target works, so ``destination="hand", target="id:<player_id>"`` hands
    cards to a specific player (e.g. an auction winner). Capped at 10 copies
    per op so one card cannot flood the game.
    """

    op: Literal["create_card"] = "create_card"
    title: str
    description: str = ""
    ops: list[dict[str, Any]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    destination: Literal["deck_shuffle", "deck_top", "deck_bottom", "hand", "discard", "center"] = "deck_shuffle"
    target: Target = "self"
    count: int = Field(default=1, ge=1, le=10)


class RegisterHookOp(BaseModel):
    """Register a persistent sandboxed hook that fires on a named game event.

    The single pipeline for dynamic behavior registration: the reducer
    validates ``code`` against the sandbox rules and appends a serialized
    ``HookSpec`` to ``GameState.hooks`` (capped at 3 hooks per source card).
    """

    op: Literal["register_hook"] = "register_hook"
    event: str  # a GameEvent value, e.g. "on_turn_start"
    scope: Literal["player", "center"] = "center"
    code: str
    title: str = Field(default="", max_length=300)
    condition_keys: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("condition_keys")
    @classmethod
    def _normalize_condition_keys(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            key = normalize_condition_key(value)
            if key and key not in normalized:
                normalized.append(key)
        return normalized


class UnregisterHookOp(BaseModel):
    """Remove every hook registered by ``source_card_id``."""

    op: Literal["unregister_hook"] = "unregister_hook"
    source_card_id: str


class SetRuleOp(BaseModel):
    """Write one path in ``GameState.rules`` (the mutable rules-as-data bag).

    Recognized paths: "draw", "play", "skip_predicate", the nested
    "end_condition[.type|.threshold]" / "win_condition[.kind|.threshold]" /
    "cannot_play[.<key>]" forms, and free-form "extra.<key>" entries. The
    reducer validates the resulting Rules model; unknown paths or invalid
    values raise (surfaced like an unresolvable target).
    """

    op: Literal["set_rule"] = "set_rule"
    path: str
    value: Any = None


class CustomNoteOp(BaseModel):
    """A no-op that logs a flavour message; useful for cards that only register hooks."""

    op: Literal["custom_note"] = "custom_note"
    note: str


class CounterPlayOp(BaseModel):
    """Reaction control-flow op: decides the fate of the play being reacted to.

    Only meaningful inside a reaction window (canonical trigger "on_reaction");
    the Room consumes it there — like reject_play in ON_VALIDATE_PLAY hooks —
    and a defensive no-op reducer logs and ignores it anywhere else.

    Modes:
      negate     — the pending card's effect never happens; the card discards.
      steal_hand — the effect never happens; the pending card goes to the
                   reactor's hand instead.
      redirect   — the pending effect resolves as if the reactor had played it.
    """

    op: Literal["counter_play"] = "counter_play"
    mode: Literal["negate", "steal_hand", "redirect"] = "negate"


class EndGameOp(BaseModel):
    """Ends the game immediately, independent of deck state or win_condition.

    The reducer only sets ``rules.end_condition`` to ``{type: "now"}``; Room
    notices the met end condition and routes to ``_end_game`` (see
    ``board.rooms.room``).

    ``winner`` names who wins the ended game ("You win the game" cards resolve
    to the card player via "self"). None keeps normal win-condition
    evaluation, so a plain "end the game" card crowns the current leader.
    """

    op: Literal["end_game"] = "end_game"
    winner: Target | None = None
    winners: list[Target] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exclusive_winner_shapes(self) -> EndGameOp:
        if self.winner is not None and self.winners:
            raise ValueError("end_game accepts winner or winners, not both")
        return self


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
        ScrambleOrderOp,
        ChangeDrawCountOp,
        StealPointsOp,
        DrawCardsOp,
        RollDieOp,
        DiscardRandomOp,
        DestroyCardOp,
        MoveCardsOp,
        ShuffleDeckOp,
        TransferCardOp,
        RevealHandOp,
        EliminatePlayerOp,
        SetWinConditionOp,
        SetRuleOp,
        RegisterHookOp,
        UnregisterHookOp,
        SetConditionOp,
        SetCardAttributeOp,
        CreateCardOp,
        CustomNoteOp,
        CounterPlayOp,
        EndGameOp,
    ],
    Field(discriminator="op"),
]

# Player targets that mean "the actor picks at play time" — their presence on
# an op flips EffectProgram.requires_choice.
_CHOICE_TARGETS: frozenset[str] = frozenset({"chooser", "target_player"})

# Op fields that hold a player Target address.
_TARGET_FIELDS: tuple[str, ...] = ("target", "from_target", "to_target", "to", "winner", "from_player", "to_player")


def op_choice_axes(op: Op) -> tuple[bool, bool]:
    """(needs_player_choice, needs_card_choice) — this op's play-time prompt axes.

    The player axis is any player-target field equal to "chooser"/
    "target_player" (including EndGameOp.winners); the card axis is a
    card_target of "chosen_card". The ONE choice-axis detector shared by
    program compilation and the Room's prompt_choice flow.
    """
    needs_player = any(
        isinstance(value, str) and value in _CHOICE_TARGETS
        for value in (getattr(op, field, None) for field in _TARGET_FIELDS)
    )
    if isinstance(op, EndGameOp) and any(target in _CHOICE_TARGETS for target in op.winners):
        needs_player = True
    card_target = getattr(op, "card_target", None)
    needs_card = isinstance(card_target, str) and card_target in _CHOICE_CARD_TARGETS
    return needs_player, needs_card


def op_requires_choice(op: Op) -> bool:
    """True if this op needs a play-time choice from the actor.

    Choice-requiring ops are only resolvable through the prompt_choice flow —
    contexts without one (snippet diffs, hooks) must reject them up front.
    """
    return any(op_choice_axes(op))


# ---------------------------------------------------------------------------
# Op-list normalization
# ---------------------------------------------------------------------------
def _flatten_op_args(ops: Any) -> Any:
    """Lower the authoring ``{"op": X, "args": {...}}`` shape to flat runtime ops.

    The runtime Op union is flat (``{"op": "destroy_card", "card_target": ...}``),
    but the LLM interpreter frequently emits the *authoring* vocabulary's nested
    ``args`` wrapper into ``program.ops`` — a shape Pydantic would otherwise accept
    while silently discarding the ``args`` key. For all-optional ops
    (destroy_card, transfer_card) that produced a no-arg op that resolved to
    nothing and applied invisibly. Merging ``args`` up keeps both shapes valid.
    """
    if not isinstance(ops, list):
        return ops
    flattened = []
    for entry in ops:
        if isinstance(entry, dict) and isinstance(entry.get("args"), dict):
            merged = {k: v for k, v in entry.items() if k != "args"}
            # Flat sibling keys win over args (explicit runtime shape takes
            # precedence over the wrapper) so a mixed payload is not clobbered.
            merged = {**entry["args"], **merged}
            flattened.append(merged)
        else:
            flattened.append(entry)
    return flattened


# ---------------------------------------------------------------------------
# EffectProgram: the full payload attached to a card play
# ---------------------------------------------------------------------------
class EffectProgram(BaseModel):
    ops: list[Op] = Field(default_factory=list)
    # True when any op needs a play-time choice from the actor: a player
    # "chooser"/"target_player" target OR a "chosen_card" CardTarget. Set when
    # the agent's emitted program is compiled (see engine.compile).
    requires_choice: bool = False

    _flatten_ops = field_validator("ops", mode="before")(_flatten_op_args)


class OpsStep(BaseModel):
    kind: Literal["ops"] = "ops"
    ops: list[Op] = Field(default_factory=list, max_length=50)

    _flatten_ops = field_validator("ops", mode="before")(_flatten_op_args)


class SnippetStep(BaseModel):
    kind: Literal["snippet"] = "snippet"
    code: str = Field(max_length=65_536)
    explanation: str = ""


class InteractionStep(BaseModel):
    kind: Literal["interaction"] = "interaction"
    result_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    request: InteractionDescriptor
    input_refs: dict[str, InteractionResultRef] = Field(default_factory=dict, max_length=20)


ResolutionStep = Annotated[Union[OpsStep, SnippetStep, InteractionStep], Field(discriminator="kind")]


class ResolutionPlan(BaseModel):
    steps: list[ResolutionStep] = Field(default_factory=list, max_length=MAX_RESOLUTION_STEPS)

    @model_validator(mode="after")
    def ordered_interaction_references(self):
        available: set[str] = set()
        interaction_count = 0
        for step in self.steps:
            if not isinstance(step, InteractionStep):
                continue
            interaction_count += 1
            if step.result_key in available:
                raise ValueError(f"duplicate interaction result_key: {step.result_key}")
            missing = {ref.result_key for ref in step.input_refs.values()} - available
            if missing:
                raise ValueError(f"interaction refs must point to prior results: {sorted(missing)}")
            if (
                isinstance(step.request, ChoiceInteraction)
                and not step.request.options
                and "options" not in step.input_refs
            ):
                raise ValueError("choice interaction requires options or an options input_ref")
            if (
                isinstance(step.request, CardPickInteraction)
                and not step.request.card_ids
                and not step.request.from_hand
                and step.request.from_deck_top is None
                and "card_ids" not in step.input_refs
            ):
                raise ValueError(
                    "card_pick interaction requires card_ids, from_hand, from_deck_top, or a card_ids input_ref"
                )
            available.add(step.result_key)
        if interaction_count > MAX_INTERACTION_STEPS:
            raise ValueError(f"resolution plan exceeds {MAX_INTERACTION_STEPS} interaction barriers")
        interaction_data = [step.model_dump(mode="json") for step in self.steps if isinstance(step, InteractionStep)]
        if len(json.dumps(interaction_data, default=str).encode()) > MAX_INTERACTION_PLAN_BYTES:
            raise ValueError(f"interaction plan exceeds {MAX_INTERACTION_PLAN_BYTES} bytes")
        if len(json.dumps(self.model_dump(mode="json"), default=str).encode()) > MAX_RESOLUTION_PLAN_BYTES:
            raise ValueError(f"resolution plan exceeds {MAX_RESOLUTION_PLAN_BYTES} bytes")
        return self

    def operations(self) -> list[Op]:
        return [op for step in self.steps if isinstance(step, OpsStep) for op in step.ops]

    @property
    def requires_choice(self) -> bool:
        return any(op_requires_choice(op) for op in self.operations())
