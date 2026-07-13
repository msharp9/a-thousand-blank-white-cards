"""Bead npb — vision input: pass card art to a multimodal arbiter.

Hermetic (no network, scripted fakes — see test_agent_skeleton.py for the
pattern). The invariants under test:

- ``Settings.vision_enabled`` defaults OFF, and while off (or with no art) the
  opening human message is the exact text-only string used today.
- On + art: the human message content becomes ``[text block, image_url block]``
  carrying the PNG data-URL, and the system prompt gains the CARD_ART_NOTE.
- A model that rejects image input degrades to a text-only retry (with a
  warning) instead of failing the play.
- The board passes ``Room.card_art`` into ``run_agent`` as the ``card_art``
  side-channel kwarg — art still never rides GameState or snapshots
  (test_card_art.py owns those invariants).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.contract import InterpretResult
from agent.persona import CARD_ART_NOTE, build_system_prompt
from agent.runtime import run_agent
from config import Settings, get_settings
from models.card import CARD_ART_PREFIX
from models.ws_messages import CreateCardMsg, PlayMsg
from board.rooms.deck import _make_blank_card
from board.rooms.room import Room

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-art-bytes"
ART = CARD_ART_PREFIX + base64.b64encode(PNG_BYTES).decode()

_LANGSMITH_ENV_KEYS = (
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _isolate_langsmith_env():
    """run_agent writes LANGSMITH_* env vars directly (by design). Snapshot and
    restore them around every test so nothing leaks into other test modules."""
    import os

    saved = {k: os.environ.get(k) for k in _LANGSMITH_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


FINAL_PAYLOAD = '{"verdict": "ok", "comment": "Nice doodle.", "persona_action": "none"}'

TEXT_ONLY_CONTENT = "Interpret the card titled 'Doodle' and produce the JSON result."


class RecordingFake(GenericFakeChatModel):
    """Scripted fake that records every message list it is invoked with."""

    recorded: list[list] = []

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003 — mirror base signature
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        self.recorded.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=FINAL_PAYLOAD))])


class ImageRejectingFake(RecordingFake):
    """Raises (like a text-only provider) whenever any message carries image blocks."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        self.recorded.append(list(messages))
        for msg in messages:
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                raise ValueError("this model does not support image input")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=FINAL_PAYLOAD))])


def _fresh_fake(cls: type[RecordingFake]) -> RecordingFake:
    fake = cls(messages=iter([]))
    fake.recorded = []
    return fake


def _enable_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_ENABLED", "true")
    get_settings.cache_clear()


def _human_contents(fake: RecordingFake) -> list:
    assert fake.recorded, "the fake model was never invoked"
    return [m.content for m in fake.recorded[0] if getattr(m, "type", None) == "human"]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_vision_enabled_defaults_off() -> None:
    assert Settings(_env_file=None).vision_enabled is False  # type: ignore[call-arg]


def test_vision_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_ENABLED", "true")
    assert Settings(_env_file=None).vision_enabled is True  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# persona
# ---------------------------------------------------------------------------


def test_system_prompt_art_note_only_when_has_art() -> None:
    base = build_system_prompt("T", "D")
    with_art = build_system_prompt("T", "D", has_art=True)
    assert CARD_ART_NOTE not in base
    assert CARD_ART_NOTE in with_art
    assert with_art.replace(CARD_ART_NOTE + "\n", "") == base


# ---------------------------------------------------------------------------
# run_agent message construction
# ---------------------------------------------------------------------------


def test_flag_off_art_is_ignored_message_identical_to_today() -> None:
    fake = _fresh_fake(RecordingFake)

    result = run_agent("Doodle", "A drawing.", model=fake, card_art=ART)

    assert result.verdict == "ok"
    assert _human_contents(fake) == [TEXT_ONLY_CONTENT]
    system = [m for m in fake.recorded[0] if getattr(m, "type", None) == "system"]
    assert CARD_ART_NOTE not in system[0].content


def test_flag_on_without_art_message_identical_to_today(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_vision(monkeypatch)
    fake = _fresh_fake(RecordingFake)

    result = run_agent("Doodle", "A drawing.", model=fake)

    assert result.verdict == "ok"
    assert _human_contents(fake) == [TEXT_ONLY_CONTENT]


def test_flag_on_with_art_sends_multimodal_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_vision(monkeypatch)
    fake = _fresh_fake(RecordingFake)

    result = run_agent("Doodle", "A drawing.", model=fake, card_art=ART)

    assert result.verdict == "ok"
    (content,) = _human_contents(fake)
    assert content == [
        {"type": "text", "text": TEXT_ONLY_CONTENT},
        {"type": "image_url", "image_url": {"url": ART}},
    ]
    system = [m for m in fake.recorded[0] if getattr(m, "type", None) == "system"]
    assert CARD_ART_NOTE in system[0].content


def test_model_rejecting_images_retries_text_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _enable_vision(monkeypatch)
    fake = _fresh_fake(ImageRejectingFake)

    with caplog.at_level(logging.WARNING, logger="agent.runtime"):
        result = run_agent("Doodle", "A drawing.", model=fake, card_art=ART)

    assert result.verdict == "ok"
    assert result.comment == "Nice doodle."
    assert any("retrying text-only" in r.message for r in caplog.records)
    # First attempt carried the image blocks; the retry was plain text.
    first = [m.content for m in fake.recorded[0] if getattr(m, "type", None) == "human"]
    last = [m.content for m in fake.recorded[-1] if getattr(m, "type", None) == "human"]
    assert isinstance(first[0], list)
    assert last == [TEXT_ONLY_CONTENT]


# ---------------------------------------------------------------------------
# board plumbing: Room.card_art -> run_agent(card_art=...)
# ---------------------------------------------------------------------------


def _no_agent_result() -> InterpretResult:
    return InterpretResult(verdict="invalid", comment="", persona_action="none")


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


def test_play_passes_room_art_as_side_channel() -> None:
    room = _playing_room_with_blank()
    with patch("agent.runtime.run_agent", return_value=_no_agent_result()) as spy:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", title="Doodle", description="Art!", art=ART)))
    spy.assert_called_once()
    assert spy.call_args.kwargs["card_art"] == ART


def test_play_without_art_passes_none() -> None:
    room = _playing_room_with_blank()
    with patch("agent.runtime.run_agent", return_value=_no_agent_result()) as spy:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", title="Plain", description="No art.")))
    spy.assert_called_once()
    assert spy.call_args.kwargs["card_art"] is None


def test_midgame_create_card_passes_art_as_side_channel() -> None:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.state = room.state.model_copy(update={"phase": "playing"})
    room.connections.connect("p1", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_no_agent_result()) as spy:
        asyncio.run(room.handle_action("p1", CreateCardMsg(title="Doodle", description="gain 1 point", art=ART)))
    spy.assert_called_once()
    assert spy.call_args.kwargs["card_art"] == ART
