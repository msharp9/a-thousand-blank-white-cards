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

from models.effects import EffectProgram


class SnippetEffect(BaseModel):
    """A generated Python hook body for novel/complex card effects."""

    code: str = Field(
        description=(
            "Complete body of `def apply(state, ctx)` as a Python string. Must not contain "
            "imports, exec, eval, open, or dunder attribute access. The function receives "
            "`state` (GameState) and `ctx` (a dict with keys 'actor_id', 'event', 'card_id', "
            "'amount'). It returns None (mutates state in place)."
        )
    )
    explanation: str = Field(description="Plain-English explanation of what the snippet does.")


class InterpretResult(BaseModel):
    """Structured result of interpreting one card into an executable effect.

    Returned by :func:`agent.runtime.run_agent`. ``program``/``snippet`` carry the
    mechanical effect (a compiled op program or a generated Python hook);
    ``verdict`` reports interpretation success; ``comment`` is the arbiter's
    in-character remark; ``persona_action`` records the persona branch chosen when
    a card could not be cleanly interpreted.
    """

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
