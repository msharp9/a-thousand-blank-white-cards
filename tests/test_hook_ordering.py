"""Hook ordering, center-scope, and uncounterable resistance tests."""

from __future__ import annotations

from engine.events import GameEvent, HookContext
from engine.hooks import HookRegistry, RegisteredHook, fire_hooks, get_default_registry
from models.cards import Card
from models.game_state import GameState, Player


def make_state_with_cards(**card_properties) -> GameState:
    """Build a minimal GameState with cards for the hook source cards."""
    players = [Player(id="p1", name="Alice", score=0)]
    cards = {}
    for card_id, props in card_properties.items():
        cards[card_id] = Card(id=card_id, title=card_id, description="", creator_id="p1", properties=props)
    return GameState(room_code="TEST", players=players, cards=cards)


def make_ctx(actor_id="p1") -> HookContext:
    return HookContext(event=GameEvent.ON_SCORE_CHANGE, actor_id=actor_id)


def make_hook(card_id: str, scope: str = "player") -> RegisteredHook:
    return RegisteredHook(
        source_card_id=card_id,
        event=GameEvent.ON_SCORE_CHANGE,
        scope=scope,
        owner_id="p1" if scope == "player" else None,
    )


class TestFireHooksEmpty:
    def test_no_hooks_returns_state_unchanged(self):
        state = make_state_with_cards()
        reg = HookRegistry()
        result = fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert result is state


class TestHookInvocationOrder:
    """Hooks fire in registration order; center-scoped fire outermost (last)."""

    def test_single_hook_fires(self):
        state = make_state_with_cards(c1={})
        reg = HookRegistry()
        fired = []
        reg.register(make_hook("c1"), lambda s, ctx: (fired.append("A"), s)[1])
        fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert fired == ["A"]

    def test_two_hooks_registration_order(self):
        """A registered first fires first; B registered second fires last (outermost)."""
        state = make_state_with_cards(c1={}, c2={})
        reg = HookRegistry()
        order = []
        reg.register(make_hook("c1"), lambda s, ctx: (order.append("A"), s)[1])
        reg.register(make_hook("c2"), lambda s, ctx: (order.append("B"), s)[1])
        fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert order == ["A", "B"]

    def test_center_hook_fires_after_player_hooks(self):
        state = make_state_with_cards(c1={}, c_center={})
        reg = HookRegistry()
        order = []
        reg.register(make_hook("c1", scope="player"), lambda s, ctx: (order.append("player"), s)[1])
        reg.register(make_hook("c_center", scope="center"), lambda s, ctx: (order.append("center"), s)[1])
        fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert order == ["player", "center"]


class TestUncounterabaleResistance:
    """An uncounterable card's hook breaks the chain early."""

    def test_uncounterable_stops_later_hooks(self):
        state = make_state_with_cards(c1={}, c2={"uncounterable": True}, c3={})
        reg = HookRegistry()
        fired = []
        reg.register(make_hook("c1"), lambda s, ctx: (fired.append("A"), s)[1])
        reg.register(make_hook("c2"), lambda s, ctx: (fired.append("B"), s)[1])
        reg.register(make_hook("c3"), lambda s, ctx: (fired.append("C"), s)[1])
        fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert "A" in fired
        assert "B" in fired
        assert "C" not in fired  # blocked by uncounterable B

    def test_non_uncounterable_does_not_stop_chain(self):
        state = make_state_with_cards(c1={}, c2={}, c3={})
        reg = HookRegistry()
        fired = []
        reg.register(make_hook("c1"), lambda s, ctx: (fired.append("A"), s)[1])
        reg.register(make_hook("c2"), lambda s, ctx: (fired.append("B"), s)[1])
        reg.register(make_hook("c3"), lambda s, ctx: (fired.append("C"), s)[1])
        fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert fired == ["A", "B", "C"]


class TestHookStateTransformation:
    """Hooks can mutate state; changes chain through the sequence."""

    def test_hooks_chain_state_changes(self):
        state = make_state_with_cards(c1={}, c2={})
        reg = HookRegistry()
        reg.register(make_hook("c1"), lambda s, ctx: s.with_log("hook-A"))
        reg.register(make_hook("c2"), lambda s, ctx: s.with_log("hook-B"))
        result = fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert "hook-A" in result.log
        assert "hook-B" in result.log


class TestMissingHandlerSkipped:
    def test_hook_without_handler_is_skipped(self):
        state = make_state_with_cards(c1={})
        reg = HookRegistry()
        h1 = make_hook("c1")
        reg._hooks.append(h1)  # registered with no handler
        result = fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert result.log == []


class TestHookRegistryRemove:
    def test_remove_drops_only_target_hook(self):
        state = make_state_with_cards(c1={}, c2={})
        reg = HookRegistry()
        fired = []
        h1 = make_hook("c1")
        h2 = make_hook("c2")
        reg.register(h1, lambda s, ctx: (fired.append("A"), s)[1])
        reg.register(h2, lambda s, ctx: (fired.append("B"), s)[1])

        reg.remove(h1.id)

        assert h1 not in reg.hooks_for_event(GameEvent.ON_SCORE_CHANGE)
        assert h2 in reg.hooks_for_event(GameEvent.ON_SCORE_CHANGE)
        assert reg.get_handler(h1.id) is None
        fire_hooks(state, GameEvent.ON_SCORE_CHANGE, make_ctx(), registry=reg)
        assert fired == ["B"]  # only the surviving hook fires


class TestGetDefaultRegistry:
    def test_returns_registry_and_is_stable(self):
        reg1 = get_default_registry()
        reg2 = get_default_registry()
        assert isinstance(reg1, HookRegistry)
        assert reg1 is reg2
