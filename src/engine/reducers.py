"""engine.reducers — pure op reducers, target resolution, and dispatch.

Every reducer takes ``(state, op, ctx)`` and returns a NEW GameState; reducers
never mutate the state passed in. ``apply_op`` dispatches on ``op.op`` via the
``_REDUCERS`` table.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from engine.events import HookContext
from engine.history import record_op_history
from models.effects import (
    AddPointsOp,
    CardTarget,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    EndGameOp,
    ExtraTurnOp,
    Op,
    ReverseOrderOp,
    ScrambleOrderOp,
    CreateCardOp,
    RegisterHookOp,
    SetCardAttributeOp,
    SetConditionOp,
    SetPointsOp,
    SetRuleOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
    Target,
    TransferCardOp,
    UnregisterHookOp,
)
from pydantic import ValidationError as PydanticValidationError

from models.game_state import EndCondition, GameState, HookSpec, Rules, WinCondition


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------
def _resolve_targets(target: Target, ctx: HookContext, state: GameState) -> list[str]:
    """Resolve a Target address into a concrete list of player ids.

    ``left_neighbor``/``right_neighbor`` derive from the actor's position in
    ``state.effective_turn_order()`` (the mutable rotation list), not raw
    ``players`` list position — so reversing or scrambling the turn order
    changes who counts as a neighbor.
    """
    players = state.players

    if target.startswith("id:"):
        pid = target[3:]
        return [pid] if any(p.id == pid for p in players) else []
    if target.startswith("has:"):
        key = target[4:]
        return [p.id for p in players if p.conditions.get(key)]

    match target:
        case "self":
            return [ctx.actor_id]
        case "left_neighbor":
            order = state.effective_turn_order()
            pos = order.index(ctx.actor_id)
            return [order[(pos - 1) % len(order)]]
        case "right_neighbor":
            order = state.effective_turn_order()
            pos = order.index(ctx.actor_id)
            return [order[(pos + 1) % len(order)]]
        case "all":
            return [p.id for p in players]
        case "all_others":
            return [p.id for p in players if p.id != ctx.actor_id]
        case "chooser" | "target_player":
            if ctx.chosen_player_id is None:
                raise ValueError(f"Target {target!r} requires ctx.chosen_player_id")
            return [ctx.chosen_player_id]
        case "player_with_most_points":
            return [max(players, key=lambda p: p.score).id]
        case "player_with_least_points":
            return [min(players, key=lambda p: p.score).id]
        case "player_with_empty_hand":
            return [p.id for p in players if not p.hand]
        case _:
            raise ValueError(f"Unknown target: {target!r}")


def _resolve_card_targets(card_target: CardTarget, ctx: HookContext, state: GameState) -> list[str]:
    """Resolve a CardTarget address into a concrete list of card ids.

    This is the CARD analogue of ``_resolve_targets`` (which resolves players).

    - ``"this"``        -> ``[ctx.card_id]`` (the card being played). If there is
                           no card in context, resolves to an empty list.
    - ``"chosen_card"`` -> ``[ctx.chosen_card_id]``; raises ValueError when the
                           actor made no choice, mirroring the "chooser" player
                           behavior.
    - ``"all_in_play"`` -> every card in every player's in-play zone.
    - ``"all_in_hand"`` -> the ACTOR's own hand (first-cut decision). Whose-hand
                           composition is a documented future extension.
    """
    if card_target.startswith("id:"):
        cid = card_target[3:]
        return [cid] if cid in state.cards else []
    if card_target.startswith("attr:"):
        key, _, expected = card_target[5:].partition("=")
        return [
            cid
            for cid, card in state.cards.items()
            if isinstance(card, dict) and str((card.get("attributes") or {}).get(key)) == expected
        ]

    match card_target:
        case "this":
            return [ctx.card_id] if ctx.card_id is not None else []
        case "chosen_card":
            if ctx.chosen_card_id is None:
                raise ValueError("CardTarget 'chosen_card' requires ctx.chosen_card_id")
            return [ctx.chosen_card_id]
        case "all_in_play":
            return state.cards_in_play()
        case "all_in_hand":
            return list(state.get_player(ctx.actor_id).hand)
        case _:
            raise ValueError(f"Unknown card target: {card_target!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _update_player_score(state: GameState, player_id: str, new_score: int) -> GameState:
    """Return a copy of state with one player's score set to new_score."""
    new_players = [p.model_copy(update={"score": new_score}) if p.id == player_id else p for p in state.players]
    return state.model_copy(update={"players": new_players})


