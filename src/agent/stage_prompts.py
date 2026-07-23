"""agent.stage_prompts — focused system prompts for the three-stage pipeline.

The legacy single prompt (:func:`agent.persona.build_system_prompt`) fuses
persona, op-catalog knowledge, sandbox rules, and the output contract. Each
pipeline stage needs only a slice of that:

- **intent**: the persona lives here and ONLY here — it resolves what the
  player wants, emits the in-character ``comment``, and owns ``persona_action``.
- **planner**: a neutral, creative game engineer that turns a
  :class:`~agent.contract.CardIntent` into a :class:`~agent.contract.MechanicsPlan`.
- **coder**: a sandbox specialist that turns the plan into an executable effect.

Like :mod:`agent.persona`, this module is deliberately dependency-light (no
LLM, no ``board``) so every builder is unit-testable as a plain string. Shared
text blocks are imported from ``agent.persona`` — there is ONE copy of each.
"""

from __future__ import annotations

from typing import Any

from agent import persona
from agent.contract import CardIntent, MechanicsPlan
from agent.persona import (
    CARD_ART_NOTE,
    COMMENT_REQUIREMENT,
    DRY_RUN_MANDATE,
    EFFECT_OUTPUT_KEYS,
    OP_CATALOG_GUIDE,
    OUTPUT_CONTRACT_PREAMBLE,
    PERSONA_DECISION_LOGIC,
    PERSONA_OUTPUT_KEYS,
    PERSONA_PREAMBLE,
    SANDBOX_RULES,
    authorship_note,
    describe_state,
)

_STRUGGLING_AUTHOR_NOTE_FALLBACK = (
    "HELP MODE: this card's author has already had {n} card(s) fail to work. They are "
    "almost certainly still learning how to phrase cards, not trolling you. TRY HARDER: "
    "re-read the card assuming best intent, prefer chaos_monkey (a generous, plausible "
    'reading) over giving up, and only return "invalid" if you truly cannot construct '
    "any effect. Stay witty - but aim the wit at the cosmos, not at this player."
)

# The canonical copy lands in persona via a parallel bead; fall back to an
# equivalent local block until it exists.
STRUGGLING_AUTHOR_NOTE: str = getattr(persona, "STRUGGLING_AUTHOR_NOTE", _STRUGGLING_AUTHOR_NOTE_FALLBACK)

INTENT_JOB = """\
Your JOB in this stage is to determine what the player WANTS this card to do. Capture
intent — do NOT design mechanics, pick engine ops, or write code; later stages do that.

- Resolve slang, memes, and game jargon into plain meaning before you summarize:
  `web_search` for pop-culture references and memes; `mtg_lookup` for Magic: The
  Gathering terms and keywords (it resolves rules terms like "trample" to definitions);
  `card_rag_hybrid` for similar past cards; `game_rules` for house-rule context.
- Fill the CardIntent fields:
  * summary: 1-2 sentences on what the player wants the card to do.
  * effects: the discrete intended effects, in order.
  * targets: who or what is affected, in plain terms.
  * persistence: "immediate" for a one-shot, "persistent" for a standing hook,
    "reaction" for a counterspell-style card, "validation" for a play-validation rule.
  * resolved_references: each named-game or pop-culture reference resolved to plain
    mechanics (e.g. 'trample (MTG): excess damage carries over -> here: excess point
    loss spills to next player').
  * ambiguity: "clear", "ambiguous", or "undecipherable".
  * complexity: "trivial" for a single obvious effect, "standard" for most cards,
    "complex" for multi-step / interactive / persistent mechanics.
"""

INTENT_OUTPUT_CONTRACT = f"""{OUTPUT_CONTRACT_PREAMBLE}
  {{
    "summary":             1-2 sentences: what the player wants the card to do,
    "effects":             ["each discrete intended effect, in order", ...],
    "targets":             who/what is affected, in plain terms,
    "persistence":         "immediate" | "persistent" | "reaction" | "validation",
    "resolved_references": ["each reference resolved to plain mechanics", ...],
    "ambiguity":           "clear" | "ambiguous" | "undecipherable",
    "complexity":          "trivial" | "standard" | "complex",
{PERSONA_OUTPUT_KEYS}
  }}
"""

PLANNER_PREAMBLE = """\
You are a creative game engineer for the party game "A Thousand Blank White Cards".
Your JOB is to turn an already-interpreted card intent into a concrete mechanics plan:
which engine ops to compose, which player interactions to insert, and where generated
sandbox code is needed. You design mechanics only — a later stage writes the final
code, and the player-facing commentary was already written. Plan EXACTLY the stated
intent: do not balance, nerf, buff, or censor it.
"""

CREATIVITY_MANDATE = """\
BE CREATIVE. When no single op expresses the mechanic ("target player discards a
random card", "target player draws a card from the discard pile"), plan direct state
manipulation through SandboxGame — zone moves are just pop-from-one-list/push-to-another
via the move_card-backed reducers. Call read_engine_methods to see the exact API and
targets before declaring anything infeasible. Only set feasible=false when the effect
needs capabilities the sandbox truly lacks (e.g. new UI).
"""

PLANNER_OUTPUT_CONTRACT = f"""{OUTPUT_CONTRACT_PREAMBLE}
  {{
    "strategy":          high-level approach in plain English,
    "steps":             ordered list of {{"kind": "ops" | "snippet" | "interaction", "description": "what this step does", "engine_ops": ["candidate op names/shapes in prose, for kind=ops", ...], "snippet_outline": "prose outline of the hook body, for kind=snippet", "interaction": "prose description of the choice barrier, for kind=interaction"}},
    "trigger":           null for an immediate effect | "on_play" | "on_turn_start" | "on_turn_end" | "on_draw_step" | "on_score_change" | "on_game_end" | "on_validate_play" | "on_reaction",
    "scope":             "center" (table-wide house rule) | "player" (bound to the actor),
    "feasible":          true | false,
    "infeasible_reason": plain-English reason when feasible is false, else ""
  }}
"""

