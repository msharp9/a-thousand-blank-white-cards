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

import pytest

from agent.tools.dry_run_effect import dry_run_resolution_plan
from engine.compile import compile_card_plan
from engine.sandbox.validate import validate_snippet
from models.game_state import GameState, Player

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "eval"
GOLD = DATA_DIR / "eval_cards.json"
REAL = DATA_DIR / "real_cards.json"
HARD = DATA_DIR / "eval_cards_hard.json"

# Schema v2 (data/eval/CANONICAL_SPEC.md): no timing, placement collapsed to
# three zones, target "center" re-annotated as "none", unified trigger.
_VALID_TARGET = {"self", "player", "all", "all_others", "card", "all_cards", "none"}
_VALID_PLACEMENT = {"discard", "player", "center"}
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
        assert "alt_text" in c
        hc = c["human_canonical"]
        assert hc is not None
        assert "timing" not in hc  # v2: derived from placement
        assert hc["target"] in _VALID_TARGET
        assert hc["placement"] in _VALID_PLACEMENT
        assert hc["magnitude_sign"] in _VALID_SIGN
        assert hc.get("ops") or hc.get("steps")
        # sandbox is the executable teaching form; steps-based cards carry
        # their code inside the plan instead.
        assert hc.get("sandbox") or hc.get("steps")


def test_gold_diversity() -> None:
    cards = _load(GOLD)
    hcs = [c["human_canonical"] for c in cards]
    assert sum(1 for h in hcs if h.get("sandbox")) >= 5
    # v2 has no timing: persistence == non-discard placement.
    assert sum(1 for h in hcs if h["placement"] != "discard") >= 6
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
        assert set(c.keys()) == {"id", "image_url", "title", "description", "alt_text", "human_canonical"}
        assert not c["description"].lstrip().startswith("["), f"unsplit alt text: {c['title']!r}"
        assert isinstance(c["title"], str)
        assert isinstance(c["description"], str)


# --------------------------------------------------------------------------- #
# real_cards.json human_canonical annotations (see data/eval/CANONICAL_SPEC.md).
# --------------------------------------------------------------------------- #

_REAL_TARGET = {"self", "player", "all", "all_others", "card", "all_cards", "none"}
_REAL_PLACEMENT = {"discard", "center", "player"}
_REAL_VENUE = {"all", "in_person", "online"}
_REAL_SIGN = {"positive", "negative", "neutral"}
_REAL_TRIGGER = {
    "on_play",
    "on_validate_play",
    "on_score_change",
    "on_turn_start",
    "on_turn_end",
    "on_draw_step",
    "on_win_check",
    "on_game_end",
    "on_reaction",
    None,
}


def test_real_every_card_is_annotated() -> None:
    """Every real card has a fully-populated human_canonical (no nulls left)."""
    for c in _load(REAL):
        hc = c["human_canonical"]
        assert hc is not None, f"unannotated card: {c['title']!r}"
        assert hc["target"] in _REAL_TARGET
        assert hc["placement"] in _REAL_PLACEMENT
        assert hc["venue"] in _REAL_VENUE
        assert hc["magnitude_sign"] in _REAL_SIGN
        assert hc.get("trigger", None) in _REAL_TRIGGER
        assert "trigger_event" not in hc  # v2 unifies on "trigger"
        assert "snippet" not in hc  # v2: prose degraded to ops, code lives in sandbox
        assert hc.get("ops") or hc.get("steps"), f"no executable form: {c['title']!r}"


def test_real_venue_distribution_is_sane() -> None:
    """Venue tagging is populated: mostly 'all', with a real 'in_person' minority."""
    venues = [c["human_canonical"]["venue"] for c in _load(REAL)]
    assert venues.count("all") > venues.count("in_person")  # most cards work anywhere
    assert venues.count("in_person") >= 10  # but physical cards are genuinely tagged


# --------------------------------------------------------------------------- #
# real_cards.json interaction-mechanics upgrades (bead 7fp): note-only cards
# whose text implies verifiable mechanics carry an interaction-steps plan
# (confirm/choice/number/text barriers) or real computed sandbox code instead
# of a bare custom_note.
# --------------------------------------------------------------------------- #


def _real_steps_cards() -> list[dict]:
    return [c for c in _load(REAL) if c["human_canonical"].get("steps")]


def _representative_state() -> GameState:
    return GameState(
        room_code="REAL",
        players=[
            Player(id="p1", name="Alice", score=7, hand=["played-card"]),
            Player(id="p2", name="Bob", score=-4, hand=[]),
            Player(id="p3", name="Ivy", score=12, hand=[]),
        ],
        cards={"played-card": {"id": "played-card", "title": "Played"}},
        deck=[f"deck-{index}" for index in range(10)],
        phase="playing",
    )


def test_real_upgraded_interaction_subset_is_nonempty() -> None:
    assert len(_real_steps_cards()) >= 20


