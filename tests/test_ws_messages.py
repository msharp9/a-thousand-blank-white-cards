"""Tests for tbwc.models.ws_messages."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from tbwc.models.ws_messages import ClientMsg, DrawMsg, JoinMsg, PlayMsg, StateMsg


def test_join_msg_json() -> None:
    assert json.loads(JoinMsg(name="Alice").model_dump_json()) == {
        "type": "join",
        "player_id": None,
        "name": "Alice",
    }


def test_client_msg_discriminates_draw() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "draw"})
    assert isinstance(msg, DrawMsg)
    assert msg.type == "draw"


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


def test_state_msg_envelope() -> None:
    m = StateMsg(state={"players": []})
    assert m.type == "state"
    assert m.state == {"players": []}
