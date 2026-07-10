"""Tests for resolve_end_of_game — kept-in-hand / in-play end-of-game scoring."""

from __future__ import annotations

from tbwc.engine.scoring import evaluate_win_condition, resolve_end_of_game
from tbwc.models.game_state import GameState, Player, WinCondition


def _keep_card(amount: int = 10) -> dict:
    """A card worth `amount` points to its holder if kept until game end."""
    return {
        "title": f"Worth {amount} Points If You Keep It",
        "description": f"Worth {amount} points if you keep it.",
        "canonical": {
            "trigger": "on_game_end",
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": amount}}],
        },
    }


def _immediate_card(amount: int = 10) -> dict:
    """A normal immediate add_points card — NO on_game_end trigger."""
    return {
        "title": f"Gain {amount}",
        "description": f"Gain {amount} points now.",
        "canonical": {
            "trigger": None,
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": amount}}],
        },
    }


def _state(players: list[Player], cards: dict, **kwargs) -> GameState:
    return GameState(room_code="AAAA", players=players, cards=cards, **kwargs)


def test_kept_card_in_hand_scores() -> None:
    cards = {"c1": _keep_card(10)}
    p1 = Player(id="p1", name="P1", score=0, hand=["c1"])
    st = _state([p1], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 10


def test_kept_card_in_play_scores() -> None:
    cards = {"c1": _keep_card(7)}
    p1 = Player(id="p1", name="P1", score=3, in_play=["c1"])
    st = _state([p1], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 10


def test_non_trigger_card_does_not_score() -> None:
    cards = {"c1": _immediate_card(10)}
    p1 = Player(id="p1", name="P1", score=5, hand=["c1"])
    st = _state([p1], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 5


def test_multiple_players_each_get_bonus() -> None:
    cards = {"c1": _keep_card(10), "c2": _keep_card(5)}
    p1 = Player(id="p1", name="P1", score=1, hand=["c1"])
    p2 = Player(id="p2", name="P2", score=2, in_play=["c2"])
    st = _state([p1, p2], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 11
    assert out.get_player("p2").score == 7


def test_spectators_are_skipped() -> None:
    cards = {"c1": _keep_card(10)}
    p1 = Player(id="p1", name="P1", score=0, hand=["c1"])
    spec = Player(id="s1", name="Spec", score=0, hand=["c1"], spectator=True)
    st = _state([p1, spec], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 10
    assert out.get_player("s1").score == 0


def test_top_level_trigger_also_detected() -> None:
    # Trigger/ops lifted to the top level (as rooms.deck._normalise_card does).
    cards = {
        "c1": {
            "title": "Kept",
            "description": "Worth 4 if kept.",
            "trigger": "on_game_end",
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": 4}}],
        }
    }
    p1 = Player(id="p1", name="P1", score=0, hand=["c1"])
    st = _state([p1], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 4


def test_missing_card_and_no_canonical_skipped() -> None:
    cards = {
        "c1": {"title": "Plain", "description": "Just text."},  # no canonical/trigger
    }
    # c2 holds a card id absent from the registry.
    p1 = Player(id="p1", name="P1", score=3, hand=["c1", "c2"])
    st = _state([p1], cards)
    out = resolve_end_of_game(st)
    assert out.get_player("p1").score == 3


def test_purity_original_unchanged() -> None:
    cards = {"c1": _keep_card(10)}
    p1 = Player(id="p1", name="P1", score=0, hand=["c1"])
    st = _state([p1], cards)
    _ = resolve_end_of_game(st)
    assert st.get_player("p1").score == 0


def test_cards_arg_overrides_state_registry() -> None:
    p1 = Player(id="p1", name="P1", score=0, hand=["c1"])
    st = _state([p1], {})  # empty state registry
    out = resolve_end_of_game(st, cards={"c1": _keep_card(6)})
    assert out.get_player("p1").score == 6


def test_win_condition_after_end_of_game_bonus() -> None:
    # Without the bonus p2 leads (20 > 15); the kept card flips it to p1.
    cards = {"c1": _keep_card(10)}
    p1 = Player(id="p1", name="P1", score=15, hand=["c1"])
    p2 = Player(id="p2", name="P2", score=20)
    st = _state([p1, p2], cards, win_condition=WinCondition(kind="highest_points"))
    assert evaluate_win_condition(st) == ["p2"]
    out = resolve_end_of_game(st)
    assert evaluate_win_condition(out) == ["p1"]
