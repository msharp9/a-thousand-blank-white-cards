"""engine.reducers — pure op reducers, target resolution, and dispatch.

Every reducer takes ``(state, op, ctx)`` and returns a NEW GameState; reducers
never mutate the state passed in. ``apply_op`` dispatches on ``op.op`` via the
``_REDUCERS`` table.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.events import HookContext
from models.effects import (
    AddPointsOp,
    CardTarget,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    ExtraTurnOp,
    Op,
    ReverseOrderOp,
    SetPointsOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
    Target,
)
from models.game_state import GameState, WinCondition


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------
def _resolve_targets(target: Target, ctx: HookContext, state: GameState) -> list[str]:
    """Resolve a Target address into a concrete list of player ids."""
    players = state.players
    n = len(players)
    actor_idx = next(i for i, p in enumerate(players) if p.id == ctx.actor_id)

    match target:
        case "self":
            return [ctx.actor_id]
        case "left_neighbor":
            return [players[(actor_idx - state.direction) % n].id]
        case "right_neighbor":
            return [players[(actor_idx + state.direction) % n].id]
        case "all":
            return [p.id for p in players]
        case "all_others":
            return [p.id for p in players if p.id != ctx.actor_id]
        case "chooser" | "target_player":
            if ctx.chosen_player_id is None:
                raise ValueError(f"Target {target!r} requires ctx.chosen_player_id")
            return [ctx.chosen_player_id]
        case "player_with_most_points":
            return [max(players, key=lambda p: p.score).id]
        case "player_with_least_points":
            return [min(players, key=lambda p: p.score).id]
        case "player_with_empty_hand":
            return [p.id for p in players if not p.hand]
        case _:
            raise ValueError(f"Unknown target: {target!r}")


def _resolve_card_targets(card_target: CardTarget, ctx: HookContext, state: GameState) -> list[str]:
    """Resolve a CardTarget address into a concrete list of card ids.

    This is the CARD analogue of ``_resolve_targets`` (which resolves players).

    - ``"this"``        -> ``[ctx.card_id]`` (the card being played). If there is
                           no card in context, resolves to an empty list.
    - ``"chosen_card"`` -> ``[ctx.chosen_card_id]``; raises ValueError when the
                           actor made no choice, mirroring the "chooser" player
                           behavior.
    - ``"all_in_play"`` -> every card in every player's in-play zone.
    - ``"all_in_hand"`` -> the ACTOR's own hand (first-cut decision). Whose-hand
                           composition is a documented future extension.
    """
    match card_target:
        case "this":
            return [ctx.card_id] if ctx.card_id is not None else []
        case "chosen_card":
            if ctx.chosen_card_id is None:
                raise ValueError("CardTarget 'chosen_card' requires ctx.chosen_card_id")
            return [ctx.chosen_card_id]
        case "all_in_play":
            return state.cards_in_play()
        case "all_in_hand":
            return list(state.get_player(ctx.actor_id).hand)
        case _:
            raise ValueError(f"Unknown card target: {card_target!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _update_player_score(state: GameState, player_id: str, new_score: int) -> GameState:
    """Return a copy of state with one player's score set to new_score."""
    new_players = [p.model_copy(update={"score": new_score}) if p.id == player_id else p for p in state.players]
    return state.model_copy(update={"players": new_players})


