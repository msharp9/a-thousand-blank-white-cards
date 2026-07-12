"""engine.compile — compile a card's authoring ops into an EffectProgram.

The card-authoring layer speaks a small, human-friendly op vocabulary (see
``models.card.CardOp``): a list of ``{"op": str, "args": dict}`` entries.
This module lowers that vocabulary into the runtime discriminated union of
``models.effects`` so the deterministic engine can execute a play without
routing it through the LLM interpreter.

``compile_card`` is a PURE function: no side effects, no network, no state.

Design notes:
  * Target translation is delegated ENTIRELY to
    ``models.effects.map_authoring_target`` — this module never reinvents
    target aliasing. Choice targets ("chooser"/"target_player") therefore fall
    out of that mapping and flip ``EffectProgram.requires_choice``.
  * Not every authoring op has a runtime reducer. Authoring-only ops
    (e.g. "multiply_points", "trade_hands", "no_op", "discard_hand") are SKIPPED
    with a debug log rather than crashing. If skipping leaves zero ops, we
    return ``None`` so the caller can fall back to the LLM / a CustomNote path.
  * Malformed args (e.g. a missing required "amount") also skip the offending op
    gracefully instead of raising.
"""

from __future__ import annotations

import logging

from models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    EffectProgram,
    ExtraTurnOp,
    Op,
    ReverseOrderOp,
    ScrambleOrderOp,
    SetPointsOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
    map_authoring_target,
)

logger = logging.getLogger(__name__)

# Player-target values that mean "the actor picks at play time". Their presence
# on any produced op flips EffectProgram.requires_choice.
_CHOICE_TARGETS: frozenset[str] = frozenset({"chooser", "target_player"})
# CardTarget values that require a play-time card choice.
_CHOICE_CARD_TARGETS: frozenset[str] = frozenset({"chosen_card"})


def _extract_ops(card: dict) -> list[dict] | None:
    """Pull the authoring op list off a card, top-level ``ops`` first.

    Reads ``card["ops"]`` (lifted to the top level by
    ``rooms.deck._normalise_card``) and falls back to
    ``card["canonical"]["ops"]``. Returns ``None`` when neither is present.
    """
    ops = card.get("ops")
    if ops is None:
        ops = (card.get("canonical") or {}).get("ops")
    if not ops:
        return None
    return ops


def _get_amount(args: dict) -> int:
    """Coerce the required ``amount`` arg to int, raising if absent/invalid.

    Raises so the per-op compiler can uniformly treat malformed args as a skip.
    """
    if "amount" not in args or args["amount"] is None:
        raise ValueError("missing required 'amount'")
    return int(args["amount"])


def _compile_op(name: str, args: dict) -> Op | None:
    """Compile a single authoring op into a runtime Op, or None to skip it.

    Returns ``None`` for unknown/unsupported op names. Raises for malformed args
    of a known op (the caller turns that into a graceful skip).
    """
    if name == "add_points":
        return AddPointsOp(
            target=map_authoring_target(args.get("target", "self"), default="self"),
            amount=_get_amount(args),
        )
    if name == "subtract_points":
        return SubtractPointsOp(
            target=map_authoring_target(args.get("target", "self"), default="self"),
            amount=_get_amount(args),
        )
    if name == "set_points":
        return SetPointsOp(
            target=map_authoring_target(args.get("target", "self"), default="self"),
            amount=_get_amount(args),
        )
    if name == "steal_points":
        raw_from = args.get("from", args.get("from_target"))
        if raw_from is None:
            raise ValueError("steal_points missing 'from'/'from_target'")
        raw_to = args.get("to", args.get("to_target", "self"))
        return StealPointsOp(
            from_target=map_authoring_target(str(raw_from), default="chooser"),
            to_target=map_authoring_target(str(raw_to), default="self"),
            amount=_get_amount(args),
        )
    if name == "skip_turn":
        return SkipTurnOp(target=map_authoring_target(args.get("target", "self"), default="self"))
    if name == "extra_turn":
        return ExtraTurnOp(target=map_authoring_target(args.get("target", "self"), default="self"))
    if name == "reverse_order":
        return ReverseOrderOp()
    if name == "scramble_order":
        return ScrambleOrderOp()
    if name == "change_draw_count":
        return ChangeDrawCountOp(amount=_get_amount(args))
    if name == "draw_cards":
        return DrawCardsOp(
            target=map_authoring_target(args.get("target", "self"), default="self"),
            amount=int(args.get("amount", 1)),
        )
    if name == "set_win_condition":
        if "kind" not in args or args["kind"] is None:
            raise ValueError("set_win_condition missing 'kind'")
        return SetWinConditionOp(kind=args["kind"], threshold=args.get("threshold"))
    if name == "custom_note":
        return CustomNoteOp(note=str(args.get("note") or args.get("text") or ""))
    if name == "destroy_card":
        return DestroyCardOp(
            card_target=args.get("card_target"),
            card_id=args.get("card_id"),
        )
    return None


def _requires_choice(op: Op) -> bool:
    """True if this op needs a play-time choice from the actor.

    A player target/from_target/to_target equal to "chooser"/"target_player", or
    a DestroyCardOp whose card_target is "chosen_card".
    """
    for field in ("target", "from_target", "to_target"):
        value = getattr(op, field, None)
        if isinstance(value, str) and value in _CHOICE_TARGETS:
            return True
    card_target = getattr(op, "card_target", None)
    if isinstance(card_target, str) and card_target in _CHOICE_CARD_TARGETS:
        return True
    return False


def compile_card(card: dict) -> EffectProgram | None:
    """Compile a card's authoring ops into a runtime ``EffectProgram``.

    Reads structured ops from ``card["ops"]`` (or ``card["canonical"]["ops"]``)
    and lowers each onto the runtime Op union. Unknown ops and ops with
    malformed args are skipped (with a debug log). Returns ``None`` when the card
    carries no structured ops, or when every op was skipped — signalling the
    caller to fall back to the LLM / a CustomNote path.

    ``requires_choice`` is set when any produced op targets "chooser"/
    "target_player" or destroys a "chosen_card".
    """
    raw_ops = _extract_ops(card)
    if raw_ops is None:
        return None

    compiled: list[Op] = []
    requires_choice = False
    for entry in raw_ops:
        if not isinstance(entry, dict):
            logger.debug("compile_card: skipping non-dict op entry %r", entry)
            continue
        name = entry.get("op")
        if not name:
            logger.debug("compile_card: skipping op entry with no name %r", entry)
            continue
        args = entry.get("args") or {}
        if not isinstance(args, dict):
            logger.debug("compile_card: skipping op %r with non-dict args %r", name, args)
            continue
        try:
            op = _compile_op(name, args)
        except Exception as exc:  # malformed args (missing amount/kind, bad types) -> skip
            logger.debug("compile_card: skipping malformed op %r: %s", name, exc)
            continue
        if op is None:
            logger.debug("compile_card: skipping unsupported op %r", name)
            continue
        if _requires_choice(op):
            requires_choice = True
        compiled.append(op)

    if not compiled:
        return None
    return EffectProgram(ops=compiled, requires_choice=requires_choice)
