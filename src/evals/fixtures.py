"""evals.fixtures — canned game states for behavioral sandbox comparison.

The sandbox_behavior scorer executes the EXPECTED sandbox code and the
GENERATED effect against these fixtures and compares the resulting op diffs.
Fixtures are deliberately tiny (subprocess per execution) but diverse enough
to distinguish targets: three players with distinct scores and hand sizes, a
card registry with queryable alt_text, and a resolved chosen player.
"""

from __future__ import annotations

import json
from typing import Any

# Ops that carry no mechanical state change — dropped before comparison so a
# flavour note never masks a scoring difference (or papers one over).
_NON_MECHANICAL_OPS = frozenset({"custom_note", "note"})

# Play-time choice aliases: generated ops may address the chosen player
# abstractly; expected sandbox code addresses them via ctx["chosen_player_id"].
_CHOICE_TARGETS = frozenset({"chooser", "target_player", "player", "chosen_player"})


def _players() -> list[dict[str, Any]]:
    return [
        {"id": "p1", "name": "Alice", "score": 12, "hand": ["c1", "c2", "c3"], "in_play": [], "connected": True},
        {"id": "p2", "name": "Bob", "score": 4, "hand": ["c4"], "in_play": ["c7"], "connected": True},
        {"id": "p3", "name": "Cara", "score": 30, "hand": ["c5", "c6"], "in_play": [], "connected": True},
    ]


def _cards() -> dict[str, dict[str, Any]]:
    return {
        "c1": {"id": "c1", "title": "Zap", "description": "Gain 5 points.", "alt_text": None, "attributes": {}},
        "c2": {
            "id": "c2",
            "title": "Monkey Business",
            "description": "Monkeys everywhere.",
            "alt_text": "three monkeys stacked in a trenchcoat",
            "attributes": {},
        },
        "c3": {"id": "c3", "title": "Blank", "description": "", "alt_text": None, "attributes": {}},
        "c4": {"id": "c4", "title": "Reverse", "description": "Reverse play.", "alt_text": None, "attributes": {}},
        "c5": {
            "id": "c5",
            "title": "Banana Stand",
            "description": "There is always money here.",
            "alt_text": "a monkey holding a banana",
            "attributes": {"color": "yellow"},
        },
        "c6": {"id": "c6", "title": "Rock", "description": "It is a rock.", "alt_text": "a rock", "attributes": {}},
        "c7": {"id": "c7", "title": "Shield", "description": "Sturdy.", "alt_text": None, "attributes": {}},
    }


def fixture_states() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(state_dict, ctx_dict) pairs consumed by engine.sandbox.runner.execute_snippet."""
    base_state = {
        "players": _players(),
        "cards": _cards(),
        "rules": {"draw": 1, "play": 1},
        "deck": ["d1", "d2", "d3", "d4"],
        "discard": [],
        "house_rules": [],
        "turn_order": ["p1", "p2", "p3"],
        "current_player_id": "p1",
        "history_events": [],
    }
    ctx = {
        "actor_id": "p1",
        "event": "on_play",
        "card_id": "c1",
        "amount": None,
        "chosen_player_id": "p2",
        "chosen_card_id": "c7",
        "interactions": {},
        "interaction_refs": {},
        # Reaction-window context (harmlessly present for non-reaction cards).
        "pending_actor_id": "p3",
        "pending_card_id": "c5",
        "pending_card_title": "Banana Stand",
        "pending_ops": [{"op": "add_points", "target": "self", "amount": 5}],
    }
    # Second fixture: near-empty deck and a different actor, so deck-size and
    # actor-relative effects diverge from fixture one.
    thin_state = {**base_state, "deck": ["d1"], "current_player_id": "p3"}
    thin_ctx = {**ctx, "actor_id": "p3", "chosen_player_id": "p1"}
    return [(base_state, ctx), (thin_state, thin_ctx)]


def normalise_ops(raw_ops: list[dict[str, Any]], ctx: dict[str, Any]) -> list[str]:
    """Canonicalise an op diff for comparison.

    Drops non-mechanical notes, resolves play-time choice aliases to the
    fixture's concrete chosen player, and renders each op as a sorted-key JSON
    string so multiset comparison is order-insensitive but count-sensitive.
    """
    chosen = f"id:{ctx.get('chosen_player_id') or ''}"
    normalised: list[str] = []
    for raw in raw_ops or []:
        if not isinstance(raw, dict) or raw.get("op") in _NON_MECHANICAL_OPS:
            continue
        entry = dict(raw)
        for key in ("target", "from_target", "to_target", "winner"):
            if entry.get(key) in _CHOICE_TARGETS:
                entry[key] = chosen
        normalised.append(json.dumps(entry, sort_keys=True, default=str))
    return sorted(normalised)


def multiset_jaccard(a: list[str], b: list[str]) -> float:
    """Multiset Jaccard similarity; both empty counts as identical (1.0)."""
    if not a and not b:
        return 1.0
    from collections import Counter

    ca, cb = Counter(a), Counter(b)
    intersection = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return intersection / union if union else 1.0
