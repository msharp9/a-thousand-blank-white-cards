"""Room-level coverage for executable multi-effect gold exemplars."""

from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, patch

from board.rooms.room import Room
from engine.compile import compile_card, compile_card_plan
from engine.sandbox.validate import validate_snippet
from models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    DestroyCardOp,
    DrawCardsOp,
    EndGameOp,
    ExtraTurnOp,
    OpsStep,
    ScrambleOrderOp,
    SetWinConditionOp,
    SkipTurnOp,
    SnippetStep,
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
    plan = compile_card_plan(card)
    assert plan is not None
    assert isinstance(plan.steps[0], OpsStep)
    assert isinstance(plan.steps[0].ops[0], DrawCardsOp)
    assert isinstance(plan.steps[1], SnippetStep)
    assert validate_snippet(plan.steps[1].code).ok is True

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


def test_basic_uno_expresses_empty_hand_end_and_zero_draw() -> None:
    card = _gold_card("Basic Uno")
    plan = compile_card_plan(card)
    assert plan is not None
    ops = plan.operations()
    assert isinstance(ops[0], SetWinConditionOp)
    assert ops[0].kind == "empty_hand"
    assert isinstance(ops[-1], ChangeDrawCountOp)
    assert ops[-1].amount == 0


def test_shuffle_and_snatch_combines_scramble_and_steal() -> None:
    card = _gold_card("Shuffle and Snatch")
    prog = compile_card({"id": "c", "title": card["title"], "ops": card["canonical"]["ops"]})
    assert [type(op) for op in prog.ops] == [ScrambleOrderOp, StealPointsOp]
    assert prog.ops[1].from_target == "player_with_most_points"
    assert prog.ops[1].to_target == "self"


def _room_for_gold(title: str, extra_cards: dict[str, dict]) -> Room:
    card = {**_gold_card(title), "id": "gold", "creator_id": "seed"}
    room = Room("GOLD")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    cards = {"gold": card, **extra_cards}
    players = [
        room.state.get_player("p1").model_copy(update={"hand": ["gold", "p1-other"]}),
        room.state.get_player("p2").model_copy(update={"hand": ["blue", "red"]}),
    ]
    room.state = room.state.model_copy(update={"phase": "playing", "players": players, "cards": cards, "deck": ["d1"]})
    room._has_drawn = True
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def test_spicy_uno_gold_executes_rules_attributes_and_created_cards() -> None:
    other = {"id": "p1-other", "title": "Other", "description": "x"}
    room = _room_for_gold("Spicy Uno", {"p1-other": other, "blue": {}, "red": {}})

    with patch("agent.runtime.run_agent") as run_agent:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="gold")))

    run_agent.assert_not_called()
    assert room.state.rules.draw == 0
    assert room.state.rules.win_condition.kind == "empty_hand"
    assert room.state.cards["p1-other"]["attributes"] == {"color": "red"}
    created = [card for card in room.state.cards.values() if card.get("id", "").startswith("created-")]
    assert {card["title"] for card in created} == {"Draw 2", "Draw 4", "Reverse"}


def test_basic_uno_gold_ends_when_a_player_empties_their_hand() -> None:
    note = {"canonical": {"ops": [{"op": "custom_note", "args": {"note": "played"}}]}}
    extra = {
        "p1-other": {"id": "p1-other", "title": "Other", "description": "x", **note},
        "blue": {"id": "blue", "title": "Blue", "description": "x", **note},
        "red": {"id": "red", "title": "Red", "description": "x", **note},
    }
    room = _room_for_gold("Basic Uno", extra)

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="gold")))
    assert room.state.rules.draw == 0
    assert room.state.rules.win_condition.kind == "empty_hand"

    asyncio.run(room.handle_action("p2", PlayMsg(card_id="blue")))
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="p1-other")))

    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p1"]


def test_wild_uno_gold_registers_and_enforces_color_alignment() -> None:
    note = {"canonical": {"ops": [{"op": "custom_note", "args": {"note": "played"}}]}}
    extra = {
        "p1-other": {"id": "p1-other", "title": "Other", "description": "x", **note},
        "blue": {
            "id": "blue",
            "title": "Blue",
            "description": "x",
            "attributes": {"color": "blue"},
            **note,
        },
        "red": {"id": "red", "title": "Red", "description": "x", "attributes": {"color": "red"}, **note},
    }
    room = _room_for_gold("Wild Uno", extra)

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="gold")))
    assert len(room.state.hooks) == 2
    assert room.state.rules.extra["current_color"] == "red"

    asyncio.run(room.handle_action("p2", PlayMsg(card_id="blue")))
    assert "blue" in room.state.get_player("p2").hand
    assert room.state.turn_index == 1

    asyncio.run(room.handle_action("p2", PlayMsg(card_id="red")))
    assert "red" not in room.state.get_player("p2").hand
