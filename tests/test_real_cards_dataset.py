"""Validates the eval datasets: the gold eval_cards.json and raw real_cards.json.

Two corpora live under ``data/eval/``:

* ``eval_cards.json`` -- the hand-annotated gold set (each card carries a
  ``human_canonical`` label; scored by the eval harness). It has no
  ``image_url`` because its entries were authored, not transcribed from photos.
* ``real_cards.json`` -- the full Imgur album transcribed verbatim (700 cards
  with real ``image_url`` direct links and ``human_canonical`` left ``None``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "eval"
GOLD = DATA_DIR / "eval_cards.json"
REAL = DATA_DIR / "real_cards.json"

_VALID_TIMING = {"immediate", "modifier"}
_VALID_TARGET = {"self", "player", "all", "center"}
_VALID_PLACEMENT = {"discard", "self", "player", "center", "destroy"}
_VALID_SIGN = {"positive", "negative", "neutral"}

# A genuine Imgur direct image link, e.g. https://i.imgur.com/abc123.jpeg.
_IMGUR_DIRECT_URL_RE = re.compile(
    r"^https://i\.imgur\.com/[A-Za-z0-9]+\.(?:jpg|jpeg|png|gif|webp)$",
    re.IGNORECASE,
)
# Substrings marking a URL as a known offline placeholder rather than a real photo.
_PLACEHOLDER_MARKERS = ("fallback_", "placeholder")


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_placeholder(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


# --------------------------------------------------------------------------- #
# Gold corpus: eval_cards.json (hand-annotated, scored by the harness).
# --------------------------------------------------------------------------- #


def test_gold_count_in_range() -> None:
    cards = _load(GOLD)
    assert 30 <= len(cards) <= 50


def test_gold_has_no_image_url() -> None:
    """The gold set is authored, not photo-derived, so it carries no image_url.

    The previous corpus shipped broken ``fallback_NNN.jpg`` placeholders; those
    were dropped when the gold set was split out into eval_cards.json.
    """
    for c in _load(GOLD):
        assert "image_url" not in c


def test_gold_every_card_has_required_fields() -> None:
    for c in _load(GOLD):
        assert c["title"]
        assert c["description"]
        hc = c["human_canonical"]
        assert hc is not None
        assert hc["timing"] in _VALID_TIMING
        assert hc["target"] in _VALID_TARGET
        assert hc["placement"] in _VALID_PLACEMENT
        assert hc["magnitude_sign"] in _VALID_SIGN
        assert hc.get("ops") or hc.get("snippet") or hc.get("steps")


def test_gold_diversity() -> None:
    cards = _load(GOLD)
    hcs = [c["human_canonical"] for c in cards]
    assert sum(1 for h in hcs if "snippet" in h and h.get("snippet")) >= 5
    assert sum(1 for h in hcs if h["timing"] == "modifier") >= 6
    assert sum(1 for h in hcs if h["target"] == "all") >= 3
    assert any(h["magnitude_sign"] == "negative" for h in hcs)
    assert any(h["magnitude_sign"] == "neutral" for h in hcs)


def test_gold_titles_unique() -> None:
    titles = [c["title"] for c in _load(GOLD)]
    assert len(titles) == len(set(titles))


def test_gold_includes_ordered_plan_and_game_altering_capability_cases() -> None:
    cards = _load(GOLD)
    titles = {card["title"] for card in cards}

    assert {"Chess Master", "Total Chaos", "Most Cards Drawn Wins", "Basic Uno", "Spicy Uno", "Wild Uno"} <= titles
    assert any(card["human_canonical"].get("steps") for card in cards)


def test_wild_uno_eval_mechanics_match_room_tested_seed_plan() -> None:
    """Keep the scored Wild Uno plan tied to the exemplar exercised in Room tests."""
    evaluated = {card["title"]: card for card in _load(GOLD)}["Wild Uno"]["human_canonical"]
    seeds = _load(DATA_DIR.parent / "seed_cards_gold.json")
    executable = {card["title"]: card for card in seeds}["Wild Uno"]["canonical"]

    assert evaluated["steps"] == executable["steps"]


# --------------------------------------------------------------------------- #
# Raw corpus: real_cards.json (full album, transcribed verbatim).
# --------------------------------------------------------------------------- #


def test_real_is_full_album() -> None:
    cards = _load(REAL)
    assert len(cards) >= 500  # the curated album holds ~700 photos


def test_real_every_image_url_is_a_real_imgur_direct_link() -> None:
    """Every real_cards.json entry links to a genuine Imgur photo (no placeholders)."""
    cards = _load(REAL)
    assert cards, "expected a non-empty corpus"
    for c in cards:
        url = c["image_url"]
        assert not _is_placeholder(url), f"placeholder URL: {url}"
        assert _IMGUR_DIRECT_URL_RE.match(url), f"not an Imgur direct link: {url}"


def test_real_image_urls_unique() -> None:
    urls = [c["image_url"] for c in _load(REAL)]
    assert len(urls) == len(set(urls))


def test_real_cards_have_transcription_shape() -> None:
    """Each raw card has the expected top-level fields."""
    for c in _load(REAL):
        assert set(c.keys()) == {"image_url", "title", "description", "human_canonical"}
        assert isinstance(c["title"], str)
        assert isinstance(c["description"], str)


# --------------------------------------------------------------------------- #
# real_cards.json human_canonical annotations (see data/eval/CANONICAL_SPEC.md).
# --------------------------------------------------------------------------- #

_REAL_TIMING = {"immediate", "modifier"}
_REAL_TARGET = {"self", "player", "all", "all_others", "card", "all_cards", "none"}
_REAL_PLACEMENT = {"discard", "center", "player", "self", "destroy"}
_REAL_VENUE = {"all", "in_person", "online"}
_REAL_SIGN = {"positive", "negative", "neutral"}
_REAL_TRIGGER = {"on_play", "on_draw", "on_turn_start", "on_turn_end", "on_score", None}


def test_real_every_card_is_annotated() -> None:
    """Every real card has a fully-populated human_canonical (no nulls left)."""
    for c in _load(REAL):
        hc = c["human_canonical"]
        assert hc is not None, f"unannotated card: {c['title']!r}"
        assert hc["timing"] in _REAL_TIMING
        assert hc["target"] in _REAL_TARGET
        assert hc["placement"] in _REAL_PLACEMENT
        assert hc["venue"] in _REAL_VENUE
        assert hc["magnitude_sign"] in _REAL_SIGN
        assert hc.get("trigger_event", None) in _REAL_TRIGGER
        # exactly one of ops / snippet
        assert bool(hc.get("ops")) != bool(hc.get("snippet")), f"ops XOR snippet violated: {c['title']!r}"


def test_real_venue_distribution_is_sane() -> None:
    """Venue tagging is populated: mostly 'all', with a real 'in_person' minority."""
    venues = [c["human_canonical"]["venue"] for c in _load(REAL)]
    assert venues.count("all") > venues.count("in_person")  # most cards work anywhere
    assert venues.count("in_person") >= 10  # but physical cards are genuinely tagged
