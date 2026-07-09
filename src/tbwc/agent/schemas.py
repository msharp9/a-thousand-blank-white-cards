"""tbwc.agent.schemas — Pydantic structured-output contracts for the interpretation agent.

These schemas are passed to ChatOpenAI.with_structured_output(); field names and
descriptions are the LLM contract, so keep them accurate and stable. Do NOT import
from tbwc.agent.nodes or tbwc.agent.graph (avoid cycles).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Interpretation(BaseModel):
    """Classification of a card's effect produced by the classify node."""

    placement: Literal["self", "player", "center"] = Field(
        description=(
            "Where the card is placed after being played (human authoring vocabulary). "
            "'self' = in front of the player who played it; 'player' = targets a "
            "specific other player the actor chooses (maps to the runtime 'chooser' "
            "target — the actor picks at play time); 'center' = shared/table area "
            "(a placement, NOT a player target)."
        )
    )
    timing: Literal["immediate", "modifier"] = Field(
        description=(
            "'immediate' = the effect fires once when played; 'modifier' = the card "
            "stays in play and modifies future events."
        )
    )
    trigger_event: str | None = Field(
        default=None,
        description=(
            "For modifier cards: the event name that fires the hook, e.g. 'on_draw', "
            "'on_play', 'on_score'. None for immediate cards."
        ),
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Open-ended card flags, e.g. {'indestructible': True, 'uncounterable': True, "
            "'playable_out_of_turn': False}."
        ),
    )
    mode: Literal["immediate", "snippet"] = Field(
        description=(
            "'immediate' = produce an EffectProgram of known ops; 'snippet' = generate a "
            "Python def apply(state, ctx) hook body."
        )
    )
    rationale: str = Field(description="One sentence explaining the classification choices.")


class SnippetEffect(BaseModel):
    """A generated Python hook body for novel/complex card effects."""

    code: str = Field(
        description=(
            "Complete body of `def apply(state, ctx)` as a Python string. Must not contain "
            "imports, exec, eval, open, or dunder attribute access. The function receives "
            "`state` (GameState) and `ctx` (a dict with keys 'player_id', 'card', 'event'). "
            "It returns None (mutates state in place)."
        )
    )
    explanation: str = Field(description="Plain-English explanation of what the snippet does.")


class Verdict(BaseModel):
    """Multi-dimensional judge verdict from the judge node."""

    intent: bool = Field(description="True if the interpretation captures the card's intended effect.")
    timing: bool = Field(description="True if the timing classification (immediate/modifier) is correct.")
    target: bool = Field(description="True if the target/placement classification is correct.")
    trigger: bool = Field(description="True if the trigger_event is correct (or correctly None for immediate cards).")
    magnitude: bool = Field(
        description=(
            "True if the magnitude/scale of the effect matches the card text "
            "(e.g. not inflating '+1 point' to '+10 points')."
        )
    )
    ok: bool = Field(
        description="Overall pass/fail. True only if ALL of intent, timing, target, trigger, and magnitude are True."
    )
    reason: str = Field(description="Brief explanation of any failures, or 'All checks passed'.")
