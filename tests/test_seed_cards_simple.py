"""Tests for the deterministic SIMPLE seed deck (seed_cards_simple.json)."""

from __future__ import annotations

import json
import pathlib

from engine.compile import compile_card
from models.card import GoldCard, parse_seed_card
from models.effects import EffectProgram

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def _load() -> list[dict]:
    return json.loads((DATA_DIR / "seed_cards_simple.json").read_text())


class TestSimpleSeedDeck:
    def test_count(self) -> None:
        assert len(_load()) >= 28

    def test_all_parse_as_gold(self) -> None:
        for d in _load():
            card = parse_seed_card(d)
            assert isinstance(card, GoldCard), f"Expected GoldCard: {d['title']}"

    def test_all_venue_all(self) -> None:
        for d in _load():
            card = parse_seed_card(d)
            assert isinstance(card, GoldCard)
            assert card.canonical.venue == "all", f"Expected venue=all: {d['title']}"

    def test_immediate_cards_compile(self) -> None:
        for d in _load():
            card = parse_seed_card(d)
            assert isinstance(card, GoldCard)
            if card.canonical.timing != "immediate":
                continue
            prog = compile_card({**d, "ops": d["canonical"].get("ops")})
            assert isinstance(prog, EffectProgram), f"Did not compile: {d['title']}"
            assert prog.ops, f"Empty program: {d['title']}"

    def test_exactly_two_on_game_end_cards(self) -> None:
        cards = [parse_seed_card(d) for d in _load()]
        kept = [c for c in cards if isinstance(c, GoldCard) and c.canonical.trigger == "on_game_end"]
        assert len(kept) == 2, f"Expected exactly 2 on_game_end cards, got {len(kept)}"
