"""Tests for room mode (online | in_person | both) chosen at creation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import create_app
from models.game_state import GameState
from rooms.manager import RoomManager, room_manager


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_default_mode_is_both() -> None:
    assert GameState(room_code="ABCDEF").mode == "both"


def test_create_room_stores_mode() -> None:
    rm = RoomManager()
    code = rm.create_room(mode="online")
    room = rm.get(code)
    assert room is not None
    assert room.state.mode == "online"


def test_create_room_default_mode() -> None:
    rm = RoomManager()
    code = rm.create_room()
    assert rm.get(code).state.mode == "both"


def test_post_rooms_with_online_mode(client: TestClient) -> None:
    resp = client.post("/rooms", json={"mode": "online"})
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert room_manager.get(code).state.mode == "online"


def test_post_rooms_with_in_person_mode(client: TestClient) -> None:
    resp = client.post("/rooms", json={"mode": "in_person"})
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert room_manager.get(code).state.mode == "in_person"


def test_post_rooms_no_body_defaults_to_both(client: TestClient) -> None:
    resp = client.post("/rooms")
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert room_manager.get(code).state.mode == "both"


def test_mode_surfaces_in_snapshot() -> None:
    rm = RoomManager()
    code = rm.create_room(mode="in_person")
    assert rm.get(code).snapshot()["mode"] == "in_person"
