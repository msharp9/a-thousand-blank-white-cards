#!/usr/bin/env python3
"""Build the combined RAG seed corpus from its gold, filler, and simple sources.

The simple deck (point-only, no-AI mode) also feeds retrieval: its cards cover
the most common basic scenarios worth surfacing on the ``seed`` benchmark. A few
titles overlap the gold set (e.g. "Gain 5 Points"); ids never collide and the
overlap is small, so all sources are concatenated without deduplication.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
GOLD = DATA / "seed_cards_gold.json"
FILLERS = DATA / "seed_cards_fillers.json"
SIMPLE = DATA / "seed_cards_simple.json"
COMBINED = DATA / "seed_cards.json"

SOURCES = (GOLD, FILLERS, SIMPLE)


def render_corpus() -> str:
    cards: list[dict] = []
    for source in SOURCES:
        cards.extend(json.loads(source.read_text()))
    return json.dumps(cards, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_corpus()
    if args.check:
        if not COMBINED.exists() or COMBINED.read_text() != rendered:
            print("data/seed_cards.json is stale; run scripts/data_prep/build_seed_corpus.py")
            return 1
        return 0
    COMBINED.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
