"""Tests for evals.fixtures op canonicalisation used by sandbox_behavior."""

from __future__ import annotations

from evals.fixtures import fixture_states, multiset_jaccard, normalise_ops

_STATE, _CTX = fixture_states()[0]  # actor p1; players p1,p2,p3; chosen_player_id p2


def _eq(a: list[dict], b: list[dict], state=_STATE) -> bool:
    return multiset_jaccard(normalise_ops(a, _CTX, state), normalise_ops(b, _CTX, state)) == 1.0


def test_drops_non_mechanical_notes() -> None:
    assert normalise_ops([{"op": "custom_note", "note": "x"}], _CTX) == []


def test_strips_empty_optional_fields() -> None:
    # An omitted optional (winners) and a defaulted-empty one compare equal.
    assert _eq(
        [{"op": "end_game", "winners": ["self"]}],
        [{"op": "end_game", "winners": ["self"], "winner": None}],
    )


def test_end_game_winner_and_winners_are_equivalent() -> None:
    assert _eq(
        [{"op": "end_game", "winner": "player_with_most_points"}],
        [{"op": "end_game", "winner": "player_with_most_points", "winners": []}],
    )


def test_keeps_zero_amount() -> None:
    # 0 is meaningful (change_draw_count 0), must not be stripped as "empty".
    normed = normalise_ops([{"op": "change_draw_count", "amount": 0}], _CTX)
    assert normed == ['{"amount": 0, "op": "change_draw_count"}']


def test_subtract_points_equals_negative_add() -> None:
    assert _eq(
        [{"op": "subtract_points", "target": "self", "amount": 3}],
        [{"op": "add_points", "target": "self", "amount": -3}],
    )


def test_choice_alias_resolves_to_chosen_player() -> None:
    assert _eq(
        [{"op": "add_points", "target": "chooser", "amount": 3}],
        [{"op": "add_points", "target": "id:p2", "amount": 3}],
    )


def test_aggregate_target_matches_enumerated_ids() -> None:
    # all_others (from actor p1) == p2 + p3, expressed two ways.
    assert _eq(
        [{"op": "subtract_points", "target": "all_others", "amount": 4}],
        [
            {"op": "subtract_points", "target": "id:p2", "amount": 4},
            {"op": "subtract_points", "target": "id:p3", "amount": 4},
        ],
    )


def test_neighbors_resolve_to_concrete_ids() -> None:
    # left_neighbor of p1 is p3, right_neighbor is p2.
    assert _eq(
        [
            {"op": "add_points", "target": "left_neighbor", "amount": 3},
            {"op": "add_points", "target": "right_neighbor", "amount": 3},
        ],
        [
            {"op": "add_points", "target": "id:p3", "amount": 3},
            {"op": "add_points", "target": "id:p2", "amount": 3},
        ],
    )


def test_genuine_target_error_still_differs() -> None:
    # Wrong direction (left vs right) must NOT be normalised away.
    assert not _eq(
        [{"op": "skip_turn", "target": "right_neighbor"}],
        [{"op": "skip_turn", "target": "left_neighbor"}],
    )


def test_resolution_skipped_without_state() -> None:
    # No state -> no relative-target resolution; all_others stays literal.
    assert normalise_ops([{"op": "add_points", "target": "all_others", "amount": 1}], _CTX) == [
        '{"amount": 1, "op": "add_points", "target": "all_others"}'
    ]
