"""evals.ceilings — per-card theoretical maxima for the deterministic scorers.

A metric's aggregate is only interpretable against what the cards *allow*. A
pure no-op card (canonical emits only a ``custom_note``) can never score
``did_something``, so 0.79 out of a 0.91 ceiling is a very different story from
0.79 out of 1.0. These helpers compile each card's OWN canonical and run it
through the same dry-run the scorers grade the agent against, so the ceiling is
computed from the same machinery — not a separate hand-rolled classifier that
would drift from the scorer's actual definition of "did something".
"""

from __future__ import annotations

from typing import Any

_NON_MECHANICAL = frozenset({"custom_note", "note"})


def _canonical_of(card: dict[str, Any]) -> dict[str, Any]:
    canonical = card.get("canonical") or card.get("human_canonical") or {}
    return canonical if isinstance(canonical, dict) else {}


def card_ceiling(card: dict[str, Any]) -> dict[str, bool]:
    """Best a perfect agent could score on this card, from its own canonical.

    ``executable`` — the canonical compiles and dry-runs clean (so
    executability can reach 1.0). ``mechanical`` — that dry-run emits at least
    one non-``custom_note`` op (so did_something can reach 1.0). Compiled with
    ``origin="seed"`` so the ops path is used (not a doubled sandbox step) and
    choice targets resolve via the fixture's choice context. Never raises.
    """
    from agent.tools.dry_run_effect import dry_run_resolution_plan
    from engine.compile import compile_card_plan
    from evals.game_fixtures import (
        EVAL_ACTOR_ID,
        EVAL_CARD_ID,
        EVAL_CHOSEN_CARD_ID,
        EVAL_CHOSEN_PLAYER_ID,
        build_eval_state,
    )

    canonical = _canonical_of(card)
    result = {"executable": False, "mechanical": False, "has_sandbox": bool(canonical.get("sandbox"))}
    try:
        plan = compile_card_plan({"canonical": canonical, "id": "ceiling", "origin": "seed"})
        if plan is None or not plan.steps:
            return result
        report = dry_run_resolution_plan(
            build_eval_state(),
            plan,
            EVAL_ACTOR_ID,
            EVAL_CARD_ID,
            chosen_player_id=EVAL_CHOSEN_PLAYER_ID,
            chosen_card_id=EVAL_CHOSEN_CARD_ID,
        )
    except Exception:  # noqa: BLE001 — a canonical that can't be classified is just not-executable
        return result
    if not report.get("ok"):
        return result
    result["executable"] = True
    result["mechanical"] = any(op.get("op") not in _NON_MECHANICAL for op in (report.get("emitted_ops") or []))
    return result


def benchmark_ceilings(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-card ceilings into summary fields (empty if no cards)."""
    if not cards:
        return {}
    per = [card_ceiling(card) for card in cards]
    n = len(per)
    return {
        "executability_ceiling": sum(1 for p in per if p["executable"]) / n,
        "did_something_ceiling": sum(1 for p in per if p["mechanical"]) / n,
        "did_something_noop_count": sum(1 for p in per if not p["mechanical"]),
        "sandbox_na_count": sum(1 for p in per if not p["has_sandbox"]),
    }
