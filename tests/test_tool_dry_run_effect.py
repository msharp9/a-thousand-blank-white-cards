from __future__ import annotations

import json

from agent.tools.dry_run_effect import dry_run_resolution_plan, make_dry_run_effect_tool
from models.effects import DrawCardsOp, OpsStep, ResolutionPlan, SnippetStep
from models.game_state import GameState, Player


def _state() -> GameState:
    return GameState(
        room_code="TEST",
        players=[Player(id="p1", name="Alice", hand=["played"]), Player(id="p2", name="Bob")],
        cards={"played": {"id": "played", "title": "Card"}},
        deck=["d1", "d2"],
        phase="playing",
    )


def test_dry_run_executes_ordered_plan_without_mutating_state() -> None:
    state = _state()
    plan = ResolutionPlan(
        steps=[
            OpsStep(ops=[DrawCardsOp(target="self", amount=2)]),
            SnippetStep(code="def apply(state, ctx):\n    state.add_points('self', len(state.my_hand()))\n"),
        ]
    )

    report = dry_run_resolution_plan(state, plan, "p1", "played")

    assert report["ok"] is True
    assert report["after"]["scores"]["p1"] == 2
    assert report["after"]["hand_sizes"]["p1"] == 2
    assert state.get_player("p1").score == 0
    assert state.get_player("p1").hand == ["played"]


def test_dry_run_tool_returns_actionable_unknown_method_error() -> None:
    tool = make_dry_run_effect_tool(_state(), "p1", "played")

    report = json.loads(tool.invoke({"code": "def apply(state, ctx):\n    state.draw('self', 2)\n"}))

    assert report["ok"] is False
    assert "draw_cards" in report["error"]


def test_dry_run_tool_requires_one_payload_shape() -> None:
    tool = make_dry_run_effect_tool(_state(), "p1", "played")

    report = json.loads(tool.invoke({}))

    assert report == {"ok": False, "error": "provide exactly one of code or plan"}


def test_dry_run_supplies_deterministic_interaction_values_to_later_steps() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "bids",
                    "request": {"kind": "number", "prompt": "Bid", "audience": "all", "minimum": 2},
                },
                {
                    "kind": "snippet",
                    "code": "def apply(state, ctx):\n    state.add_points('self', int(sum(ctx['interactions']['bids'].values())))\n",
                },
            ]
        }
    )

    report = dry_run_resolution_plan(_state(), plan, "p1", "played")

    assert report["ok"] is True
    assert report["interactions"] == {"bids": {"p1": 2, "p2": 2}}
    assert report["after"]["scores"]["p1"] == 4
