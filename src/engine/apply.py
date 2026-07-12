"""engine.apply — apply_effect bridges pure reducers with the event bus.

Iterates an EffectProgram's ops through apply_op, emitting ON_SCORE_CHANGE
after each op that changes any player's score so persistent hooks can react.
"""

from __future__ import annotations

import random

from engine.events import EventBus, GameEvent, HookContext
from engine.reducers import apply_op
from models.effects import EffectProgram
from models.game_state import GameState

_bus = EventBus()  # module-level singleton; tests can inject their own


def apply_effect(
    state: GameState,
    program: EffectProgram,
    ctx: HookContext,
    *,
    bus: EventBus | None = None,
    rng: random.Random | None = None,
) -> GameState:
    """Apply all ops in `program` to `state`.

    After each op that touches player scores, emits ON_SCORE_CHANGE so
    persistent hooks can react. Original `state` is never mutated. ``rng`` is
    forwarded to ``apply_op`` (only consumed by ``scramble_order``).
    """
    active_bus = bus or _bus
    score_ops = {"add_points", "subtract_points", "set_points", "steal_points"}

    for op in program.ops:
        is_score_op = op.op in score_ops
        # Only snapshot scores for scoring ops — the diff is unused otherwise.
        before_scores = {p.id: p.score for p in state.players} if is_score_op else {}
        state = apply_op(state, op, ctx, rng=rng)

        if is_score_op:
            changed_pids = [pid for pid in before_scores if before_scores[pid] != state.get_player(pid).score]
            if changed_pids:
                score_ctx = HookContext(
                    event=GameEvent.ON_SCORE_CHANGE,
                    actor_id=ctx.actor_id,
                    card_id=ctx.card_id,
                    target_player_ids=changed_pids,
                    extra={"op": op.op},
                )
                state = active_bus.emit(GameEvent.ON_SCORE_CHANGE, state, score_ctx)

    return state