def test_real_steps_cards_follow_interaction_contract() -> None:
    """Interaction-plan cards are steps-only (CANONICAL_SPEC: sandbox null) and never target 'chooser' in code."""
    for c in _real_steps_cards():
        hc = c["human_canonical"]
        assert hc["sandbox"] is None, c["title"]
        assert hc["ops"] is None, c["title"]
        steps = hc["steps"]
        assert any(isinstance(s, dict) and s.get("kind") == "interaction" for s in steps), c["title"]
        for step in steps:
            if step.get("kind") == "snippet":
                assert "'chooser'" not in step["code"] and '"chooser"' not in step["code"], c["title"]


def test_real_all_sandbox_and_snippet_code_validates() -> None:
    """Every executable string in the corpus passes the engine's static sandbox check."""
    checked = 0
    for c in _load(REAL):
        hc = c["human_canonical"]
        codes = [hc["sandbox"]] if hc.get("sandbox") else []
        codes += [s["code"] for s in hc.get("steps") or [] if isinstance(s, dict) and s.get("kind") == "snippet"]
        for code in codes:
            result = validate_snippet(code)
            assert result.ok, f"{c['title']}: {result.error}"
            checked += 1
    assert checked >= 500


@pytest.mark.parametrize("card", _real_steps_cards(), ids=lambda c: c["id"])
def test_real_steps_cards_compile_and_dry_run(card: dict) -> None:
    """Each upgraded plan compiles to a ResolutionPlan and survives engine dry-run revalidation."""
    plan = compile_card_plan({**card, "canonical": card["human_canonical"]})
    assert plan is not None and plan.steps, card["title"]

    report = dry_run_resolution_plan(
        _representative_state(),
        plan,
        "p1",
        "played-card",
        chosen_player_id="p2",
    )
    assert report["ok"] is True, f"{card['title']}: {report}"
    assert report["emitted_ops"], card["title"]


@pytest.mark.parametrize(
    "card",
    [c for c in _load(REAL) if c["id"] in {"real-122", "real-431", "real-652"}],
    ids=lambda c: c["id"],
)
def test_real_computed_sandbox_upgrades_dry_run(card: dict) -> None:
    """Score/name-computable cards carry real sandbox code that runs and emits ops."""
    plan = compile_card_plan({**card, "canonical": card["human_canonical"]})
    assert plan is not None and plan.steps, card["title"]

    report = dry_run_resolution_plan(
        _representative_state(),
        plan,
        "p1",
        "played-card",
        chosen_player_id="p2",
    )
    assert report["ok"] is True, f"{card['title']}: {report}"
    assert any(op.get("op") != "custom_note" for op in report["emitted_ops"]), card["title"]


# --------------------------------------------------------------------------- #
# Hard corpus: eval_cards_hard.json (effects too complex for flat ops — the
# benchmark for whether the card-interpretation agent can compose sandbox
# code or ordered steps).
# --------------------------------------------------------------------------- #


def _has_interaction_steps(hc: dict) -> bool:
    return any(isinstance(s, dict) and s.get("kind") == "interaction" for s in hc.get("steps") or [])


class TestHardEvalCards:
    """The hard set is sandbox/steps-only by design: every ops slot is null."""

    def test_exactly_25_cards(self) -> None:
        assert len(_load(HARD)) == 25

    def test_ids_and_titles_unique(self) -> None:
        cards = _load(HARD)
        ids = [c["id"] for c in cards]
        titles = [c["title"] for c in cards]
        assert len(ids) == len(set(ids))
        assert len(titles) == len(set(titles))

    def test_ops_always_null(self) -> None:
        for c in _load(HARD):
            assert c["human_canonical"]["ops"] is None, c["title"]

    def test_sandbox_xor_interaction_steps(self) -> None:
        """One executable teaching form each: sandbox code, or an interaction plan."""
        for c in _load(HARD):
            hc = c["human_canonical"]
            assert bool(hc.get("sandbox")) != _has_interaction_steps(hc), c["title"]

    def test_reaction_coverage(self) -> None:
        cards = _load(HARD)
        assert sum(1 for c in cards if c["human_canonical"]["trigger"] == "on_reaction") >= 3

    def test_register_hook_coverage(self) -> None:
        cards = _load(HARD)
        assert sum(1 for c in cards if "register_hook" in (c["human_canonical"].get("sandbox") or "")) >= 3

    def test_interaction_step_coverage(self) -> None:
        cards = _load(HARD)
        assert sum(1 for c in cards if _has_interaction_steps(c["human_canonical"])) >= 2

    def test_all_sandbox_code_passes_static_validation(self) -> None:
        for c in _load(HARD):
            hc = c["human_canonical"]
            codes = [hc["sandbox"]] if hc.get("sandbox") else []
            codes += [s["code"] for s in hc.get("steps") or [] if isinstance(s, dict) and s.get("kind") == "snippet"]
            assert codes, c["title"]
            for code in codes:
                result = validate_snippet(code)
                assert result.ok, f"{c['title']}: {result.error}"
