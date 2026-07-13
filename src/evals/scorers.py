"""evals.scorers — scorer callables for the card-interpretation eval harness.

Each scorer conforms to ScorerFunction: scorer(context: ScorerContext) -> Score.
  context.input    = raw card dict {title, description, ...}
  context.output   = dict from evals.harness.normalise_agent_output
                     (keys: effect_program, snippet_effect, verdict, ...)
  context.expected = human_canonical dict
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from evals.eval_core import Score, ScorerContext, create_scorer
from evals.judge import JudgeLLM, Verdict


@lru_cache(maxsize=1)
def _judge() -> JudgeLLM:
    return JudgeLLM()


def _effect_summary(output: dict[str, Any]) -> str:
    """Extract a text summary of the agent's generated effect for the judge.

    Prefers the complete ordered resolution_plan, then the legacy structured
    effect_program, then a generated snippet body. When the
    agent produced neither (e.g. an "invalid" verdict), fall back to the verdict +
    in-character comment so the judge still has something to score. The old graph's
    "classification" dict no longer exists (the single agent has no classify step),
    so it is not consulted here.
    """
    plan = output.get("resolution_plan")
    if plan:
        return json.dumps(plan, default=str)
    ep = output.get("effect_program")
    if ep:
        return json.dumps(ep, default=str)
    se = output.get("snippet_effect")
    if se:
        return str(se)
    verdict = output.get("verdict")
    comment = output.get("comment")
    return json.dumps({"verdict": verdict, "comment": comment}, default=str)


def _run_judge(context: ScorerContext) -> Verdict:
    card = context.input
    canonical = context.expected or {}
    summary = _effect_summary(context.output or {})
    return _judge().evaluate(
        card_description=f"{card.get('title', '')}\n{card.get('description', '')}",
        generated_summary=summary,
        human_canonical=canonical,
    )


def _intent_match_scorer(context: ScorerContext) -> Score:
    verdict = _run_judge(context)
    return Score(score=verdict.intent_match, metadata={"overall": verdict.overall, "reason": verdict.reason})


intent_match_judge = create_scorer(
    name="intent_match",
    description="LLM judge: does the generated effect match the card's intent?",
    scorer=_intent_match_scorer,
)


def _dsl_validity_scorer(context: ScorerContext) -> Score:
    """Validate a non-empty ordered plan, falling back to legacy EffectProgram."""
    output = context.output or {}
    raw_plan = output.get("resolution_plan")
    if raw_plan:
        try:
            from engine.sandbox.validate import validate_snippet
            from models.effects import RegisterHookOp, ResolutionPlan, SnippetStep

            plan = ResolutionPlan.model_validate(raw_plan) if isinstance(raw_plan, dict) else raw_plan
            if not plan.steps:
                return Score(score=0.0, metadata={"reason": "empty ResolutionPlan"})
            codes = [step.code for step in plan.steps if isinstance(step, SnippetStep)]
            codes.extend(op.code for op in plan.operations() if isinstance(op, RegisterHookOp))
            for code in codes:
                validation = validate_snippet(code)
                if not validation.ok:
                    return Score(score=0.0, metadata={"reason": validation.error or "invalid snippet"})
            return Score(score=1.0)
        except Exception as exc:
            return Score(score=0.0, metadata={"reason": str(exc)})

    ep = output.get("effect_program")
    if not ep:
        return Score(score=0.0, metadata={"reason": "no effect_program in output"})
    try:
        from models.effects import EffectProgram

        program = EffectProgram.model_validate(ep) if isinstance(ep, dict) else ep
        # non-empty program required
        ops = getattr(program, "ops", None)
        if not ops:
            return Score(score=0.0, metadata={"reason": "empty EffectProgram"})
        return Score(score=1.0)
    except Exception as exc:  # any validation failure -> invalid DSL
        return Score(score=0.0, metadata={"reason": str(exc)})


dsl_validity = create_scorer(
    name="dsl_validity",
    description="Structural check: is the ordered plan (or legacy program) valid and non-empty?",
    scorer=_dsl_validity_scorer,
)


def _target_accuracy_scorer(context: ScorerContext) -> Score:
    verdict = _run_judge(context)
    return Score(score=verdict.target_placement_correct, metadata={"reason": verdict.reason})


target_accuracy = create_scorer(
    name="target_accuracy",
    description="LLM judge: is the target/placement of the effect correct?",
    scorer=_target_accuracy_scorer,
)


def _timing_accuracy_scorer(context: ScorerContext) -> Score:
    verdict = _run_judge(context)
    return Score(score=verdict.timing_correct, metadata={"reason": verdict.reason})


timing_accuracy = create_scorer(
    name="timing_accuracy",
    description="LLM judge: is the timing (immediate/persistent/triggered) correct?",
    scorer=_timing_accuracy_scorer,
)


ALL_SCORERS = [intent_match_judge, dsl_validity, target_accuracy, timing_accuracy]
