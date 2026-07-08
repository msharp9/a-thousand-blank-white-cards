"""Tests for the runtime Card model."""

from __future__ import annotations

from tbwc.models.cards import Card


def test_card_constructs_minimally() -> None:
    card = Card(id="c1", title="Test", description="", creator_id="p1")
    assert card.id == "c1"
    assert card.title == "Test"


def test_properties_defaults_to_empty_dict() -> None:
    card = Card(id="c1", title="Test", description="", creator_id="p1")
    assert card.properties == {}
    assert card.immediate_ops == []
    assert card.hook_ids == []


def test_open_ended_properties() -> None:
    card = Card(
        id="c2",
        title="Indestructible",
        description="cannot be destroyed",
        creator_id="p1",
        properties={"indestructible": True, "power": 5},
    )
    assert card.properties["indestructible"] is True
    assert card.properties["power"] == 5
