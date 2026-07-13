"""Validate human_canonical annotations for real_cards.json.

Usage: python data/eval/validate_canonical.py [path-to-real_cards.json]
Prints any schema violations and a distribution summary. Exit 1 on violations.
Enum values mirror data/eval/CANONICAL_SPEC.md.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

TIMING = {"immediate", "modifier"}
TARGET = {"self", "player", "all", "all_others", "center", "card", "all_cards", "none"}
PLACEMENT = {"discard", "center", "player", "self", "destroy"}
VENUE = {"all", "in_person", "online"}
SIGN = {"positive", "negative", "neutral"}
TRIGGER = {
    "on_play",
    "on_draw",
    "on_draw_step",
    "on_turn_start",
    "on_turn_end",
    "on_score",
    "on_score_change",
    "on_game_end",
    "on_validate_play",
    None,
}
OP_NAMES = {
    "add_points",
    "subtract_points",
    "steal_points",
    "set_points",
    "skip_turn",
    "extra_turn",
    "draw_cards",
    "reverse_order",
    "scramble_order",
    "change_draw_count",
    "destroy_card",
    "set_win_condition",
    "set_rule",
    "register_hook",
    "unregister_hook",
    "set_condition",
    "set_card_attribute",
    "create_card",
    "end_game",
    "custom_note",
}


def validate(path: Path) -> int:
    cards = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    dist: dict[str, Counter] = {k: Counter() for k in ("timing", "target", "placement", "venue", "magnitude_sign")}

    for i, c in enumerate(cards):
        hc = c.get("human_canonical")
        tag = f"[{i}] {c.get('title', '')[:30]!r}"
        if hc is None:
            errors.append(f"{tag}: human_canonical is null")
            continue
        for field, allowed in (
            ("timing", TIMING),
            ("target", TARGET),
            ("placement", PLACEMENT),
            ("venue", VENUE),
            ("magnitude_sign", SIGN),
        ):
            val = hc.get(field, "all" if field == "venue" else "<missing>")
            if val not in allowed:
                errors.append(f"{tag}: {field}={val!r} not in {sorted(allowed)}")
            else:
                dist[field][val] += 1
        if hc.get("trigger_event", "<m>") not in TRIGGER:
            errors.append(f"{tag}: trigger_event={hc.get('trigger_event')!r} invalid")
        has_ops = bool(hc.get("ops"))
        has_snip = bool(hc.get("snippet"))
        has_steps = bool(hc.get("steps"))
        if sum((has_ops, has_snip, has_steps)) > 1:
            errors.append(f"{tag}: has more than one of ops, snippet, or steps")
        if not any((has_ops, has_snip, has_steps)):
            errors.append(f"{tag}: has none of ops, snippet, or steps")
        for op in hc.get("ops") or []:
            if op.get("op") not in OP_NAMES:
                errors.append(f"{tag}: unknown op {op.get('op')!r}")

    print(f"validated {len(cards)} cards; {len(errors)} error(s)")
    for e in errors[:40]:
        print("  ERR", e)
    print("\n-- distributions --")
    for field, ctr in dist.items():
        print(f"  {field}: {dict(ctr.most_common())}")
    return 1 if errors else 0


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "real_cards.json"
    sys.exit(validate(p))
