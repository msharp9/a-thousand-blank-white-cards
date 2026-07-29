"""play_on_draw auto-plays (bead agd.2): cards that play themselves when drawn.

The mechanic is a card ATTRIBUTE (attributes.play_on_draw), not a hook event.
Room choke points — after the turn-start auto-draw and after every play's
accounting tail — scan hands and auto-play such cards for their owner through
the normal resolve/execute path at no action cost, hard-capped per turn.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult
from models.effects import AddPointsOp, EffectProgram, SetCardAttributeOp
from models.ws_messages import PlayMsg
from board.rooms.deck import _normalise_card
from board.rooms.room import MAX_AUTO_PLAYS_PER_TURN, Room


def _pod_card(cid: str, ops: list[dict], title: str | None = None) -> dict:
    return {
        "id": cid,
        "title": title or cid.upper(),
        "description": "",
        "attributes": {"play_on_draw": True},
        "canonical": {"ops": ops},
    }


def _plain_card(cid: str, ops: list[dict]) -> dict:
    return {"id": cid, "title": cid.upper(), "description": "", "canonical": {"ops": ops}}


ADD3 = [{"op": "add_points", "args": {"target": "self", "amount": 3}}]
DRAW1 = [{"op": "draw_cards", "args": {"target": "self", "amount": 1}}]


def _room(cards: dict[str, dict], *, deck: list[str], p1_hand: list[str] = []) -> Room:
    room = Room("PODTST")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    players = [p.model_copy(update={"hand": list(p1_hand)}) if p.id == "p1" else p for p in room.state.players]
    room.state = room.state.model_copy(
        update={"phase": "playing", "cards": dict(cards), "deck": list(deck), "players": players}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def _sent(room: Room, player_id: str) -> list[dict]:
    ws = room.connections._connections[player_id]
    return [json.loads(call.args[0]) for call in ws.send_text.call_args_list]


def test_auto_play_after_draw_costs_no_action() -> None:
    room = _room({"pod1": _pod_card("pod1", ADD3), "filler": _plain_card("filler", ADD3)}, deck=["pod1", "filler"])

    asyncio.run(room._start_turn("p1"))

    assert room.state.get_player("p1").score == 3
    assert "pod1" in room.state.discard
    assert room.state.get_player("p1").hand == []
    assert room._plays_this_turn == 0  # the auto-play took no action
    assert room._auto_plays_this_turn == 1
    assert room.state.active_player().id == "p1"
    assert any("plays itself" in line for line in room.state.log)


def test_mid_effect_draw_chain() -> None:
    cards = {
        "starter": _plain_card("starter", DRAW1),
        "pod1": _pod_card("pod1", ADD3),
        "filler": _plain_card("filler", ADD3),
    }
    room = _room(cards, deck=["pod1", "filler"], p1_hand=["starter"])
    room._has_drawn = True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="starter")))

    assert room.state.get_player("p1").score == 3  # the drawn pod card resolved
    assert {"starter", "pod1"} <= set(room.state.discard)
    assert room.state.active_player().id == "p2"  # only the manual play consumed the turn
    assert "filler" in room.state.get_player("p2").hand


def test_recursion_cap_defers_with_log_line_then_plays_next_turn() -> None:
    cards = {f"c{i}": _pod_card(f"c{i}", DRAW1) for i in range(1, 5)}
    cards["filler"] = _plain_card("filler", ADD3)
    room = _room(cards, deck=["c1", "c2", "c3", "c4", "filler"])

    asyncio.run(room._start_turn("p1"))

    assert room._auto_plays_this_turn == MAX_AUTO_PLAYS_PER_TURN
    assert {"c1", "c2", "c3"} <= set(room.state.discard)
    assert room.state.get_player("p1").hand == ["c4"]  # capped: stays in hand
    assert any("auto-play limit" in line for line in room.state.log)

    # A later turn's scan picks the deferred card back up (per-turn counters reset).
    asyncio.run(room._start_turn("p1"))
    assert "c4" in room.state.discard


def test_requires_choice_prompts_the_owner_then_resumes_at_no_cost() -> None:
    chooser_ops = [{"op": "add_points", "args": {"target": "chooser", "amount": 5}}]
    room = _room(
        {"podc": _pod_card("podc", chooser_ops), "filler": _plain_card("filler", ADD3)},
        deck=["podc", "filler"],
    )

    asyncio.run(room._start_turn("p1"))

    prompts = [m for m in _sent(room, "p1") if m.get("type") == "prompt_choice"]
    assert prompts and prompts[0]["card_id"] == "podc"
    assert room._pending_auto_play is not None
    assert "podc" in room.state.get_player("p1").hand  # waits for the answer

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="podc", chosen_player_id="p2")))

    assert room._pending_auto_play is None
    assert room.state.get_player("p2").score == 5
    assert "podc" in room.state.discard
    assert room._plays_this_turn == 0
    assert room.state.active_player().id == "p1"


def test_requires_choice_rejects_invalid_target_and_keeps_waiting() -> None:
    chooser_ops = [{"op": "add_points", "args": {"target": "chooser", "amount": 5}}]
    room = _room(
        {"podc": _pod_card("podc", chooser_ops), "filler": _plain_card("filler", ADD3)},
        deck=["podc", "filler"],
    )

    async def scenario() -> None:
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="podc", chosen_player_id="ghost"))

    asyncio.run(scenario())
    assert room._pending_auto_play is not None
    assert any("Invalid target player" in m.get("message", "") for m in _sent(room, "p1"))


def test_reaction_window_opens_for_auto_plays() -> None:
    cards = {
        "pod1": _pod_card("pod1", ADD3),
        "filler": _plain_card("filler", ADD3),
        "rc": {"id": "rc", "title": "Counter!", "canonical": {"trigger": "on_reaction"}},
    }
    room = _room(cards, deck=["pod1", "filler"])
    players = [p.model_copy(update={"hand": ["rc"]}) if p.id == "p2" else p for p in room.state.players]
    room.state = room.state.model_copy(update={"players": players})

    asyncio.run(room._start_turn("p1"))

    pending = room._pending
    assert pending is not None and pending.card_id == "pod1"
    assert pending.count_as_play is False
    assert "pod1" in room.state.get_player("p1").hand  # suspended before the zone move

    asyncio.run(room._commit_pending("resolved"))

    assert room.state.get_player("p1").score == 3
    assert "pod1" in room.state.discard
    assert room._plays_this_turn == 0
    assert room.state.active_player().id == "p1"  # an auto-play never advances the turn


def test_countered_auto_play_is_negated_without_costing_the_turn() -> None:
    cards = {
        "pod1": _pod_card("pod1", ADD3),
        "filler": _plain_card("filler", ADD3),
        "rc": {"id": "rc", "title": "Counter!", "canonical": {"trigger": "on_reaction"}},
    }
    room = _room(cards, deck=["pod1", "filler"])
    players = [p.model_copy(update={"hand": ["rc"]}) if p.id == "p2" else p for p in room.state.players]
    room.state = room.state.model_copy(update={"players": players})

    async def scenario() -> None:
        await room._start_turn("p1")
        await room._commit_pending("countered")

    asyncio.run(scenario())
    assert room.state.get_player("p1").score == 0
    assert "pod1" in room.state.discard
    assert room._plays_this_turn == 0
    assert room.state.active_player().id == "p1"


def test_brewing_path_for_free_text_play_on_draw_card() -> None:
    free_pod = {
        "id": "pf",
        "title": "Surprise",
        "description": "gain 2 points",
        "creator_id": "p1",
        "attributes": {"play_on_draw": True},
    }
    room = _room({"pf": free_pod, "filler": _plain_card("filler", ADD3)}, deck=["pf", "filler"])
    result = InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=2)]),
        verdict="ok",
        comment="zap",
    )

    with patch("agent.runtime.run_agent", return_value=result) as spy:
        asyncio.run(room._start_turn("p1"))

    assert spy.call_count == 1  # authored free text still brews
    assert room.state.get_player("p1").score == 2
    assert "pf" in room.state.discard
    assert room._plays_this_turn == 0
    attrs = room.state.cards["pf"].get("attributes") or {}
    assert attrs.get("play_on_draw") is True  # attribute survives canonical merge


def test_minted_cards_auto_play_deterministically_without_llm() -> None:
    mint_ops = [
        {
            "op": "create_card",
            "args": {
                "title": "Sprout",
                "ops": [{"op": "add_points", "args": {"target": "self", "amount": 2}}],
                "attributes": {"play_on_draw": True},
                "destination": "hand",
            },
        }
    ]
    room = _room(
        {"minter": _plain_card("minter", mint_ops), "filler": _plain_card("filler", ADD3)},
        deck=["filler", "filler2"],
        p1_hand=["minter"],
    )
    room._has_drawn = True

    with patch("agent.runtime.run_agent", side_effect=AssertionError("minted cards must not brew")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="minter")))

    assert room.state.get_player("p1").score == 2  # minted card auto-played its compiled ops
    minted = [cid for cid in room.state.discard if cid.startswith("created-")]
    assert len(minted) == 1
    assert room.state.active_player().id == "p2"  # only the minting play consumed the turn


def test_seed_landmine_auto_plays_on_draw_via_deck_normalisation() -> None:
    # Regression for bead 100.3: a fresh-deck seed card (built via
    # board.rooms.deck, never played before) must still auto-play on draw.
    # Its play_on_draw attribute only exists as a set_card_attribute op inside
    # canonical["ops"] — deck._normalise_card must hoist it the same way
    # Room._canonical_payload does for LLM-interpreted cards.
    raw_landmine = {
        "id": "seed-gold-072",
        "title": "Landmine",
        "description": "This card plays itself the moment it is drawn.",
        "canonical": {
            "target": "self",
            "placement": "discard",
            "venue": "all",
            "ops": [
                {"op": "set_card_attribute", "args": {"card_target": "this", "key": "play_on_draw", "value": True}},
                {"op": "add_points", "args": {"target": "self", "amount": -3}},
            ],
        },
    }
    landmine = _normalise_card(raw_landmine, 0)
    assert landmine["attributes"] == {"play_on_draw": True}

    room = _room({landmine["id"]: landmine, "filler": _plain_card("filler", ADD3)}, deck=[landmine["id"], "filler"])

    asyncio.run(room._start_turn("p1"))

    assert room.state.get_player("p1").score == -3
    assert landmine["id"] in room.state.discard
    assert room._plays_this_turn == 0  # the auto-play took no action


def test_interpretation_canonicalizes_play_on_draw_attribute() -> None:
    room = Room("PODCAN")
    result = InterpretResult(
        program=EffectProgram(
            ops=[
                SetCardAttributeOp(card_target="this", key="play_on_draw", value=True),
                AddPointsOp(target="self", amount=1),
            ]
        ),
        verdict="ok",
    )
    merged = room._canonicalize_interpretation(result)
    assert merged["attributes"] == {"play_on_draw": True}
    assert merged["canonical"]["steps"]

    plain = InterpretResult(program=EffectProgram(ops=[AddPointsOp(target="self", amount=1)]), verdict="ok")
    assert "attributes" not in room._canonicalize_interpretation(plain)


def test_eliminated_players_hands_are_not_scanned() -> None:
    room = _room({"pod1": _pod_card("pod1", ADD3), "filler": _plain_card("filler", ADD3)}, deck=["filler"])
    players = [
        p.model_copy(update={"hand": ["pod1"], "eliminated": True}) if p.id == "p2" else p for p in room.state.players
    ]
    room.state = room.state.model_copy(update={"players": players})

    asyncio.run(room._process_play_on_draw())

    assert room.state.get_player("p2").hand == ["pod1"]
    assert room._auto_plays_this_turn == 0


CHOOSER5 = [{"op": "add_points", "args": {"target": "chooser", "amount": 5}}]


def test_outstanding_choice_prompt_suspends_the_counted_plays_turn() -> None:
    cards = {
        "starter": _plain_card("starter", DRAW1),
        "podc": _pod_card("podc", CHOOSER5),
        "filler": _plain_card("filler", ADD3),
    }
    room = _room(cards, deck=["podc", "filler"], p1_hand=["starter"])
    room._has_drawn = True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="starter")))

    # The manual play's turn decision waits for the auto-play's answer.
    assert room._pending_auto_play is not None and room._pending_auto_play.card_id == "podc"
    assert room.state.active_player().id == "p1"
    assert room._advance_after_auto_play is True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="podc", chosen_player_id="p2")))

    assert room._pending_auto_play is None
    assert room.state.get_player("p2").score == 5
    assert "podc" in room.state.discard
    assert room.state.active_player().id == "p2"  # deferred decision resumed


def test_two_choice_needing_cards_prompt_one_at_a_time() -> None:
    cards = {
        "poda": _pod_card("poda", CHOOSER5),
        "podb": _pod_card("podb", CHOOSER5),
        "filler": _plain_card("filler", ADD3),
    }
    room = _room(cards, deck=["poda", "podb", "filler"])
    room.state = room.state.model_copy(update={"rules": room.state.rules.model_copy(update={"draw": 2})})

    asyncio.run(room._start_turn("p1"))

    # The scan stops at the first prompt — podb neither clobbers the pending
    # slot nor burns the auto-play budget on a duplicate resolution.
    assert room._pending_auto_play is not None and room._pending_auto_play.card_id == "poda"
    assert room._auto_plays_this_turn == 1
    assert sum("plays itself" in line for line in room.state.log) == 1

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="poda", chosen_player_id="p2")))

    assert room._pending_auto_play is not None and room._pending_auto_play.card_id == "podb"
    assert room._auto_plays_this_turn == 2

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="podb", chosen_player_id="p1")))

    assert room._pending_auto_play is None
    assert {"poda", "podb"} <= set(room.state.discard)
    assert room.state.get_player("p1").score == 5
    assert room.state.get_player("p2").score == 5
    assert room.state.active_player().id == "p1"  # auto-plays cost no action


VOID_OPS = [{"op": "move_cards", "args": {"card_target": "chosen_card", "from_zone": "center", "to_zone": "exile"}}]


def _center_room(center: list[str]) -> Room:
    room = _room(
        {"podv": _pod_card("podv", VOID_OPS), "filler": _plain_card("filler", ADD3)},
        deck=["podv", "filler"],
    )
    cards = {**room.state.cards, **{cid: {"id": cid, "title": cid.upper()} for cid in center}}
    room.state = room.state.model_copy(update={"house_rules": list(center), "cards": cards})
    return room


def test_auto_play_card_choice_is_scoped_to_the_center() -> None:
    room = _center_room(["hr1", "hr2"])

    asyncio.run(room._start_turn("p1"))

    prompts = [m for m in _sent(room, "p1") if m.get("type") == "prompt_choice"]
    assert prompts and [c["card_id"] for c in prompts[0]["choices"]] == ["hr1", "hr2"]
    assert room._pending_auto_play is not None

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="podv", chosen_card_id="hr1")))

    assert room._pending_auto_play is None
    assert room.state.exiled == ["hr1"]
    assert room.state.house_rules == ["hr2"]
    assert "podv" in room.state.discard
    assert room._plays_this_turn == 0


def test_auto_play_rejects_a_forged_hand_card_and_keeps_waiting() -> None:
    room = _center_room(["hr1"])

    async def scenario() -> None:
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="podv", chosen_card_id="filler"))

    asyncio.run(scenario())
    assert room._pending_auto_play is not None
    assert any("Invalid target card" in m.get("message", "") for m in _sent(room, "p1"))
    assert room.state.exiled == []


def test_auto_play_with_empty_center_defers_without_falling_back_to_a_hand() -> None:
    room = _center_room([])

    asyncio.run(room._start_turn("p1"))

    assert room._pending_auto_play is None
    assert "podv" in room._auto_play_deferred
    assert "podv" in room.state.get_player("p1").hand  # safe no-op, card stays
    assert room.state.exiled == []
    assert not [m for m in _sent(room, "p1") if m.get("type") == "prompt_choice"]
    assert any("no eligible target card" in line for line in room.state.log)


THEFT_OPS = [
    {
        "op": "move_cards",
        "args": {
            "card_target": "chosen_card",
            "from_zone": "hand",
            "from_player": "chooser",
            "to_zone": "hand",
            "to_player": "self",
        },
    },
    {"op": "subtract_points", "args": {"target": "chooser", "amount": 3}},
]


def _theft_pod_room() -> Room:
    room = _room(
        {"podt": _pod_card("podt", THEFT_OPS), "filler": _plain_card("filler", ADD3)},
        deck=["podt", "filler"],
    )
    cards = {**room.state.cards, **{cid: {"id": cid, "title": cid.upper()} for cid in ("bs1", "bs2")}}
    players = [
        p.model_copy(update={"hand": ["bs1", "bs2"], "score": 5}) if p.id == "p2" else p for p in room.state.players
    ]
    room.state = room.state.model_copy(update={"cards": cards, "players": players})
    return room


def test_auto_play_two_axis_prompts_player_then_the_chosen_hand_with_context() -> None:
    room = _theft_pod_room()

    async def scenario() -> None:
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="podt", chosen_player_id="p2"))

    asyncio.run(scenario())

    prompts = [m for m in _sent(room, "p1") if m.get("type") == "prompt_choice"]
    assert len(prompts) == 2
    assert {c["player_id"] for c in prompts[0]["choices"]} == {"p1", "p2"}
    card_prompt = prompts[1]
    assert [c["card_id"] for c in card_prompt["choices"]] == ["bs1", "bs2"]
    assert card_prompt["chosen_player_id"] == "p2"
    assert set(card_prompt["cards"]) == {"bs1", "bs2"}
    assert room._pending_auto_play is not None

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="podt", chosen_player_id="p2", chosen_card_id="bs2")))

    assert room._pending_auto_play is None
    assert "bs2" in room.state.get_player("p1").hand
    assert room.state.get_player("p2").hand == ["bs1"]
    assert room.state.get_player("p2").score == 2
    assert "podt" in room.state.discard
    assert room._plays_this_turn == 0  # no action cost
    assert room.state.active_player().id == "p1"


def test_auto_play_two_axis_rejects_a_forged_card_and_keeps_waiting() -> None:
    room = _theft_pod_room()

    async def scenario() -> None:
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="podt", chosen_player_id="p2"))
        await room.handle_action("p1", PlayMsg(card_id="podt", chosen_player_id="p2", chosen_card_id="filler"))

    asyncio.run(scenario())

    assert room._pending_auto_play is not None
    assert any("Invalid target card" in m.get("message", "") for m in _sent(room, "p1"))
    assert room.state.get_player("p2").hand == ["bs1", "bs2"]
    assert room.state.get_player("p2").score == 5
    assert "podt" in room.state.get_player("p1").hand


def test_dead_prompt_still_runs_the_deferred_turn_decision() -> None:
    cards = {
        "starter": _plain_card("starter", DRAW1),
        "podc": _pod_card("podc", CHOOSER5),
        "filler": _plain_card("filler", ADD3),
    }
    room = _room(cards, deck=["podc", "filler"], p1_hand=["starter"])
    room._has_drawn = True

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="starter"))
        players = [p.model_copy(update={"hand": [c for c in p.hand if c != "podc"]}) for p in room.state.players]
        room.state = room.state.model_copy(update={"players": players})
        await room.handle_action("p1", PlayMsg(card_id="podc", chosen_player_id="p2"))

    asyncio.run(scenario())

    assert room._pending_auto_play is None
    assert room._advance_after_auto_play is False
    assert any("no longer in your hand" in m.get("message", "") for m in _sent(room, "p1"))
    assert room.state.active_player().id == "p2"  # the spent turn did not strand
