from __future__ import annotations

from agent.contract import InterpretResult, SnippetEffect
from engine.compile import compile_card, compile_card_plan
from models.effects import AddPointsOp, EffectProgram, OpsStep, ResolutionPlan, SnippetStep


SNIPPET = "def apply(state, ctx):\n    state.add_points('self', len(state.my_hand()))\n"


def test_legacy_ops_and_snippet_compile_in_order() -> None:
    card = {
        "canonical": {
            "ops": [{"op": "draw_cards", "args": {"target": "self", "amount": 2}}],
            "snippet": SNIPPET,
        }
    }

    plan = compile_card_plan(card)

    assert plan is not None
    assert [step.kind for step in plan.steps] == ["ops", "snippet"]
    assert plan.steps[0].ops[0].op == "draw_cards"
    assert plan.steps[1].code == SNIPPET


def test_seed_origin_skips_sandbox_mirror_of_ops() -> None:
    card = {
        "origin": "seed",
        "canonical": {
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": 5}}],
            "sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)\n",
        },
    }

    plan = compile_card_plan(card)

    assert plan is not None
    assert [step.kind for step in plan.steps] == ["ops"]


def test_seed_origin_still_uses_sandbox_when_ops_missing() -> None:
    card = {"origin": "seed", "canonical": {"ops": [], "sandbox": SNIPPET}}

    plan = compile_card_plan(card)

    assert plan is not None
    assert [step.kind for step in plan.steps] == ["snippet"]
    assert plan.steps[0].code == SNIPPET


def test_explicit_steps_take_precedence_over_legacy_fields() -> None:
    card = {
        "canonical": {
            "steps": [{"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 7}]}],
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": 1}}],
            "snippet": SNIPPET,
        }
    }

    plan = compile_card_plan(card)

    assert plan is not None
    assert len(plan.steps) == 1
    assert plan.steps[0].ops[0].amount == 7


def test_interpret_result_lowers_program_before_snippet() -> None:
    result = InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]),
        snippet=SnippetEffect(code=SNIPPET, explanation="score the current hand"),
        verdict="ok",
    )

    plan = result.to_plan()

    assert [step.kind for step in plan.steps] == ["ops", "snippet"]
    assert ResolutionPlan.model_validate(plan.model_dump()) == plan


def test_explicit_interpretation_plan_wins() -> None:
    explicit = ResolutionPlan(steps=[OpsStep(ops=[AddPointsOp(target="self", amount=9)])])
    result = InterpretResult(
        plan=explicit,
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=1)]),
        snippet=SnippetEffect(code=SNIPPET, explanation="ignored"),
        verdict="ok",
    )

    assert result.to_plan() == explicit


def test_compile_card_remains_ops_only_compatible() -> None:
    card = {
        "canonical": {
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": 4}}],
            "snippet": SNIPPET,
        }
    }

    program = compile_card(card)

    assert program is not None
    assert [op.op for op in program.ops] == ["add_points"]


def test_plan_choice_aggregates_across_ops_steps() -> None:
    plan = ResolutionPlan(
        steps=[
            SnippetStep(code=SNIPPET),
            OpsStep(ops=[AddPointsOp(target="chooser", amount=2)]),
        ]
    )

    assert plan.requires_choice is True
