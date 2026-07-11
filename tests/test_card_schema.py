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
    assert card.canonical.timing == "immediate"
    assert card.canonical.ops is not None
    assert card.canonical.ops[0].op == "add_points"


def test_parse_filler_card() -> None:
    card = parse_seed_card(FILLER_DICT)
    assert isinstance(card, FillerCard)
    assert card.title == "Nothing Happens"


def test_gold_card_ops_and_snippet_are_optional() -> None:
    """A gold card may use snippet instead of ops."""
    d = {**GOLD_DICT, "canonical": {**GOLD_DICT["canonical"], "ops": None, "snippet": "Custom rule."}}
    card = parse_seed_card(d)
    assert isinstance(card, GoldCard)
    assert card.canonical.snippet == "Custom rule."


def test_filler_has_no_canonical() -> None:
    card = parse_seed_card(FILLER_DICT)
    assert not hasattr(card, "canonical")
