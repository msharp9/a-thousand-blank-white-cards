"""Tests for board.rooms.deck — deck building/shuffling (offline, no network)."""

from __future__ import annotations

import random

import pytest

from board.rooms.deck import (
    BLANK_CARD_RATIO,
    MIN_DECK,
    PREMADE_POOL_SIZE,
    build_blanks,
    build_deck,
    build_premade_pool,
    collect_cards,
    finalize_deck,
    venue_allowed,
)


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
    # The legacy v1 canonical (timing + placement "self") is normalised to v2.
    assert card["canonical"]["placement"] == "discard"
    assert "timing" not in card["canonical"]
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


def test_collect_cards_hoists_play_on_draw_attribute() -> None:
    # A card that stages set_card_attribute(this, play_on_draw) on itself must
    # carry that attribute from the moment it enters state.cards — it fires
    # ops only run when the card is PLAYED, so a fresh-deck Landmine would
    # never auto-play on draw without this hoist (bead 100.3).
    def source() -> list[dict]:
        return [
            {
                "id": "seed-gold-072",
                "title": "Landmine",
                "description": "Whoever drew it loses 3 points.",
                "canonical": {
                    "target": "self",
                    "placement": "discard",
                    "venue": "all",
                    "ops": [
                        {
                            "op": "set_card_attribute",
                            "args": {"card_target": "this", "key": "play_on_draw", "value": True},
                        },
                        {"op": "add_points", "args": {"target": "self", "amount": -3}},
                    ],
                },
            }
        ]

    (card,) = collect_cards(source)
    assert card["attributes"] == {"play_on_draw": True}


def test_collect_cards_without_static_attribute_op_has_no_attributes_key() -> None:
    def source() -> list[dict]:
        return [
            {
                "id": "plain",
                "title": "Plain",
                "description": "d",
                "canonical": {
                    "target": "self",
                    "placement": "discard",
                    "ops": [{"op": "add_points", "args": {"target": "self", "amount": 3}}],
                },
            }
        ]

    (card,) = collect_cards(source)
    assert "attributes" not in card


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
    import agent.rag.store as store

    store._client = None
    cards, deck = build_deck(rng=random.Random(0))
    assert len(deck) >= MIN_DECK
    assert all(cid in cards for cid in deck)


# --------------------------------------------------------------------------
# Venue filtering (bd 70n.16)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("card_venue", "mode", "expected"),
    [
        # mode "both" allows everything.
        ("all", "both", True),
        ("online", "both", True),
        ("in_person", "both", True),
        # mode "online" drops in_person.
        ("all", "online", True),
        ("online", "online", True),
        ("in_person", "online", False),
        # mode "in_person" drops online.
        ("all", "in_person", True),
        ("in_person", "in_person", True),
        ("online", "in_person", False),
    ],
)
def test_venue_allowed_truth_table(card_venue: str, mode: str, expected: bool) -> None:
    assert venue_allowed(card_venue, mode) is expected


def test_venue_allowed_filtered_modes_reject_unknown_or_missing_venue() -> None:
    for venue in ("teleport", "", None):
        assert venue_allowed(venue, "online") is False
        assert venue_allowed(venue, "in_person") is False
        assert venue_allowed(venue, "both") is True


def _mixed_venue_source():
    """A source with one card of each venue plus a venue-less filler."""

    def source() -> list[dict]:
        base = {"timing": "immediate", "target": "self", "placement": "self", "ops": []}
        return [
            {"id": "a", "title": "All", "description": "d", "canonical": {**base, "venue": "all"}},
            {"id": "o", "title": "Online", "description": "d", "canonical": {**base, "venue": "online"}},
            {"id": "p", "title": "InPerson", "description": "d", "canonical": {**base, "venue": "in_person"}},
            {"id": "f", "title": "Filler", "description": "d"},  # no canonical -> venue "all"
        ]

    return source


