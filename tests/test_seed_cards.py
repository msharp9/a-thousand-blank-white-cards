"""Validate the gold seed card corpus parses against the schema."""

from __future__ import annotations

import json
from pathlib import Path

from tbwc.models.card import GoldCard, parse_seed_card

GOLD_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_cards_gold.json"


def _load_gold() -> list[dict]:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


def test_gold_file_has_20_cards() -> None:
    assert len(_load_gold()) == 20


def test_gold_all_parse_as_gold_cards() -> None:
    cards = [parse_seed_card(d) for d in _load_gold()]
    assert all(isinstance(c, GoldCard) for c in cards)
    assert len(cards) == 20


def test_gold_corpus_diversity() -> None:
    """At least one ops card, one snippet card, and one modifier."""
    cards = [parse_seed_card(d) for d in _load_gold()]
    gold = [c for c in cards if isinstance(c, GoldCard)]
    assert any(c.canonical.ops for c in gold), "expected at least one ops-based card"
    assert any(c.canonical.snippet for c in gold), "expected at least one snippet card"
    assert any(c.canonical.timing == "modifier" for c in gold), "expected at least one modifier"
