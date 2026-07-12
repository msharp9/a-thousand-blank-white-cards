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
