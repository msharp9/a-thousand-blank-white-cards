"""Tests for the canonical interpretation contract (agent.contract).

Verify the :class:`agent.contract.InterpretResult` model — its defaults, its
validation/round-trip behaviour, and that it can carry a
:class:`agent.contract.SnippetEffect`. The runtime entry point (``run_agent``)
result shape is covered separately in tests/test_agent_skeleton.py.
"""

from __future__ import annotations

from typing import get_args

from agent.contract import CardIntent, InterpretResult, MechanicsPlan, PlanStep, SnippetEffect
from models.effects import CustomNoteOp, EffectProgram


def test_interpret_result_defaults():
    r = InterpretResult()
    assert r.program is None
    assert r.snippet is None
    assert r.verdict == "invalid"
    assert r.comment == ""
    assert r.persona_action == "none"


def test_snippet_effect_import_and_fields():
    s = SnippetEffect(code="def apply(state, ctx): return None", explanation="noop")
    assert s.code.startswith("def apply")
    assert s.explanation == "noop"


def test_interpret_result_validate_and_round_trip():
    program = EffectProgram(ops=[CustomNoteOp(note="note")])
    snippet = SnippetEffect(code="def apply(state, ctx): return None", explanation="noop")
    r = InterpretResult(
        program=program,
        snippet=snippet,
        verdict="ok",
        comment="funny",
        persona_action="chaos_monkey",
    )
    dumped = r.model_dump()
    assert dumped["verdict"] == "ok"
    assert dumped["comment"] == "funny"
    assert dumped["persona_action"] == "chaos_monkey"

    restored = InterpretResult.model_validate(dumped)
    assert restored.verdict == "ok"
    assert restored.comment == "funny"
    assert restored.persona_action == "chaos_monkey"
    assert restored.program is not None
    assert restored.snippet is not None
    assert restored.snippet.code == snippet.code


def test_card_intent_minimal_payload():
    intent = CardIntent(summary="x")
    assert intent.effects == []
    assert intent.targets == ""
    assert intent.persistence == "immediate"
    assert intent.ambiguity == "clear"
    assert intent.complexity == "standard"
    assert intent.persona_action == "none"


def test_card_intent_round_trip():
    intent = CardIntent(
        summary="Draw an extra card and give it to your left neighbor.",
        effects=["draw 1", "transfer to left neighbor"],
        targets="left neighbor",
        persistence="immediate",
        resolved_references=["trample (MTG): excess damage carries over -> here: n/a"],
        ambiguity="ambiguous",
        complexity="complex",
        comment="a bold move",
        persona_action="chaos_monkey",
    )
    dumped = intent.model_dump()
    restored = CardIntent.model_validate(dumped)
    assert restored == intent


def test_mechanics_plan_minimal_payload():
    plan = MechanicsPlan(strategy="y")
    assert plan.steps == []
    assert plan.trigger is None
    assert plan.scope == "center"
    assert plan.feasible is True
    assert plan.infeasible_reason == ""


def test_mechanics_plan_round_trip_with_steps():
    step = PlanStep(
        kind="ops",
        description="Add one point to the actor.",
        engine_ops=["add_points(actor, 1)"],
    )
    plan = MechanicsPlan(
        strategy="Bump the actor's score by one via a single op.",
        steps=[step],
        trigger="on_play",
        scope="player",
        feasible=False,
        infeasible_reason="no matching op exists",
    )
    dumped = plan.model_dump()
    restored = MechanicsPlan.model_validate(dumped)
    assert restored == plan


def test_card_intent_persona_action_matches_interpret_result():
    intent_values = get_args(CardIntent.model_fields["persona_action"].annotation)
    result_values = get_args(InterpretResult.model_fields["persona_action"].annotation)
    assert intent_values == result_values
