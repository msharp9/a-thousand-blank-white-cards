from __future__ import annotations

import json
import math
import random
from typing import Any

from langchain_core.tools import StructuredTool

from engine.apply import apply_effect
from engine.events import EventBus, GameEvent, HookContext
from engine.hooks import build_registry
from engine.sandbox.revalidate import apply_snippet_diff, extract_counter
from engine.sandbox.runner import execute_snippet
from models.effects import CounterPlayOp, EffectProgram, InteractionStep, OpsStep, ResolutionPlan, SnippetStep
from models.game_state import GameState
from models.interactions import (
    CardPickInteraction,
    ChoiceInteraction,
    ConfirmInteraction,
    DrawingInteraction,
    NumberInteraction,
    TextInteraction,
    InteractionOption,
)


def _resolve_ref(results: dict[str, Any], result_key: str, path: list[str | int]) -> Any:
    value = results[result_key]
    for part in path:
        value = value[part]
    return value


# Reaction plans are only meaningful inside a reaction window: counter_play is
# rejected outside one and the pending_* ctx keys don't exist there. Detected
# from the plan itself (there is no trigger field on a ResolutionPlan).
_REACTION_MARKERS = ("counter_play", "pending_card_id", "pending_actor_id", "pending_card_title", "pending_ops")


def _is_reaction_plan(plan: ResolutionPlan) -> bool:
    for step in plan.steps:
        if isinstance(step, OpsStep) and any(isinstance(op, CounterPlayOp) for op in step.ops):
            return True
        if isinstance(step, SnippetStep) and any(marker in step.code for marker in _REACTION_MARKERS):
            return True
    return False


def _pending_play(state: GameState, actor_id: str) -> dict[str, Any]:
    """Synthesize a plausible pending play so a reaction plan can be dry-run.

    Mirrors the ctx the Room supplies in a live reaction window (see
    ``Room._execute_reaction``): another player just played a card. The top of
    the discard is the most natural stand-in; failing that, any card of the
    pending actor's, then a placeholder id.
    """
    others = [player for player in state.players if player.id != actor_id]
    pending_actor = others[0] if others else state.get_player(actor_id)
    card_id = state.discard[-1] if state.discard else (pending_actor.hand[0] if pending_actor.hand else "pending-card")
    card = state.cards.get(card_id) or {}
    title = card.get("title") if isinstance(card, dict) else getattr(card, "title", None)
    return {
        "pending_card_id": card_id,
        "pending_actor_id": pending_actor.id,
        "pending_card_title": title or "Pending Card",
        "pending_ops": [{"op": "add_points", "target": "self", "amount": 5}],
    }


def _snapshot(state: GameState) -> dict[str, Any]:
    return {
        "scores": {player.id: player.score for player in state.players},
        "hand_sizes": {player.id: len(player.hand) for player in state.players},
        "deck_size": len(state.deck),
        "rules": state.rules.model_dump(),
        "hooks": len(state.hooks),
        "phase": state.phase,
        "winner_override": list(state.winner_override),
    }


