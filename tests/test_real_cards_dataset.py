"""Validates the real_cards.json eval dataset shape and coverage."""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "eval" / "real_cards.json"

_VALID_TIMING = {"immediate", "modifier"}
_VALID_TARGET = {"self", "player", "all", "center"}
_VALID_PLACEMENT = {"self", "player", "center"}
_VALID_SIGN = {"positive", "negative", "neutral"}


def _load() -> list[dict]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_count_in_range() -> None:
    cards = _load()
    assert 30 <= len(cards) <= 50


def test_every_card_has_required_fields() -> None:
    for c in _load():
        assert c["title"]
        assert c["description"]
        assert "image_url" in c
        hc = c["human_canonical"]
        assert hc is not None
        assert hc["timing"] in _VALID_TIMING
        assert hc["target"] in _VALID_TARGET
        assert hc["placement"] in _VALID_PLACEMENT
        assert hc["magnitude_sign"] in _VALID_SIGN
        assert ("ops" in hc) or ("snippet" in hc)


def test_diversity() -> None:
    cards = _load()
    hcs = [c["human_canonical"] for c in cards]
    assert sum(1 for h in hcs if "snippet" in h and h.get("snippet")) >= 5
    assert sum(1 for h in hcs if h["timing"] == "modifier") >= 6
    assert sum(1 for h in hcs if h["target"] == "all") >= 3
    assert any(h["magnitude_sign"] == "negative" for h in hcs)
    assert any(h["magnitude_sign"] == "neutral" for h in hcs)


def test_titles_unique() -> None:
    titles = [c["title"] for c in _load()]
    assert len(titles) == len(set(titles))
