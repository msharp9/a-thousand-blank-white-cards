"""Tests for evals.ceilings — per-card theoretical maxima."""

from __future__ import annotations

import json
import pathlib

from evals.ceilings import benchmark_ceilings, card_ceiling

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def _card(ops: list[dict], sandbox: str | None = None) -> dict:
    canonical: dict = {"target": "self", "placement": "discard", "ops": ops}
    if sandbox is not None:
        canonical["sandbox"] = sandbox
    return {"id": "t", "title": "T", "description": "d", "canonical": canonical}


def test_mechanical_card_is_executable_and_mechanical() -> None:
    c = card_ceiling(_card([{"op": "add_points", "args": {"target": "self", "amount": 5}}]))
    assert c == {"executable": True, "mechanical": True, "has_sandbox": False}


def test_noop_card_is_executable_but_not_mechanical() -> None:
    c = card_ceiling(_card([{"op": "custom_note", "args": {"note": "nothing happens"}}]))
    assert c["executable"] is True
    assert c["mechanical"] is False


def test_has_sandbox_flag() -> None:
    c = card_ceiling(
        _card([{"op": "add_points", "args": {"target": "self", "amount": 1}}], sandbox="def apply(s, c):\n    pass\n")
    )
    assert c["has_sandbox"] is True


def test_uncompilable_canonical_is_not_executable() -> None:
    c = card_ceiling({"id": "x", "canonical": {}})
    assert c == {"executable": False, "mechanical": False, "has_sandbox": False}


def test_benchmark_ceilings_aggregate() -> None:
    noop = _card([{"op": "custom_note", "args": {"note": "n"}}])
    mech = _card([{"op": "add_points", "args": {"target": "self", "amount": 3}}])
    agg = benchmark_ceilings([noop, mech])
    assert agg["executability_ceiling"] == 1.0
    assert agg["did_something_ceiling"] == 0.5
    assert agg["did_something_noop_count"] == 1
    assert agg["sandbox_na_count"] == 2


def test_benchmark_ceilings_empty() -> None:
    assert benchmark_ceilings([]) == {}


def test_real_seed_corpus_ceilings_are_sane() -> None:
    cards = json.loads((DATA_DIR / "seed_cards.json").read_text())
    agg = benchmark_ceilings(cards)
    # Every seed canonical is executable (even no-ops emit custom_note).
    assert agg["executability_ceiling"] == 1.0
    # A minority are genuine no-ops, so the did_something ceiling is below 1.0.
    assert 0.80 < agg["did_something_ceiling"] < 1.0
    assert agg["did_something_noop_count"] >= 1
