"""tbwc.engine.events — canonical event names, HookContext, and the EventBus.

The engine is event-driven: every meaningful action emits a named event. The
EventBus is the call-site interface; the actual hook dispatch lives in
tbwc.engine.hooks (late-imported to avoid a circular dependency).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class GameEvent(StrEnum):
    """All event names the engine can emit. Hooks subscribe by name."""

    ON_PLAY = "on_play"
    ON_SCORE_CHANGE = "on_score_change"
    ON_TURN_START = "on_turn_start"
    ON_TURN_END = "on_turn_end"
    ON_DRAW_STEP = "on_draw_step"
    ON_DESTROY_ATTEMPT = "on_destroy_attempt"
    ON_WIN_CHECK = "on_win_check"
    ON_GAME_END = "on_game_end"


@dataclass
class HookContext:
    """Carries all per-event data into a hook handler.

    Fields that don't apply to a given event are left None.
    """

    event: GameEvent
    actor_id: str  # player who triggered the event
    card_id: str | None = None  # card being played / destroyed
    chosen_player_id: str | None = None  # resolution of Target.chooser
    chosen_card_id: str | None = None  # resolution of CardTarget.chosen_card
    amount: int | None = None  # points delta, draw count, etc.
    target_player_ids: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def with_amount(self, amount: int) -> HookContext:
        """Return a shallow copy with amount updated."""
        return replace(self, amount=amount)


class EventBus:
    """Lightweight synchronous event bus.

    The engine calls ``bus.emit(event, state, ctx)``; the bus delegates to
    ``fire_hooks`` from tbwc.engine.hooks. The import cycle is avoided via a
    late import inside emit().

    An optional ``registry`` (a HookRegistry) may be supplied so a bus can route
    its emits to a specific registry; when None (the default), ``fire_hooks``
    falls back to the module-level default registry, preserving behavior.
    """

    def __init__(self, registry: Any = None) -> None:  # HookRegistry | None; Any avoids cycle
        self._registry = registry

    def emit(
        self,
        event: GameEvent,
        state: Any,  # GameState — typed Any to avoid circular import
        ctx: HookContext,
    ) -> Any:  # returns (potentially modified) GameState
        from tbwc.engine.hooks import fire_hooks  # late import — avoids cycle

        return fire_hooks(state, str(event), ctx, registry=self._registry)
