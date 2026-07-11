"""Happy-path sandbox tests: benign snippets produce correct state changes.

Pipeline: execute_snippet() -> raw ops -> apply_snippet_diff() -> new GameState.

Every test here runs the REAL subprocess runner and the REAL engine reducers.
Snippets that flow through apply_snippet_diff must use valid Target LITERALS
("self", "all", etc.) — the reducers' _resolve_targets rejects raw player ids.
"""

from __future__ import annotations

from engine.events import GameEvent, HookContext
from models.effects import AddPointsOp, CustomNoteOp, EffectProgram, SubtractPointsOp
from models.game_state import GameState, Player
from engine.sandbox.revalidate import apply_snippet_diff, parse_diff
from engine.sandbox.runner import execute_snippet

STATE_DICT = {
    "players": [
        {"id": "p1", "name": "Alice", "score": 10, "hand": ["c1"], "connected": True},
        {"id": "p2", "name": "Bob", "score": 5, "hand": [], "connected": True},
    ],
    "turn_index": 0,
    "draw_count": 1,
    "direction": 1,
}
CTX_DICT = {"actor_id": "p1", "event": "on_turn_start"}


def _game_state() -> GameState:
    return GameState(
        room_code="TEST",
        players=[
            Player(id="p1", name="Alice", score=10, hand=["c1"]),
            Player(id="p2", name="Bob", score=5),
        ],
        turn_index=0,
    )


def _ctx() -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id="p1")


# ---------------------------------------------------------------------------
# 1. execute_snippet round-trip: exact op shapes
# ---------------------------------------------------------------------------
def test_add_points_and_note_round_trip() -> None:
    code = "def apply(s, c):\n    s.add_points('self', 5)\n    s.note('hi')\n"
    ops = execute_snippet(code, STATE_DICT, CTX_DICT)
    assert ops == [
        {"op": "add_points", "target": "self", "amount": 5},
        {"op": "custom_note", "note": "hi"},
    ]


# ---------------------------------------------------------------------------
# 2. Full pipeline: execute_snippet -> apply_snippet_diff -> new GameState
# ---------------------------------------------------------------------------
def test_full_pipeline_add_points_to_self() -> None:
    code = "def apply(s, c):\n    s.add_points('self', 5)\n"
    ops = execute_snippet(code, STATE_DICT, CTX_DICT)

    state = _game_state()
    new_state = apply_snippet_diff(state, ops, _ctx())

    # "self" resolves to ctx.actor_id == "p1"; p1 started at 10.
    assert new_state.get_player("p1").score == 15
    assert new_state.get_player("p2").score == 5
    # Original state is never mutated.
    assert state.get_player("p1").score == 10


# ---------------------------------------------------------------------------
# 3. parse_diff on a hand-written op list -> typed EffectProgram
# ---------------------------------------------------------------------------
def test_parse_diff_produces_typed_ops() -> None:
    raw_ops = [
        {"op": "add_points", "target": "self", "amount": 3},
        {"op": "subtract_points", "target": "all_others", "amount": 2},
        {"op": "custom_note", "note": "flavour"},
    ]
    program = parse_diff(raw_ops)

    assert isinstance(program, EffectProgram)
    assert len(program.ops) == 3

    add_op = program.ops[0]
    assert isinstance(add_op, AddPointsOp)
    assert add_op.target == "self"
    assert add_op.amount == 3

    sub_op = program.ops[1]
    assert isinstance(sub_op, SubtractPointsOp)
    assert sub_op.target == "all_others"
    assert sub_op.amount == 2

    note_op = program.ops[2]
    assert isinstance(note_op, CustomNoteOp)
    assert note_op.note == "flavour"


# ---------------------------------------------------------------------------
# 4. Snippet that reads state and conditionally acts
# ---------------------------------------------------------------------------
def test_conditional_snippet_awards_when_score_below_threshold() -> None:
    # Award 3 to self only if any player is below 8 points. p2 has 5 -> awards.
    code = "def apply(s, c):\n    if any(p.score < 8 for p in s.players()):\n        s.add_points('self', 3)\n"
    ops = execute_snippet(code, STATE_DICT, CTX_DICT)
    assert ops == [{"op": "add_points", "target": "self", "amount": 3}]

    new_state = apply_snippet_diff(_game_state(), ops, _ctx())
    assert new_state.get_player("p1").score == 13


def test_conditional_snippet_noop_when_condition_false() -> None:
    # All players at/above 20 -> no player below 8 -> no ops recorded.
    high_state = {
        "players": [
            {"id": "p1", "name": "Alice", "score": 20, "hand": ["c1"], "connected": True},
            {"id": "p2", "name": "Bob", "score": 30, "hand": [], "connected": True},
        ],
        "turn_index": 0,
        "draw_count": 1,
        "direction": 1,
    }
    code = "def apply(s, c):\n    if any(p.score < 8 for p in s.players()):\n        s.add_points('self', 3)\n"
    ops = execute_snippet(code, high_state, CTX_DICT)
    assert ops == []


def test_snippet_reads_current_player_id() -> None:
    # current_player_id is turn_index 0 -> "p1"; award to self when it matches actor.
    code = "def apply(s, c):\n    if s.current_player_id == c['actor_id']:\n        s.add_points('self', 1)\n"
    ops = execute_snippet(code, STATE_DICT, CTX_DICT)
    assert ops == [{"op": "add_points", "target": "self", "amount": 1}]

    new_state = apply_snippet_diff(_game_state(), ops, _ctx())
    assert new_state.get_player("p1").score == 11


# ---------------------------------------------------------------------------
# 5. Multiple ops applied in order -> cumulative state change
# ---------------------------------------------------------------------------
def test_multiple_ops_apply_in_order_cumulatively() -> None:
    code = (
        "def apply(s, c):\n    s.add_points('self', 5)\n    s.add_points('self', 2)\n    s.subtract_points('all', 1)\n"
    )
    ops = execute_snippet(code, STATE_DICT, CTX_DICT)
    assert ops == [
        {"op": "add_points", "target": "self", "amount": 5},
        {"op": "add_points", "target": "self", "amount": 2},
        {"op": "subtract_points", "target": "all", "amount": 1},
    ]

    new_state = apply_snippet_diff(_game_state(), ops, _ctx())
    # p1: 10 + 5 + 2 - 1 = 16; p2: 5 - 1 = 4 (subtract_points "all" hits both).
    assert new_state.get_player("p1").score == 16
    assert new_state.get_player("p2").score == 4


# ---------------------------------------------------------------------------
# 6. Read-only snippet produces no ops; apply leaves state unchanged
# ---------------------------------------------------------------------------
def test_read_only_snippet_produces_no_ops_and_no_change() -> None:
    code = "def apply(s, c):\n    total = sum(p.score for p in s.players())\n    _ = s.draw_count\n    _ = total\n"
    ops = execute_snippet(code, STATE_DICT, CTX_DICT)
    assert ops == []

    state = _game_state()
    new_state = apply_snippet_diff(state, ops, _ctx())
    assert [(p.id, p.score) for p in new_state.players] == [("p1", 10), ("p2", 5)]
