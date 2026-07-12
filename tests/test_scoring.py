"""Tests for win-condition evaluation."""

from __future__ import annotations

from engine.scoring import check_win, evaluate_win_condition, win_condition_met
from models.game_state import GameState, Player, WinCondition


def _state(kind: str, threshold: int | None = None, **scores: int) -> GameState:
    players = [Player(id=pid, name=pid.upper(), score=sc) for pid, sc in scores.items()]
    return GameState(room_code="AAAA", players=players, win_condition=WinCondition(kind=kind, threshold=threshold))


def test_highest_points() -> None:
    st = _state("highest_points", p1=10, p2=25, p3=5)
    assert evaluate_win_condition(st) == ["p2"]


def test_lowest_points() -> None:
    st = _state("lowest_points", p1=10, p2=25, p3=5)
    assert evaluate_win_condition(st) == ["p3"]


def test_first_to_threshold() -> None:
    st = _state("first_to", threshold=10, p1=10, p2=9, p3=15)
    assert set(evaluate_win_condition(st)) == {"p1", "p3"}


def test_last_standing() -> None:
    players = [Player(id="p1", name="A", connected=True), Player(id="p2", name="B", connected=False)]
    st = GameState(room_code="AAAA", players=players, win_condition=WinCondition(kind="last_standing"))
    assert evaluate_win_condition(st) == ["p1"]


def test_last_standing_multiple_connected_no_winner() -> None:
    st = _state("last_standing", p1=1, p2=2)
    assert evaluate_win_condition(st) == []


def test_none_returns_empty() -> None:
    st = _state("none", p1=10, p2=25)
    assert evaluate_win_condition(st) == []


def test_highest_points_tie() -> None:
    st = _state("highest_points", p1=25, p2=25, p3=5)
    assert set(evaluate_win_condition(st)) == {"p1", "p2"}


def test_no_connected_players_returns_empty() -> None:
    players = [Player(id="p1", name="A", connected=False)]
    st = GameState(room_code="AAAA", players=players, win_condition=WinCondition(kind="highest_points"))
    assert evaluate_win_condition(st) == []


def test_check_win_sets_ended() -> None:
    st = _state("highest_points", p1=10, p2=25)
    out = check_win(st)
    assert out.phase == "ended"
    assert any("Winner" in line for line in out.log)
    assert st.phase != "ended"  # original unchanged


def test_check_win_no_winner_leaves_state() -> None:
    st = _state("none", p1=10)
    out = check_win(st)
    assert out.phase == st.phase


def test_win_condition_met_highest_points_never_live() -> None:
    # highest_points always has a winner once any active player exists, so it
    # must never be treated as "met" mid-game (that would end turn one).
    st = _state("highest_points", p1=10, p2=25)
    assert win_condition_met(st) is False


def test_win_condition_met_lowest_points_never_live() -> None:
    st = _state("lowest_points", p1=10, p2=25)
    assert win_condition_met(st) is False


def test_win_condition_met_first_to_threshold_reached() -> None:
    st = _state("first_to", threshold=10, p1=10, p2=3)
    assert win_condition_met(st) is True


def test_win_condition_met_first_to_threshold_not_reached() -> None:
    st = _state("first_to", threshold=10, p1=5, p2=3)
    assert win_condition_met(st) is False


def test_win_condition_met_last_standing() -> None:
    players = [Player(id="p1", name="A", connected=True), Player(id="p2", name="B", connected=False)]
    st = GameState(room_code="AAAA", players=players, win_condition=WinCondition(kind="last_standing"))
    assert win_condition_met(st) is True


def test_win_condition_met_none_never_live() -> None:
    st = _state("none", p1=10)
    assert win_condition_met(st) is False


def test_win_condition_met_first_to_without_threshold_is_inert() -> None:
    st = _state("first_to", threshold=None, p1=0, p2=0)
    assert win_condition_met(st) is False


def test_win_condition_met_first_to_zero_threshold_is_inert() -> None:
    st = _state("first_to", threshold=0, p1=0, p2=0)
    assert win_condition_met(st) is False


def test_win_condition_met_first_to_negative_threshold_is_inert() -> None:
    st = _state("first_to", threshold=-10, p1=0, p2=0)
    assert win_condition_met(st) is False


def test_win_condition_met_first_to_already_met_fires() -> None:
    st = _state("first_to", threshold=10, p1=15, p2=0)
    assert win_condition_met(st) is True
