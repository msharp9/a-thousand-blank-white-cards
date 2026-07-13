"""Tests for models.card schema validation."""

from __future__ import annotations

from models.card import FillerCard, GoldCard, parse_seed_card


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
