"""Corpus lint — every seed card's ops must compile without silent drift.

Bead ao7: gold seed cards used authoring vocabulary (``chosen_player``,
``next_player``, a mismatched ``set_win_condition`` schema) the compiler
silently dropped, so exemplars taught the agent broken cards. Once
``engine.compile`` logs every skipped op and every silently-defaulted target
at WARNING level, "no drift" becomes mechanically checkable: iterate every
card in every seed file and fail if compiling its ops emits a warning.
"""

from __future__ import annotations

import json
import logging
import pathlib

import pytest

from engine.compile import compile_card
from engine.compile import compile_card_plan
from engine.history import append_history_event
from agent.tools.dry_run_effect import dry_run_resolution_plan
from models.game_state import GameState, Player

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"

SEED_FILES = [
    "seed_cards_gold.json",
    "seed_cards.json",
    "seed_cards_simple.json",
    "seed_cards_fillers.json",
]


def _iter_cards_with_ops() -> list[tuple[str, str, list[dict]]]:
    """Every (filename, title, ops) triple across all seed files that has ops."""
    cases: list[tuple[str, str, list[dict]]] = []
    for filename in SEED_FILES:
        data = json.loads((DATA_DIR / filename).read_text())
        for card in data:
            canonical = card.get("canonical")
            if not canonical:
                continue
            ops = canonical.get("ops")
            if not ops:
                continue
            cases.append((filename, card["title"], ops))
    return cases


_CASES = _iter_cards_with_ops()
_IDS = [f"{filename}::{title}" for filename, title, _ in _CASES]


def test_corpus_has_cards_with_ops() -> None:
    """Sanity check the lint below actually exercises the corpus."""
    assert len(_CASES) >= 50


@pytest.mark.parametrize(("filename", "title", "ops"), _CASES, ids=_IDS)
def test_card_ops_compile_without_drift(
    filename: str, title: str, ops: list[dict], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="engine.compile"):
        compile_card({"id": "lint", "title": title, "ops": ops})
    drift = [record.getMessage() for record in caplog.records]
    assert not drift, f"{filename}::{title} triggered compiler drift warnings: {drift}"


def _representative_state(card: dict) -> GameState:
    state = GameState(
        room_code="CORPUS",
        players=[
            Player(id="p1", name="Alice", score=7, hand=["gold", "hand-card"]),
            Player(id="p2", name="Bob", score=4, hand=["target-card"]),
        ],
        cards={
            "gold": {**card, "id": "gold"},
            "hand-card": {"id": "hand-card", "title": "Hand"},
            "target-card": {"id": "target-card", "title": "Target"},
            "in-play": {"id": "in-play", "title": "In play"},
        },
        deck=[f"deck-{index}" for index in range(20)],
        house_rules=["in-play"],
        phase="playing",
    )
    state = append_history_event(state, "draw", actor_id="p1", target_player_ids=["p1"], amount=4)
    return append_history_event(state, "draw", actor_id="p2", target_player_ids=["p2"], amount=2)


@pytest.mark.parametrize("card", json.loads((DATA_DIR / "seed_cards_gold.json").read_text()), ids=lambda c: c["title"])
def test_every_gold_canonical_compiles_and_dry_runs_end_to_end(card: dict) -> None:
    plan = compile_card_plan({**card, "id": "gold"})

    assert plan is not None and plan.steps, card["title"]
    report = dry_run_resolution_plan(
        _representative_state(card),
        plan,
        "p1",
        "gold",
        chosen_player_id="p2",
        chosen_card_id="target-card",
    )

    assert report["ok"] is True, f"{card['title']}: {report}"
    assert report["emitted_ops"], card["title"]


def test_combined_seed_is_generated_from_gold_and_fillers() -> None:
    gold = json.loads((DATA_DIR / "seed_cards_gold.json").read_text())
    fillers = json.loads((DATA_DIR / "seed_cards_fillers.json").read_text())
    combined = json.loads((DATA_DIR / "seed_cards.json").read_text())

    assert combined == gold + fillers
