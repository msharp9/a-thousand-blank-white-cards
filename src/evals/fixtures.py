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


# Ops with a single player "target" field, safe to expand to concrete ids so an
# aggregate address (all_others) matches an enumerated one (id:p2 + id:p3).
_SINGLE_TARGET_OPS = frozenset(
    {"add_points", "subtract_points", "set_points", "draw_cards", "skip_turn", "extra_turn", "set_condition"}
)


def _make_resolver(ctx: dict[str, Any], state: dict[str, Any] | None):
    """A target->player-ids resolver bound to the fixture, or None if unavailable.

    Lets ``normalise_ops`` canonicalise equivalent target vocabularies
    (all_others vs enumerated ids, next_player vs right_neighbor) to the same
    concrete players, so a faithful reading isn't scored a false 0.
    """
    if state is None:
        return None
    try:
        from engine.events import GameEvent, HookContext
        from engine.reducers import _resolve_targets
        from models.game_state import GameState

        game_state = GameState.model_validate({"room_code": "EVAL", "phase": "playing", **state})
        hook_ctx = HookContext(
            event=GameEvent.ON_PLAY,
            actor_id=ctx.get("actor_id"),
            chosen_player_id=ctx.get("chosen_player_id"),
            chosen_card_id=ctx.get("chosen_card_id"),
        )
    except Exception:  # noqa: BLE001 — resolution is an enhancement; fall back to literal comparison
        return None

    def resolve(target: str) -> list[str] | None:
        try:
            return _resolve_targets(target, hook_ctx, game_state)
        except Exception:  # noqa: BLE001 — unknown/unresolvable target keeps its literal form
            return None

    return resolve


def _is_empty(value: Any) -> bool:
    """Falsy-but-meaningless: an omitted optional field. NOT 0 or False, which
    are real (e.g. ``change_draw_count amount=0``)."""
    return value is None or value == [] or value == {} or value == ""


def _canonicalise_op(entry: dict[str, Any]) -> dict[str, Any]:
    """Collapse equivalent op encodings so a faithful reading isn't marked wrong.

    Two effects that change state identically must hash identically: a plan
    serialized through Pydantic carries every optional field at its default
    (``winners: []``, ``card_id: null``) while the sandbox API omits falsy ones,
    and ``end_game`` accepts either ``winner="X"`` or ``winners=["X"]`` for the
    same outcome.
    """
    if entry.get("op") == "end_game":
        winners = list(entry.get("winners") or [])
        if entry.get("winner"):
            winners.append(entry["winner"])
        entry = {k: v for k, v in entry.items() if k not in ("winner", "winners")}
        if winners:
            entry["winners"] = sorted(dict.fromkeys(winners))
    return {key: value for key, value in entry.items() if not _is_empty(value)}


def _expand_target(entry: dict[str, Any], resolve) -> list[dict[str, Any]]:
    """For single-target ops, resolve ``target`` to concrete ids, one op per id.

    So ``add_points all_others 3`` and ``add_points id:p2 3`` + ``add_points
    id:p3 3`` compare equal. Leaves multi-player ops (steal/transfer/end_game)
    to the literal + alias comparison.
    """
    if resolve is None or entry.get("op") not in _SINGLE_TARGET_OPS:
        return [entry]
    target = entry.get("target")
    if not isinstance(target, str):
        return [entry]
    ids = resolve(target)
    if not ids:
        return [entry]
    return [{**entry, "target": f"id:{pid}"} for pid in ids]


def normalise_ops(raw_ops: list[dict[str, Any]], ctx: dict[str, Any], state: dict[str, Any] | None = None) -> list[str]:
    """Canonicalise an op diff for comparison.

    Drops non-mechanical notes, resolves target vocabularies (choice aliases and
    — when ``state`` is given — relative/aggregate addresses) to the fixture's
    concrete players, collapses equivalent op encodings, and renders each op as a
    sorted-key JSON string so multiset comparison is order-insensitive but
    count-sensitive.
    """
    chosen = f"id:{ctx.get('chosen_player_id') or ''}"
    resolve = _make_resolver(ctx, state)
    normalised: list[str] = []
    for raw in raw_ops or []:
        if not isinstance(raw, dict) or raw.get("op") in _NON_MECHANICAL_OPS:
            continue
        entry = dict(raw)
        for key in ("target", "from_target", "to_target", "winner"):
            if entry.get(key) in _CHOICE_TARGETS:
                entry[key] = chosen
        for atom in _expand_target(entry, resolve):
            normalised.append(json.dumps(_canonicalise_op(atom), sort_keys=True, default=str))
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
