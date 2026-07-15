"""Validate canonical annotations against schema v2 (CANONICAL_SPEC.md).

Usage: python scripts/data_prep/validate_canonical.py [path-to-cards.json]
Prints any schema violations and a distribution summary. Exit 1 on violations.
Reads the ``human_canonical`` key (eval datasets) or ``canonical`` (seed
datasets), whichever a card carries.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

TARGET = {"self", "player", "all", "all_others", "card", "all_cards", "none"}
PLACEMENT = {"discard", "center", "player"}
VENUE = {"all", "in_person", "online"}
SIGN = {"positive", "negative", "neutral", None}
# GameEvent values (src/engine/events.py) — "on_reaction" marks reaction cards.
TRIGGER = {
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
    "transfer_card",
    "set_win_condition",
    "set_rule",
    "register_hook",
    "unregister_hook",
    "set_condition",
    "set_card_attribute",
    "create_card",
    "end_game",
    "custom_note",
    "counter_play",
}
STEP_KINDS = {"ops", "snippet", "interaction"}


def validate(path: Path) -> int:
    cards = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    dist: dict[str, Counter] = {k: Counter() for k in ("target", "placement", "venue", "trigger", "magnitude_sign")}

    for i, c in enumerate(cards):
        hc = c.get("human_canonical") or c.get("canonical")
        tag = f"[{i}] {c.get('title', '')[:30]!r}"
        if "alt_text" not in c:
            errors.append(f"{tag}: missing alt_text key (null is fine, absence is not)")
        if hc is None:
            continue  # fillers: title/description only, annotated in a later pass
        if "timing" in hc:
            errors.append(f"{tag}: legacy 'timing' field present (v2 derives it from placement)")
        if "snippet" in hc:
            errors.append(f"{tag}: legacy 'snippet' field present (v2 uses executable 'sandbox')")
        if "trigger_event" in hc:
            errors.append(f"{tag}: legacy 'trigger_event' field present (v2 unifies on 'trigger')")
        for field, allowed in (
            ("target", TARGET),
            ("placement", PLACEMENT),
            ("venue", VENUE),
            ("trigger", TRIGGER),
            ("magnitude_sign", SIGN),
        ):
            val = hc.get(field)
            if val not in allowed:
                errors.append(f"{tag}: {field}={val!r} not in {sorted(str(a) for a in allowed)}")
            else:
                dist[field][str(val)] += 1
        # One-shot (discard) cards never carry a hook trigger — only null or
        # "on_reaction". Persistent modifiers usually name the event that
        # re-fires them, but table-adjudicated ongoing rules (honor-system
        # cards where the engine only displays the reminder) leave it null,
        # so that direction is not enforced.
        trigger = hc.get("trigger")
        placement = hc.get("placement")
        if trigger not in (None, "on_reaction") and placement == "discard":
            errors.append(f"{tag}: one-shot card carries hook trigger {trigger!r}")

        has_steps = bool(hc.get("steps"))
        has_interaction = any(isinstance(s, dict) and s.get("kind") == "interaction" for s in hc.get("steps") or [])
        # Executable form: ops, steps, and/or sandbox — ops is null only when
        # the effect can't be expressed as structured ops (sandbox-only cards);
        # sandbox required unless the card teaches through an ordered steps
        # plan (barriers can't run in one sandbox function).
        if not (hc.get("ops") or has_steps or hc.get("sandbox")):
            errors.append(f"{tag}: no executable form (needs ops, steps, or sandbox)")
        if not hc.get("sandbox") and not has_steps:
            errors.append(f"{tag}: missing sandbox code (required unless the card is steps-based)")
        if has_interaction and hc.get("sandbox"):
            errors.append(f"{tag}: interaction-step card must not carry a standalone sandbox")
        for op in hc.get("ops") or []:
            if op.get("op") not in OP_NAMES:
                errors.append(f"{tag}: unknown op {op.get('op')!r}")
        for step in hc.get("steps") or []:
            if not isinstance(step, dict) or step.get("kind") not in STEP_KINDS:
                errors.append(f"{tag}: bad step kind {step!r}")

    print(f"validated {len(cards)} cards; {len(errors)} error(s)")
    for e in errors[:40]:
        print("  ERR", e)
    print("\n-- distributions --")
    for field, ctr in dist.items():
        print(f"  {field}: {dict(ctr.most_common())}")
    return 1 if errors else 0


if __name__ == "__main__":
    p = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[2] / "data" / "eval" / "real_cards.json"
    )
    sys.exit(validate(p))
