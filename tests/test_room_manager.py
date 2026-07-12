"""Tests for RoomManager."""

from __future__ import annotations

from board.rooms.manager import RoomManager, _build_room_manager, room_manager
from board.rooms.store import FileRoomStore, InMemoryRoomStore


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


def test_dev_mode_uses_file_store_and_writes(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEV_MODE", "true")
    mgr = _build_room_manager()
    assert isinstance(mgr._store, FileRoomStore)
    code = mgr.create_room()
    assert (tmp_path / ".devstate" / "rooms" / f"{code}.json").exists()


def test_non_dev_mode_uses_memory_store_and_writes_nothing(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEV_MODE", "false")
    mgr = _build_room_manager()
    assert isinstance(mgr._store, InMemoryRoomStore)
    mgr.create_room()
    assert not (tmp_path / ".devstate").exists()
