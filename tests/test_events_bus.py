"""Tests for GameEvent, HookContext, and EventBus."""

from __future__ import annotations

from tbwc.engine.events import EventBus, GameEvent, HookContext
from tbwc.engine.hooks import HookRegistry, RegisteredHook
from tbwc.models.game_state import GameState, Player


def test_game_event_is_str_enum() -> None:
    assert GameEvent.ON_PLAY == "on_play"
    assert GameEvent.ON_SCORE_CHANGE == "on_score_change"
    assert isinstance(GameEvent.ON_PLAY, str)


def test_hook_context_constructs() -> None:
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1")
    assert ctx.actor_id == "p1"
    assert ctx.card_id is None
    assert ctx.target_player_ids == []
    assert ctx.extra == {}


def test_event_bus_exists() -> None:
    bus = EventBus()
    assert bus is not None


def test_event_bus_routes_to_custom_registry() -> None:
    """A bus built with a custom registry routes emits to that registry."""
    reg = HookRegistry()
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    log: list[str] = []
    hook = RegisteredHook(
        source_card_id="c1",
        event=str(GameEvent.ON_TURN_START),
        scope="player",
        owner_id="p1",
    )
    reg.register(hook, lambda s, ctx: (log.append("fired"), s)[1])

    bus = EventBus(registry=reg)
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    bus.emit(GameEvent.ON_TURN_START, state, ctx)

    assert log == ["fired"]  # routed to the custom registry


def test_event_bus_default_registry_does_not_see_custom_hooks() -> None:
    """A default bus (no registry) does not fire hooks from a separate registry."""
    reg = HookRegistry()
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    log: list[str] = []
    hook = RegisteredHook(
        source_card_id="c1",
        event=str(GameEvent.ON_TURN_START),
        scope="player",
        owner_id="p1",
    )
    reg.register(hook, lambda s, ctx: (log.append("fired"), s)[1])

    bus = EventBus()  # default registry
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    bus.emit(GameEvent.ON_TURN_START, state, ctx)

    assert log == []
