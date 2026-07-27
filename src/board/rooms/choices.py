"""board.rooms.choices — the shared play-time choice policy.

One canonical implementation of the prompt_choice preconditions (which axes a
resolved plan needs) and the ordered chosen_card candidate universe, used by
prompt creation AND response validation across direct plays, play_on_draw
auto-plays, and reaction plays. Room must never derive either ad hoc.
"""

from __future__ import annotations

from engine.events import GameEvent, HookContext
from engine.reducers import _resolve_targets
from models.effects import MoveCardsOp, Op, ResolutionPlan, op_choice_axes
from models.game_state import GameState


def plan_choice_needs(plan: ResolutionPlan) -> tuple[bool, bool]:
    """(needs_player_choice, needs_card_choice) for a resolved plan's ops —
    the prompt_choice preconditions shared by direct, reaction, and
    auto-play resolution."""
    needs_player = False
    needs_card = False
    for op in plan.operations():
        op_player, op_card = op_choice_axes(op)
        needs_player = needs_player or op_player
        needs_card = needs_card or op_card
    return needs_player, needs_card


def chosen_card_candidates(
    state: GameState,
    plan: ResolutionPlan,
    actor_id: str,
    source_card_id: str | None,
    *,
    chosen_player_id: str | None = None,
) -> list[str]:
    """The ordered card ids a chosen_card prompt may offer — and accept.

    Policy, per choice-requiring op:

    - a ``move_cards`` that declares ``from_zone`` scopes its candidates to
      that zone: center/exile/discard (already public) in state order;
      hand/in_play only from the resolved ``from_player`` owners; ``deck``
      is hidden and never offered — a chosen_card cannot be scoped to the
      deck without leaking its order/identity, so it yields no candidates;
    - an unscoped chosen_card keeps the legacy universe: every public
      in-play card plus the actor's own hand.

    The card being played is excluded, duplicates collapse in first-seen
    order, and when several ops share the single chosen_card_id their
    allowed sets intersect (preserving the first set's order). An empty
    result means nothing is legal to offer — callers must error / safely
    no-op, never fall back to a hand.
    """
    allowed: list[str] | None = None
    for op in plan.operations():
        if not op_choice_axes(op)[1]:
            continue
        candidates = _op_candidates(state, op, actor_id, chosen_player_id)
        if allowed is None:
            allowed = candidates
        else:
            keep = set(candidates)
            allowed = [cid for cid in allowed if cid in keep]
    ordered: list[str] = []
    seen: set[str] = set()
    for cid in allowed or ():
        if cid != source_card_id and cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    return ordered


def _op_candidates(state: GameState, op: Op, actor_id: str, chosen_player_id: str | None) -> list[str]:
    from_zone = op.from_zone if isinstance(op, MoveCardsOp) else None
    if from_zone is None:
        return [*state.cards_in_play(), *state.get_player(actor_id).hand]
    if from_zone in ("hand", "in_play"):
        cards: list[str] = []
        for pid in _owner_ids(state, op.from_player, actor_id, chosen_player_id):
            cards.extend(getattr(state.get_player(pid), from_zone))
        return cards
    if from_zone == "center":
        return state.center_cards()
    if from_zone == "exile":
        return list(state.exiled)
    if from_zone == "discard":
        return list(state.discard)
    return []


def _owner_ids(state: GameState, from_player: str, actor_id: str, chosen_player_id: str | None) -> list[str]:
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=actor_id, chosen_player_id=chosen_player_id)
    try:
        return _resolve_targets(from_player, ctx, state)
    except ValueError:
        return []
    except KeyError:
        return []
