"""Contract tests for the deterministic PETS starter deck."""

from __future__ import annotations

import json
from pathlib import Path

from engine.compile import compile_card_plan
from models.card import GoldCard, parse_seed_card

DATA = Path(__file__).parent.parent / "data"


def _cards() -> list[dict]:
    return json.loads((DATA / "seed_cards_pets.json").read_text())


def test_pet_deck_has_exactly_30_unique_cards() -> None:
    cards = _cards()
    assert len(cards) == 30
    assert len({card["id"] for card in cards}) == 30
    assert len({card["title"] for card in cards}) == 30


def test_pet_deck_is_mostly_player_zone_and_explicit_about_ownership() -> None:
    cards = [parse_seed_card(card) for card in _cards()]
    gold = [card for card in cards if isinstance(card, GoldCard)]
    assert len(gold) == 30
    player_cards = [card for card in gold if card.canonical.placement == "player"]
    assert len(player_cards) >= 20
    assert all(card.canonical.placement_owner in {"actor", "chosen_player"} for card in player_cards)


def test_every_pet_card_compiles_without_the_agent() -> None:
    for card in _cards():
        plan = compile_card_plan(card)
        assert plan is not None and plan.steps, f"Did not compile: {card['title']}"


def test_pet_deck_teaches_direct_in_play_stealing() -> None:
    by_title = {card["title"]: card for card in _cards()}
    for title in ("Klepto Cat", "Adoption Day", "Pet Rustler"):
        canonical = by_title[title]["canonical"]
        text = json.dumps(canonical)
        assert '"from_zone": "in_play"' in text
        assert '"to_zone": "in_play"' in text
        assert '"to_player": "self"' in text
        assert '"to_zone": "hand"' not in text


def test_pet_deck_contains_cat_and_dog_win_conditions() -> None:
    descriptions = " ".join(card["description"].lower() for card in _cards())
    assert "most cats" in descriptions
    assert "most dogs" in descriptions
