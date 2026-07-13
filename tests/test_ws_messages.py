"""Tests for models.ws_messages."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from models.card import MAX_CARD_DESCRIPTION, MAX_CARD_TITLE
from models.ws_messages import ClientMsg, DrawMsg, EndTurnMsg, JoinMsg, PassMsg, PlayMsg, StateMsg


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


# ─── card text length limits (enforced on all authoring messages) ────────────


def test_create_card_at_limit_ok() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python(
        {"type": "create_card", "title": "x" * MAX_CARD_TITLE, "description": "y" * MAX_CARD_DESCRIPTION}
    )
    assert msg.type == "create_card"


def test_create_card_over_title_limit_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "create_card", "title": "x" * (MAX_CARD_TITLE + 1), "description": "ok"})


def test_create_card_over_description_limit_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "create_card", "title": "ok", "description": "y" * (MAX_CARD_DESCRIPTION + 1)})


def test_preview_card_over_limit_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "preview_card", "title": "x" * (MAX_CARD_TITLE + 1), "description": "ok"})


def test_play_authoring_over_limit_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "play", "card_id": "blank-0", "description": "y" * (MAX_CARD_DESCRIPTION + 1)})


def test_play_msg_as_reaction_defaults_false() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "play", "card_id": "c1"})
    assert msg.as_reaction is False


def test_play_msg_carries_as_reaction() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "play", "card_id": "c1", "as_reaction": True})
    assert isinstance(msg, PlayMsg)
    assert msg.as_reaction is True


def test_client_msg_discriminates_pass_reaction() -> None:
    from models.ws_messages import PassReactionMsg

    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "pass_reaction", "window_id": "w1"})
    assert isinstance(msg, PassReactionMsg)
    assert msg.window_id == "w1"
    # window_id is an optional stale-window guard.
    assert ta.validate_python({"type": "pass_reaction"}).window_id is None


def test_reaction_server_messages_round_trip() -> None:
    from models.ws_messages import ReactionResultMsg, ReactionWindowMsg

    window = ReactionWindowMsg(window_id="w1", card_id="c1", actor_id="p1", deadline_epoch_ms=123)
    assert json.loads(window.model_dump_json())["type"] == "reaction_window"
    result = ReactionResultMsg(window_id="w1", outcome="countered", reactor_id="p2", reaction_card_id="c9")
    assert json.loads(result.model_dump_json())["outcome"] == "countered"
