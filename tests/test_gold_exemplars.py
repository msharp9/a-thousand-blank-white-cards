"""Tests for the multi-effect / game-altering gold exemplars added by bead xxt.

The gold corpus previously taught the agent that cards do ONE thing. These
exemplars teach: cards can draw-then-score off dynamic state, combine a rule
change with ending the game, chain 5 ops in one play, and encode a whole
house-ruleset (Uno) as far as today's op vocabulary allows.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, patch

from board.rooms.room import Room
from engine.compile import compile_card
from engine.sandbox.validate import validate_snippet
from models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    DestroyCardOp,
    DrawCardsOp,
    EndGameOp,
    ExtraTurnOp,
    ScrambleOrderOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
)
from models.ws_messages import PlayMsg

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def _gold_card(title: str) -> dict:
    cards = json.loads((DATA_DIR / "seed_cards_gold.json").read_text())
    for card in cards:
        if card["title"] == title:
            return card
    raise AssertionError(f"No gold card titled {title!r}")


def test_card_counter_compiles_draw_then_scores_hand_via_snippet() -> None:
    card = _gold_card("Card Counter")
    ops = card["canonical"]["ops"]
    prog = compile_card({"id": "c", "title": card["title"], "ops": ops})
    assert [type(op) for op in prog.ops] == [DrawCardsOp]
    assert prog.ops[0].amount == 2

    snippet = card["canonical"]["snippet"]
    assert validate_snippet(snippet).ok is True

    card = {**card, "id": "card-counter", "creator_id": "seed"}
    room = Room("TEST")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    players = [
        room.state.players[0].model_copy(update={"hand": ["card-counter", "c1", "c2"]}),
        room.state.players[1],
    ]
    room.state = room.state.model_copy(
        update={"phase": "playing", "players": players, "cards": {"card-counter": card}, "deck": ["d1", "d2"]}
    )
    room._has_drawn = True
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    with patch("agent.runtime.run_agent") as run_agent:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="card-counter")))

    run_agent.assert_not_called()
    assert room.state.get_player("p1").score == 4
    assert room.state.get_player("p1").hand == ["c1", "c2", "d1", "d2"]


def test_sudden_death_combines_set_win_condition_and_end_game() -> None:
    card = _gold_card("Sudden Death")
    prog = compile_card({"id": "c", "title": card["title"], "ops": card["canonical"]["ops"]})
    assert [type(op) for op in prog.ops] == [SetWinConditionOp, EndGameOp]
    assert prog.ops[0].kind == "highest_points"


def test_total_chaos_chains_five_ops() -> None:
    card = _gold_card("Total Chaos")
    prog = compile_card({"id": "c", "title": card["title"], "ops": card["canonical"]["ops"]})
    assert [type(op) for op in prog.ops] == [
        AddPointsOp,
        SubtractPointsOp,
        SkipTurnOp,
        ExtraTurnOp,
        DestroyCardOp,
    ]
    assert prog.requires_choice is False


def test_house_rules_uno_expresses_win_condition_and_draw_step() -> None:
    card = _gold_card("House Rules: Uno")
    canonical = card["canonical"]
    assert canonical["trigger"] == "on_empty_hand"
    prog = compile_card({"id": "c", "title": card["title"], "ops": canonical["ops"]})
    assert [type(op) for op in prog.ops] == [SetWinConditionOp, ChangeDrawCountOp]
    assert prog.ops[1].amount == 0
    # The real empty-hand win condition isn't expressible yet — that gap is
    # documented in prose, not smuggled into the ops.
    assert "empty" in card["description"].lower()


def test_shuffle_and_snatch_combines_scramble_and_steal() -> None:
    card = _gold_card("Shuffle and Snatch")
    prog = compile_card({"id": "c", "title": card["title"], "ops": card["canonical"]["ops"]})
    assert [type(op) for op in prog.ops] == [ScrambleOrderOp, StealPointsOp]
    assert prog.ops[1].from_target == "player_with_most_points"
    assert prog.ops[1].to_target == "self"