CODER_PREAMBLE = """\
You are a sandbox specialist for the game engine of "A Thousand Blank White Cards".
Your JOB is to turn a mechanics plan into an executable effect: an EffectProgram of
known ops, an ordered ResolutionPlan, and/or generated sandbox code. Follow the plan's
steps in order. You never write the player-facing comment — that was already written.
If the plan has no steps, design the mechanics yourself from the intent.
"""

SNIPPET_CONSTRAINTS = """\
Snippet code is the body of `def apply(state, ctx)` given as a Python string.
- No imports, no exec/eval, no open(), no dunder attribute access — the validator
  rejects them.
- `state` is a SandboxGame facade, NOT the GameEngine: mutate via the exact op-named
  methods documented by `read_engine_methods` (e.g. `state.draw_cards('self', 2)`) and
  read via helpers like `my_hand()`, `rules()`, `conditions()`, and `state.card(id)`.
- `ctx` is a dict with keys 'actor_id', 'event', 'card_id', 'amount', and
  'target_player_ids'. on_score_change fires add per-player changes in ctx['deltas']
  ('amount' is None when players moved by different amounts); on_validate_play fires
  add 'card_title' and 'card_attributes'; reaction code reads ctx['pending_card_id'],
  ctx['pending_actor_id'], ctx['pending_card_title'], and ctx['pending_ops'].
- After an interaction barrier, ctx['interactions'][result_key] maps each responding
  player_id to their validated value (a list of card ids when max_picks > 1).
"""

CODER_OUTPUT_CONTRACT = f"{OUTPUT_CONTRACT_PREAMBLE}\n  {{\n{EFFECT_OUTPUT_KEYS}\n  }}\n"


def _render_intent(intent: CardIntent, *, exclude: set[str] | None = None) -> str:
    lines = ["--- The interpreted card intent (from the intent stage) ---"]
    for key, value in intent.model_dump(exclude=exclude).items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _render_plan(plan: MechanicsPlan) -> str:
    lines = [
        "--- The mechanics plan (from the planner stage) ---",
        f"strategy: {plan.strategy}",
        f"trigger: {plan.trigger}",
        f"scope: {plan.scope}",
        f"feasible: {plan.feasible}",
    ]
    if plan.infeasible_reason:
        lines.append(f"infeasible_reason: {plan.infeasible_reason}")
    for i, step in enumerate(plan.steps, start=1):
        lines.append(f"step {i} ({step.kind}): {step.description}")
        if step.engine_ops:
            lines.append(f"  engine_ops: {step.engine_ops}")
        if step.snippet_outline:
            lines.append(f"  snippet_outline: {step.snippet_outline}")
        if step.interaction:
            lines.append(f"  interaction: {step.interaction}")
    return "\n".join(lines)


def build_intent_prompt(
    title: str,
    description: str,
    state: Any | None = None,
    actor_id: str | None = None,
    creator_id: str | None = None,
    *,
    has_art: bool = False,
    struggling_author: bool = False,
    author_fallbacks: int = 0,
) -> str:
    """The intent-stage system prompt: persona + intent capture, no mechanics.

    This is the ONLY stage that speaks in the persona's voice: it owns the
    ``comment`` and the ``persona_action``. ``struggling_author`` prepends the
    HELP MODE block (formatted with ``author_fallbacks``) for an author whose
    previous cards fizzled.
    """
    return "\n".join(
        [
            PERSONA_PREAMBLE,
            INTENT_JOB,
            PERSONA_DECISION_LOGIC,
            COMMENT_REQUIREMENT,
            *([STRUGGLING_AUTHOR_NOTE.format(n=author_fallbacks)] if struggling_author else []),
            INTENT_OUTPUT_CONTRACT,
            "--- The card that was just played ---",
            f"Title: {title}",
            f"Description: {description}",
            *([CARD_ART_NOTE] if has_art else []),
            authorship_note(actor_id, creator_id),
            describe_state(state, actor_id),
        ]
    )


def build_planner_prompt(
    intent: CardIntent,
    state: Any | None = None,
    actor_id: str | None = None,
    creator_id: str | None = None,
) -> str:
    """The planner-stage system prompt: neutral game engineer, no persona voice."""
    author_line = ""
    if actor_id is not None and creator_id is not None:
        author_line = f"\nThis card was played by actor_id={actor_id!r} and authored by creator_id={creator_id!r}.\n"
    return "\n".join(
        [
            PLANNER_PREAMBLE,
            OP_CATALOG_GUIDE,
            SANDBOX_RULES,
            CREATIVITY_MANDATE,
            PLANNER_OUTPUT_CONTRACT,
            _render_intent(intent),
            author_line,
            describe_state(state, actor_id),
        ]
    )


def build_coder_prompt(
    intent: CardIntent,
    plan: MechanicsPlan,
    state: Any | None = None,
    actor_id: str | None = None,
) -> str:
    """The coder-stage system prompt: sandbox specialist, effect-only contract.

    The rendered intent omits ``comment``/``persona_action`` — the coder never
    touches them and its output contract has no such keys.
    """
    return "\n".join(
        [
            CODER_PREAMBLE,
            SANDBOX_RULES,
            SNIPPET_CONSTRAINTS,
            DRY_RUN_MANDATE,
            CODER_OUTPUT_CONTRACT,
            _render_intent(intent, exclude={"comment", "persona_action"}),
            _render_plan(plan),
            describe_state(state, actor_id),
        ]
    )
