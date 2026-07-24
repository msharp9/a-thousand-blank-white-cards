"""engine.reducers — pure op reducers, target resolution, and dispatch.

Every reducer takes ``(state, op, ctx)`` and returns a NEW GameState; reducers
never mutate the state passed in. ``apply_op`` dispatches on ``op.op`` via the
``_REDUCERS`` table.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from engine.events import HookContext
from engine.history import append_history_event, record_op_history
from models.effects import (
    CARD_OWNER,
    AddPointsOp,
    CardTarget,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DiscardRandomOp,
    DrawCardsOp,
    EliminatePlayerOp,
    EndGameOp,
    ExtraTurnOp,
    MoveCardsOp,
    Op,
    RevealHandOp,
    ReverseOrderOp,
    RollDieOp,
    ScrambleOrderOp,
    CreateCardOp,
    RegisterHookOp,
    SetCardAttributeOp,
    SetConditionOp,
    SetPointsOp,
    SetRuleOp,
    SetWinConditionOp,
    ShuffleDeckOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
    Target,
    TransferCardOp,
    UnregisterHookOp,
)
from pydantic import ValidationError as PydanticValidationError

from models.game_state import EndCondition, GameState, HookSpec, RuleBinding, Rules, WinCondition

_hand_reveal_drain: ContextVar[list[dict[str, Any]] | None] = ContextVar("hand_reveal_drain", default=None)


@contextmanager
def collect_hand_reveals() -> Iterator[list[dict[str, Any]]]:
    """Collect one-shot hand reveals for the duration of the block.

    A ``reveal_hand`` with ``persistent=False`` changes no state, so the
    reducer records the reveal here — ``{"player_id", "viewer_ids",
    "card_ids", "cards"}`` per revealed hand — for the board layer to push to
    the resolved audience, without the engine importing board. ContextVar
    propagation makes this visible across ``asyncio.to_thread``. Outside a
    collecting block, one-shot reveals are silently dropped.
    """
    reveals: list[dict[str, Any]] = []
    token = _hand_reveal_drain.set(reveals)
    try:
        yield reveals
    finally:
        _hand_reveal_drain.reset(token)


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
    - ``"all_in_center"`` -> every card in the shared center zone
                           (``state.center_cards()``).
    - ``"last_played"`` -> the card of the most recent "play" history event,
                           EXCLUDING the card currently resolving (ctx.card_id):
                           "the last card played", read from the played card's
                           own perspective, means the PREVIOUS play — and while
                           it resolves the acting card IS ctx.card_id. Plays
                           with no card and plays whose card has since left the
                           registry are skipped; no surviving prior play
                           resolves to an empty list.
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
        case "all_in_center":
            return state.center_cards()
        case "last_played":
            for event in reversed(state.history_events):
                if event.kind != "play" or event.card_id is None:
                    continue
                if event.card_id == ctx.card_id or event.card_id not in state.cards:
                    continue
                return [event.card_id]
            return []
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


def _reduce_roll_die(
    state: GameState, op: RollDieOp, ctx: HookContext, *, rng: random.Random | None = None
) -> GameState:
    """Roll dice, record the roll, then apply the outcome with the total.

    A pre-resolved ``op.result`` (sandbox/replay) is used verbatim instead of
    rolling, so revalidation replays the same roll deterministically. The roll
    is recorded FIRST (dice_roll history event + log line), then the outcome is
    delegated through ``apply_op`` so its own history (score_change/draw) is
    recorded exactly like a directly-authored op.
    """
    rng = rng or random.Random()
    values = list(op.result) if op.result is not None else [rng.randint(1, op.sides) for _ in range(op.count)]
    total = sum(values)
    try:
        actor_name = state.get_player(ctx.actor_id).name
    except KeyError:
        actor_name = ctx.actor_id
    rolled = " + ".join(str(v) for v in values)
    line = f"{actor_name} rolled {op.count}d{op.sides}: {rolled}"
    if op.count > 1:
        line += f" = {total}"
    targets = _resolve_targets(op.target, ctx, state) if op.outcome != "none" else []
    state = append_history_event(
        state,
        "dice_roll",
        actor_id=ctx.actor_id,
        target_player_ids=targets,
        card_id=ctx.card_id,
        amount=total,
        data={"sides": op.sides, "values": values, "total": total},
    ).with_log(line)
    if op.outcome == "add_points":
        state = apply_op(state, AddPointsOp(target=op.target, amount=total), ctx)
    elif op.outcome == "subtract_points":
        state = apply_op(state, SubtractPointsOp(target=op.target, amount=total), ctx)
    elif op.outcome == "draw_cards":
        state = apply_op(state, DrawCardsOp(target=op.target, amount=total), ctx)
    return state


def _reduce_discard_random(
    state: GameState, op: DiscardRandomOp, ctx: HookContext, *, rng: random.Random | None = None
) -> GameState:
    """Discard ``op.count`` random cards from each resolved target's hand.

    The picks happen HERE with the injected rng — never pre-resolved in the
    sandbox, because snippets cannot read other players' hands or observe the
    picks, so reduce-time resolution cannot desync a snippet branch. A player
    holding fewer than ``op.count`` cards discards their whole hand. Each
    target's discard is recorded as a "discard" history event (the discard
    pile is public, so the picked card ids ride along in ``data``).
    """
    rng = rng or random.Random()
    for pid in _resolve_targets(op.target, ctx, state):
        player = state.get_player(pid)
        if not player.hand:
            state = state.with_log(f"[discard_random no-op] {player.name} has no cards to discard")
            continue
        picked = rng.sample(list(player.hand), min(op.count, len(player.hand)))
        removed = set(picked)
        new_players = [
            p.model_copy(update={"hand": [c for c in p.hand if c not in removed]}) if p.id == pid else p
            for p in state.players
        ]
        discard = list(state.discard)
        discard.extend(cid for cid in picked if cid not in discard)
        state = state.model_copy(update={"players": new_players, "discard": discard})
        state = append_history_event(
            state,
            "discard",
            actor_id=ctx.actor_id,
            target_player_ids=[pid],
            card_id=ctx.card_id,
            amount=len(picked),
            source="discard_random",
            data={"card_ids": list(picked)},
        ).with_log(f"{player.name} discards {len(picked)} random card{'s' if len(picked) != 1 else ''}")
    return state


def _reduce_destroy_card(state: GameState, op: DestroyCardOp, ctx: HookContext) -> GameState:
    """Remove one or more cards from wherever they live and send them to discard.

    Resolution precedence (non-breaking migration):
      - If ``op.card_target`` is set, resolve it via ``_resolve_card_targets``
        (may yield MANY card ids).
      - Otherwise fall back to the legacy single ``op.card_id``.

    Each resolved id is scrubbed from every player's ``hand`` and ``in_play``
    zones and from the shared ``center`` zone (house_rules), then appended to the
    discard pile (once, no duplicates). Persistent hooks registered by a
    destroyed card are unregistered too, and rules it set via set_rule revert
    (see ``_release_rule_bindings``) — destroying a board card removes its
    ongoing effect, not just the card.
    """
    if op.card_target is not None:
        card_ids = _resolve_card_targets(op.card_target, ctx, state)
    elif op.card_id is not None:
        card_ids = [op.card_id]
    else:
        card_ids = []

    if not card_ids:
        # A destroy_card that names no target (both fields None) or resolves to an
        # empty zone would otherwise vanish with no trace — the classic "the card
        # did nothing" bug. Log it so a mis-authored/mis-interpreted discard is
        # diagnosable rather than mysteriously inert.
        addr = op.card_target if op.card_target is not None else op.card_id
        return state.with_log(f"[destroy_card no-op] resolved no cards for target {addr!r}")

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
    hooks = [h for h in state.hooks if h.source_card_id not in targets]
    new_state = state.model_copy(
        update={"players": new_players, "house_rules": house_rules, "discard": discard, "hooks": hooks}
    )
    for source in dict.fromkeys(h.source_card_id for h in state.hooks if h.source_card_id in targets):
        new_state = new_state.with_log(f"[hook] unregistered {source} (card destroyed)")
    return _release_rule_bindings(new_state, targets)


def _reduce_transfer_card(state: GameState, op: TransferCardOp, ctx: HookContext) -> GameState:
    """Move resolved cards from any current zone into player hands.

    Finding the source zone here is intentional: during a resolution plan the
    played card is already staged in discard, while persistent cards or chosen
    cards may live in a hand, in-play, center, deck, or discard.

    ``to_target`` resolves to exactly one recipient — except "card_owner",
    which routes EACH card to its own owner (see ``_resolve_card_owner``);
    cards whose owner cannot be resolved stay put as logged per-card no-ops.
    """
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

    if op.to_target == CARD_OWNER:
        assignments: list[tuple[str, str]] = []
        for card_id in known:
            owner = _resolve_card_owner(state, card_id)
            if owner is None:
                state = state.with_log(f"[transfer_card no-op] no resolvable owner for card {card_id!r}")
                continue
            assignments.append((card_id, owner))
        if not assignments:
            return state
    else:
        recipients = _resolve_targets(op.to_target, ctx, state)
        if len(recipients) != 1:
            raise ValueError("transfer_card requires exactly one destination player")
        assignments = [(card_id, recipients[0]) for card_id in known]

    targets = {card_id for card_id, _ in assignments}
    by_recipient: dict[str, list[str]] = {}
    for card_id, recipient in assignments:
        by_recipient.setdefault(recipient, []).append(card_id)
    players = []
    for player in state.players:
        hand = [card for card in player.hand if card not in targets]
        in_play = [card for card in player.in_play if card not in targets]
        hand.extend(card for card in by_recipient.get(player.id, ()) if card not in hand)
        players.append(player.model_copy(update={"hand": hand, "in_play": in_play}))
    return state.model_copy(
        update={
            "players": players,
            "house_rules": [card for card in state.house_rules if card not in targets],
            "discard": [card for card in state.discard if card not in targets],
            "deck": [card for card in state.deck if card not in targets],
        }
    )


# Op-level zone name -> GameState.move_card zone literal ("exile" is the only
# spelling difference; see models.effects.Zone).
_MOVE_CARD_ZONE: dict[str, str] = {
    "deck": "deck",
    "discard": "discard",
    "hand": "hand",
    "in_play": "in_play",
    "center": "center",
    "exile": "exiled",
}

_PLAYER_ZONES: frozenset[str] = frozenset({"hand", "in_play"})


def _zone_card_ids(state: GameState, zone: str, player_id: str | None = None) -> list[str]:
    """The card ids currently in one op-level zone (a copy, list order kept)."""
    if zone in _PLAYER_ZONES:
        return list(getattr(state.get_player(player_id), zone))
    if zone == "center":
        return state.center_cards()
    if zone == "exile":
        return list(state.exiled)
    return list(getattr(state, zone))


def _locate_card_zone(state: GameState, card_id: str) -> tuple[str | None, str | None]:
    """Find which op-level zone (and owner, for hand/in_play) holds ``card_id``."""
    for zone in ("deck", "discard"):
        if card_id in getattr(state, zone):
            return zone, None
    if card_id in state.exiled:
        return "exile", None
    if card_id in state.house_rules:
        return "center", None
    for player in state.players:
        if card_id in player.hand:
            return "hand", player.id
        if card_id in player.in_play:
            return "in_play", player.id
    return None, None


def _resolve_card_owner(state: GameState, card_id: str) -> str | None:
    """Resolve the "card_owner" destination for ONE card, or None if unowned.

    Precedence (most defensible claim first):
      1. The player whose hand/in_play zone currently holds the card.
      2. The actor of the card's most recent "play" history event — the hand
         the card was played FROM, so a played-then-discarded card ("return
         the last card played to its owner's hand") goes back to whoever
         played it, not whoever wrote it.
      3. The ``creator_id`` recorded on the card dict, when it names a live
         player (seed/blank cards hold a source label there, not a player id).
    """
    zone, holder = _locate_card_zone(state, card_id)
    if zone in _PLAYER_ZONES and holder is not None:
        return holder
    player_ids = {player.id for player in state.players}
    for event in reversed(state.history_events):
        if event.kind == "play" and event.card_id == card_id and event.actor_id in player_ids:
            return event.actor_id
    card = state.cards.get(card_id)
    creator = card.get("creator_id") if isinstance(card, dict) else None
    return creator if creator in player_ids else None


def _select_from_zone(cards: list[str], zone: str, selector: str, count: int, rng: random.Random) -> list[str]:
    """Apply a MoveCardsOp selector to one zone's card list, top-most first.

    The deck is front-ordered (index 0 is the next draw); every other zone
    appends, so its "top" (most recent) is the END of the list.
    """
    if selector == "all":
        return list(cards)
    n = min(count, len(cards))
    if n == 0:
        return []
    if selector == "random":
        return rng.sample(cards, n)
    top_first = cards[:n] if zone == "deck" else list(reversed(cards[-n:]))
    if selector == "top":
        return top_first
    return list(reversed(cards[-n:])) if zone == "deck" else cards[:n]


def _reduce_move_cards(
    state: GameState, op: MoveCardsOp, ctx: HookContext, *, rng: random.Random | None = None
) -> GameState:
    """Move cards between zones (see :class:`~models.effects.MoveCardsOp`).

    ``selector="random"`` and ``to_position="shuffle"`` draw from the injected
    rng at reduce time — never pre-resolved by the sandbox, so snippets cannot
    observe which hidden card moved. An empty source resolution is a logged
    no-op. The log and history stay privacy-safe: counts and zone names only,
    except moves INTO the discard pile (public), which record a "discard"
    history event carrying the card ids like discard_random.

    A card moved OFF the board (out of center/in_play into any other zone)
    retires its ongoing effect exactly like destroy_card: its persistent hooks
    unregister and any rule it set via set_rule reverts (see
    ``_release_rule_bindings``).
    """
    rng = rng or random.Random()

    moves: list[tuple[str, str, str | None]] = []
    if op.card_target is not None:
        for cid in _resolve_card_targets(op.card_target, ctx, state):
            zone, owner = _locate_card_zone(state, cid)
            if zone is not None:
                moves.append((cid, zone, owner))
    else:
        if op.from_zone in _PLAYER_ZONES:
            sources = [(op.from_zone, pid) for pid in _resolve_targets(op.from_player, ctx, state)]
        else:
            sources = [(op.from_zone, None)]
        for zone, owner in sources:
            for cid in _select_from_zone(_zone_card_ids(state, zone, owner), zone, op.selector, op.count, rng):
                moves.append((cid, zone, owner))

    source_label = op.card_target if op.card_target is not None else op.from_zone
    # An explicit id moving into a HIDDEN zone (deck/hand) must not be named in
    # the shared log: a scry write-back that logged "id:X -> deck" would pin
    # deck positions to ids the table can correlate with earlier public events.
    if op.card_target is not None and op.card_target.startswith("id:") and op.to_zone in ("deck", "hand"):
        source_label = "a chosen card"
    if not moves:
        return state.with_log(f"[move_cards no-op] no cards to move from {source_label!r}")

    to_player_id: str | None = None
    owner_routing = op.to_zone in _PLAYER_ZONES and op.to_player == CARD_OWNER
    if owner_routing:
        # Owners resolve against the PRE-move state, per card ("card_owner" is
        # a per-card destination — see _resolve_card_owner); ownerless cards
        # stay put as logged per-card no-ops.
        owners = {cid: _resolve_card_owner(state, cid) for cid, _, _ in moves}
        for cid in (cid for cid, resolved in owners.items() if resolved is None):
            state = state.with_log(f"[move_cards no-op] no resolvable owner for card {cid!r}")
        moves = [move for move in moves if owners[move[0]] is not None]
        if not moves:
            return state
    elif op.to_zone in _PLAYER_ZONES:
        recipients = _resolve_targets(op.to_player, ctx, state)
        if not recipients:
            return state.with_log(f"[move_cards no-op] resolved no players for to_player {op.to_player!r}")
        if len(recipients) > 1:
            raise ValueError("move_cards requires exactly one destination player")
        to_player_id = recipients[0]

    for position, (cid, zone, owner) in enumerate(moves):
        deck_index: int | None = None
        if op.to_zone == "deck":
            if op.to_position == "top":
                deck_index = position
            elif op.to_position == "shuffle":
                deck_index = rng.randint(0, max(len(state.deck) - (1 if zone == "deck" else 0), 0))
        state = state.move_card(
            cid,
            _MOVE_CARD_ZONE[zone],
            _MOVE_CARD_ZONE[op.to_zone],
            from_player_id=owner,
            to_player_id=owners[cid] if owner_routing else to_player_id,
            deck_index=deck_index,
        )

    if op.to_zone not in ("center", "in_play"):
        departed = {cid for cid, zone, _ in moves if zone in ("center", "in_play")}
        retired = list(dict.fromkeys(h.source_card_id for h in state.hooks if h.source_card_id in departed))
        if retired:
            state = state.model_copy(update={"hooks": [h for h in state.hooks if h.source_card_id not in departed]})
            for source in retired:
                state = state.with_log(f"[hook] unregistered {source} (card left play)")
        if departed:
            state = _release_rule_bindings(state, departed)

    count = len(moves)
    if owner_routing:
        dest_label = f"{op.to_zone}(card_owner)"
    else:
        dest_label = op.to_zone if to_player_id is None else f"{op.to_zone}({to_player_id})"
    state = state.with_log(f"[move_cards] {count} card{'s' if count != 1 else ''}: {source_label} -> {dest_label}")
    if op.to_zone == "discard":
        state = append_history_event(
            state,
            "discard",
            actor_id=ctx.actor_id,
            target_player_ids=list(dict.fromkeys(owner for _, _, owner in moves if owner is not None)),
            card_id=ctx.card_id,
            amount=count,
            source="move_cards",
            data={"card_ids": [cid for cid, _, _ in moves]},
        )
    return state


def _reduce_shuffle_deck(
    state: GameState, op: ShuffleDeckOp, ctx: HookContext, *, rng: random.Random | None = None
) -> GameState:
    """Shuffle the deck in place; ``include_discard`` folds the discard pile in first."""
    rng = rng or random.Random()
    deck = list(state.deck)
    update: dict[str, Any] = {}
    if op.include_discard:
        deck = [*deck, *state.discard]
        update["discard"] = []
    rng.shuffle(deck)
    update["deck"] = deck
    line = "shuffled the discard pile into the deck" if op.include_discard else "shuffled the deck"
    return state.model_copy(update=update).with_log(f"[shuffle_deck] {line}")


def _update_player_visibility(state: GameState, player_id: str, update: dict[str, Any]) -> GameState:
    players = [p.model_copy(update=update) if p.id == player_id else p for p in state.players]
    return state.model_copy(update={"players": players})


def _reduce_reveal_hand(state: GameState, op: RevealHandOp, ctx: HookContext) -> GameState:
    """Reveal or conceal hands (see :class:`~models.effects.RevealHandOp`).

    Records its own "reveal" history event here (not in record_op_history)
    because the one-shot form changes no state to diff — and the event carries
    PLAYER ids only, never card ids, preserving the history privacy invariant.
    One-shot reveals are handed to the board via ``collect_hand_reveals``.
    """
    owners = _resolve_targets(op.target, ctx, state)
    if not owners:
        return state.with_log(f"[reveal_hand no-op] resolved no players for target {op.target!r}")

    if op.mode == "conceal":
        for pid in owners:
            if op.to == "all":
                state = _update_player_visibility(state, pid, {"hand_public": False, "hand_revealed_to": []})
            else:
                removed = set(_resolve_targets(op.to, ctx, state))
                remaining = [v for v in state.get_player(pid).hand_revealed_to if v not in removed]
                state = _update_player_visibility(state, pid, {"hand_revealed_to": remaining})
    elif op.persistent:
        viewers = _resolve_targets(op.to, ctx, state)
        for pid in owners:
            if op.to == "all":
                state = _update_player_visibility(state, pid, {"hand_public": True})
            else:
                current = state.get_player(pid).hand_revealed_to
                added = [v for v in viewers if v != pid and v not in current]
                state = _update_player_visibility(state, pid, {"hand_revealed_to": [*current, *added]})
    else:
        viewers = _resolve_targets(op.to, ctx, state)
        drain = _hand_reveal_drain.get()
        if drain is not None:
            for pid in owners:
                hand = list(state.get_player(pid).hand)
                drain.append(
                    {
                        "player_id": pid,
                        "viewer_ids": [v for v in viewers if v != pid],
                        "card_ids": hand,
                        # Card bodies captured NOW, from the working state: the
                        # audience's redacted snapshots never carry hidden hand
                        # content, so the push must be self-contained.
                        "cards": {cid: state.cards[cid] for cid in hand if cid in state.cards},
                    }
                )

    return append_history_event(
        state,
        "reveal",
        actor_id=ctx.actor_id,
        target_player_ids=owners,
        source=op.mode,
    )


def _reduce_eliminate_player(state: GameState, op: EliminatePlayerOp, ctx: HookContext) -> GameState:
    """Knock resolved players out: set ``Player.eliminated`` and discard the hand.

    ``in_play`` is untouched — an eliminated player's table cards (and any
    hooks/rules they registered) keep working. Targets resolve sequentially and
    the guard holds per player: a target who would be the LAST non-eliminated
    player survives as a logged no-op, so "eliminate everyone" leaves exactly
    one player standing. Already-eliminated targets are skipped silently.
    """
    for pid in _resolve_targets(op.target, ctx, state):
        player = state.get_player(pid)
        if player.eliminated:
            continue
        if all(p.eliminated or p.id == pid for p in state.players):
            state = state.with_log(f"[eliminate_player no-op] {player.name} is the last player standing")
            continue
        discard = list(state.discard)
        for cid in player.hand:
            if cid not in discard:
                discard.append(cid)
        players = [p.model_copy(update={"eliminated": True, "hand": []}) if p.id == pid else p for p in state.players]
        state = state.model_copy(update={"players": players, "discard": discard}).with_log(
            f"{player.name} has been eliminated"
        )
    return state


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
            state = state.with_condition(pid, op.key, op.value, ttl=op.duration_turns)
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
    discard = list(state.discard)
    house_rules = list(state.house_rules)
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

    destination_label = op.destination
    if op.destination == "deck_top":
        deck = [*new_ids, *deck]
    elif op.destination == "deck_bottom":
        deck = [*deck, *new_ids]
    elif op.destination == "deck_shuffle":
        for cid in new_ids:
            deck.insert(rng.randint(0, len(deck)), cid)
    elif op.destination == "discard":
        discard = [*discard, *new_ids]
    elif op.destination == "center":
        house_rules = [*house_rules, *new_ids]
    else:
        targets = set(_resolve_targets(op.target, ctx, state))
        players = [p.model_copy(update={"hand": [*p.hand, *new_ids]}) if p.id in targets else p for p in players]
        destination_label = f"hand({op.target})"

    return state.model_copy(
        update={"cards": cards, "deck": deck, "discard": discard, "house_rules": house_rules, "players": players}
    ).with_log(f"[created] {op.count}x '{op.title}' -> {destination_label}")


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


_SCALAR_RULE_PATHS = frozenset({"draw", "play", "skip_predicate", "hand_limit", "turn_timer"})
_NESTED_RULE_HEADS = frozenset({"end_condition", "win_condition", "cannot_play"})


def _read_rule_path(rules: dict, path: str) -> object:
    """Return the current value at a set_rule path in a dumped Rules dict."""
    if "." in path:
        head, key = path.split(".", 1)
        sub = rules.get(head)
        return sub.get(key) if isinstance(sub, dict) else None
    return rules.get(path)


def _write_rule_path(rules: dict, path: str, value: object) -> None:
    """Write one set_rule path into a dumped Rules dict (in place).

    Raises ValueError on unknown paths so callers surface them the same way as
    unresolvable targets.
    """
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


def _reduce_set_rule(state: GameState, op: SetRuleOp, ctx: HookContext) -> GameState:
    """Write one rule path. Unknown paths / invalid values raise ValueError.

    When the write comes from a known source card, a RuleBinding recording the
    path's previous value is appended so destroying that card can revert the
    rule (see ``_release_rule_bindings``). Attribution prefers
    ``ctx.source_card_id`` (set by hook dispatch to the firing hook's own card)
    over ``ctx.card_id`` (the triggering play). Source-less writes (house-rule
    flows) record nothing and behave as before.
    """
    rules = state.rules.model_dump()
    previous = _read_rule_path(rules, op.path)
    _write_rule_path(rules, op.path, op.value)
    try:
        new_rules = Rules.model_validate(rules)
    except PydanticValidationError as exc:
        raise ValueError(f"set_rule: invalid value for {op.path!r}: {exc}") from exc
    update: dict = {"rules": new_rules}
    source = ctx.source_card_id or ctx.card_id
    if source is not None:
        binding = RuleBinding(source_card_id=source, path=op.path, previous_value=previous)
        update["rule_bindings"] = [*state.rule_bindings, binding]
    return state.model_copy(update=update)


def _release_rule_bindings(state: GameState, destroyed: set[str]) -> GameState:
    """Drop destroyed cards' rule bindings, reverting rules where needed.

    Bindings for one path form a stack (list order). Removing the most recent
    binding for a path reverts the rule to its recorded previous value; removing
    a buried binding splices it out — the binding above inherits its
    previous_value and the live rule value is untouched.
    """
    if not any(b.source_card_id in destroyed for b in state.rule_bindings):
        return state
    remaining: list[RuleBinding] = []
    carried: dict[str, object] = {}
    for binding in state.rule_bindings:
        if binding.source_card_id in destroyed:
            carried.setdefault(binding.path, binding.previous_value)
        elif binding.path in carried:
            remaining.append(binding.model_copy(update={"previous_value": carried.pop(binding.path)}))
        else:
            remaining.append(binding)
    new_state = state.model_copy(update={"rule_bindings": remaining})
    if carried:
        # Still-carried paths lost their topmost binding: revert the live rule.
        rules = state.rules.model_dump()
        for path, value in carried.items():
            _write_rule_path(rules, path, value)
        new_state = new_state.model_copy(update={"rules": Rules.model_validate(rules)})
        for path in carried:
            new_state = new_state.with_log(f"[rule] reverted {path} (card destroyed)")
    return new_state


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
    "reveal_hand": _reduce_reveal_hand,
    "eliminate_player": _reduce_eliminate_player,
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

    ``rng`` is only consumed by ``scramble_order``, ``create_card``,
    ``roll_die``, ``discard_random``, ``move_cards`` and ``shuffle_deck``
    (dependency-injectable for deterministic tests); every other op ignores it.
    """
    before = state
    if op.op == "scramble_order":
        state = _reduce_scramble_order(state, op, ctx, rng=rng)
    elif op.op == "create_card":
        state = _reduce_create_card(state, op, ctx, rng=rng)
    elif op.op == "roll_die":
        state = _reduce_roll_die(state, op, ctx, rng=rng)
    elif op.op == "discard_random":
        state = _reduce_discard_random(state, op, ctx, rng=rng)
    elif op.op == "move_cards":
        state = _reduce_move_cards(state, op, ctx, rng=rng)
    elif op.op == "shuffle_deck":
        state = _reduce_shuffle_deck(state, op, ctx, rng=rng)
    else:
        state = _REDUCERS[op.op](state, op, ctx)

    return record_op_history(before, state, op, ctx)


__all__ = ["apply_op", "collect_hand_reveals"]
