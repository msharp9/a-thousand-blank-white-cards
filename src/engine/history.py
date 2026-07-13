"""Structured, privacy-safe history recording and queries."""

from __future__ import annotations

from typing import Any

from engine.events import HookContext
from models.effects import Op
from models.game_state import GameState, HistoryEvent, HistoryKind


def append_history_event(
    state: GameState,
    kind: HistoryKind,
    *,
    actor_id: str | None = None,
    target_player_ids: list[str] | None = None,
    card_id: str | None = None,
    amount: int | None = None,
    source: str | None = None,
    rule_path: str | None = None,
) -> GameState:
    return state.with_history_event(
        HistoryEvent(
            sequence=1,
            kind=kind,
            actor_id=actor_id,
            target_player_ids=list(target_player_ids or []),
            card_id=card_id,
            amount=amount,
            source=source,
            rule_path=rule_path,
        )
    )


def record_draw(
    state: GameState,
    player_id: str,
    amount: int,
    *,
    actor_id: str | None = None,
    card_id: str | None = None,
    source: str = "turn",
) -> GameState:
    if amount <= 0:
        return state
    return append_history_event(
        state,
        "draw",
        actor_id=actor_id or player_id,
        target_player_ids=[player_id],
        card_id=card_id,
        amount=amount,
        source=source,
    )


def record_game_end(
    state: GameState,
    winner_ids: list[str] | None = None,
    *,
    actor_id: str | None = None,
    source: str,
) -> GameState:
    if any(event.kind == "game_end" for event in state.history_events):
        return state
    return append_history_event(
        state,
        "game_end",
        actor_id=actor_id,
        target_player_ids=list(winner_ids or []),
        source=source,
    )


def record_op_history(before: GameState, after: GameState, op: Op, ctx: HookContext) -> GameState:
    if op.op == "draw_cards":
        for player in after.players:
            previous = before.get_player(player.id)
            amount = len(player.hand) - len(previous.hand)
            if amount > 0:
                after = record_draw(
                    after,
                    player.id,
                    amount,
                    actor_id=ctx.actor_id,
                    card_id=ctx.card_id,
                    source="effect",
                )
        return after

    if op.op in {"add_points", "subtract_points", "set_points", "steal_points"}:
        for player in after.players:
            amount = player.score - before.get_player(player.id).score
            if amount:
                after = append_history_event(
                    after,
                    "score_change",
                    actor_id=ctx.actor_id,
                    target_player_ids=[player.id],
                    card_id=ctx.card_id,
                    amount=amount,
                    source=op.op,
                )
        return after

    if op.op in {"register_hook", "unregister_hook"} and after.hooks != before.hooks:
        return append_history_event(
            after,
            "rule_change",
            actor_id=ctx.actor_id,
            card_id=ctx.card_id,
            source=op.op,
            rule_path="hooks",
        )

    if op.op in {"change_draw_count", "set_win_condition", "set_rule", "end_game"} and after.rules != before.rules:
        path = getattr(op, "path", None)
        if op.op == "change_draw_count":
            path = "draw"
        elif op.op == "set_win_condition":
            path = "win_condition"
        elif op.op == "end_game":
            path = "end_condition"
        return append_history_event(
            after,
            "rule_change",
            actor_id=ctx.actor_id,
            card_id=ctx.card_id,
            source=op.op,
            rule_path=path,
        )
    return after


def public_history(
    state: GameState,
    *,
    kind: str | None = None,
    player_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded = max(1, min(limit, 200))
    events = state.history_events
    if kind is not None:
        events = [event for event in events if event.kind == kind]
    if player_id is not None:
        events = [event for event in events if event.actor_id == player_id or player_id in event.target_player_ids]
    return [event.model_dump() for event in events[-bounded:]]


def draw_totals(state: GameState) -> dict[str, int]:
    totals = {player.id: 0 for player in state.players}
    for event in state.history_events:
        if event.kind != "draw" or event.amount is None:
            continue
        for player_id in event.target_player_ids:
            if player_id in totals:
                totals[player_id] += event.amount
    return totals


__all__ = [
    "append_history_event",
    "draw_totals",
    "public_history",
    "record_draw",
    "record_game_end",
    "record_op_history",
]
