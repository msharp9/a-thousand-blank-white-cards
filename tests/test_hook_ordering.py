"""Tests for HookRegistry and fire_hooks ordering."""

from __future__ import annotations

from typing import Any

from tbwc.engine.hooks import HookRegistry, RegisteredHook, fire_hooks
from tbwc.models.cards import Card
from tbwc.models.game_state import GameState


def _state_with_cards(*cards: Card) -> GameState:
    st = GameState(room_code="AAAA")
    st = st.model_copy(update={"cards": {c.id: c for c in cards}})
    return st


def _appender(tag: str):
    def handler(state: GameState, ctx: Any) -> GameState:
        return state.with_log(tag)

    return handler


def test_empty_registry_returns_state_unchanged() -> None:
    reg = HookRegistry()
    st = GameState(room_code="AAAA")
    out = fire_hooks(st, "on_score_change", None, registry=reg)
    assert out is st


def test_reverse_registration_order_within_player_scope() -> None:
    reg = HookRegistry()
    c1 = Card(id="c1", title="", description="", creator_id="p1")
    c2 = Card(id="c2", title="", description="", creator_id="p1")
    st = _state_with_cards(c1, c2)
    h1 = RegisteredHook(source_card_id="c1", event="on_play", scope="player", owner_id="p1")
    h2 = RegisteredHook(source_card_id="c2", event="on_play", scope="player", owner_id="p1")
    reg.register(h1, _appender("first"))
    reg.register(h2, _appender("second"))
    out = fire_hooks(st, "on_play", None, registry=reg)
    # registered-first-fires-last => "second" appended before "first"
    assert out.log == ["second", "first"]


def test_center_fires_outermost() -> None:
    reg = HookRegistry()
    cp = Card(id="cp", title="", description="", creator_id="p1")
    cc = Card(id="cc", title="", description="", creator_id="p1")
    st = _state_with_cards(cp, cc)
    hp = RegisteredHook(source_card_id="cp", event="on_play", scope="player", owner_id="p1")
    hc = RegisteredHook(source_card_id="cc", event="on_play", scope="center")
    reg.register(hp, _appender("player"))
    reg.register(hc, _appender("center"))
    out = fire_hooks(st, "on_play", None, registry=reg)
    # player group fires first, center last
    assert out.log == ["player", "center"]


def test_uncounterable_breaks_chain() -> None:
    reg = HookRegistry()
    cu = Card(id="cu", title="", description="", creator_id="p1", properties={"uncounterable": True})
    cc = Card(id="cc", title="", description="", creator_id="p1")
    st = _state_with_cards(cu, cc)
    # uncounterable player hook registered last => fires first in player group => breaks chain
    hu = RegisteredHook(source_card_id="cu", event="on_play", scope="player", owner_id="p1")
    hc = RegisteredHook(source_card_id="cc", event="on_play", scope="center")
    reg.register(hc, _appender("center"))
    reg.register(hu, _appender("uncounterable"))
    out = fire_hooks(st, "on_play", None, registry=reg)
    assert out.log == ["uncounterable"]  # center never fired


def test_missing_handler_is_skipped() -> None:
    reg = HookRegistry()
    c1 = Card(id="c1", title="", description="", creator_id="p1")
    st = _state_with_cards(c1)
    h1 = RegisteredHook(source_card_id="c1", event="on_play", scope="player", owner_id="p1")
    reg._hooks.append(h1)  # registered without a handler
    out = fire_hooks(st, "on_play", None, registry=reg)
    assert out.log == []


def test_remove_and_hooks_for_event() -> None:
    reg = HookRegistry()
    h1 = RegisteredHook(source_card_id="c1", event="on_play", scope="player")
    reg.register(h1, _appender("x"))
    assert reg.hooks_for_event("on_play") == [h1]
    reg.remove(h1.id)
    assert reg.hooks_for_event("on_play") == []
    assert reg.get_handler(h1.id) is None
