"""tbwc.engine.hooks — RegisteredHook, HookRegistry, and the fire_hooks ordering algorithm.

Persistent effects are hooks: callables that fire when a named event occurs.
Ordering: hooks fire in REGISTRATION order (first-registered fires first,
last-registered fires last/outermost); center-scoped hooks fire outermost of
all; an `uncounterable` source card ends the chain early.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

# A hook handler signature: (state: GameState, ctx: HookContext) -> GameState
HookHandler = Callable[..., Any]


class RegisteredHook(BaseModel):
    """A persistent effect registered by a card play."""

    model_config = {"arbitrary_types_allowed": True}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_card_id: str  # the card that registered this hook
    event: str  # GameEvent string, e.g. "on_score_change"
    scope: Literal["player", "center"]  # "center" = table-wide house rule
    owner_id: str | None = None  # player id for player-scoped hooks; None for center


class HookRegistry:
    """In-process registry of hook handlers."""

    def __init__(self) -> None:
        self._hooks: list[RegisteredHook] = []  # ordered by registration time
        self._handlers: dict[str, HookHandler] = {}  # hook.id -> callable

    def register(self, hook: RegisteredHook, handler: HookHandler) -> None:
        self._hooks.append(hook)
        self._handlers[hook.id] = handler

    def remove(self, hook_id: str) -> None:
        self._hooks = [h for h in self._hooks if h.id != hook_id]
        self._handlers.pop(hook_id, None)

    def hooks_for_event(self, event: str) -> list[RegisteredHook]:
        return [h for h in self._hooks if h.event == event]

    def get_handler(self, hook_id: str) -> HookHandler | None:
        return self._handlers.get(hook_id)


# Module-level default registry used by the engine.
_default_registry = HookRegistry()


def get_default_registry() -> HookRegistry:
    return _default_registry


def _get_card(state: Any, card_id: str) -> Any | None:
    """Safely retrieve a card from state.cards; returns None if missing."""
    return state.cards.get(card_id)


def fire_hooks(
    state: Any,  # GameState (typed Any to avoid circular import)
    event: str,
    ctx: Any,  # HookContext
    *,
    registry: HookRegistry | None = None,
) -> Any:  # returns GameState
    """Fire all hooks subscribed to `event` in the correct order.

    1. Partition hooks into player-scoped and center-scoped.
    2. Player hooks fire first, center hooks fire last (outermost/override).
    3. Within each group, fire in REGISTRATION order (first-registered fires
       first; last-registered fires last/outermost, getting the final say).
    4. If a fired hook's source card has properties.uncounterable == True,
       stop the chain immediately (later hooks — even center — do NOT fire).
    """
    reg = registry or _default_registry
    matching = reg.hooks_for_event(event)
    if not matching:
        return state

    player_hooks = [h for h in matching if h.scope == "player"]
    center_hooks = [h for h in matching if h.scope == "center"]
    ordered = player_hooks + center_hooks

    for hook in ordered:
        handler = reg.get_handler(hook.id)
        if handler is None:
            continue

        card = _get_card(state, hook.source_card_id)
        is_uncounterable = bool(card.properties.get("uncounterable", False)) if card is not None else False

        state = handler(state, ctx)

        if is_uncounterable:
            break

    return state
