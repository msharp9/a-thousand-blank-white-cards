"""Tests for tbwc.models.ws_messages."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from tbwc.models.ws_messages import ClientMsg, DrawMsg, EndTurnMsg, JoinMsg, PassMsg, PlayMsg, StateMsg


def test_join_msg_json() -> None:
    assert json.loads(JoinMsg(name="Alice").model_dump_json()) == {
        "type": "join",
        "player_id": None,
        "name": "Alice",
    }


def test_client_msg_discriminates_pass() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "pass"})
    assert isinstance(msg, PassMsg)
    assert msg.type == "pass"


def test_client_msg_discriminates_draw() -> None:
    # The draw→play→end model has an explicit `draw` client message: the active
    # player draws before they may play or end their turn.
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "draw"})
    assert isinstance(msg, DrawMsg)
    assert msg.type == "draw"


def test_client_msg_discriminates_end_turn() -> None:
    # `end_turn` is an accepted alias for `pass` in the draw→play→end model.
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "end_turn"})
    assert isinstance(msg, EndTurnMsg)
    assert msg.type == "end_turn"


def test_client_msg_discriminates_play() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "play", "card_id": "c1", "placement": {"zone": "center"}})
    assert isinstance(msg, PlayMsg)
    assert msg.placement.zone == "center"


def test_play_msg_carries_chosen_player_id() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python(
        {
            "type": "play",
            "card_id": "c1",
            "placement": {"zone": "player", "target_player_id": "p2"},
            "chosen_player_id": "p2",
        }
    )
    assert isinstance(msg, PlayMsg)
    assert msg.chosen_player_id == "p2"


def test_play_msg_chosen_player_id_defaults_none() -> None:
    msg = PlayMsg(card_id="c1", placement={"zone": "center"})
    assert msg.chosen_player_id is None


def test_play_msg_carries_chosen_card_id() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python(
        {
            "type": "play",
            "card_id": "c1",
            "placement": {"zone": "center"},
            "chosen_card_id": "target_card",
        }
    )
    assert isinstance(msg, PlayMsg)
    assert msg.chosen_card_id == "target_card"


def test_play_msg_chosen_card_id_defaults_none() -> None:
    msg = PlayMsg(card_id="c1", placement={"zone": "center"})
    assert msg.chosen_card_id is None


def test_play_msg_carries_blank_authoring_fields() -> None:
    # A play of a blank card carries the authored title+description; both default
    # to None for a normal play.
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "play", "card_id": "blank-0", "title": "Gain 3", "description": "Gain 3 points."})
    assert isinstance(msg, PlayMsg)
    assert msg.title == "Gain 3"
    assert msg.description == "Gain 3 points."


def test_play_msg_authoring_fields_default_none() -> None:
    msg = PlayMsg(card_id="c1")
    assert msg.title is None
    assert msg.description is None


def test_play_msg_without_placement_parses() -> None:
    # The UI no longer collects a zone/target up front; a play message may omit
    # placement entirely (defaults to None).
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "play", "card_id": "c1"})
    assert isinstance(msg, PlayMsg)
    assert msg.placement is None


def test_state_msg_envelope() -> None:
    m = StateMsg(state={"players": []})
    assert m.type == "state"
    assert m.state == {"players": []}
