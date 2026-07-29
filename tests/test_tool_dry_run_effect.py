from __future__ import annotations

import json

from agent.tools.dry_run_effect import dry_run_resolution_plan, make_dry_run_effect_tool
from models.effects import DrawCardsOp, OpsStep, ResolutionPlan, SnippetStep
from models.effects import InteractionStep
from models.interactions import ChoiceInteraction, InteractionOption
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


def test_dry_run_flags_random_results_as_illustrative() -> None:
    plan = ResolutionPlan(
        steps=[
            SnippetStep(
                code="def apply(state, ctx):\n    state.add_points('self', state.roll_die(sides=6, count=2))\n"
            ),
        ]
    )

    report = dry_run_resolution_plan(_state(), plan, "p1", "played")

    assert report["ok"] is True
    assert "illustrative" in report["note"]
    assert 2 <= report["after"]["scores"]["p1"] <= 12


def test_dry_run_snippet_rolls_replay_identically_across_previews() -> None:
    plan = ResolutionPlan(
        steps=[
            SnippetStep(
                code="def apply(state, ctx):\n    state.add_points('self', state.roll_die(sides=1000, count=3))\n"
            ),
        ]
    )

    first = dry_run_resolution_plan(_state(), plan, "p1", "played")
    second = dry_run_resolution_plan(_state(), plan, "p1", "played")

    assert first["ok"] is True and second["ok"] is True
    assert first["after"]["scores"]["p1"] == second["after"]["scores"]["p1"]
    assert first["emitted_ops"] == second["emitted_ops"]


def test_dry_run_omits_random_note_for_deterministic_plans() -> None:
    plan = ResolutionPlan(steps=[OpsStep(ops=[DrawCardsOp(target="self", amount=1)])])

    report = dry_run_resolution_plan(_state(), plan, "p1", "played")

    assert report["ok"] is True
    assert "note" not in report


def test_interaction_misplumbing_error_includes_shape_hint() -> None:
    # A snippet that treats ctx['interactions'][key] as a scalar fails; the error
    # must remind the agent of the {player_id: value} shape so it can self-correct.
    state = _state()
    plan = ResolutionPlan(
        steps=[
            InteractionStep(
                result_key="victim",
                request=ChoiceInteraction(
                    prompt="pick", audience="active", options=[InteractionOption(id="p2", label="Bob")]
                ),
            ),
            SnippetStep(
                code="def apply(state, ctx):\n    state.add_points('id:' + ctx['interactions']['victim'], 1)\n"
            ),
        ]
    )
    report = dry_run_resolution_plan(state, plan, "p1", "played", chosen_player_id="p2")
    assert report["ok"] is False
    assert "ctx['interactions']" in report["error"]
    assert "player_id" in report["error"]
    assert "victim" in report["error"]


class TestReactionPlans:
    """Reaction plans dry-run inside a synthesized reaction window instead of
    failing (snippet counter_play was rejected, pending_* ctx keys missing)."""

    def test_counter_play_snippet_runs_with_pending_ctx(self) -> None:
        code = (
            "def apply(state, ctx):\n"
            "    state.counter_play('negate')\n"
            "    state.steal_points('id:' + ctx['pending_actor_id'], 'self', 3)\n"
        )
        plan = ResolutionPlan(steps=[SnippetStep(code=code)])
        state = _state().model_copy(
            update={"players": [Player(id="p1", name="Alice", hand=["played"]), Player(id="p2", name="Bob", score=5)]}
        )

        report = dry_run_resolution_plan(state, plan, "p1", "played")

        assert report["ok"] is True
        assert {op.get("op") for op in report["emitted_ops"]} == {"counter_play", "steal_points"}
        assert report["after"]["scores"] == {"p1": 3, "p2": 2}

    def test_pending_ctx_reads_mark_plan_as_reaction(self) -> None:
        code = "def apply(state, ctx):\n    if ctx.get('pending_card_id'):\n        state.add_points('self', 1)\n"
        report = dry_run_resolution_plan(_state(), ResolutionPlan(steps=[SnippetStep(code=code)]), "p1", "played")

        assert report["ok"] is True
        assert report["after"]["scores"]["p1"] == 1

    def test_non_reaction_snippet_still_rejects_counter_free_path(self) -> None:
        # A plain play snippet keeps the ON_PLAY context: no pending keys.
        code = "def apply(state, ctx):\n    state.add_points('self', 2 if ctx.get('event') == 'on_play' else 0)\n"
        report = dry_run_resolution_plan(_state(), ResolutionPlan(steps=[SnippetStep(code=code)]), "p1", "played")

        assert report["ok"] is True
        assert report["after"]["scores"]["p1"] == 2
