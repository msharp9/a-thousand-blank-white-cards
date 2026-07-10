"""Tests for tbwc.rooms.deck — deck building/shuffling (offline, no network)."""

from __future__ import annotations

import random

import pytest

from tbwc.rooms.deck import BLANK_CARD_RATIO, MIN_DECK, build_deck, collect_cards


def _fake_source(n: int):
    """Return a card_source yielding n distinct seed-shaped card dicts."""

    def source() -> list[dict]:
        return [{"id": f"c{i}", "title": f"T{i}", "description": f"D{i}"} for i in range(n)]

    return source


def test_collect_cards_normalises_and_dedupes() -> None:
    def source() -> list[dict]:
        return [
            {"card_id": "a", "title": "A", "description": "da", "source": "seed"},
            {"id": "b", "title": "B", "description": "db"},
            {"card_id": "a", "title": "A dup", "description": "dup"},  # duplicate id -> dropped
        ]

    cards = collect_cards(source)
    assert [c["id"] for c in cards] == ["a", "b"]
    # RAG payload maps card_id -> id and source -> creator_id.
    assert cards[0]["creator_id"] == "seed"


def test_collect_cards_preserves_canonical_ops_and_venue() -> None:
    # A structured (gold) card must carry its canonical/ops/venue through
    # normalisation — otherwise the deterministic play path has no ops to run.
    def source() -> list[dict]:
        return [
            {
                "id": "g",
                "title": "Gain 5",
                "description": "Gain 5 points.",
                "canonical": {
                    "timing": "immediate",
                    "target": "self",
                    "placement": "self",
                    "venue": "in_person",
                    "ops": [{"op": "add_points", "args": {"amount": 5, "target": "self"}}],
                },
            }
        ]

    (card,) = collect_cards(source)
    assert card["canonical"]["timing"] == "immediate"
    assert card["ops"] == [{"op": "add_points", "args": {"amount": 5, "target": "self"}}]
    assert card["venue"] == "in_person"


def test_collect_cards_parses_canonical_json_string_from_rag() -> None:
    # The RAG store persists canonical as a JSON string payload; normalisation
    # must parse it back into a dict and default venue to "all".
    import json as _json

    def source() -> list[dict]:
        return [
            {
                "card_id": "r",
                "title": "R",
                "description": "d",
                "source": "seed",
                "canonical": _json.dumps({"timing": "immediate", "target": "self", "placement": "self", "ops": []}),
            }
        ]

    (card,) = collect_cards(source)
    assert isinstance(card["canonical"], dict)
    assert card["ops"] == []
    assert card["venue"] == "all"


def test_collect_cards_without_canonical_has_no_ops_key() -> None:
    # Filler cards (no canonical) stay minimal — no canonical/ops keys leak in.
    def source() -> list[dict]:
        return [{"id": "f", "title": "F", "description": "d"}]

    (card,) = collect_cards(source)
    assert "canonical" not in card
    assert "ops" not in card


def test_collect_cards_empty_canonical_string_is_ignored() -> None:
    def source() -> list[dict]:
        return [{"id": "e", "title": "E", "description": "d", "canonical": ""}]

    (card,) = collect_cards(source)
    assert "canonical" not in card


def test_build_deck_meets_minimum_with_small_source() -> None:
    # Only 4 unique cards, but the deck must still reach MIN_DECK via padding.
    rng = random.Random(1)
    cards, deck = build_deck(card_source=_fake_source(4), rng=rng)
    assert len(deck) >= MIN_DECK
    # Every id in the deck resolves in the card registry.
    assert all(cid in cards for cid in deck)


def test_build_deck_no_padding_when_source_large_enough() -> None:
    # 40 real cards already exceed MIN_DECK, so no duplicate padding is needed —
    # but blanks are ALWAYS seeded on top (num_blanks = round(MIN_DECK * ratio)),
    # so the deck is 40 real + num_blanks blank cards.
    rng = random.Random(1)
    num_blanks = round(MIN_DECK * BLANK_CARD_RATIO)
    cards, deck = build_deck(card_source=_fake_source(40), rng=rng)
    assert len(deck) == 40 + num_blanks
    assert len(cards) == 40 + num_blanks
    # No padded copies were needed (padding only duplicates real cards as <id>#N).
    assert not any("#" in cid for cid in deck)


def test_build_deck_seeds_blank_cards() -> None:
    # Blanks are seeded ON TOP of the real cards and count toward the deck. Each
    # is a real registry entry flagged blank with empty title/description.
    num_blanks = round(MIN_DECK * BLANK_CARD_RATIO)
    cards, deck = build_deck(card_source=_fake_source(40), rng=random.Random(1))
    blank_ids = [cid for cid in deck if cards[cid].get("blank")]
    assert len(blank_ids) == num_blanks
    for cid in blank_ids:
        card = cards[cid]
        assert card["title"] == ""
        assert card["description"] == ""
        assert card["creator_id"] == "blank"
        assert cid.startswith("blank-")


def test_build_deck_blanks_count_toward_min_deck_padding() -> None:
    # A tiny source (2 real cards) plus blanks may still fall short of MIN_DECK;
    # the remainder is padded with duplicate copies of the REAL cards only —
    # blanks are never duplicated (no 'blank-*#N' ids).
    cards, deck = build_deck(card_source=_fake_source(2), rng=random.Random(1))
    assert len(deck) >= MIN_DECK
    assert all(cid in cards for cid in deck)
    assert not any(cid.startswith("blank-") and "#" in cid for cid in deck)


def test_build_deck_is_deterministic_with_seeded_rng() -> None:
    d1 = build_deck(card_source=_fake_source(40), rng=random.Random(7))[1]
    d2 = build_deck(card_source=_fake_source(40), rng=random.Random(7))[1]
    assert d1 == d2


def test_build_deck_empty_source_raises() -> None:
    with pytest.raises(ValueError, match="no cards available"):
        build_deck(card_source=lambda: [])


def test_build_deck_default_source_uses_offline_seed_file() -> None:
    # No RAG store initialised, no network: falls back to data/seed_cards.json.
    import tbwc.rag.store as store

    store._client = None
    cards, deck = build_deck(rng=random.Random(0))
    assert len(deck) >= MIN_DECK
    assert all(cid in cards for cid in deck)


def test_default_source_prefers_rag_when_populated() -> None:
    from unittest.mock import patch

    from tbwc.rooms.deck import _default_card_source

    rag_cards = [{"card_id": f"r{i}", "title": f"T{i}", "description": "d"} for i in range(3)]
    with patch("tbwc.rag.store.list_all_cards", return_value=rag_cards):
        result = _default_card_source()
    assert result == rag_cards
