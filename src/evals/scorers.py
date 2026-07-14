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


# One Verdict per (card, output): the four judge-backed scorers below would
# otherwise each call the LLM, judging the same interpretation 4×. Memoize on the
# identity of the output dict (a distinct object per eval row) so a full run makes
# exactly one judge call per card. Bounded so it never grows without limit.
_VERDICT_CACHE: dict[int, Verdict] = {}
_VERDICT_CACHE_MAX = 512


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
    """Judge this (card, output) once, caching by output identity.

    The four judge-backed scorers all call this within a single eval row, so the
    cache collapses them to one LLM call per card. The key is ``id(output)``,
    which is stable within a run (the runner builds one output dict per row).
    """
    output = context.output or {}
    key = id(output)
    cached = _VERDICT_CACHE.get(key)
    if cached is not None:
        return cached
    card = context.input
    verdict = _judge().evaluate(
        card_description=f"{card.get('title', '')}\n{card.get('description', '')}",
        generated_summary=_effect_summary(output),
        human_canonical=context.expected or {},
    )
    if len(_VERDICT_CACHE) >= _VERDICT_CACHE_MAX:
        _VERDICT_CACHE.clear()
    _VERDICT_CACHE[key] = verdict
    return verdict


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


def _persistence_accuracy_scorer(context: ScorerContext) -> Score:
    verdict = _run_judge(context)
    return Score(score=verdict.persistence_correct, metadata={"reason": verdict.reason})


persistence_accuracy = create_scorer(
    name="persistence_accuracy",
    description="LLM judge: is the persistence (one-shot vs ongoing modifier + trigger) correct?",
    scorer=_persistence_accuracy_scorer,
)


def _magnitude_sign_scorer(context: ScorerContext) -> Score:
    verdict = _run_judge(context)
    return Score(score=verdict.magnitude_sign_correct, metadata={"reason": verdict.reason})


magnitude_sign = create_scorer(
    name="magnitude_sign",
    description="LLM judge: is the magnitude sign (positive/negative/neutral) correct?",
    scorer=_magnitude_sign_scorer,
)


def _resolution_plan_from_output(output: dict[str, Any]) -> Any | None:
    """Rebuild a ResolutionPlan from a normalised agent output, or None.

    Prefers the ordered ``resolution_plan``; falls back to a legacy
    ``effect_program`` wrapped in a single OpsStep so executability still scores.
    """
    from models.effects import EffectProgram, OpsStep, ResolutionPlan

    raw_plan = output.get("resolution_plan")
    if raw_plan:
        try:
            return ResolutionPlan.model_validate(raw_plan) if isinstance(raw_plan, dict) else raw_plan
        except Exception:  # noqa: BLE001 — a malformed plan simply isn't executable
            return None
    ep = output.get("effect_program")
    if ep:
        try:
            program = EffectProgram.model_validate(ep) if isinstance(ep, dict) else ep
            if program.ops:
                return ResolutionPlan(steps=[OpsStep(ops=program.ops)])
        except Exception:  # noqa: BLE001
            return None
    return None


# executability and did_something both dry-run the same generated plan; cache by
# output identity (like _VERDICT_CACHE) so a snippet-heavy card spawns one
# subprocess per run, not one per scorer.
_DRY_RUN_CACHE: dict[int, dict[str, Any]] = {}


def _dry_run_output(output: dict[str, Any]) -> dict[str, Any]:
    """Compile the generated plan and dry-run it against the eval state.

    Returns the dry-run report dict (``{"ok", "emitted_ops", ...}``) or a
    synthetic ``{"ok": False, "error": ...}`` when there is nothing to run. Never
    raises — a broken plan is a score of 0, not a harness crash. Memoized on the
    output's identity so the two scorers that need it share one execution.
    """
    key = id(output)
    cached = _DRY_RUN_CACHE.get(key)
    if cached is not None:
        return cached

    from agent.tools.dry_run_effect import dry_run_resolution_plan
    from evals.game_fixtures import EVAL_ACTOR_ID, EVAL_CARD_ID, build_eval_state

    plan = _resolution_plan_from_output(output)
    if plan is None or not getattr(plan, "steps", None):
        report: dict[str, Any] = {"ok": False, "error": "no executable plan", "emitted_ops": []}
    else:
        try:
            report = dry_run_resolution_plan(build_eval_state(), plan, EVAL_ACTOR_ID, EVAL_CARD_ID)
        except Exception as exc:  # noqa: BLE001 — dry_run is defensive, but stay belt-and-suspenders
            report = {"ok": False, "error": str(exc), "emitted_ops": []}

    if len(_DRY_RUN_CACHE) >= _VERDICT_CACHE_MAX:
        _DRY_RUN_CACHE.clear()
    _DRY_RUN_CACHE[key] = report
    return report


def _executability_scorer(context: ScorerContext) -> Score:
    """Does the generated effect actually validate and run end-to-end?

    Compiles the generated ResolutionPlan and dry-runs it against the parity
    state; 1.0 iff the dry-run reports ``ok``. This is distinct from
    ``sandbox_behavior`` (which measures *similarity* to the canonical): a plan
    can run cleanly yet do the wrong thing, or match intent yet crash.
    """
    report = _dry_run_output(context.output or {})
    if report.get("ok"):
        return Score(score=1.0, metadata={"emitted_ops": len(report.get("emitted_ops") or [])})
    return Score(score=0.0, metadata={"reason": report.get("error", "dry-run failed")})