def test_collect_cards_venue_mode_online_drops_in_person() -> None:
    ids = [c["id"] for c in collect_cards(_mixed_venue_source(), venue_mode="online")]
    assert ids == ["a", "o"]  # in_person and venue-less cards dropped


def test_collect_cards_venue_mode_in_person_drops_online() -> None:
    ids = [c["id"] for c in collect_cards(_mixed_venue_source(), venue_mode="in_person")]
    assert ids == ["a", "p"]  # online and venue-less cards dropped


def test_collect_cards_venue_mode_both_keeps_all() -> None:
    ids = [c["id"] for c in collect_cards(_mixed_venue_source(), venue_mode="both")]
    assert ids == ["a", "o", "p", "f"]


def test_collect_cards_default_venue_mode_is_both() -> None:
    # No venue_mode arg = "both" = no filtering (back-compat).
    ids = [c["id"] for c in collect_cards(_mixed_venue_source())]
    assert ids == ["a", "o", "p", "f"]


def test_build_deck_online_contains_no_in_person_cards() -> None:
    cards, deck = build_deck(
        card_source=_mixed_venue_source(),
        rng=random.Random(1),
        venue_mode="online",
    )
    # No card (real or padded copy) carries venue in_person.
    assert all(c.get("venue", "all") != "in_person" for c in cards.values())


def test_venue_less_card_is_rejected_by_filtered_modes() -> None:
    def source() -> list[dict]:
        return [{"id": "nv", "title": "NoVenue", "description": "d"}]

    assert collect_cards(source, venue_mode="online") == []
    assert collect_cards(source, venue_mode="in_person") == []
    assert collect_cards(source, venue_mode="both")[0]["id"] == "nv"


def test_seed_data_has_an_in_person_card() -> None:
    # The premade pool must tag at least one physical card as in_person so that
    # venue filtering has a real effect on the default deck.
    import json
    from pathlib import Path

    seed = json.loads(Path("data/seed_cards.json").read_text())
    venues = [c.get("canonical", {}).get("venue") for c in seed]
    assert "in_person" in venues


def test_online_premade_pool_from_full_seed_corpus_has_only_allowed_venues() -> None:
    import json
    from pathlib import Path

    seed = json.loads(Path("data/seed_cards.json").read_text())
    cards, pool = build_premade_pool(
        count=PREMADE_POOL_SIZE,
        card_source=lambda: seed,
        rng=random.Random(0),
        venue_mode="online",
    )

    assert len(pool) == PREMADE_POOL_SIZE
    assert {cards[card_id]["venue"] for card_id in pool} <= {"all", "online"}


def test_default_source_prefers_rag_when_populated() -> None:
    from unittest.mock import patch

    from board.rooms.deck import _default_card_source

    rag_cards = [{"card_id": f"r{i}", "title": f"T{i}", "description": "d"} for i in range(3)]
    with patch("agent.rag.store.list_all_cards", return_value=rag_cards):
        result = _default_card_source()
    assert result == rag_cards


# --------------------------------------------------------------------------
# Pre-made pool + deck finalisation (setup two-step start flow, bd 70n.8/9)
# --------------------------------------------------------------------------


def test_build_premade_pool_samples_30_cards_from_full_source() -> None:
    cards, pool = build_premade_pool(count=30, card_source=_fake_source(50), rng=random.Random(0))
    assert len(pool) == PREMADE_POOL_SIZE == 30
    assert len(set(pool)) == 30
    assert all(cid in cards for cid in pool)
    # Membership comes from the whole source, not only the first 30 entries.
    assert any(int(cid.removeprefix("c")) >= 30 for cid in pool)


def test_build_premade_pool_is_deterministic_with_seeded_rng() -> None:
    p1 = build_premade_pool(count=30, card_source=_fake_source(50), rng=random.Random(3))[1]
    p2 = build_premade_pool(count=30, card_source=_fake_source(50), rng=random.Random(3))[1]
    assert p1 == p2


