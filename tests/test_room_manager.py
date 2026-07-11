"""Tests for RoomManager."""

from __future__ import annotations

from rooms.manager import RoomManager, room_manager


def test_create_and_join() -> None:
    rm = RoomManager()
    code = rm.create_room()
    assert len(code) == 6
    result = rm.join(code, "Alice")
    assert result is not None
    room_code, pid, spectator = result
    assert room_code == code
    assert spectator is False
    assert len(pid) > 0
    room = rm.get(code)
    assert room is not None
    assert any(p.id == pid for p in room.state.players)


def test_join_missing_room() -> None:
    rm = RoomManager()
    assert rm.join("ZZZZZZ", "Bob") is None


def test_get_is_case_insensitive() -> None:
    rm = RoomManager()
    code = rm.create_room()
    assert rm.get(code.lower()) is rm.get(code)


def test_codes_are_unique() -> None:
    rm = RoomManager()
    codes = {rm.create_room() for _ in range(25)}
    assert len(codes) == 25


def test_singleton_exists() -> None:
    assert isinstance(room_manager, RoomManager)
