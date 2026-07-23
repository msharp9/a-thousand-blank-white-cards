"""evals.scorers — scorer callables for the card-interpretation eval harness.

Each scorer conforms to ScorerFunction: scorer(context: ScorerContext) -> Score.
  context.input    = raw card dict {title, description, ...}
  context.output   = dict from evals.harness.normalise_agent_output
                     (keys: resolution_plan, verdict, comment, persona_action)
  context.expected = human_canonical dict

Two per-run caches keyed on the output dict's identity collapse repeated work
within one row: the four judge scorers share a single LLM Verdict, and the two
execution scorers share a single dry-run. id() keys are only safe while the
output objects stay alive, so runners call :func:`reset_run_caches` before each
run — otherwise a recycled id from a previous run could serve stale results.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from evals.eval_core import Score, ScorerContext, create_scorer
from evals.judge import JudgeLLM, Verdict

_CACHE_MAX = 512
_VERDICT_CACHE: dict[int, Verdict] = {}
_DRY_RUN_CACHE: dict[int, dict[str, Any]] = {}


def reset_run_caches() -> None:
    """Clear the per-run scorer caches. Call at the start of every eval run."""
    _VERDICT_CACHE.clear()
    _DRY_RUN_CACHE.clear()


@lru_cache(maxsize=1)
def _judge() -> JudgeLLM:
    return JudgeLLM()


def _effect_summary(output: dict[str, Any]) -> str:
    """Serialize the generated plan for the judge; fall back to verdict+comment
    so an effect-less interpretation (e.g. verdict="invalid") still gets scored."""
    plan = output.get("resolution_plan")
    if plan:
        return json.dumps(plan, default=str)
    return json.dumps({"verdict": output.get("verdict"), "comment": output.get("comment")}, default=str)


def _run_judge(context: ScorerContext) -> Verdict:
    """One judge LLM call per (card, output); the four judge scorers share it."""
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
    if len(_VERDICT_CACHE) >= _CACHE_MAX:
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


def _dsl_validity_scorer(context: ScorerContext) -> Score:
    """Structural check: the plan is well-formed, non-empty, and every snippet
    and hook body passes the engine's static sandbox validation."""
    raw_plan = (context.output or {}).get("resolution_plan")
    if not raw_plan:
        return Score(score=0.0, metadata={"reason": "no resolution_plan in output"})
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
    except Exception as exc:  # noqa: BLE001 — any validation failure means invalid DSL
        return Score(score=0.0, metadata={"reason": str(exc)})


dsl_validity = create_scorer(
    name="dsl_validity",
    description="Structural check: is the ordered plan valid and non-empty?",
    scorer=_dsl_validity_scorer,
)


def _resolution_plan_from_output(output: dict[str, Any]) -> Any | None:
    from models.effects import ResolutionPlan

    raw_plan = output.get("resolution_plan")
    if not raw_plan:
        return None
    try:
        return ResolutionPlan.model_validate(raw_plan) if isinstance(raw_plan, dict) else raw_plan
    except Exception:  # noqa: BLE001 — a malformed plan simply isn't executable
        return None


