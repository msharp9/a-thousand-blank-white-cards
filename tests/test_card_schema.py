"""Tests for models.card schema validation."""

from __future__ import annotations

from models.card import FillerCard, GoldCard, hoist_static_attributes, parse_seed_card
from models.effects import AddPointsOp, SetCardAttributeOp


GOLD_DICT = {
    "title": "Gain 5 Points",
    "description": "You feel great about yourself. Gain 5 points.",
    "canonical": {
        "timing": "immediate",
        "target": "self",
        "placement": "self",
        "ops": [{"op": "add_points", "args": {"amount": 5, "target": "self"}}],
    },
}

FILLER_DICT = {
    "title": "Nothing Happens",
    "description": "Play this card. Nothing happens. You wonder why you did that.",
}


def test_parse_gold_card() -> None:
    card = parse_seed_card(GOLD_DICT)
    assert isinstance(card, GoldCard)
    assert card.title == "Gain 5 Points"
    # Legacy v1 dict: timing "immediate" + placement "self" normalise to v2 "discard".
    assert card.canonical.placement == "discard"
    assert card.canonical.ops is not None
    assert card.canonical.ops[0].op == "add_points"


def test_parse_filler_card() -> None:
    card = parse_seed_card(FILLER_DICT)
    assert isinstance(card, FillerCard)
    assert card.title == "Nothing Happens"


def test_gold_card_legacy_prose_snippet_becomes_note() -> None:
    """A legacy prose snippet degrades to a custom_note op (v2 has no prose field)."""
    d = {**GOLD_DICT, "canonical": {**GOLD_DICT["canonical"], "ops": None, "snippet": "Custom rule."}}
    card = parse_seed_card(d)
    assert isinstance(card, GoldCard)
    assert card.canonical.sandbox is None
    assert card.canonical.ops is not None
    assert card.canonical.ops[-1].op == "custom_note"
    assert card.canonical.ops[-1].args["note"] == "Custom rule."


def test_gold_card_legacy_code_snippet_becomes_sandbox() -> None:
    """A legacy `def apply` snippet is carried as executable sandbox code."""
    code = 'def apply(state, ctx):\n    state.add_points("self", 1)'
    d = {**GOLD_DICT, "canonical": {**GOLD_DICT["canonical"], "ops": None, "snippet": code}}
    card = parse_seed_card(d)
    assert isinstance(card, GoldCard)
    assert card.canonical.sandbox == code
    assert card.canonical.ops is None


def test_filler_has_no_canonical() -> None:
    card = parse_seed_card(FILLER_DICT)
    assert not hasattr(card, "canonical")


def test_hoist_static_attributes_reads_authoring_op_dicts() -> None:
    ops = [
        {"op": "set_card_attribute", "args": {"card_target": "this", "key": "play_on_draw", "value": True}},
        {"op": "add_points", "args": {"target": "self", "amount": -3}},
    ]
    assert hoist_static_attributes(ops) == {"play_on_draw": True}


def test_hoist_static_attributes_reads_runtime_op_instances() -> None:
    ops = [SetCardAttributeOp(card_target="this", key="play_on_draw", value=True), AddPointsOp(target="self", amount=1)]
    assert hoist_static_attributes(ops) == {"play_on_draw": True}


def test_hoist_static_attributes_ignores_other_targets_and_keys() -> None:
    ops = [
        {"op": "set_card_attribute", "args": {"card_target": "all", "key": "play_on_draw", "value": True}},
        {"op": "set_card_attribute", "args": {"card_target": "this", "key": "color", "value": "red"}},
    ]
    assert hoist_static_attributes(ops) == {}


def test_hoist_static_attributes_empty_or_missing_ops() -> None:
    assert hoist_static_attributes(None) == {}
    assert hoist_static_attributes([]) == {}
    assert hoist_static_attributes([{"op": "add_points", "args": {"target": "self", "amount": 1}}]) == {}
