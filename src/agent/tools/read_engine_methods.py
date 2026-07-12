"""agent.tools.read_engine_methods — CONTEXT-FREE engine-introspection tool.

Before the agent can translate a card into an effect it needs to know WHAT it can
express. This tool answers that by INTROSPECTING the engine at call time rather
than hardcoding a list that would silently drift as ops change:

- It walks the :data:`models.effects.Op` discriminated union, reading each member
  model's ``op`` literal + its non-default field names to print op signatures like
  ``add_points(target, amount)``.
- It reads the valid :data:`~models.effects.Target` / :data:`~models.effects.CardTarget`
  literal values.
- It introspects the public method names on :class:`engine.facade.GameEngine`.
- It notes that genuinely novel effects can be a sandboxed ``def apply(state, ctx)``
  snippet (no imports, no I/O), referencing the sandbox validator at a high level.

Layering: imports only ``models`` / ``engine`` / stdlib / LangChain — never
``board``. It NEVER raises: any introspection failure degrades to a short string.
"""

from __future__ import annotations

import logging
from typing import Any, get_args, get_origin

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _op_signatures() -> list[str]:
    """Introspect the ``Op`` union and return ``op_literal(field, field)`` strings.

    Reads the union members programmatically (``get_args`` on the annotated union),
    then for each pydantic model reads ``model_fields`` and prints the ``op`` literal
    plus every non-``op`` field name. This stays correct as ops are added/removed.
    """
    from models.effects import Op

    # Op is Annotated[Union[...], Field(discriminator=...)]; the first arg of the
    # annotation is the Union, whose args are the member models.
    annotated_args = get_args(Op)
    union = annotated_args[0] if annotated_args else Op
    members = get_args(union)

    signatures: list[str] = []
    for model in members:
        fields = getattr(model, "model_fields", {})
        # The discriminator literal value is the default of the ``op`` field.
        op_field = fields.get("op")
        op_literal = None
        if op_field is not None:
            default = getattr(op_field, "default", None)
            if isinstance(default, str):
                op_literal = default
            else:
                # Fall back to the Literal's single arg.
                literal_args = get_args(op_field.annotation)
                op_literal = literal_args[0] if literal_args else None
        if op_literal is None:
            op_literal = getattr(model, "__name__", "op")
        arg_names = [name for name in fields if name != "op"]
        signatures.append(f"  - {op_literal}({', '.join(arg_names)})")
    return signatures


def _literal_values(annotation: Any) -> list[str]:
    """Return the string values of a ``Literal[...]`` annotation."""
    if get_origin(annotation) is not None or get_args(annotation):
        return [str(v) for v in get_args(annotation)]
    return []


def _facade_methods() -> list[str]:
    """Introspect the public method names of ``engine.facade.GameEngine``."""
    from engine.facade import GameEngine

    return sorted(
        name for name in vars(GameEngine) if not name.startswith("_") and callable(getattr(GameEngine, name, None))
    )


def _build_reference() -> str:
    """Assemble the full introspected engine reference text."""
    from models.effects import _VALID_CARD_TARGETS, _VALID_TARGETS

    parts: list[str] = []

    parts.append('Available engine ops (compose these into an EffectProgram {"ops": [...]}):')
    parts.extend(_op_signatures())

    parts.append("")
    parts.append(
        "Valid player Target values: "
        + ", ".join(sorted(_VALID_TARGETS))
        + ". Open prefixed forms are also valid: 'id:<player_id>' (one specific player) and "
        "'has:<condition_key>' (every player whose conditions bag has a truthy key)."
    )
    parts.append(
        "Valid CardTarget values (for card-manipulating ops): "
        + ", ".join(sorted(_VALID_CARD_TARGETS))
        + ". Open prefixed forms: 'id:<card_id>' and 'attr:<key>=<value>' (cards whose "
        "attributes bag matches, e.g. attr:color=red)."
    )

    methods = _facade_methods()
    if methods:
        parts.append("")
        parts.append("GameEngine facade methods (the engine's physics surface): " + ", ".join(methods) + ".")

    parts.append("")
    parts.append(
        "For genuinely novel effects that no combination of ops can express, you may return a "
        "Python snippet defining `def apply(state, ctx)`. It runs in a locked-down sandbox "
        "(engine.sandbox.validate): NO imports, NO I/O (open/exec/eval), NO dunder access — "
        "just plain state/ctx manipulation. Prefer ops; use a snippet only as a last resort."
    )
    return "\n".join(parts)


@tool
def read_engine_methods() -> str:
    """Look up what the game engine can do — the available effect ops and their fields, the valid targets, the engine facade methods, and the sandboxed-snippet escape hatch — so you know how to express a card's effect."""
    try:
        return _build_reference()
    except Exception:  # noqa: BLE001 — introspection failure must never break the agent
        logger.warning("read_engine_methods: introspection failed", exc_info=True)
        return "engine methods reference unavailable"


def get_read_engine_methods_tool():
    """Return the ``read_engine_methods`` LangChain tool object."""
    return read_engine_methods
