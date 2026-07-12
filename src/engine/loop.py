"""engine.loop — the turn loop.

Wires the pure reducers and the event bus into a playable turn cycle:

- ``draw_step``   — draw the active player's cards for the turn.
- ``advance_turn``— move ``turn_index`` respecting direction, skip-next,
  extra-turn, and a pluggable skip-predicate registry.
- ``run_turn``    — one full turn: start events, draw, play, end/win events,
  then advance.

Turn bookkeeping (``skip_next`` / ``extra_turn``) lives as reserved keys in
each ``Player.conditions`` — a serialized, open-ended per-player status map
(see ``models.game_state.Player``). ``advance_turn`` consumes those keys via
``GameState.without_condition``, which returns a fresh immutable copy.
"""

from __future__ import annotations

from collections.abc import Callable

from engine.apply import apply_effect
from engine.events import EventBus, GameEvent, HookContext
from models.effects import DrawCardsOp, EffectProgram
from models.game_state import GameState

_bus = EventBus()  # module-level singleton; tests can inject their own

# ---------------------------------------------------------------------------
# Skip-predicate registry
# ---------------------------------------------------------------------------
# A skip predicate takes (candidate_player, state) and returns True when the
# candidate should be skipped. Cards register named predicates here and set
# ``state.skip_predicate`` to the name.
_SKIP_PREDICATES: dict[str, Callable[..., bool]] = {}


def register_skip_predicate(name: str, fn: Callable[..., bool]) -> None:
    """Register a named skip predicate usable via ``state.skip_predicate``."""
    _SKIP_PREDICATES[name] = fn


# ---------------------------------------------------------------------------
# Draw step
# ---------------------------------------------------------------------------
def draw_step(state: GameState, player_id: str, *, bus: EventBus | None = None) -> GameState:
    """Draw ``state.draw_count`` cards for ``player_id`` at turn start.

    Emits ON_DRAW_STEP first (hooks may react). If the deck is empty the game
    ends. Otherwise the draw is applied via the standard effect pipeline.
    """
    active_bus = bus or _bus
    ctx = HookContext(
        event=GameEvent.ON_DRAW_STEP,
        actor_id=player_id,
        amount=state.draw_count,
    )
    state = active_bus.emit(GameEvent.ON_DRAW_STEP, state, ctx)

    if not state.deck:
        return state.model_copy(update={"phase": "ended"}).with_log("Deck exhausted — game ended.")

    program = EffectProgram(ops=[DrawCardsOp(target="self", amount=state.draw_count)])
    return apply_effect(state, program, ctx, bus=active_bus)


# ---------------------------------------------------------------------------
# Advance turn
# ---------------------------------------------------------------------------
def advance_turn(state: GameState) -> GameState:
    """Advance ``turn_index`` to the next player.

    Honours (in order): extra-turn (stay put, consume the condition),
    direction, skip-next (consumed off the skipped player), and a registered
    skip predicate. Consumed conditions are removed via
    ``GameState.without_condition``, which always returns a fresh copy.
    """
    players = state.players
    n = len(players)
    current_player = state.active_player()

    # Extra turn: the current player goes again; turn_index unchanged.
    if current_player.conditions.get("extra_turn"):
        return state.without_condition(current_player.id, "extra_turn")

    next_idx = (state.turn_index + state.direction) % n
    next_player = players[next_idx]

    if next_player.conditions.get("skip_next"):
        state = state.without_condition(next_player.id, "skip_next")
        next_idx = (next_idx + state.direction) % n

    if state.skip_predicate is not None:
        pred_fn = _SKIP_PREDICATES.get(state.skip_predicate)
        if pred_fn is not None:
            candidate = players[next_idx]
            if pred_fn(candidate, state):
                if candidate.conditions.get("skip_next"):
                    state = state.without_condition(candidate.id, "skip_next")
                next_idx = (next_idx + state.direction) % n

    # Spectators (joined after the game started) NEVER take a turn: keep
    # stepping past any spectator we'd otherwise land on. Bounded by ``n`` so a
    # degenerate all-spectator state can't spin forever (it just leaves the
    # index put). Game start seats turn_index on a non-spectator, so as long as
    # one non-spectator remains this always lands on a real player.
    guard = 0
    while players[next_idx].spectator and guard < n:
        next_idx = (next_idx + state.direction) % n
        guard += 1

    return state.model_copy(update={"turn_index": next_idx})


# ---------------------------------------------------------------------------
# Run a full turn
# ---------------------------------------------------------------------------
def run_turn(
    state: GameState,
    play_fn: Callable[[GameState, str], tuple[GameState, EffectProgram, HookContext]],
    *,
    bus: EventBus | None = None,
) -> GameState:
    """Execute one full turn for the active player.

    ``play_fn(state, player_id)`` returns ``(state, program, play_ctx)`` — the
    (possibly mutated) state, the effect program for the played card, and the
    HookContext for the play. Fires ON_TURN_START, draws, applies the play,
    then ON_TURN_END and ON_WIN_CHECK, and finally advances the turn.
    """
    if state.phase == "ended":
        return state

    active_bus = bus or _bus
    player_id = state.active_player().id

    start_ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id=player_id)
    state = active_bus.emit(GameEvent.ON_TURN_START, state, start_ctx)

    state = draw_step(state, player_id, bus=active_bus)
    if state.phase == "ended":
        return state

    state, program, play_ctx = play_fn(state, player_id)
    state = apply_effect(state, program, play_ctx, bus=active_bus)

    end_ctx = HookContext(event=GameEvent.ON_TURN_END, actor_id=player_id)
    state = active_bus.emit(GameEvent.ON_TURN_END, state, end_ctx)

    win_ctx = HookContext(event=GameEvent.ON_WIN_CHECK, actor_id=player_id)
    state = active_bus.emit(GameEvent.ON_WIN_CHECK, state, win_ctx)

    return advance_turn(state)


__all__ = ["advance_turn", "draw_step", "register_skip_predicate", "run_turn"]
