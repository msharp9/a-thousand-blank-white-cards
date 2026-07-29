"""Validation, dry-run preview, and pure application of host-admin actions."""

from __future__ import annotations

import random
from dataclasses import dataclass

from engine.events import GameEvent, HookContext
from engine.reducers import apply_op
from models.admin import (
    AdminAction,
    AdminProposalPreviewItem,
    EliminatePlayersAdminAction,
    EndGameAdminAction,
    MoveCardAdminAction,
    RemoveConditionAdminAction,
    RemoveHookAdminAction,
    SetConditionAdminAction,
    SetResultWinnersAdminAction,
    SetScoreAdminAction,
    ShuffleDeckAdminAction,
)
from models.effects import (
    EliminatePlayerOp,
    EndGameOp,
    MoveCardsOp,
    SetConditionOp,
    SetPointsOp,
    ShuffleDeckOp,
)
from models.game_state import GameState


@dataclass(frozen=True)
class AdminApplication:
    state: GameState
    preview: list[AdminProposalPreviewItem]
    warnings: list[str]
    ends_game: bool
    active_player_eliminated: bool


def _player_name(state: GameState, player_id: str) -> str:
    try:
        return state.get_player(player_id).name
    except KeyError as exc:
        raise ValueError(f"Unknown player {player_id!r}") from exc


def _participant_name(state: GameState, participant_id: str) -> str:
    try:
        return state.participant_name(participant_id)
    except KeyError as exc:
        raise ValueError(f"Unknown participant {participant_id!r}") from exc


def _card_title(state: GameState, card_id: str) -> str:
    card = state.cards.get(card_id)
    if card is None:
        raise ValueError(f"Unknown card {card_id!r}")
    if isinstance(card, dict):
        return card.get("title") or "Untitled card"
    return getattr(card, "title", None) or "Untitled card"


def _locate_card(state: GameState, action: MoveCardAdminAction) -> None:
    card_id = action.card_id
    if card_id is None:
        raise ValueError("Exact card move requires a card id")
    if action.source_zone == "deck":
        present = card_id in state.deck
    elif action.source_zone == "discard":
        present = card_id in state.discard
    elif action.source_zone == "center":
        present = card_id in state.house_rules
    elif action.source_zone == "exile":
        present = card_id in state.exiled
    elif action.source_zone == "in_play":
        present = card_id in state.get_player(action.source_player_id or "").in_play
    elif action.source_zone == "hand":
        present = card_id in state.get_player(action.source_player_id or "").hand
    else:
        present = False
    if not present:
        raise ValueError(f"Card is not in the selected {action.source_zone} zone")


def _destination_label(state: GameState, action: MoveCardAdminAction) -> str:
    if action.to_zone in {"hand", "in_play"}:
        return f"{_player_name(state, action.to_player_id or '')}'s {action.to_zone.replace('_', ' ')}"
    if action.to_zone == "deck":
        position = "random position" if action.deck_position == "shuffle" else action.deck_position
        return f"{position} of deck"
    return action.to_zone


def _condition_label(key: str, value: object) -> str:
    name = key.replace("_", " ")
    if value is True:
        return name
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{name} ×{value}"
    return f"{name}: {value}"


def _move_op(action: MoveCardAdminAction) -> MoveCardsOp:
    destination = "exile" if action.to_zone == "exile" else action.to_zone
    kwargs: dict = {
        "to_zone": destination,
        "to_position": action.deck_position or "top",
    }
    if action.to_player_id is not None:
        kwargs["to_player"] = f"id:{action.to_player_id}"
    if action.source_zone == "deck" and action.selector is not None:
        kwargs.update({"from_zone": "deck", "selector": action.selector, "count": 1})
    else:
        kwargs["card_target"] = f"id:{action.card_id}"
    return MoveCardsOp(**kwargs)


def _validate_phase(actions: list[AdminAction], phase: str) -> None:
    if phase == "results":
        if any(action.kind not in {"set_score", "set_result_winners"} for action in actions):
            raise ValueError("Results proposals may only change scores and final winners")
        if sum(action.kind == "set_result_winners" for action in actions) != 1:
            raise ValueError("Results proposals must explicitly confirm final winners")
        return

    if phase != "playing":
        raise ValueError("Host corrections are only available during play or results")
    if any(action.kind == "set_result_winners" for action in actions):
        raise ValueError("Final result winners can only be corrected on the results screen")
    terminal = [index for index, action in enumerate(actions) if action.kind == "end_game"]
    if len(terminal) > 1 or (terminal and terminal[0] != len(actions) - 1):
        raise ValueError("End game may appear once and must be the final action")


