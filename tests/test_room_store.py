"""Tests for the RoomStore seam and the single-worker safety guard."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock


from conftest import drive_to_playing

from models.ws_messages import StartMsg
from board.rooms.manager import (
    RoomManager,
    _detect_worker_count,
    check_single_worker,
)
from board.rooms.room import Room
from board.rooms.store import FileRoomStore, InMemoryRoomStore, RoomStore


class TestInMemoryRoomStore:
    def test_put_and_get_roundtrip(self) -> None:
        store = InMemoryRoomStore()
        room = Room("ABCDEF")
        store.put("ABCDEF", room)
        assert store.get("ABCDEF") is room

    def test_get_missing_returns_none(self) -> None:
        assert InMemoryRoomStore().get("NOPE00") is None

    def test_exists_reflects_puts(self) -> None:
        store = InMemoryRoomStore()
        assert not store.exists("ABCDEF")
        store.put("ABCDEF", Room("ABCDEF"))
        assert store.exists("ABCDEF")

    def test_count_tracks_number_of_rooms(self) -> None:
        store = InMemoryRoomStore()
        assert store.count() == 0
        store.put("AAAAAA", Room("AAAAAA"))
        store.put("BBBBBB", Room("BBBBBB"))
        assert store.count() == 2

    def test_put_overwrites_existing(self) -> None:
        store = InMemoryRoomStore()
        first, second = Room("ABCDEF"), Room("ABCDEF")
        store.put("ABCDEF", first)
        store.put("ABCDEF", second)
        assert store.get("ABCDEF") is second
        assert store.count() == 1

    def test_satisfies_roomstore_protocol(self) -> None:
        assert isinstance(InMemoryRoomStore(), RoomStore)


class TestFileRoomStore:
    def test_satisfies_roomstore_protocol(self, tmp_path) -> None:
        assert isinstance(FileRoomStore(tmp_path), RoomStore)

    def test_roundtrip_persists_and_rehydrates(self, tmp_path) -> None:
        store = FileRoomStore(tmp_path)
        code = "ABCDEF"
        room = Room(code)
        store.put(code, room)
        p1, p2 = "p1", "p2"
        room.add_player(player_id=p1, name="Alice")
        room.add_player(player_id=p2, name="Bob")
        drive_to_playing(room, [p1, p2])
        store.put(code, room)

        # A fresh store on the same dir rehydrates from disk (cold start).
        reloaded = FileRoomStore(tmp_path)
        got = reloaded.get(code)
        assert got is not None
        assert got.state.phase == "playing"
        assert got.state.turn_index == room.state.turn_index
        assert got.get_player_ids() == room.get_player_ids()
        assert len(got.state.cards) == len(room.state.cards)

    def test_live_object_is_reused_within_process(self, tmp_path) -> None:
        store = FileRoomStore(tmp_path)
        room = Room("ABCDEF")
        store.put("ABCDEF", room)
        assert store.get("ABCDEF") is room

    def test_malformed_file_is_skipped(self, tmp_path) -> None:
        good = Room("GOODAA")
        store = FileRoomStore(tmp_path)
        store.put("GOODAA", good)
        (tmp_path / "BADCODE.json").write_text("{ not valid json ][")

        reloaded = FileRoomStore(tmp_path)
        assert reloaded.count() == 1
        assert reloaded.exists("GOODAA")
        assert not reloaded.exists("BADCODE")

    def test_rewire_on_change_attaches_hook(self, tmp_path) -> None:
        FileRoomStore(tmp_path).put("ABCDEF", Room("ABCDEF"))
        reloaded = FileRoomStore(tmp_path)
        cb = Mock()
        reloaded.rewire_on_change(cb)
        assert reloaded.get("ABCDEF").on_change is cb


class TestRoomOnChangeHook:
    def test_on_change_fires_after_handle_action(self) -> None:
        cb = Mock()
        room = Room("ABCDEF", on_change=cb)
        room.add_player(player_id="p1", name="Alice")
        asyncio.run(room.handle_action("p1", StartMsg()))
        cb.assert_called_with(room)

    def test_no_on_change_is_harmless(self) -> None:
        room = Room("ABCDEF")
        room.add_player(player_id="p1", name="Alice")
        asyncio.run(room.handle_action("p1", StartMsg()))
        assert room.state.phase == "setup"


class TestRoomManagerUsesStore:
    def test_default_store_is_in_memory(self) -> None:
        mgr = RoomManager()
        code = mgr.create_room()
        assert mgr.get(code) is not None

    def test_injected_store_is_used(self) -> None:
        store = InMemoryRoomStore()
        mgr = RoomManager(store=store)
        code = mgr.create_room()
        # the room is written straight through to the injected store
        assert store.get(code) is mgr.get(code)
        assert store.count() == 1

    def test_get_is_case_insensitive_through_store(self) -> None:
        mgr = RoomManager(store=InMemoryRoomStore())
        code = mgr.create_room()
        assert mgr.get(code.lower()) is mgr.get(code)


class TestSingleWorkerGuard:
    def test_no_warning_for_single_worker(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="board.rooms.manager"):
            check_single_worker(worker_count=1)
        assert caplog.records == []

    def test_no_warning_when_unknown(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="board.rooms.manager"):
            check_single_worker(worker_count=None)
        assert caplog.records == []

    def test_warns_for_multi_worker(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="board.rooms.manager"):
            check_single_worker(worker_count=4)
        assert any("workers" in r.message.lower() for r in caplog.records)
        assert any("4" in r.getMessage() for r in caplog.records)

    def test_detect_worker_count_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        assert _detect_worker_count() is None

    def test_detect_worker_count_parses_env(self, monkeypatch) -> None:
        monkeypatch.setenv("WEB_CONCURRENCY", "3")
        assert _detect_worker_count() == 3

    def test_detect_worker_count_invalid_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("WEB_CONCURRENCY", "notanint")
        assert _detect_worker_count() is None

    def test_check_reads_env_when_no_arg(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("WEB_CONCURRENCY", "2")
        with caplog.at_level(logging.WARNING, logger="board.rooms.manager"):
            check_single_worker()
        assert any("workers" in r.message.lower() for r in caplog.records)
