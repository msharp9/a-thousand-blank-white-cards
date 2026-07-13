"""engine.sandbox.revalidate — parse raw snippet op dicts back to a validated EffectProgram, then apply.

Final safety net: even if a malicious snippet smuggles an unexpected op dict through stdout,
Pydantic validation here rejects anything not in the Op discriminated union. The resulting
EffectProgram is applied through the SAME engine reducers as a normal card play — snippets
get no special mutation path.
"""

from __future__ import annotations

import random
from typing import Any

from pydantic import TypeAdapter, ValidationError as PydanticValidationError

from engine.apply import apply_effect
from engine.events import EventBus, HookContext
from models.effects import EffectProgram, Op, op_requires_choice
from models.game_state import GameState

_MAX_OPS = 50


class DiffValidationError(Exception):
    """Raised when the op diff from the child cannot be parsed as valid ops."""


_op_adapter: TypeAdapter[Op] = TypeAdapter(Op)


def parse_diff(raw_ops: list[dict[str, Any]], *, origin: str = "play") -> EffectProgram:
    """Parse a list of raw op dicts into a validated EffectProgram.

    ``origin`` is where the diff came from and gates pseudo/dangerous ops:
      - "play": an immediate card-play snippet. register_hook allowed;
        reject_play and counter_play rejected (nothing to veto/counter).
      - "hook": a persistent hook fire. register_hook REJECTED (depth cap: no
        self-replicating hooks); reject_play and counter_play rejected.
      - "reaction": a reaction-card snippet inside a reaction window. The
        caller extracts counter_play via ``extract_counter`` BEFORE parse_diff,
        so parse_diff still rejects any counter_play that leaks through.
      - "validate": an ON_VALIDATE_PLAY fire. Handled by the caller via
        ``extract_veto`` BEFORE parse_diff — any remaining ops are dropped by
        the caller, so parse_diff never sees "validate".

    Raises DiffValidationError if the input is not a list, exceeds the op cap,
    contains a non-dict element, or any op fails Pydantic validation.
    """
    if not isinstance(raw_ops, list):
        raise DiffValidationError(f"Expected list of ops, got {type(raw_ops).__name__}")
    if len(raw_ops) > _MAX_OPS:
        raise DiffValidationError(f"Op diff too large: {len(raw_ops)} ops (max {_MAX_OPS})")

    parsed: list[Op] = []
    for i, raw in enumerate(raw_ops):
        if not isinstance(raw, dict):
            raise DiffValidationError(f"Op[{i}] is not a dict: {raw!r}")
        if raw.get("op") == "reject_play":
            raise DiffValidationError(f"Op[{i}] reject_play is only valid in ON_VALIDATE_PLAY hooks")
        if raw.get("op") == "counter_play":
            raise DiffValidationError(f"Op[{i}] counter_play is only valid in a reaction window")
        if raw.get("op") == "register_hook" and origin == "hook":
            raise DiffValidationError(f"Op[{i}] register_hook inside a hook-produced diff (no self-replicating hooks)")
        try:
            op = _op_adapter.validate_python(raw)
        except PydanticValidationError as exc:
            raise DiffValidationError(f"Op[{i}] failed validation: {exc}") from exc
        if op_requires_choice(op):
            raise DiffValidationError(f"Op[{i}] uses a choice-requiring target — snippets have no prompt_choice flow")
        parsed.append(op)

    return EffectProgram(ops=parsed)


def extract_veto(raw_ops: list[dict[str, Any]]) -> str | None:
    """Return the first reject_play reason in a raw diff, or None.

    ON_VALIDATE_PLAY hooks are pure predicates: the caller checks the veto and
    DISCARDS any other recorded ops (documented; keeps validation side-effect
    free).
    """
    if not isinstance(raw_ops, list):
        return None
    for raw in raw_ops:
        if isinstance(raw, dict) and raw.get("op") == "reject_play":
            return str(raw.get("reason") or "play rejected")
    return None


def extract_counter(raw_ops: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split counter_play ops out of a reaction diff.

    Returns ``(mode, remaining_ops)`` where mode is the first counter_play's
    mode ("negate"/"steal_hand"/"redirect", defaulting "negate") or None when
    the reaction chose not to counter. The remaining ops are the reaction's
    side effects, to be applied via ``apply_snippet_diff`` as usual.
    """
    if not isinstance(raw_ops, list):
        return None, []
    mode: str | None = None
    rest: list[dict[str, Any]] = []
    for raw in raw_ops:
        if isinstance(raw, dict) and raw.get("op") == "counter_play":
            if mode is None:
                mode = str(raw.get("mode") or "negate")
            continue
        rest.append(raw)
    return mode, rest


def apply_snippet_diff(
    state: GameState,
    raw_ops: list[dict[str, Any]],
    ctx: HookContext,
    *,
    origin: str = "play",
    bus: EventBus | None = None,
    rng: random.Random | None = None,
) -> GameState:
    """Parse the child's op diff and apply it through the engine reducers.

    Single call-site for snippet-generated state changes; identical apply path to a card play.
    """
    program = parse_diff(raw_ops, origin=origin)
    return apply_effect(state, program, ctx, bus=bus, rng=rng)
