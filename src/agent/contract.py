"""agent.contract — the shared, forward-looking interpretation result contract.

This module defines :class:`InterpretResult`, the canonical shape returned by the
card-interpretation entry point (``agent.runtime.run_agent``), plus
:class:`SnippetEffect`, the generated-hook payload that result can carry.

Design constraint: this module imports ONLY from ``models.*`` and ``typing`` — no
``board``, no heavy agent/LangChain dependencies — so it stays a clean, cheap-to-import
shared contract that both the agent and its callers can depend on.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from models.effects import EffectProgram, OpsStep, RegisterHookOp, ResolutionPlan, SnippetStep


class SnippetEffect(BaseModel):
    """A generated Python hook body for novel/complex card effects."""

    code: str = Field(
        description=(
            "Complete body of `def apply(state, ctx)` as a Python string. Must not contain "
            "imports, exec, eval, open, or dunder attribute access. The function receives "
            "`state` (a SandboxGame facade — reads like my_hand()/rules()/conditions() and "
            "op-recording mutators) and `ctx` (a dict with keys 'actor_id', 'event', "
            "'card_id', 'amount'; ON_VALIDATE_PLAY fires additionally carry 'card_title' "
            "and 'card_attributes'). It returns None (recorded ops are re-validated and "
            "applied by the engine). After an interaction barrier, ctx['interactions'] "
            "contains result_key -> {player_id: validated_value}."
        )
    )
    explanation: str = Field(description="Plain-English explanation of what the snippet does.")
    trigger: str | None = Field(
        default=None,
        description=(
            "None for an immediate one-shot effect (runs once, now). A GameEvent value "
            "('on_play', 'on_turn_start', 'on_turn_end', 'on_draw_step', 'on_score_change', "
            "'on_game_end', 'on_validate_play') declares a PERSISTENT hook: the room "
            "registers it via RegisterHookOp and it fires on every such event. "
            "'on_reaction' is special: it marks the card as a REACTION (counterspell-type) "
            "— no hook is registered; the code runs when the card is played into a "
            "reaction window, where it may call state.counter_play(mode)."
        ),
    )
    scope: Literal["player", "center"] = Field(
        default="center",
        description="Persistent hooks only: 'center' = table-wide house rule; 'player' = bound to the actor.",
    )


class InterpretResult(BaseModel):
    """Structured result of interpreting one card into an executable effect.

    Returned by :func:`agent.runtime.run_agent`. ``program``/``snippet`` carry the
    mechanical effect (a compiled op program or a generated Python hook);
    ``verdict`` reports interpretation success; ``comment`` is the arbiter's
    in-character remark; ``persona_action`` records the persona branch chosen when
    a card could not be cleanly interpreted.
    """

    plan: ResolutionPlan | None = Field(
        default=None,
        description="An ordered resolution plan. When present it supersedes legacy program/snippet fields.",
    )
    program: EffectProgram | None = Field(
        default=None,
        description="The compiled effect program of known ops, or None when no program was produced.",
    )
    snippet: SnippetEffect | None = Field(
        default=None,
        description="A generated Python hook body for novel/complex effects, or None.",
    )
    verdict: str = Field(
        default="invalid",
        description="Overall interpretation verdict: 'ok', 'invalid', or 'needs_choice'.",
    )
    comment: str = Field(
        default="",
        description=(
            "A short, in-character funny comment about the card / game state. Populated by "
            "the real agent later; empty string for now."
        ),
    )
    persona_action: Literal["none", "do_nothing", "punish_author", "chaos_monkey", "random_solution"] = Field(
        default="none",
        description=(
            "The in-character branch chosen when a card can't be cleanly interpreted. "
            "Populated by the real agent later; 'none' for now."
        ),
    )

    def to_plan(self) -> ResolutionPlan:
        if self.plan is not None:
            return self.plan

        steps: list[OpsStep | SnippetStep] = []
        if self.program is not None and self.program.ops:
            steps.append(OpsStep(ops=self.program.ops))
        if self.snippet is not None:
            # "on_reaction" marks the card as a reaction, not a persistent hook:
            # the code is a run-now step executed inside the reaction window.
            if self.snippet.trigger is not None and self.snippet.trigger != "on_reaction":
                steps.append(
                    OpsStep(
                        ops=[
                            RegisterHookOp(
                                event=self.snippet.trigger,
                                scope=self.snippet.scope,
                                code=self.snippet.code,
                            )
                        ]
                    )
                )
            else:
                steps.append(SnippetStep(code=self.snippet.code, explanation=self.snippet.explanation))
        return ResolutionPlan(steps=steps)
