from __future__ import annotations

import json
from pathlib import Path

from agent.tools.dry_run_effect import dry_run_resolution_plan
from engine.compile import compile_card_plan
from engine.history import append_history_event
from models.game_state import GameState, Player

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _filler(title: str) -> dict:
    cards = json.loads((DATA_DIR / "seed_cards_fillers.json").read_text())
    return next(card for card in cards if card["title"] == title)


def _state(card: dict, hands: dict[str, list[str]], *, deck_size: int = 10) -> GameState:
    card_ids = {card_id for hand in hands.values() for card_id in hand}
    deck = [f"deck-{index}" for index in range(deck_size)]
    cards = {
        card_id: (
            {**card, "id": card_id, "origin": "seed"} if card_id == card["id"] else {"id": card_id, "title": card_id}
        )
        for card_id in card_ids
    }
    cards.update({card_id: {"id": card_id, "title": card_id} for card_id in deck})
    return GameState(
        room_code="SEED",
        players=[Player(id=player_id, name=player_id.upper(), hand=hand) for player_id, hand in hands.items()],
        cards=cards,
        deck=deck,
        turn_order=list(hands),
        phase="playing",
    )


def _run(card: dict, state: GameState) -> dict:
    plan = compile_card_plan({**card, "origin": "seed"})
    assert plan is not None and plan.steps
    return dry_run_resolution_plan(state, plan, "p1", card["id"])


def test_chaos_shuffle_rotates_entire_live_hands() -> None:
    card = _filler("Chaos Shuffle")
    report = _run(
        card,
        _state(
            card,
            {
                "p1": [card["id"], "alice-card"],
                "p2": ["bob-1", "bob-2"],
                "p3": ["carol-1", "carol-2", "carol-3"],
            },
        ),
    )

    assert report["ok"] is True, report
    assert report["after"]["hand_sizes"] == {"p1": 2, "p2": 3, "p3": 1}
    assert {op["op"] for op in report["emitted_ops"]} == {
        "move_cards",
        "set_card_attribute",
    }


def test_clone_takes_the_latest_played_card_still_in_the_game() -> None:
    card = _filler("The Clone")
    state = _state(card, {"p1": [card["id"], "keeper"], "p2": []})
    state = state.model_copy(
        update={
            "cards": {
                **state.cards,
                "old-play": {"id": "old-play", "title": "Old Play"},
            },
            "discard": ["old-play"],
        }
    )
    state = append_history_event(state, "play", actor_id="p2", card_id="old-play")

    report = _run(card, state)

    assert report["ok"] is True, report
    assert report["after"]["hand_sizes"]["p1"] == 2
    assert report["emitted_ops"] == [{"op": "transfer_card", "card_target": "id:old-play", "to_target": "self"}]


def test_timeout_waits_for_every_player_with_a_two_minute_cap() -> None:
    card = _filler("Timeout")
    plan = compile_card_plan({**card, "origin": "seed"})
    assert plan is not None
    interaction = card["canonical"]["steps"][0]["request"]
    report = dry_run_resolution_plan(
        _state(card, {"p1": [card["id"]], "p2": []}),
        plan,
        "p1",
        card["id"],
    )

    assert interaction["audience"] == "all"
    assert interaction["timeout_seconds"] == 120
    assert interaction["options"] == [{"id": "ready", "label": "Ready"}]
    assert report["ok"] is True, report
    assert report["interactions"] == {"ready": {"p1": ["ready"], "p2": ["ready"]}}


def test_hot_potato_penalizes_the_right_neighbor_and_moves_itself() -> None:
    card = _filler("Hot Potato")
    report = _run(
        card,
        _state(card, {"p1": [card["id"]], "p2": [], "p3": []}),
    )

    assert report["ok"] is True, report
    assert report["after"]["scores"] == {"p1": 0, "p2": -2, "p3": 0}
    assert report["after"]["hand_sizes"] == {"p1": 0, "p2": 1, "p3": 0}


def test_nap_time_draws_three_and_schedules_the_players_next_turn_skip() -> None:
    card = _filler("Nap Time")
    report = _run(card, _state(card, {"p1": [card["id"], "keeper"], "p2": []}))

    assert report["ok"] is True, report
    assert report["after"]["hand_sizes"]["p1"] == 4
    assert report["after"]["deck_size"] == 7
    assert any(op["op"] == "skip_turn" and op["target"] == "self" for op in report["emitted_ops"])


def _gold(title: str) -> dict:
    cards = json.loads((DATA_DIR / "seed_cards_gold.json").read_text())
    return next(card for card in cards if card["title"] == title)


def test_into_the_void_exiles_only_the_chosen_center_card() -> None:
    card = _gold("Into the Void")
    (op,) = card["canonical"]["ops"]
    assert op["args"] == {"card_target": "chosen_card", "from_zone": "center", "to_zone": "exile"}
    assert "from_zone='center'" in card["canonical"]["sandbox"]

    from engine.events import GameEvent, HookContext
    from engine.reducers import apply_op

    plan = compile_card_plan({**card, "origin": "seed"})
    assert plan is not None and plan.steps
    state = _state(card, {"p1": [card["id"], "keeper"], "p2": []})
    state = state.model_copy(
        update={
            "cards": {**state.cards, "house-rule": {"id": "house-rule", "title": "House Rule"}},
            "house_rules": ["house-rule"],
        }
    )
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id=card["id"], chosen_card_id="house-rule")
    (compiled_op,) = plan.operations()
    new = apply_op(state, compiled_op, ctx)

    assert new.exiled == ["house-rule"]
    assert new.house_rules == []
    assert new.get_player("p1").hand == [card["id"], "keeper"]


def test_into_the_void_ignores_a_card_that_is_not_in_the_center() -> None:
    card = _gold("Into the Void")

    from engine.events import GameEvent, HookContext
    from engine.reducers import apply_op

    plan = compile_card_plan({**card, "origin": "seed"})
    state = _state(card, {"p1": [card["id"], "keeper"], "p2": []})
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id=card["id"], chosen_card_id="keeper")
    (compiled_op,) = plan.operations()
    new = apply_op(state, compiled_op, ctx)

    assert new.exiled == []
    assert "keeper" in new.get_player("p1").hand
    assert any("[move_cards no-op]" in line for line in new.log)


def test_mystery_box_discards_one_then_draws_two() -> None:
    card = _filler("Mystery Box")
    report = _run(
        card,
        _state(card, {"p1": [card["id"], "first", "second"], "p2": []}),
    )

    assert report["ok"] is True, report
    assert report["after"]["hand_sizes"]["p1"] == 3
    assert report["after"]["deck_size"] == 8
    assert [op["op"] for op in report["emitted_ops"]] == [
        "discard_random",
        "draw_cards",
    ]
