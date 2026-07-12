"""models.effects — immediate Op discriminated union, Target, EffectProgram."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union, get_args

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
CardTarget = Literal[
    "this",
    "chosen_card",
    "all_in_play",
    "all_in_hand",
]

# Valid CardTarget literals, derived from the Literal so it never drifts.
_VALID_CARD_TARGETS: frozenset[str] = frozenset(get_args(CardTarget))

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
    # state.effective_turn_order(); that is exactly right_neighbor's formula.
    "next_player": "right_neighbor",
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


def is_known_target(raw: str) -> bool:
    """True if ``raw`` normalizes to a valid runtime Target or a known authoring alias.

    Used by callers (see ``engine.compile``) to distinguish an explicit-but-unknown
    target — which ``map_authoring_target(..., default=...)`` would silently paper
    over — from an omitted one, so drift can be logged instead of swallowed.
    """
    key = raw.strip().lower()
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


class DestroyCardOp(BaseModel):
    op: Literal["destroy_card"] = "destroy_card"
    # Back-compat: the raw single card id to remove (from hand / in_play /
    # center). Still honoured when ``card_target`` is not set.
    card_id: str | None = None
    # Preferred: a CardTarget axis resolved by the reducer. When set, it takes
    # precedence over ``card_id`` and may resolve to MANY cards.
    card_target: CardTarget | None = None


class SetWinConditionOp(BaseModel):
    op: Literal["set_win_condition"] = "set_win_condition"
    kind: Literal["highest_points", "lowest_points", "first_to", "empty_hand", "last_standing", "none"]
    threshold: int | None = None


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
        DestroyCardOp,
        SetWinConditionOp,
        SetRuleOp,
        CustomNoteOp,
        EndGameOp,
    ],
    Field(discriminator="op"),
]

# Player targets that mean "the actor picks at play time" — their presence on
# an op flips EffectProgram.requires_choice.
_CHOICE_TARGETS: frozenset[str] = frozenset({"chooser", "target_player"})

# Op fields that hold a player Target address.
_TARGET_FIELDS: tuple[str, ...] = ("target", "from_target", "to_target", "winner")


def op_requires_choice(op: Op) -> bool:
    """True if this op needs a play-time choice from the actor.

    Any player-target field equal to "chooser"/"target_player", or a
    card_target of "chosen_card". Choice-requiring ops are only resolvable
    through the prompt_choice flow — contexts without one (snippet diffs,
    hooks) must reject them up front.
    """
    for field in _TARGET_FIELDS:
        value = getattr(op, field, None)
        if isinstance(value, str) and value in _CHOICE_TARGETS:
            return True
    card_target = getattr(op, "card_target", None)
    return isinstance(card_target, str) and card_target in _CHOICE_CARD_TARGETS


# ---------------------------------------------------------------------------
# EffectProgram: the full payload attached to a card play
# ---------------------------------------------------------------------------
class EffectProgram(BaseModel):
    ops: list[Op] = Field(default_factory=list)
    # True when any op needs a play-time choice from the actor: a player
    # "chooser"/"target_player" target OR a "chosen_card" CardTarget. Set when
    # the agent's emitted program is compiled (see engine.compile).
    requires_choice: bool = False
