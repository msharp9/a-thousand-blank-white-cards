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


# --- snippet hook support (added by rbb.6) ---

# Per-card snippet cache: source_card_id -> validated code (avoids re-parsing per fire).
_SNIPPET_CACHE: dict[str, str] = {}


def cache_snippet(card_id: str, code: str) -> None:
    """Pre-validate a snippet's AST and cache it. Call once at registration time.

    Raises via the runner's validation on unsafe code (execute_snippet re-checks too).
    """
    from tbwc.sandbox.validate import validate_snippet

    result = validate_snippet(code)
    if not result.ok:
        raise ValueError(f"Snippet failed validation: {result.error}")
    _SNIPPET_CACHE[card_id] = code


def make_snippet_handler(card_id: str, code: str) -> HookHandler:
    """Return a hook handler (state, ctx) -> state that runs `code` in the sandbox.

    The handler serialises state+ctx to dicts, calls execute_snippet, and applies the
    returned op diff via the engine (apply_snippet_diff). Failures are non-fatal: the
    handler logs to the game state and returns it unchanged. Respects the
    snippet_execution_enabled feature flag.

    Performance: each fire spawns a subprocess (the security boundary). AST is cached
    at registration via cache_snippet to avoid re-parsing; consider batching for
    high-frequency events in future.
    """
    cache_snippet(card_id, code)

    def _handler(state: Any, ctx: Any) -> Any:
        import json

        from tbwc.config import get_settings

        if not get_settings().snippet_execution_enabled:
            return state

        from tbwc.sandbox.revalidate import DiffValidationError, apply_snippet_diff
        from tbwc.sandbox.runner import SnippetExecutionError, execute_snippet

        state_dict = json.loads(state.model_dump_json())
        ctx_dict = {
            "actor_id": getattr(ctx, "actor_id", None),
            "event": str(getattr(ctx, "event", "")),
            "card_id": getattr(ctx, "card_id", None),
            "amount": getattr(ctx, "amount", None),
        }
        try:
            raw_ops = execute_snippet(_SNIPPET_CACHE.get(card_id, code), state_dict, ctx_dict)
            return apply_snippet_diff(state, raw_ops, ctx)
        except (SnippetExecutionError, DiffValidationError) as exc:
            return state.with_log(f"[hook error] {card_id}: {exc}")

    return _handler


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