def test_build_premade_pool_different_rng_changes_membership() -> None:
    p1 = build_premade_pool(count=30, card_source=_fake_source(50), rng=random.Random(1))[1]
    p2 = build_premade_pool(count=30, card_source=_fake_source(50), rng=random.Random(2))[1]
    assert set(p1) != set(p2)


def test_build_premade_pool_treats_simple_cards_like_other_cards() -> None:
    def source() -> list[dict]:
        return [
            {"id": "seed-simple-001", "title": "Simple", "description": "Gain 1 point."},
            {"id": "seed-gold-001", "title": "Gold", "description": "Reverse order."},
        ]

    cards, pool = build_premade_pool(count=2, card_source=source, rng=random.Random(0))
    assert set(pool) == {"seed-simple-001", "seed-gold-001"}
    assert set(cards) == set(pool)


def test_build_premade_pool_pads_small_source_with_copies() -> None:
    # Only 4 real cards but count=30: pad with distinct '<id>#N' copies.
    cards, pool = build_premade_pool(count=30, card_source=_fake_source(4), rng=random.Random(1))
    assert len(pool) == 30
    assert all(cid in cards for cid in pool)
    assert any("#" in cid for cid in pool)  # padding copies were needed


def test_build_premade_pool_venue_mode_online_excludes_in_person() -> None:
    cards, pool = build_premade_pool(
        count=10,
        card_source=_mixed_venue_source(),
        rng=random.Random(1),
        venue_mode="online",
    )
    assert all(c.get("venue", "all") != "in_person" for c in cards.values())


def test_build_premade_pool_empty_source_raises() -> None:
    with pytest.raises(ValueError, match="no cards available"):
        build_premade_pool(card_source=lambda: [])


def test_finalize_deck_composition_for_two_players() -> None:
    premade = [f"pm{i}" for i in range(30)]
    authored = [f"au{i}" for i in range(10)]
    blank_cards, deck = finalize_deck(premade, authored, 2, rng=random.Random(0))
    # 30 premade + 10 authored + 5 blanks/player * 2 = 50.
    assert len(deck) == 50
    # Exactly 10 new blank cards returned.
    assert len(blank_cards) == 10
    for card in blank_cards.values():
        assert card["blank"] is True
    # Every deck id resolves either as premade, authored, or a new blank.
    known = set(premade) | set(authored) | set(blank_cards)
    assert set(deck) == known


def test_finalize_deck_is_deterministic_with_seeded_rng() -> None:
    premade = [f"pm{i}" for i in range(30)]
    authored = [f"au{i}" for i in range(10)]
    d1 = finalize_deck(premade, authored, 2, rng=random.Random(9))[1]
    d2 = finalize_deck(premade, authored, 2, rng=random.Random(9))[1]
    assert d1 == d2


def test_build_blanks_returns_distinct_blank_ids() -> None:
    blanks = build_blanks(5)
    assert set(blanks) == {f"blank-{i}" for i in range(5)}
    for cid, card in blanks.items():
        assert card["id"] == cid
        assert card["blank"] is True
        assert card["title"] == ""
        assert card["description"] == ""


def test_build_blanks_namespace_prevents_cross_game_id_collisions() -> None:
    first = build_blanks(5, namespace="ROOM01")
    second = build_blanks(5, namespace="ROOM02")
    assert set(first).isdisjoint(second)
    assert set(first) == {f"blank-ROOM01-{i}" for i in range(5)}


def test_finalize_deck_uses_blank_namespace() -> None:
    blanks, deck = finalize_deck(
        ["premade"],
        ["authored"],
        1,
        blanks_per_player=1,
        blank_namespace="ROOM01",
        rng=random.Random(0),
    )
    assert set(blanks) == {"blank-ROOM01-0"}
    assert "blank-ROOM01-0" in deck
