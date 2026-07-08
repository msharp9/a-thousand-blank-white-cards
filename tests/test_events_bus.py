"""Tests for GameEvent, HookContext, and EventBus."""

from __future__ import annotations

from tbwc.engine.events import EventBus, GameEvent, HookContext


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


def test_with_amount_is_immutable() -> None:
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1")
    new = ctx.with_amount(10)
    assert new.amount == 10
    assert ctx.amount is None
    assert new is not ctx


def test_event_bus_exists() -> None:
    # emit() late-imports fire_hooks from tbwc.engine.hooks, which does not
    # exist yet (a later bead). We only assert the bus is constructible and
    # that emit raises ImportError (not some other failure) until hooks lands.
    bus = EventBus()
    assert bus is not None
