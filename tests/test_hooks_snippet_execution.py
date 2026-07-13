"""Tests for snippet hook dispatch via engine.hooks.make_snippet_handler."""

from __future__ import annotations

import pytest

from engine.events import GameEvent, HookContext
from engine.hooks import HookRegistry, RegisteredHook, cache_snippet, fire_hooks, make_snippet_handler
from models.cards import Card
from models.game_state import GameState, Player

SNIPPET_ADD_10 = "def apply(state, ctx):\n    state.add_points('self', 10)\n"
BAD_SNIPPET = "def apply(state, ctx):\n    raise RuntimeError('oops')\n"


def _state_with_card(card_id: str) -> GameState:
    st = GameState(room_code="TEST", players=[Player(id="p1", name="Alice", score=0)])
    card = Card(id=card_id, title="t", description="d", creator_id="p1")
    return st.model_copy(update={"cards": {card_id: card}})


def test_snippet_hook_fires_and_mutates_state() -> None:
    reg = HookRegistry()
    state = _state_with_card("card-abc")
    hook = RegisteredHook(source_card_id="card-abc", event=GameEvent.ON_TURN_START, scope="player", owner_id="p1")
    reg.register(hook, make_snippet_handler("card-abc", SNIPPET_ADD_10))
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    new_state = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=reg)
    assert new_state.get_player("p1").score == 10
    assert state.get_player("p1").score == 0  # original unchanged


def test_hook_failure_logs_and_continues() -> None:
    reg = HookRegistry()
    state = _state_with_card("card-bad")
    hook = RegisteredHook(source_card_id="card-bad", event=GameEvent.ON_TURN_START, scope="player", owner_id="p1")
    reg.register(hook, make_snippet_handler("card-bad", BAD_SNIPPET))
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    new_state = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=reg)
    assert new_state.get_player("p1").score == 0
    assert any("hook error" in entry for entry in new_state.log)


def test_cache_snippet_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        cache_snippet("card-x", "import os\ndef apply(state, ctx): pass")


def test_same_card_id_handlers_keep_room_specific_code() -> None:
    first = make_snippet_handler("shared-id", "def apply(state, ctx):\n    state.add_points('self', 1)\n")
    make_snippet_handler("shared-id", "def apply(state, ctx):\n    state.add_points('self', 9)\n")
    state = _state_with_card("shared-id")
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")

    result = first(state, ctx)

    assert result.get_player("p1").score == 1


def test_fire_hooks_caps_and_logs_skipped() -> None:
    reg = HookRegistry()
    for i in range(3):
        hook = RegisteredHook(source_card_id=f"card-{i}", event=str(GameEvent.ON_TURN_START), scope="center")
        reg.register(hook, lambda state, ctx: state)
    state = _state_with_card("card-0")
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    new_state = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=reg, max_hooks=2)
    assert any("skipped" in line for line in new_state.log)


def test_build_registry_from_serialized_hooks() -> None:
    from engine.hooks import build_registry
    from models.game_state import HookSpec

    state = _state_with_card("card-abc")
    state = state.model_copy(
        update={
            "hooks": [
                HookSpec(
                    id="hook-card-abc-0",
                    source_card_id="card-abc",
                    event=str(GameEvent.ON_TURN_START),
                    scope="player",
                    owner_id="p1",
                    code=SNIPPET_ADD_10,
                )
            ]
        }
    )
    reg = build_registry(state)
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    new_state = fire_hooks(state, GameEvent.ON_TURN_START, ctx, registry=reg)
    assert new_state.get_player("p1").score == 10


def test_build_registry_skips_invalid_code() -> None:
    from engine.hooks import build_registry
    from models.game_state import HookSpec

    state = _state_with_card("card-abc")
    state = state.model_copy(
        update={
            "hooks": [
                HookSpec(
                    id="h0",
                    source_card_id="card-abc",
                    event=str(GameEvent.ON_TURN_START),
                    scope="center",
                    code="import os\ndef apply(state, ctx): pass",
                )
            ]
        }
    )
    reg = build_registry(state)
    assert reg.hooks_for_event(str(GameEvent.ON_TURN_START)) == []