executability = create_scorer(
    name="executability",
    description="Deterministic: does the generated plan compile and dry-run without error?",
    scorer=_executability_scorer,
)


_NON_MECHANICAL_EMITTED = frozenset({"custom_note", "note"})


def _did_something_scorer(context: ScorerContext) -> Score:
    """The fun metric: did the agent DO something, or leave a dead no-op?

    1.0 iff the verdict isn't "invalid" AND the dry-run runs cleanly AND emits at
    least one mechanical op (a bare ``custom_note`` doesn't count — it changes no
    game state). A no-op is a fun-killer even when the interpretation is "right",
    so this is scored separately from correctness.
    """
    output = context.output or {}
    if output.get("verdict") == "invalid":
        return Score(score=0.0, metadata={"reason": "verdict=invalid"})
    report = _dry_run_output(output)
    if not report.get("ok"):
        return Score(score=0.0, metadata={"reason": report.get("error", "dry-run failed")})
    mechanical = [op for op in report.get("emitted_ops") or [] if op.get("op") not in _NON_MECHANICAL_EMITTED]
    if not mechanical:
        return Score(score=0.0, metadata={"reason": "no mechanical ops emitted (no-op)"})
    return Score(score=1.0, metadata={"mechanical_ops": len(mechanical)})


did_something = create_scorer(
    name="did_something",
    description="Fun metric: verdict != invalid AND the dry-run emits >=1 mechanical op (not a no-op).",
    scorer=_did_something_scorer,
)


def _generated_effect_forms(output: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Split the agent's generated effect into (snippet codes, plain op dicts)."""
    codes: list[str] = []
    ops: list[dict[str, Any]] = []
    plan = output.get("resolution_plan")
    if isinstance(plan, dict):
        for step in plan.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if step.get("kind") == "snippet" and isinstance(step.get("code"), str):
                codes.append(step["code"])
            elif step.get("kind") == "ops":
                ops.extend(op for op in step.get("ops") or [] if isinstance(op, dict))
    else:
        ep = output.get("effect_program")
        if isinstance(ep, dict):
            ops.extend(op for op in ep.get("ops") or [] if isinstance(op, dict))
        snippet = output.get("snippet_effect")
        if isinstance(snippet, str) and snippet.strip():
            codes.append(snippet)
    return codes, ops


def _sandbox_behavior_scorer(context: ScorerContext) -> Score:
    """Deterministic behavioral comparison: execute the EXPECTED sandbox and the
    GENERATED effect against canned fixtures and compare the op diffs.

    Sandbox code cannot be text-matched; behavior can. Scores the average
    multiset-Jaccard similarity across fixtures. N/A cases (no expected
    sandbox — e.g. interaction-step cards) score 1.0 with a skipped marker,
    matching the trigger_event_correct N/A convention. Execution failures and
    timeouts score 0 with the reason — never crash the harness.
    """
    expected_code = (context.expected or {}).get("sandbox")
    if not expected_code:
        return Score(score=1.0, metadata={"skipped": "no expected sandbox (steps-based or unannotated)"})
    from config import get_settings

    if not get_settings().snippet_execution_enabled:
        return Score(score=1.0, metadata={"skipped": "snippet execution disabled"})

    from engine.sandbox.runner import SnippetExecutionError, execute_snippet
    from evals.fixtures import fixture_states, multiset_jaccard, normalise_ops

    codes, plain_ops = _generated_effect_forms(context.output or {})
    if not codes and not plain_ops:
        return Score(score=0.0, metadata={"reason": "no generated effect to execute"})

    similarities: list[float] = []
    for state_dict, ctx_dict in fixture_states():
        try:
            expected_diff = normalise_ops(execute_snippet(expected_code, state_dict, ctx_dict), ctx_dict)
            generated_raw: list[dict[str, Any]] = list(plain_ops)
            for code in codes:
                generated_raw.extend(execute_snippet(code, state_dict, ctx_dict))
            generated_diff = normalise_ops(generated_raw, ctx_dict)
        except SnippetExecutionError as exc:
            return Score(score=0.0, metadata={"reason": f"execution failed: {exc}"})
        except Exception as exc:
            return Score(score=0.0, metadata={"reason": str(exc)})
        similarities.append(multiset_jaccard(expected_diff, generated_diff))
    return Score(score=sum(similarities) / len(similarities), metadata={"per_fixture": similarities})


sandbox_behavior = create_scorer(
    name="sandbox_behavior",
    description="Deterministic: generated effect's op diff matches the expected sandbox's diff on fixtures.",
    scorer=_sandbox_behavior_scorer,
)


# Scorers split by cost: the judge-backed set makes LLM calls (deduped to one
# per card via _run_judge's cache); the deterministic set is free and offline.
JUDGE_SCORERS = [intent_match_judge, target_accuracy, persistence_accuracy, magnitude_sign]
DETERMINISTIC_SCORERS = [dsl_validity, sandbox_behavior, executability, did_something]
ALL_SCORERS = [*JUDGE_SCORERS, *DETERMINISTIC_SCORERS]