# ---------------------------------------------------------------------------
# Point reducers
# ---------------------------------------------------------------------------
def _reduce_add_points(state: GameState, op: AddPointsOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = _update_player_score(state, pid, state.get_player(pid).score + op.amount)
    return state


def _reduce_subtract_points(state: GameState, op: SubtractPointsOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = _update_player_score(state, pid, state.get_player(pid).score - op.amount)
    return state


def _reduce_set_points(state: GameState, op: SetPointsOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = _update_player_score(state, pid, op.amount)
    return state


# ---------------------------------------------------------------------------
# Turn-flow reducers (per-player conditions)
# ---------------------------------------------------------------------------
def _reduce_skip_turn(state: GameState, op: SkipTurnOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = state.with_condition(pid, "skip_next", True)
    return state


def _reduce_extra_turn(state: GameState, op: ExtraTurnOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = state.with_condition(pid, "extra_turn", True)
    return state


def _reduce_reverse_order(state: GameState, op: ReverseOrderOp, ctx: HookContext) -> GameState:
    return state.model_copy(update={"direction": -state.direction})


def _reduce_change_draw_count(state: GameState, op: ChangeDrawCountOp, ctx: HookContext) -> GameState:
    return state.model_copy(update={"draw_count": op.amount})


# ---------------------------------------------------------------------------
# Steal / cards / win-condition / note
# ---------------------------------------------------------------------------
def _reduce_steal_points(state: GameState, op: StealPointsOp, ctx: HookContext) -> GameState:
    from_ids = _resolve_targets(op.from_target, ctx, state)
    to_ids = _resolve_targets(op.to_target, ctx, state)
    for from_id in from_ids:
        stolen = min(op.amount, state.get_player(from_id).score)
        state = _update_player_score(state, from_id, state.get_player(from_id).score - stolen)
        for to_id in to_ids:
            state = _update_player_score(state, to_id, state.get_player(to_id).score + stolen)
    return state


def _reduce_draw_cards(state: GameState, op: DrawCardsOp, ctx: HookContext) -> GameState:
    deck = list(state.deck)
    new_players = list(state.players)
    for pid in _resolve_targets(op.target, ctx, state):
        drawn = deck[: op.amount]
        deck = deck[op.amount :]
        idx = next(i for i, p in enumerate(new_players) if p.id == pid)
        player = new_players[idx]
        new_players[idx] = player.model_copy(update={"hand": [*player.hand, *drawn]})
    return state.model_copy(update={"players": new_players, "deck": deck})


def _reduce_destroy_card(state: GameState, op: DestroyCardOp, ctx: HookContext) -> GameState:
    """Remove one or more cards from wherever they live and send them to discard.

    Resolution precedence (non-breaking migration):
      - If ``op.card_target`` is set, resolve it via ``_resolve_card_targets``
        (may yield MANY card ids).
      - Otherwise fall back to the legacy single ``op.card_id``.

    Each resolved id is scrubbed from every player's ``hand`` and ``in_play``
    zones and from the shared ``center`` zone (house_rules), then appended to the
    discard pile (once, no duplicates).
    """
    if op.card_target is not None:
        card_ids = _resolve_card_targets(op.card_target, ctx, state)
    elif op.card_id is not None:
        card_ids = [op.card_id]
    else:
        card_ids = []

    if not card_ids:
        return state

    targets = set(card_ids)
    new_players = [
        p.model_copy(
            update={
                "hand": [c for c in p.hand if c not in targets],
                "in_play": [c for c in p.in_play if c not in targets],
            }
        )
        if any(c in targets for c in (*p.hand, *p.in_play))
        else p
        for p in state.players
    ]
    house_rules = [c for c in state.house_rules if c not in targets]
    discard = list(state.discard)
    for cid in card_ids:
        if cid not in discard:
            discard.append(cid)
    return state.model_copy(update={"players": new_players, "house_rules": house_rules, "discard": discard})


def _reduce_set_win_condition(state: GameState, op: SetWinConditionOp, ctx: HookContext) -> GameState:
    wc = WinCondition(kind=op.kind, threshold=op.threshold)
    return state.model_copy(update={"win_condition": wc})


def _reduce_custom_note(state: GameState, op: CustomNoteOp, ctx: HookContext) -> GameState:
    return state.with_log(f"[note] {op.note}")


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------
_REDUCERS: dict[str, Callable[[GameState, Op, HookContext], GameState]] = {
    "add_points": _reduce_add_points,
    "subtract_points": _reduce_subtract_points,
    "set_points": _reduce_set_points,
    "skip_turn": _reduce_skip_turn,
    "extra_turn": _reduce_extra_turn,
    "reverse_order": _reduce_reverse_order,
    "change_draw_count": _reduce_change_draw_count,
    "steal_points": _reduce_steal_points,
    "draw_cards": _reduce_draw_cards,
    "destroy_card": _reduce_destroy_card,
    "set_win_condition": _reduce_set_win_condition,
    "custom_note": _reduce_custom_note,
}


def apply_op(state: GameState, op: Op, ctx: HookContext) -> GameState:
    """Dispatch a single op to its reducer, returning a new GameState."""
    return _REDUCERS[op.op](state, op, ctx)


__all__ = ["apply_op"]
