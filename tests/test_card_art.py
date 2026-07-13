"""Bead 0yp.4 — out-of-band card art: validation, registry, REST serving, RAG carry.

Art is a PNG data-URL that lives in ``Room.card_art`` (card_id -> data-URL),
never in GameState — snapshots broadcast to every client carry only a
``has_art`` flag and the bytes are served from
``GET /rooms/{code}/cards/{card_id}/art``. Kept cards carry their art through
the Qdrant payload and back into future decks via ``deck._normalise_card`` +
``Room._absorb_card_art``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from agent.contract import InterpretResult
from models.card import CARD_ART_PREFIX, MAX_CARD_ART_BYTES
from models.ws_messages import ClientMsg, CreateCardMsg, PlayMsg
from board.app import create_app
from board.rooms.deck import _make_blank_card, _normalise_card
from board.rooms.room import Room

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-art-bytes"
ART = CARD_ART_PREFIX + base64.b64encode(PNG_BYTES).decode()


def _no_agent_result() -> InterpretResult:
    return InterpretResult(verdict="invalid", comment="", persona_action="none")


# ─── inbound validation (ws message boundary) ────────────────────────────────


def test_create_card_with_valid_art_accepted() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "create_card", "title": "T", "description": "D", "art": ART})
    assert isinstance(msg, CreateCardMsg)
    assert msg.art == ART


def test_play_with_valid_art_accepted() -> None:
    ta = TypeAdapter(ClientMsg)
    msg = ta.validate_python({"type": "play", "card_id": "blank-0", "title": "T", "description": "D", "art": ART})
    assert isinstance(msg, PlayMsg)
    assert msg.art == ART


def test_art_defaults_none() -> None:
    assert CreateCardMsg(title="T", description="D").art is None
    assert PlayMsg(card_id="c1").art is None


def test_art_wrong_prefix_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    jpeg = "data:image/jpeg;base64," + base64.b64encode(PNG_BYTES).decode()
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "create_card", "title": "T", "description": "D", "art": jpeg})


def test_art_oversized_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    oversized = CARD_ART_PREFIX + "A" * MAX_CARD_ART_BYTES
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "play", "card_id": "blank-0", "art": oversized})


def test_art_bad_base64_rejected() -> None:
    ta = TypeAdapter(ClientMsg)
    with pytest.raises(ValidationError):
        ta.validate_python({"type": "create_card", "title": "T", "description": "D", "art": ART + "!!!"})


# ─── room registry: create_card and author-on-play ──────────────────────────


def _setup_room() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.state = room.state.model_copy(update={"phase": "setup"})
    room.connections.connect("p1", AsyncMock())
    return room


def test_create_card_with_art_stores_out_of_band() -> None:
    room = _setup_room()
    asyncio.run(room.handle_action("p1", CreateCardMsg(title="Doodle", description="gain 1 point", art=ART)))
    (card_id,) = [cid for cid, c in room.state.cards.items() if c.get("creator_id") == "p1"]
    assert room.card_art[card_id] == ART
    card = room.state.cards[card_id]
    assert card["has_art"] is True
    assert "art" not in card


def test_create_card_without_art_flags_false() -> None:
    room = _setup_room()
    asyncio.run(room.handle_action("p1", CreateCardMsg(title="Plain", description="gain 1 point")))
    (card,) = [c for c in room.state.cards.values() if c.get("creator_id") == "p1"]
    assert card["has_art"] is False
    assert room.card_art == {}


def _playing_room_with_blank() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    blank = _make_blank_card(0)
    room.state = room.state.model_copy(update={"phase": "playing", "cards": {blank["id"]: blank}})
    room.state = room.state.model_copy(
        update={"players": [p.model_copy(update={"hand": [blank["id"]]}) for p in room.state.players]}
    )
    room.connections.connect("p1", AsyncMock())
    return room


def test_author_on_play_with_art_stores_out_of_band() -> None:
    room = _playing_room_with_blank()
    with patch("agent.runtime.run_agent", return_value=_no_agent_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", title="Doodle", description="Art!", art=ART)))
    assert room.card_art["blank-0"] == ART
    card = room.state.cards["blank-0"]
    assert card["has_art"] is True
    assert "art" not in card
    assert "blank" not in card


def test_author_on_play_without_art_flags_false() -> None:
    room = _playing_room_with_blank()
    with patch("agent.runtime.run_agent", return_value=_no_agent_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", title="Plain", description="No art.")))
    assert room.state.cards["blank-0"]["has_art"] is False
    assert room.card_art == {}


def test_snapshot_carries_has_art_but_never_the_data_url() -> None:
    room = _setup_room()
    asyncio.run(room.handle_action("p1", CreateCardMsg(title="Doodle", description="gain 1 point", art=ART)))
    snap = json.dumps(room.snapshot())
    assert '"has_art": true' in snap
    assert ART not in snap
    assert CARD_ART_PREFIX not in snap


# ─── REST art endpoint ───────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_get_card_art_returns_png_bytes_with_immutable_cache(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    from board.rooms.manager import room_manager

    room_manager.get(code).card_art["c1"] = ART
    resp = client.get(f"/rooms/{code}/cards/c1/art")
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_get_card_art_404_when_absent(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    resp = client.get(f"/rooms/{code}/cards/nope/art")
    assert resp.status_code == 404


def test_get_card_art_404_when_room_missing(client: TestClient) -> None:
    resp = client.get("/rooms/ZZZZZZ/cards/c1/art")
    assert resp.status_code == 404


# ─── epilogue keep → RAG payload → next deck ─────────────────────────────────


def test_epilogue_keep_persists_art_to_rag_payload() -> None:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored", "has_art": True}}}
    )
    room.card_art["c1"] = ART
    room.connections.connect("p1", AsyncMock())
    with patch("agent.rag.store.upsert_card") as mock_upsert:
        asyncio.run(room.start_epilogue())
        from models.ws_messages import EpilogueDoneMsg, EpilogueVoteMsg

        asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="c1", keep=True)))
        asyncio.run(room.handle_action("p1", EpilogueDoneMsg()))
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["card_id"] == "c1"
    assert kwargs["art"] == ART


def test_epilogue_broadcast_never_carries_art_inline() -> None:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored", "has_art": True}}}
    )
    room.card_art["c1"] = ART
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.start_epilogue())
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list if c.args]
    epilogue_msg = next(m for m in sent if m["type"] == "epilogue")
    assert epilogue_msg["cards"][0]["has_art"] is True
    assert ART not in json.dumps(sent)


def test_upsert_card_stores_art_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import init_store, list_all_cards, upsert_card

        init_store()
        upsert_card("c1", "Drawn", "with art", "{}", "player", art=ART)
        upsert_card("c2", "Plain", "no art", "{}", "player")
        cards = {c["card_id"]: c for c in list_all_cards()}
        assert cards["c1"]["art"] == ART
        assert "art" not in cards["c2"]


def test_normalise_card_surfaces_rag_art() -> None:
    card = _normalise_card({"card_id": "c1", "title": "T", "description": "D", "source": "player", "art": ART}, 0)
    assert card["has_art"] is True
    assert card["art"] == ART


def test_normalise_card_without_art_flags_false() -> None:
    card = _normalise_card({"card_id": "c1", "title": "T", "description": "D", "source": "player"}, 0)
    assert card["has_art"] is False
    assert "art" not in card


def test_enter_setup_repopulates_card_art_and_strips_state() -> None:
    # A card kept in a prior game re-enters the pre-made pool carrying a
    # transient "art" key; the room absorbs it into card_art before the dict
    # lands in GameState.cards — art must never ride the snapshot.
    kept = _normalise_card({"card_id": "c1", "title": "Legacy", "description": "D", "source": "player", "art": ART}, 0)
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.connections.connect("p1", AsyncMock())
    with patch("board.rooms.room.build_premade_pool", return_value=({"c1": kept}, ["c1"])):
        asyncio.run(room._enter_setup())
    assert room.card_art["c1"] == ART
    card = room.state.cards["c1"]
    assert card["has_art"] is True
    assert "art" not in card
    assert ART not in json.dumps(room.snapshot())


def test_blank_cards_default_has_art_false() -> None:
    assert _make_blank_card(0)["has_art"] is False
