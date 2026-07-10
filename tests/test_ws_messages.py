"""Tests for tbwc.models.ws_messages."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from tbwc.models.ws_messages import ClientMsg, JoinMsg, PassMsg, PlayMsg, StateMsg


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


def test_client_msg_rejects_removed_draw() -> None:
    # The manual `draw` action was removed with the draw→play→pass turn model.
    ta = TypeAdapter(ClientMsg)
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "draw"})


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