# ---------------------------------------------------------------------------
# Point reducers
# ---------------------------------------------------------------------------
def _reduce_add_points(state: GameState, op: AddPointsOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = _update_player_score(state, pid, state.get_player(pid).score + op.amount)
    return state


def _reduce_subtract_points(state: GameState, op: SubtractPointsOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = _update_player_score(state, pid, state.get_player(pid).score - op.amount)
    return state


def _reduce_set_points(state: GameState, op: SetPointsOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = _update_player_score(state, pid, op.amount)
    return state


# ---------------------------------------------------------------------------
# Turn-flow reducers (per-player conditions)
# ---------------------------------------------------------------------------
def _reduce_skip_turn(state: GameState, op: SkipTurnOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = state.with_condition(pid, "skip_next", True)
    return state


def _reduce_extra_turn(state: GameState, op: ExtraTurnOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        state = state.with_condition(pid, "extra_turn", True)
    return state


def _reduce_reverse_order(state: GameState, op: ReverseOrderOp, ctx: HookContext) -> GameState:
    """Reverse the turn rotation order.

    Reversing the list changes who plays next; it never moves ``turn_index``
    (a pointer into ``players``, untouched here), so the active player stays
    exactly who it was.
    """
    return state.model_copy(update={"turn_order": list(reversed(state.effective_turn_order()))})


def _reduce_scramble_order(
    state: GameState, op: ScrambleOrderOp, ctx: HookContext, *, rng: random.Random | None = None
) -> GameState:
    """Randomize the turn rotation order.

    ``rng`` is dependency-injected for deterministic tests, mirroring
    ``board.rooms.deck.build_deck``'s convention; defaults to a fresh
    ``random.Random()`` when not supplied.
    """
    rng = rng or random.Random()
    order = list(state.effective_turn_order())
    rng.shuffle(order)
    return state.model_copy(update={"turn_order": order})


def _reduce_change_draw_count(state: GameState, op: ChangeDrawCountOp, ctx: HookContext) -> GameState:
    return state.model_copy(update={"rules": state.rules.model_copy(update={"draw": op.amount})})


# ---------------------------------------------------------------------------
# Steal / cards / win-condition / note
# ---------------------------------------------------------------------------
def _reduce_steal_points(state: GameState, op: StealPointsOp, ctx: HookContext) -> GameState:
    from_ids = _resolve_targets(op.from_target, ctx, state)
    to_ids = _resolve_targets(op.to_target, ctx, state)
    for from_id in from_ids:
        stolen = min(op.amount, state.get_player(from_id).score)
        state = _update_player_score(state, from_id, state.get_player(from_id).score - stolen)
        for to_id in to_ids:
            state = _update_player_score(state, to_id, state.get_player(to_id).score + stolen)
    return state


def _reduce_draw_cards(state: GameState, op: DrawCardsOp, ctx: HookContext) -> GameState:
    deck = list(state.deck)
    new_players = list(state.players)
    for pid in _resolve_targets(op.target, ctx, state):
        drawn = deck[: op.amount]
        deck = deck[op.amount :]
        idx = next(i for i, p in enumerate(new_players) if p.id == pid)
        player = new_players[idx]
        new_players[idx] = player.model_copy(update={"hand": [*player.hand, *drawn]})
    return state.model_copy(update={"players": new_players, "deck": deck})


def _reduce_destroy_card(state: GameState, op: DestroyCardOp, ctx: HookContext) -> GameState:
    """Remove one or more cards from wherever they live and send them to discard.

    Resolution precedence (non-breaking migration):
      - If ``op.card_target`` is set, resolve it via ``_resolve_card_targets``
        (may yield MANY card ids).
      - Otherwise fall back to the legacy single ``op.card_id``.

    Each resolved id is scrubbed from every player's ``hand`` and ``in_play``
    zones and from the shared ``center`` zone (house_rules), then appended to the
    discard pile (once, no duplicates).
    """
    if op.card_target is not None:
        card_ids = _resolve_card_targets(op.card_target, ctx, state)
    elif op.card_id is not None:
        card_ids = [op.card_id]
    else:
        card_ids = []

    if not card_ids:
        return state

    targets = set(card_ids)
    new_players = [
        p.model_copy(
            update={
                "hand": [c for c in p.hand if c not in targets],
                "in_play": [c for c in p.in_play if c not in targets],
            }
        )
        if any(c in targets for c in (*p.hand, *p.in_play))
        else p
        for p in state.players
    ]
    house_rules = [c for c in state.house_rules if c not in targets]
    discard = list(state.discard)
    for cid in card_ids:
        if cid not in discard:
            discard.append(cid)
    return state.model_copy(update={"players": new_players, "house_rules": house_rules, "discard": discard})


def _reduce_transfer_card(state: GameState, op: TransferCardOp, ctx: HookContext) -> GameState:
    """Move resolved cards from any current zone into exactly one player's hand.

    Finding the source zone here is intentional: during a resolution plan the
    played card is already staged in discard, while persistent cards or chosen
    cards may live in a hand, in-play, center, deck, or discard.
    """
    recipients = _resolve_targets(op.to_target, ctx, state)
    if len(recipients) != 1:
        raise ValueError("transfer_card requires exactly one destination player")
    card_ids = _resolve_card_targets(op.card_target, ctx, state)
    located = {
        *state.deck,
        *state.discard,
        *state.house_rules,
        *(card for player in state.players for card in (*player.hand, *player.in_play)),
    }
    known = [card_id for card_id in card_ids if card_id in state.cards and card_id in located]
    if not known:
        raise ValueError("transfer_card resolved no cards")
    targets = set(known)
    recipient = recipients[0]
    players = []
    for player in state.players:
        hand = [card for card in player.hand if card not in targets]
        in_play = [card for card in player.in_play if card not in targets]
        if player.id == recipient:
            hand.extend(card for card in known if card not in hand)
        players.append(player.model_copy(update={"hand": hand, "in_play": in_play}))
    return state.model_copy(
        update={
            "players": players,
            "house_rules": [card for card in state.house_rules if card not in targets],
            "discard": [card for card in state.discard if card not in targets],
            "deck": [card for card in state.deck if card not in targets],
        }
    )


def _reduce_set_win_condition(state: GameState, op: SetWinConditionOp, ctx: HookContext) -> GameState:
    wc = WinCondition(kind=op.kind, threshold=op.threshold)
    return state.model_copy(update={"rules": state.rules.model_copy(update={"win_condition": wc})})


def _reduce_custom_note(state: GameState, op: CustomNoteOp, ctx: HookContext) -> GameState:
    return state.with_log(f"[note] {op.note}")


def _reduce_set_condition(state: GameState, op: SetConditionOp, ctx: HookContext) -> GameState:
    for pid in _resolve_targets(op.target, ctx, state):
        if op.value is None:
            state = state.without_condition(pid, op.key)
        else:
            state = state.with_condition(pid, op.key, op.value)
    return state


def _reduce_set_card_attribute(state: GameState, op: SetCardAttributeOp, ctx: HookContext) -> GameState:
    cards = dict(state.cards)
    for cid in _resolve_card_targets(op.card_target, ctx, state):
        card = cards.get(cid)
        if not isinstance(card, dict):
            continue
        attributes = dict(card.get("attributes") or {})
        if op.value is None:
            attributes.pop(op.key, None)
        else:
            attributes[op.key] = op.value
        cards[cid] = {**card, "attributes": attributes}
    return state.model_copy(update={"cards": cards})


def _reduce_create_card(
    state: GameState, op: CreateCardOp, ctx: HookContext, *, rng: random.Random | None = None
) -> GameState:
    """Register ``op.count`` copies and route them to the requested destination.

    Ids are derived from the source card + a running per-state counter so the
    reducer stays deterministic; deck_shuffle randomness comes from the
    injected ``rng`` (same convention as scramble_order).
    """
    rng = rng or random.Random()
    cards = dict(state.cards)
    deck = list(state.deck)
    players = list(state.players)
    base = ctx.card_id or "card"
    serial = sum(1 for cid in cards if cid.startswith("created-"))
    new_ids: list[str] = []
    for _ in range(op.count):
        cid = f"created-{base}-{serial}"
        while cid in cards:
            serial += 1
            cid = f"created-{base}-{serial}"
        serial += 1
        cards[cid] = {
            "id": cid,
            "title": op.title,
            "description": op.description,
            "creator_id": ctx.actor_id,
            "origin": "authored",
            "canonical": {"ops": [dict(o) for o in op.ops]},
            "attributes": dict(op.attributes),
            "has_art": False,
        }
        new_ids.append(cid)

    if op.destination == "deck_top":
        deck = [*new_ids, *deck]
    elif op.destination == "deck_shuffle":
        for cid in new_ids:
            deck.insert(rng.randint(0, len(deck)), cid)
    else:
        players = [p.model_copy(update={"hand": [*p.hand, *new_ids]}) if p.id == ctx.actor_id else p for p in players]

    return state.model_copy(update={"cards": cards, "deck": deck, "players": players}).with_log(
        f"[created] {op.count}x '{op.title}' -> {op.destination}"
    )


_MAX_HOOKS_PER_CARD = 3


def _reduce_register_hook(state: GameState, op: RegisterHookOp, ctx: HookContext) -> GameState:
    """Validate and append a serialized HookSpec (the ONE registration path)."""
    from engine.events import GameEvent
    from engine.sandbox.validate import validate_snippet

    if op.event not in {e.value for e in GameEvent}:
        raise ValueError(f"register_hook: unknown event {op.event!r}")
    result = validate_snippet(op.code)
    if not result.ok:
        raise ValueError(f"register_hook: snippet failed validation: {result.error}")
    source = ctx.card_id or "unknown"
    existing = [h for h in state.hooks if h.source_card_id == source]
    if len(existing) >= _MAX_HOOKS_PER_CARD:
        raise ValueError(f"register_hook: card {source!r} already registered {_MAX_HOOKS_PER_CARD} hooks")
    spec = HookSpec(
        id=f"hook-{source}-{len(existing)}",
        source_card_id=source,
        event=op.event,
        scope=op.scope,
        owner_id=ctx.actor_id if op.scope == "player" else None,
        code=op.code,
    )
    return state.model_copy(update={"hooks": [*state.hooks, spec]}).with_log(
        f"[hook] registered on {op.event} by {source}"
    )


def _reduce_unregister_hook(state: GameState, op: UnregisterHookOp, ctx: HookContext) -> GameState:
    remaining = [h for h in state.hooks if h.source_card_id != op.source_card_id]
    if len(remaining) == len(state.hooks):
        return state
    return state.model_copy(update={"hooks": remaining}).with_log(f"[hook] unregistered {op.source_card_id}")


_SCALAR_RULE_PATHS = frozenset({"draw", "play", "skip_predicate"})
_NESTED_RULE_HEADS = frozenset({"end_condition", "win_condition", "cannot_play"})


def _reduce_set_rule(state: GameState, op: SetRuleOp, ctx: HookContext) -> GameState:
    """Write one rule path. Unknown paths / invalid values raise ValueError so
    callers surface them the same way as unresolvable targets."""
    rules = state.rules.model_dump()
    path, value = op.path, op.value
    if path in _SCALAR_RULE_PATHS or path in _NESTED_RULE_HEADS:
        rules[path] = value
    elif path.startswith("extra."):
        rules["extra"] = {**rules["extra"], path.removeprefix("extra."): value}
    elif "." in path and path.split(".", 1)[0] in _NESTED_RULE_HEADS:
        head, key = path.split(".", 1)
        sub = dict(rules[head]) if isinstance(rules[head], dict) else {}
        sub[key] = value
        rules[head] = sub
    else:
        raise ValueError(f"set_rule: unknown rule path {path!r}")
    try:
        new_rules = Rules.model_validate(rules)
    except PydanticValidationError as exc:
        raise ValueError(f"set_rule: invalid value for {path!r}: {exc}") from exc
    return state.model_copy(update={"rules": new_rules})


def _reduce_counter_play(state: GameState, op: Op, ctx: HookContext) -> GameState:
    """Defensive no-op: counter_play is control flow the Room consumes inside a
    reaction window (like reject_play in ON_VALIDATE_PLAY). If one leaks into a
    normal play/hook it must never crash — just log and change nothing."""
    return state.with_log("[counter_play ignored outside a reaction window]")


def _reduce_end_game(state: GameState, op: EndGameOp, ctx: HookContext) -> GameState:
    update: dict = {"rules": state.rules.model_copy(update={"end_condition": EndCondition(type="now")})}
    if op.winner is not None:
        update["winner_override"] = _resolve_targets(op.winner, ctx, state)
    elif op.winners:
        resolved = {player_id for target in op.winners for player_id in _resolve_targets(target, ctx, state)}
        update["winner_override"] = [player.id for player in state.players if player.id in resolved]
    return state.model_copy(update=update)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------
_REDUCERS: dict[str, Callable[[GameState, Op, HookContext], GameState]] = {
    "add_points": _reduce_add_points,
    "subtract_points": _reduce_subtract_points,
    "set_points": _reduce_set_points,
    "skip_turn": _reduce_skip_turn,
    "extra_turn": _reduce_extra_turn,
    "reverse_order": _reduce_reverse_order,
    "change_draw_count": _reduce_change_draw_count,
    "steal_points": _reduce_steal_points,
    "draw_cards": _reduce_draw_cards,
    "destroy_card": _reduce_destroy_card,
    "transfer_card": _reduce_transfer_card,
    "set_win_condition": _reduce_set_win_condition,
    "custom_note": _reduce_custom_note,
    "counter_play": _reduce_counter_play,
    "end_game": _reduce_end_game,
    "set_rule": _reduce_set_rule,
    "register_hook": _reduce_register_hook,
    "unregister_hook": _reduce_unregister_hook,
    "set_condition": _reduce_set_condition,
    "set_card_attribute": _reduce_set_card_attribute,
    "create_card": _reduce_create_card,
}


def apply_op(state: GameState, op: Op, ctx: HookContext, *, rng: random.Random | None = None) -> GameState:
    """Dispatch a single op to its reducer, returning a new GameState.

    ``rng`` is only consumed by ``scramble_order`` and ``create_card``
    (dependency-injectable for deterministic tests); every other op ignores it.
    """
    before = state
    if op.op == "scramble_order":
        state = _reduce_scramble_order(state, op, ctx, rng=rng)
    elif op.op == "create_card":
        state = _reduce_create_card(state, op, ctx, rng=rng)
    else:
        state = _REDUCERS[op.op](state, op, ctx)

    return record_op_history(before, state, op, ctx)


__all__ = ["apply_op"]
