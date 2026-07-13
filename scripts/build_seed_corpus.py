#!/usr/bin/env python3
"""Build the combined RAG seed corpus from its gold and filler sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GOLD = DATA / "seed_cards_gold.json"
FILLERS = DATA / "seed_cards_fillers.json"
COMBINED = DATA / "seed_cards.json"


def render_corpus() -> str:
    cards = json.loads(GOLD.read_text()) + json.loads(FILLERS.read_text())
    return json.dumps(cards, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_corpus()
    if args.check:
        if not COMBINED.exists() or COMBINED.read_text() != rendered:
            print("data/seed_cards.json is stale; run scripts/build_seed_corpus.py")
            return 1
        return 0
    COMBINED.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
