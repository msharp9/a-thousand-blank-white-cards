from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from board.rooms.manager import RoomManager
from board.rooms.room import Room
from models.ws_messages import LobbySetDeckMsg, LobbySetHostMsg, LobbySetRoleMsg, StartMsg


def test_first_lobby_join_becomes_explicit_host() -> None:
    manager = RoomManager()
    code = manager.create_room()
    _, player_id, spectator = manager.join(code, "Alice")

    assert spectator is False
    assert manager.get(code).state.host_id == player_id


def test_host_can_transfer_to_spectator_and_roles_are_reversible() -> None:
    room = Room("LOBBY1")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")

    asyncio.run(
        room.handle_action(
            "p1",
            LobbySetRoleMsg(participant_id="p2", role="spectator"),
        )
    )
    asyncio.run(
        room.handle_action(
            "p1",
            LobbySetHostMsg(participant_id="p2"),
        )
    )

    assert room.state.host_id == "p2"
    assert room.state.is_spectator("p2")
    assert [player.id for player in room.state.players] == ["p1"]

    asyncio.run(
        room.handle_action(
            "p2",
            LobbySetRoleMsg(participant_id="p2", role="player"),
        )
    )
    assert room.state.host_id == "p2"
    assert not room.state.is_spectator("p2")
    assert [player.id for player in room.state.players] == ["p1", "p2"]


def test_non_host_and_post_lobby_role_changes_are_rejected() -> None:
    room = Room("LOBBY2")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    p2_socket = AsyncMock()
    room.connections.connect("p2", p2_socket)

    asyncio.run(
        room.handle_action(
            "p2",
            LobbySetRoleMsg(participant_id="p1", role="spectator"),
        )
    )
    assert [player.id for player in room.state.players] == ["p1", "p2"]
    assert "Only the host" in p2_socket.send_text.call_args.args[0]

    room.state = room.state.model_copy(update={"phase": "setup"})
    p1_socket = AsyncMock()
    room.connections.connect("p1", p1_socket)
    asyncio.run(
        room.handle_action(
            "p1",
            LobbySetHostMsg(participant_id="p2"),
        )
    )
    assert room.state.host_id == "p1"
    assert "lobby-only" in p1_socket.send_text.call_args.args[0]


def test_last_player_cannot_become_spectator() -> None:
    room = Room("LOBBY3")
    room.add_player("p1", "Alice")
    socket = AsyncMock()
    room.connections.connect("p1", socket)

    asyncio.run(
        room.handle_action(
            "p1",
            LobbySetRoleMsg(participant_id="p1", role="spectator"),
        )
    )

    assert [player.id for player in room.state.players] == ["p1"]
    assert room.state.spectators == []
    assert "At least one player" in socket.send_text.call_args.args[0]


def test_spectator_host_can_start_but_other_spectator_cannot() -> None:
    room = Room("LOBBY4")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    asyncio.run(
        room.handle_action(
            "p1",
            LobbySetRoleMsg(participant_id="p1", role="spectator"),
        )
    )
    room._enter_setup = AsyncMock()

    asyncio.run(room.handle_action("p1", StartMsg()))
    room._enter_setup.assert_awaited_once()

    room.add_spectator("s2", "Watcher")
    spectator_socket = AsyncMock()
    room.connections.connect("s2", spectator_socket)
    asyncio.run(room.handle_action("s2", StartMsg()))
    assert "Spectators cannot" in spectator_socket.send_text.call_args.args[0]


def test_player_start_is_server_host_only() -> None:
    room = Room("LOBBY5")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    socket = AsyncMock()
    room.connections.connect("p2", socket)

    asyncio.run(room.handle_action("p2", StartMsg()))

    assert room.state.phase == "lobby"
    assert "Only the host" in socket.send_text.call_args.args[0]


def test_host_can_choose_pet_deck_in_lobby() -> None:
    room = Room("LOBBY6")
    room.add_player("p1", "Alice")

    asyncio.run(room.handle_action("p1", LobbySetDeckMsg(deck="pets")))

    assert room.state.starter_deck == "pets"


def test_non_host_and_post_lobby_deck_changes_are_rejected() -> None:
    room = Room("LOBBY7")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    p2_socket = AsyncMock()
    room.connections.connect("p2", p2_socket)

    asyncio.run(room.handle_action("p2", LobbySetDeckMsg(deck="simple")))
    assert room.state.starter_deck == "random"
    assert "Only the host" in p2_socket.send_text.call_args.args[0]

    room.state = room.state.model_copy(update={"phase": "setup"})
    p1_socket = AsyncMock()
    room.connections.connect("p1", p1_socket)
    asyncio.run(room.handle_action("p1", LobbySetDeckMsg(deck="pets")))
    assert room.state.starter_deck == "random"
    assert "lobby-only" in p1_socket.send_text.call_args.args[0]