def _dry_run_output(output: dict[str, Any]) -> dict[str, Any]:
    """Dry-run the generated plan against the parity state, once per output.

    Returns the dry-run report (``{"ok", "emitted_ops", ...}``) or a synthetic
    ``{"ok": False, "error": ...}``. Never raises — a broken plan is a score of
    0, not a harness crash.
    """
    key = id(output)
    cached = _DRY_RUN_CACHE.get(key)
    if cached is not None:
        return cached

    from agent.tools.dry_run_effect import dry_run_resolution_plan
    from evals.game_fixtures import (
        EVAL_ACTOR_ID,
        EVAL_CARD_ID,
        EVAL_CHOSEN_CARD_ID,
        EVAL_CHOSEN_PLAYER_ID,
        build_eval_state,
    )

    plan = _resolution_plan_from_output(output)
    if plan is None or not plan.steps:
        report: dict[str, Any] = {"ok": False, "error": "no executable plan", "emitted_ops": []}
    else:
        try:
            report = dry_run_resolution_plan(
                build_eval_state(),
                plan,
                EVAL_ACTOR_ID,
                EVAL_CARD_ID,
                chosen_player_id=EVAL_CHOSEN_PLAYER_ID,
                chosen_card_id=EVAL_CHOSEN_CARD_ID,
            )
        except Exception as exc:  # noqa: BLE001 — dry_run is defensive, but stay belt-and-suspenders
            report = {"ok": False, "error": str(exc), "emitted_ops": []}

    if len(_DRY_RUN_CACHE) >= _CACHE_MAX:
        _DRY_RUN_CACHE.clear()
    _DRY_RUN_CACHE[key] = report
    return report


def _executability_scorer(context: ScorerContext) -> Score:
    """1.0 iff the generated plan compiles and dry-runs without error.

    Distinct from ``sandbox_behavior`` (similarity to canonical): a plan can run
    cleanly yet do the wrong thing, or match intent yet crash.
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
    """The fun metric: a no-op kills the fun even when the reading is "right",
    so 1.0 requires verdict != invalid AND a clean dry-run AND >=1 mechanical op
    (a bare custom_note changes no game state and doesn't count)."""
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
    """Split the generated plan into (snippet codes, plain op dicts)."""
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
    return codes, ops


def _plan_has_interaction(output: dict[str, Any]) -> bool:
    """True when the generated plan contains a play-time interaction step."""
    plan = output.get("resolution_plan")
    if not isinstance(plan, dict):
        return False
    return any(isinstance(step, dict) and step.get("kind") == "interaction" for step in plan.get("steps") or [])


def _sandbox_behavior_scorer(context: ScorerContext) -> Score:
    """Behavioral similarity: execute the EXPECTED sandbox and the GENERATED
    effect against canned fixtures and compare the op diffs (multiset Jaccard).

    Sandbox code cannot be text-matched; behavior can. N/A cases (no expected
    sandbox) score 1.0 with a skipped marker; execution failures score 0 with
    the reason — never crash the harness.
    """
    expected_code = (context.expected or {}).get("sandbox")
    if not expected_code:
        return Score(score=1.0, metadata={"skipped": "no expected sandbox (steps-based or unannotated)"})

    # A plan with an interaction step resolves its player/card choice at play
    # time, independently of how the fixed canonical resolves it (via
    # ctx.chosen_player_id). The two cannot be behaviorally aligned, so a
    # comparison here would be a false 0. Abstain — executability and the judge
    # scorers still grade these cards.
    if _plan_has_interaction(context.output or {}):
        return Score(
            score=1.0, metadata={"skipped": "interaction/choice plan — free choice not comparable to a fixed canonical"}
        )

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
        except Exception as exc:  # noqa: BLE001
            return Score(score=0.0, metadata={"reason": str(exc)})
        similarities.append(multiset_jaccard(expected_diff, generated_diff))
    return Score(score=sum(similarities) / len(similarities), metadata={"per_fixture": similarities})


sandbox_behavior = create_scorer(
    name="sandbox_behavior",
    description="Deterministic: generated effect's op diff matches the expected sandbox's diff on fixtures.",
    scorer=_sandbox_behavior_scorer,
)


# Split by cost: JUDGE_SCORERS make LLM calls (one shared call per card);
# DETERMINISTIC_SCORERS are free and offline.
JUDGE_SCORERS = [intent_match_judge, target_accuracy, persistence_accuracy, magnitude_sign]
DETERMINISTIC_SCORERS = [dsl_validity, sandbox_behavior, executability, did_something]
ALL_SCORERS = [*JUDGE_SCORERS, *DETERMINISTIC_SCORERS]
