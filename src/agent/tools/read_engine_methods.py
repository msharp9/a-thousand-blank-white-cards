"""Build the agent's exact, runtime-introspected sandbox API reference.

The reference pairs every effect op with its canonical ``SandboxGame`` method,
lists the read-only helpers and target values, and explains ordered plans and dry
runs. It never imports the board layer and degrades safely if introspection fails.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, get_args, get_origin

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _call_signature(member: object) -> inspect.Signature:
    signature = inspect.signature(member)
    return signature.replace(parameters=list(signature.parameters.values())[1:])


def _op_signatures() -> list[str]:
    """Return exact SandboxGame mutator signatures for every Op."""
    from engine.sandbox.api_surface import SandboxGame
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
        method = getattr(SandboxGame, str(op_literal), None)
        signature = _call_signature(method) if method is not None else "(unavailable)"
        signatures.append(f"  - state.{op_literal}{signature}")
    return signatures


def _literal_values(annotation: Any) -> list[str]:
    """Return the string values of a ``Literal[...]`` annotation."""
    if get_origin(annotation) is not None or get_args(annotation):
        return [str(v) for v in get_args(annotation)]
    return []


def _read_signatures() -> list[str]:
    from engine.sandbox.api_surface import SandboxGame
    from models.effects import Op

    union = get_args(Op)[0]
    op_names = {model.model_fields["op"].default for model in get_args(union)}
    excluded = {"ops", "skip", "set_draw_count", "note", "shuffle_into_deck"}
    reads = []
    for name, member in inspect.getmembers(SandboxGame):
        if name.startswith("_") or name in op_names or name in excluded:
            continue
        if isinstance(member, property):
            reads.append(f"  - state.{name}")
        elif callable(member):
            reads.append(f"  - state.{name}{_call_signature(member)}")
    return reads


def _build_reference() -> str:
    """Assemble the full introspected engine reference text."""
    from models.effects import _VALID_CARD_TARGETS, _VALID_TARGETS

    parts: list[str] = []

    parts.append("Sandbox mutators (these exact names/signatures record validated engine ops):")
    parts.extend(_op_signatures())

    parts.append("")
    parts.append("Sandbox read/control helpers:")
    parts.extend(_read_signatures())

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

    parts.append("")
    parts.append(
        "A snippet defines def apply(state, ctx) and receives SandboxGame, not GameEngine. "
        "state.draw is invalid; use "
        "state.draw_cards(target, amount). Mutators record ops, so reads within one snippet see "
        "the state at that step's start. For post-effect values, return an ordered ResolutionPlan "
        "with an ops step followed by a snippet step."
    )
    parts.append(
        "Before returning any snippet or hook, call dry_run_effect with the code or complete plan. "
        "No imports, I/O, private attributes, exec, eval, or open are allowed."
    )
    parts.append(
        "ResolutionPlan may also pause at an interaction step: "
        "{'kind':'interaction','result_key':'bids','request':{'kind':'number','prompt':'Bid',"
        "'audience':'all','sealed':true}}. Kinds: choice, number, text, card_pick, confirm, drawing. "
        "Audiences: active, all, all_others, player:<id>. Later snippets read "
        "ctx['interactions'][result_key]. Use input_refs {'options':{'result_key':'drawings','path':[]}} "
        "to turn prior submissions into options for a chained vote. "
        "A card_pick with 'from_hand':true shows EACH audience member their OWN hand "
        "(no static card_ids needed) — the way to run 'everyone discards a card they "
        "choose'; the following snippet reads ctx['interactions'][result_key] as "
        "{player_id: chosen_card_id} and calls state.destroy_card(card_id=...) on each. "
        "Set card_pick 'max_picks':N (and 'min_picks', default 1) to let each responder "
        "pick a SET of N cards ('discard 2 cards'); the collected value for that player "
        "is then a LIST of card ids instead of a single id (iterate it in the snippet)."
    )
    return "\n".join(parts)


@tool
def read_engine_methods() -> str:
    """Read the exact engine-op and SandboxGame API available to interpreted cards."""
    try:
        return _build_reference()
    except Exception:  # noqa: BLE001 — introspection failure must never break the agent
        logger.warning("read_engine_methods: introspection failed", exc_info=True)
        return "engine methods reference unavailable"


def get_read_engine_methods_tool():
    """Return the ``read_engine_methods`` LangChain tool object."""
    return read_engine_methods
