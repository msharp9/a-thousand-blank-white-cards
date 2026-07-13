"""Tests for seed card data files."""

from __future__ import annotations

import json
import pathlib

from models.card import FillerCard, GoldCard, parse_seed_card

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def _load(filename: str) -> list[dict]:
    return json.loads((DATA_DIR / filename).read_text())


class TestGoldCards:
    def test_count(self) -> None:
        assert len(_load("seed_cards_gold.json")) == 27

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
    def test_count(self) -> None:
        assert len(_load("seed_cards_fillers.json")) == 40

    def test_all_parse_as_filler(self) -> None:
        for d in _load("seed_cards_fillers.json"):
            card = parse_seed_card(d)
            assert isinstance(card, FillerCard), f"Expected FillerCard: {d['title']}"

    def test_no_canonical_key(self) -> None:
        for d in _load("seed_cards_fillers.json"):
            assert "canonical" not in d


class TestCombinedFile:
    def test_count(self) -> None:
        assert len(_load("seed_cards.json")) == 67

    def test_all_parse(self) -> None:
        for d in _load("seed_cards.json"):
            card = parse_seed_card(d)
            assert card.title