def dry_run_resolution_plan(
    state: GameState | dict[str, Any],
    plan: ResolutionPlan,
    actor_id: str | None,
    card_id: str | None = None,
    *,
    chosen_player_id: str | None = None,
    chosen_card_id: str | None = None,
) -> dict[str, Any]:
    working = state.model_copy(deep=True) if isinstance(state, GameState) else GameState.model_validate(state)
    if actor_id is None:
        actor_id = working.active_player().id
    if card_id and card_id in working.get_player(actor_id).hand:
        working = working.move_card(card_id, "hand", "discard", from_player_id=actor_id)

    before = _snapshot(working)
    reaction = _is_reaction_plan(plan)
    pending = _pending_play(working, actor_id) if reaction else {}
    ctx = HookContext(
        event=GameEvent.ON_REACTION if reaction else GameEvent.ON_PLAY,
        actor_id=actor_id,
        card_id=card_id,
        chosen_player_id=chosen_player_id,
        chosen_card_id=chosen_card_id,
        extra=dict(pending),
    )
    ctx_dict = {
        "actor_id": actor_id,
        "event": str(ctx.event),
        "card_id": card_id,
        "amount": None,
        "chosen_player_id": chosen_player_id,
        "chosen_card_id": chosen_card_id,
        **pending,
    }
    emitted: list[dict[str, Any]] = []
    interactions: dict[str, Any] = {}
    rng = random.Random(0)

    try:
        for step in plan.steps:
            if isinstance(step, InteractionStep):
                audience = [player.id for player in working.players]
                if step.request.audience == "active":
                    audience = [actor_id]
                elif step.request.audience == "all_others":
                    audience = [player_id for player_id in audience if player_id != actor_id]
                elif step.request.audience.startswith("player:"):
                    requested = step.request.audience.removeprefix("player:")
                    audience = [requested] if any(player.id == requested for player in working.players) else []
                if not audience:
                    raise ValueError("interaction has no eligible audience")
                request = step.request
                refs = {
                    name: _resolve_ref(interactions, ref.result_key, ref.path) for name, ref in step.input_refs.items()
                }
                if isinstance(request, ChoiceInteraction) and "options" in refs:
                    source = refs["options"]
                    if not isinstance(source, dict):
                        raise ValueError("choice options reference must resolve to an object")
                    request = ChoiceInteraction.model_validate(
                        {
                            **request.model_dump(mode="python"),
                            "options": [
                                InteractionOption(id=str(key), label=str(key), payload=value).model_dump()
                                for key, value in source.items()
                            ],
                            "max_selections": min(request.max_selections, len(source)),
                        }
                    )
                if isinstance(request, CardPickInteraction) and "card_ids" in refs:
                    if not isinstance(refs["card_ids"], list) or not refs["card_ids"]:
                        raise ValueError("card_ids reference must resolve to a non-empty list")
                    request = CardPickInteraction.model_validate(
                        {**request.model_dump(mode="python"), "card_ids": list(refs["card_ids"])}
                    )
                if isinstance(request, NumberInteraction):
                    bounded = max(request.minimum, min(0, request.maximum))
                    value: Any = (
                        int(bounded)
                        if request.integer and bounded.is_integer()
                        else math.ceil(request.minimum)
                        if request.integer
                        else bounded
                    )
                elif isinstance(request, TextInteraction):
                    value = ""
                elif isinstance(request, ChoiceInteraction):
                    options = request.options
                    value = [option.id for option in options[: request.min_selections]]
                elif isinstance(request, CardPickInteraction):
                    value = request.card_ids[0] if request.card_ids else None
                elif isinstance(request, ConfirmInteraction):
                    value = False
                elif isinstance(request, DrawingInteraction):
                    value = []
                interactions[step.result_key] = {player_id: value for player_id in audience}
                ctx.interactions = interactions
                ctx.interaction_refs = refs
                ctx_dict["interactions"] = interactions
                ctx_dict["interaction_refs"] = refs
                emitted.append({"interaction": step.result_key, "kind": request.kind})
                continue
            bus = EventBus(build_registry(working), max_hooks=8)
            if isinstance(step, OpsStep):
                working = apply_effect(working, EffectProgram(ops=step.ops), ctx, bus=bus, rng=rng)
                emitted.extend(op.model_dump() for op in step.ops)
                continue
            raw_ops = execute_snippet(step.code, json.loads(working.model_dump_json()), ctx_dict)
            # counter_play is control flow the Room consumes, not a reducer op:
            # split it out (mirroring Room._execute_reaction) but keep it in
            # emitted_ops so callers see the counter decision.
            side_ops = extract_counter(raw_ops)[1] if reaction else raw_ops
            working = apply_snippet_diff(
                working, side_ops, ctx, origin="reaction" if reaction else "play", bus=bus, rng=rng
            )
            emitted.extend(raw_ops)
    except Exception as exc:
        error = str(exc)
        if interactions:
            keys = ", ".join(sorted(interactions))
            error = (
                f"{error} — reminder: ctx['interactions'][key] is a dict {{player_id: value}}, "
                f"and a choice value is a LIST of selected option ids (e.g. "
                f"ctx['interactions']['{next(iter(interactions))}'][ctx['actor_id']][0]). "
                f"Available keys: {keys}."
            )
        return {"ok": False, "error": error, "emitted_ops": emitted}

    return {
        "ok": True,
        "before": before,
        "after": _snapshot(working),
        "emitted_ops": emitted,
        "interactions": interactions,
    }


def make_dry_run_effect_tool(
    state: GameState | dict[str, Any],
    actor_id: str | None = None,
    card_id: str | None = None,
):
    def dry_run_effect(
        code: str | None = None,
        plan: dict[str, Any] | None = None,
        chosen_player_id: str | None = None,
        chosen_card_id: str | None = None,
    ) -> str:
        """Dry-run proposed sandbox code or a ResolutionPlan against a cloned game state."""
        if (code is None) == (plan is None):
            return json.dumps({"ok": False, "error": "provide exactly one of code or plan"})
        try:
            resolution = (
                ResolutionPlan(steps=[SnippetStep(code=code or "")])
                if code is not None
                else ResolutionPlan.model_validate(plan)
            )
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"invalid plan: {exc}"})
        return json.dumps(
            dry_run_resolution_plan(
                state,
                resolution,
                actor_id,
                card_id,
                chosen_player_id=chosen_player_id,
                chosen_card_id=chosen_card_id,
            ),
            default=str,
        )

    return StructuredTool.from_function(
        func=dry_run_effect,
        name="dry_run_effect",
        description=(
            "Execute proposed sandbox code or an ordered ResolutionPlan against a cloned live state. "
            "Use this before returning every generated snippet or hook; it reports validation/runtime errors, "
            "emitted ops, and projected public state changes without mutating the game. Reaction plans "
            "(counter_play / pending_* ctx reads) are dry-run inside a synthesized reaction window."
        ),
    )
