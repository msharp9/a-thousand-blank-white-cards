"""Plain-string tests for the three stage prompt builders (bead 47b.4).

No LLM, no network: each builder is a pure function of its inputs, so the tests
assert which shared blocks each stage's prompt contains — and, just as
importantly, which it must NOT contain.
"""

from __future__ import annotations

from agent.contract import CardIntent, MechanicsPlan, PlanStep
from agent.persona import (
    COMMENT_REQUIREMENT,
    DRY_RUN_MANDATE,
    OP_CATALOG_GUIDE,
    PERSONA_DECISION_LOGIC,
    PERSONA_PREAMBLE,
    SANDBOX_RULES,
)
from agent.stage_prompts import build_coder_prompt, build_intent_prompt, build_planner_prompt

INTENT = CardIntent(
    summary="Everyone with a hat gains 3 points.",
    effects=["award 3 points to each hat-wearer"],
    targets="all players wearing hats",
)

PLAN = MechanicsPlan(
    strategy="Ask who wears a hat, then add points.",
    steps=[
        PlanStep(kind="interaction", description="Confirm hat status", interaction="confirm, audience all"),
        PlanStep(kind="snippet", description="Add points to confirmers", snippet_outline="read ctx, add_points"),
    ],
)

STATE = {"players": [{"id": "p1", "name": "Alice", "score": 3}, {"id": "p2", "name": "Bob", "score": 7}]}


def test_intent_prompt_contains_persona_and_intent_contract():
    prompt = build_intent_prompt(
        "Hat Bonus", "Everyone with a hat gains 3 points.", state=STATE, actor_id="p1", creator_id="p1"
    )
    assert PERSONA_PREAMBLE in prompt
    assert PERSONA_DECISION_LOGIC in prompt
    assert COMMENT_REQUIREMENT in prompt
    for key in CardIntent.model_fields:
        assert f'"{key}"' in prompt
    assert "Hat Bonus" in prompt
    assert "IS the author" in prompt
    assert "Alice" in prompt


def test_intent_prompt_excludes_sandbox_rules():
    prompt = build_intent_prompt("T", "D")
    assert SANDBOX_RULES not in prompt
    assert DRY_RUN_MANDATE not in prompt
    assert "ctx['interactions']" not in prompt
    assert "do NOT design mechanics" in prompt


def test_intent_prompt_help_mode_only_when_struggling():
    calm = build_intent_prompt("T", "D")
    assert "HELP MODE" not in calm

    helped = build_intent_prompt("T", "D", struggling_author=True, author_fallbacks=2)
    assert "HELP MODE" in helped
    assert "2 card(s)" in helped
    assert "aim the wit at the cosmos" in helped


def test_intent_prompt_art_note_only_when_has_art():
    assert "CARD ART" not in build_intent_prompt("T", "D")
    assert "CARD ART" in build_intent_prompt("T", "D", has_art=True)


def test_planner_prompt_contains_op_catalog_and_creativity():
    prompt = build_planner_prompt(INTENT, state=STATE, actor_id="p1", creator_id="p2")
    assert OP_CATALOG_GUIDE in prompt
    assert "BE CREATIVE" in prompt
    assert INTENT.summary in prompt
    for key in MechanicsPlan.model_fields:
        assert f'"{key}"' in prompt
    assert "Bob" in prompt


def test_planner_prompt_excludes_persona_duties():
    prompt = build_planner_prompt(INTENT)
    assert COMMENT_REQUIREMENT not in prompt
    assert PERSONA_PREAMBLE not in prompt
    assert PERSONA_DECISION_LOGIC not in prompt


def test_coder_prompt_contains_sandbox_rules_and_dry_run():
    prompt = build_coder_prompt(INTENT, PLAN, state=STATE, actor_id="p1")
    assert SANDBOX_RULES in prompt
    assert DRY_RUN_MANDATE in prompt
    assert "No imports, no exec/eval" in prompt
    assert "def apply(state, ctx)" in prompt
    assert PLAN.strategy in prompt
    assert "Confirm hat status" in prompt
    assert "If the plan has no steps, design the mechanics yourself from the intent." in prompt


def test_coder_prompt_excludes_persona_and_comment_key():
    prompt = build_coder_prompt(INTENT, PLAN)
    assert PERSONA_PREAMBLE not in prompt
    assert COMMENT_REQUIREMENT not in prompt
    assert '"comment"' not in prompt
    assert '"persona_action"' not in prompt
    assert '"plan"' in prompt
    assert '"program"' in prompt
    assert '"snippet"' in prompt
    assert '"verdict"' in prompt
