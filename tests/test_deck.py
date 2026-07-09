"""Tests for tbwc.rooms.deck — deck building/shuffling (offline, no network)."""

from __future__ import annotations

import random

import pytest

from tbwc.rooms.deck import MIN_DECK, build_deck, collect_cards


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


def test_build_deck_meets_minimum_with_small_source() -> None:
    # Only 4 unique cards, but the deck must still reach MIN_DECK via padding.
    rng = random.Random(1)
    cards, deck = build_deck(card_source=_fake_source(4), rng=rng)
    assert len(deck) >= MIN_DECK
    # Every id in the deck resolves in the card registry.
    assert all(cid in cards for cid in deck)


def test_build_deck_no_padding_when_source_large_enough() -> None:
    rng = random.Random(1)
    cards, deck = build_deck(card_source=_fake_source(40), rng=rng)
    assert len(deck) == 40
    assert len(cards) == 40
    assert "#" not in "".join(deck)  # no padded copies were needed


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
