"""Tests for the multi-effect / game-altering gold exemplars added by bead xxt.

The gold corpus previously taught the agent that cards do ONE thing. These
exemplars teach: cards can draw-then-score off dynamic state, combine a rule
change with ending the game, chain 5 ops in one play, and encode a whole
house-ruleset (Uno) as far as today's op vocabulary allows.
"""

from __future__ import annotations

import json
import pathlib

from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from engine.hooks import make_snippet_handler
from engine.sandbox.validate import validate_snippet
from models.cards import Card
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
from models.game_state import GameState, Player

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

    state = GameState(room_code="TEST", players=[Player(id="p1", name="Alice", score=0, hand=["c1", "c2", "c3"])])
    state = state.model_copy(
        update={"cards": {"card-cc": Card(id="card-cc", title="t", description="d", creator_id="p1")}}
    )
    handler = make_snippet_handler("card-cc", snippet)
    ctx = HookContext(event=GameEvent.ON_TURN_START, actor_id="p1")
    new_state = handler(state, ctx)
    assert new_state.get_player("p1").score == 3
    assert new_state.log == []


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
