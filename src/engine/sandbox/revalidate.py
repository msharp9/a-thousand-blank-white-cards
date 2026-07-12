"""engine.sandbox.revalidate — parse raw snippet op dicts back to a validated EffectProgram, then apply.

Final safety net: even if a malicious snippet smuggles an unexpected op dict through stdout,
Pydantic validation here rejects anything not in the Op discriminated union. The resulting
EffectProgram is applied through the SAME engine reducers as a normal card play — snippets
get no special mutation path.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError as PydanticValidationError

from engine.apply import apply_effect
from engine.events import HookContext
from models.effects import EffectProgram, Op, op_requires_choice
from models.game_state import GameState

_MAX_OPS = 50


class DiffValidationError(Exception):
    """Raised when the op diff from the child cannot be parsed as valid ops."""


_op_adapter: TypeAdapter[Op] = TypeAdapter(Op)


def parse_diff(raw_ops: list[dict[str, Any]]) -> EffectProgram:
    """Parse a list of raw op dicts into a validated EffectProgram.

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
        try:
            op = _op_adapter.validate_python(raw)
        except PydanticValidationError as exc:
            raise DiffValidationError(f"Op[{i}] failed validation: {exc}") from exc
        if op_requires_choice(op):
            raise DiffValidationError(f"Op[{i}] uses a choice-requiring target — snippets have no prompt_choice flow")
        parsed.append(op)

    return EffectProgram(ops=parsed)


def apply_snippet_diff(state: GameState, raw_ops: list[dict[str, Any]], ctx: HookContext) -> GameState:
    """Parse the child's op diff and apply it through the engine reducers.

    Single call-site for snippet-generated state changes; identical apply path to a card play.
    """
    program = parse_diff(raw_ops)
    return apply_effect(state, program, ctx)
