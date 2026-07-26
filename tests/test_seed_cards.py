"""Tests for seed card data files."""

from __future__ import annotations

import json
import pathlib

from models.card import GoldCard, parse_seed_card

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"

MIGRATED_TITLES = {
    "Win the Game",
    "Person with Fewest Points Wins",
    "Red Card Rule",
    "Spicy Uno",
    "Sudden Death",
    "Total Chaos",
    "Wild Uno",
    "Going Once, Going Twice",
    "The Big Finish",
    "The Ejector Seat",
    "Boomerang",
}

IN_PERSON_TITLES = {
    "Roll for It",
    "Compliment Someone",
    "Speak Only in Questions",
    "Rename a Player",
    "Dramatic Reading",
    "The Wild Card",
    "The Referendum",
    "Tourist Mode",
    "Forbidden Word",
    "The Floor Vote",
    "Two Truths and a Lie (Card Edition)",
    "Unreliable Narrator",
    "The Hype Card",
    "The Historian",
}


def _load(filename: str) -> list[dict]:
    return json.loads((DATA_DIR / filename).read_text())


class TestGoldCards:
    def test_count(self) -> None:
        assert len(_load("seed_cards_gold.json")) == 77

    def test_all_parse_as_gold(self) -> None:
        for d in _load("seed_cards_gold.json"):
            card = parse_seed_card(d)
            assert isinstance(card, GoldCard), f"Expected GoldCard: {d['title']}"

    def test_placement_variety(self) -> None:
        """The exemplar set must show both one-shot (discard) and persistent
        (center/player) placements — that variety is what teaches the agent."""
        cards = [parse_seed_card(d) for d in _load("seed_cards_gold.json")]
        placements = {c.canonical.placement for c in cards if isinstance(c, GoldCard)}
        assert "discard" in placements
        assert placements & {"center", "player"}

    def test_has_ops_and_effect_examples(self) -> None:
        cards = [parse_seed_card(d) for d in _load("seed_cards_gold.json")]
        gold = [c for c in cards if isinstance(c, GoldCard)]
        assert any(c.canonical.ops for c in gold)
        # Effect coverage beyond plain point ops: ordered plans with executable
        # snippet steps, standalone sandbox code, or a legacy prose snippet
        # degraded to a custom_note by the v1→v2 shim.
        assert any(any(step.get("kind") == "snippet" for step in (c.canonical.steps or [])) for c in gold)
        assert any(
            c.canonical.sandbox or c.canonical.steps or any(op.op == "custom_note" for op in (c.canonical.ops or []))
            for c in gold
        )


class TestFillerCards:
    """Fillers carry full canonical annotations since the schema-v2 pass —
    they parse as GoldCard and only differ from the gold set in provenance."""

    def test_count(self) -> None:
        assert len(_load("seed_cards_fillers.json")) == 40

    def test_all_parse_as_gold(self) -> None:
        for d in _load("seed_cards_fillers.json"):
            card = parse_seed_card(d)
            assert isinstance(card, GoldCard), f"Expected GoldCard: {d['title']}"


class TestCombinedFile:
    def test_count_matches_sources(self) -> None:
        expected = sum(
            len(_load(name)) for name in ("seed_cards_gold.json", "seed_cards_fillers.json", "seed_cards_simple.json")
        )
        assert len(_load("seed_cards.json")) == expected

    def test_all_parse(self) -> None:
        for d in _load("seed_cards.json"):
            card = parse_seed_card(d)
            assert card.title

    def test_every_card_has_an_explicit_valid_venue(self) -> None:
        allowed = {"all", "in_person", "online"}
        for card in _load("seed_cards.json"):
            venue = card.get("canonical", {}).get("venue")
            assert venue in allowed, f"Invalid or missing venue for {card['id']}: {venue!r}"

    def test_migrated_eval_cards_are_not_playable_seeds(self) -> None:
        titles = {card["title"] for card in _load("seed_cards.json")}
        assert titles.isdisjoint(MIGRATED_TITLES)
        assert "Controlled Chaos" in titles

    def test_reviewed_in_person_edits_live_in_authoritative_sources(self) -> None:
        sources = _load("seed_cards_gold.json") + _load("seed_cards_fillers.json")
        venues = {card["title"]: card["canonical"]["venue"] for card in sources}
        assert {title for title in IN_PERSON_TITLES if venues.get(title) != "in_person"} == set()

    def test_fresh_deck_energy_describes_the_created_card(self) -> None:
        cards = {card["title"]: card for card in _load("seed_cards_gold.json")}
        assert cards["Fresh Deck Energy"]["description"].endswith("Wild Point: Gain 2 points.")