def apply_admin_actions(
    state: GameState,
    actions: list[AdminAction],
    proposer_id: str,
    *,
    rng_seed: int,
    allow_hidden_sources: bool = False,
) -> AdminApplication:
    """Apply a validated action bundle to an immutable state copy.

    This deliberately calls ``apply_op`` rather than ``apply_effect`` so a
    correction cannot fire score-change hooks that were not part of the vote.
    """

    _validate_phase(actions, state.phase)
    _participant_name(state, proposer_id)
    working = state
    previews: list[AdminProposalPreviewItem] = []
    warnings: list[str] = []
    rng = random.Random(rng_seed)
    active_id = state.active_player().id if state.players else None

    for action in actions:
        before = working
        ctx = HookContext(event=GameEvent.ON_PLAY, actor_id=proposer_id)

        if isinstance(action, SetScoreAdminAction):
            player = working.get_player(action.player_id)
            if player.score == action.score:
                raise ValueError(f"{player.name}'s score is already {action.score}")
            working = apply_op(
                working,
                SetPointsOp(target=f"id:{action.player_id}", amount=action.score),
                ctx,
            )
            previews.append(
                AdminProposalPreviewItem(
                    kind=action.kind,
                    title=f"Set {player.name}'s score",
                    detail=f"{player.score} → {action.score}",
                )
            )

        elif isinstance(action, MoveCardAdminAction):
            hidden_source = action.source_zone == "hand" or (
                action.source_zone == "deck" and action.card_id is not None
            )
            # Authorize before checking membership/title so a normal host
            # cannot use guessed card ids as a hidden-state oracle.
            if hidden_source and not allow_hidden_sources:
                raise ValueError("Exact hidden-card selection requires a spectator host")

            if action.source_zone == "deck" and action.selector is not None:
                if not working.deck:
                    raise ValueError("The deck is empty")
                card_label = f"{action.selector.title()} card of deck"
            else:
                _locate_card(working, action)
                card_label = _card_title(working, action.card_id or "")
            if action.to_player_id is not None:
                _player_name(working, action.to_player_id)
            before_hooks = len(working.hooks)
            before_rules = len(working.rule_bindings)
            before_conditions = len(working.condition_bindings)
            working = apply_op(working, _move_op(action), ctx, rng=rng)
            if action.source_zone == "hand":
                source = f"{_player_name(before, action.source_player_id or '')}'s hand"
            else:
                source = "deck" if action.source_zone == "deck" else action.source_zone.replace("_", " ")
            destination = _destination_label(before, action)
            cascades = []
            if len(working.hooks) < before_hooks:
                cascades.append(f"{before_hooks - len(working.hooks)} hook(s) removed")
            if len(working.rule_bindings) < before_rules:
                cascades.append(f"{before_rules - len(working.rule_bindings)} rule binding(s) released")
            if len(working.condition_bindings) < before_conditions:
                cascades.append(f"{before_conditions - len(working.condition_bindings)} condition binding(s) released")
            detail = f"{card_label}: {source} → {destination}"
            if cascades:
                detail += f"; {', '.join(cascades)}"
            if action.source_zone in {"deck", "discard", "hand"} and action.to_zone in {"center", "in_play"}:
                warning_label = "The selected hidden card" if hidden_source else card_label
                warnings.append(f"{warning_label} will move into play without replaying its old effect.")
            if hidden_source:
                public_detail = f"A selected hidden card: {source} → {destination}"
                if cascades:
                    public_detail += f"; {', '.join(cascades)}"
                private_viewers = [proposer_id]
                if action.source_zone == "hand" and action.source_player_id is not None:
                    private_viewers.append(action.source_player_id)
                if action.to_zone == "hand" and action.to_player_id is not None:
                    private_viewers.append(action.to_player_id)
                previews.append(
                    AdminProposalPreviewItem(
                        kind=action.kind,
                        title="Move hidden card",
                        detail=public_detail,
                        private_detail=detail,
                        private_viewer_ids=list(dict.fromkeys(private_viewers)),
                    )
                )
            else:
                previews.append(AdminProposalPreviewItem(kind=action.kind, title="Move card", detail=detail))

        elif isinstance(action, ShuffleDeckAdminAction):
            total = len(working.deck) + (len(working.discard) if action.include_discard else 0)
            if total < 2:
                raise ValueError("At least two cards are required to shuffle")
            working = apply_op(
                working,
                ShuffleDeckOp(include_discard=action.include_discard),
                ctx,
                rng=rng,
            )
            detail = (
                f"Shuffle {len(before.deck)} deck card(s) with {len(before.discard)} discard(s)"
                if action.include_discard
                else f"Shuffle {len(before.deck)} deck card(s)"
            )
            previews.append(AdminProposalPreviewItem(kind=action.kind, title="Shuffle deck", detail=detail))

        elif isinstance(action, SetConditionAdminAction):
            player = working.get_player(action.player_id)
            previous = player.conditions.get(action.key)
            working = apply_op(
                working,
                SetConditionOp(
                    target=f"id:{action.player_id}",
                    key=action.key,
                    value=action.value,
                    duration_turns=action.duration_turns,
                ),
                ctx,
            )
            detail = f"{player.name}: {_condition_label(action.key, action.value)}"
            if previous is not None:
                detail += f" (was {_condition_label(action.key, previous)})"
            if action.duration_turns is not None:
                detail += f" for {action.duration_turns} turn(s)"
            previews.append(AdminProposalPreviewItem(kind=action.kind, title="Set condition", detail=detail))

        elif isinstance(action, RemoveConditionAdminAction):
            player = working.get_player(action.player_id)
            if action.key not in player.conditions:
                raise ValueError(f"{player.name} does not have condition {action.key!r}")
            previous = player.conditions[action.key]
            working = apply_op(
                working,
                SetConditionOp(target=f"id:{action.player_id}", key=action.key, value=None),
                ctx,
            )
            previews.append(
                AdminProposalPreviewItem(
                    kind=action.kind,
                    title="Remove condition",
                    detail=f"{player.name}: remove {_condition_label(action.key, previous)}",
                )
            )

        elif isinstance(action, RemoveHookAdminAction):
            hook = next((candidate for candidate in working.hooks if candidate.id == action.hook_id), None)
            if hook is None:
                raise ValueError(f"Unknown hook {action.hook_id!r}")
            source_title = _card_title(working, hook.source_card_id)
            working = working.model_copy(update={"hooks": [h for h in working.hooks if h.id != hook.id]})
            previews.append(
                AdminProposalPreviewItem(
                    kind=action.kind,
                    title="Remove hook",
                    detail=f"{source_title}: {hook.event} ({hook.scope})",
                )
            )

        elif isinstance(action, EliminatePlayersAdminAction):
            names = []
            for player_id in action.player_ids:
                player = working.get_player(player_id)
                if player.eliminated:
                    raise ValueError(f"{player.name} is already eliminated")
                working = apply_op(working, EliminatePlayerOp(target=f"id:{player_id}"), ctx)
                if not working.get_player(player_id).eliminated:
                    raise ValueError(f"{player.name} cannot be eliminated as the final active player")
                names.append(player.name)
            previews.append(
                AdminProposalPreviewItem(
                    kind=action.kind,
                    title="Mark player(s) as losers",
                    detail=f"Eliminate {', '.join(names)} and discard their hands",
                )
            )

        elif isinstance(action, EndGameAdminAction):
            winners = action.winner_ids
            if winners is not None:
                for player_id in winners:
                    player = working.get_player(player_id)
                    if player.eliminated:
                        raise ValueError(f"Eliminated player {player.name} cannot be declared a winner")
                names = [_player_name(working, player_id) for player_id in winners]
                op = EndGameOp(winners=[f"id:{player_id}" for player_id in winners])
                detail = f"End the game with winner(s): {', '.join(names)}"
            else:
                op = EndGameOp()
                detail = "End the game using the current win condition"
            working = apply_op(working, op, ctx)
            previews.append(AdminProposalPreviewItem(kind=action.kind, title="End game", detail=detail))

        elif isinstance(action, SetResultWinnersAdminAction):
            names = [_player_name(working, player_id) for player_id in action.winner_ids]
            working = working.model_copy(update={"winner_ids": list(action.winner_ids)})
            previews.append(
                AdminProposalPreviewItem(
                    kind=action.kind,
                    title="Correct final winner(s)",
                    detail=f"Winner(s): {', '.join(names)}" if names else "No winner",
                )
            )

        else:  # pragma: no cover - discriminated union is exhaustive
            raise ValueError(f"Unsupported admin action {action!r}")

    return AdminApplication(
        state=working,
        preview=previews,
        warnings=list(dict.fromkeys(warnings)),
        ends_game=any(isinstance(action, EndGameAdminAction) for action in actions),
        active_player_eliminated=bool(
            active_id is not None
            and working.get_player(active_id).eliminated
            and not state.get_player(active_id).eliminated
        ),
    )


__all__ = ["AdminApplication", "apply_admin_actions"]
