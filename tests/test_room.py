"""Tests for the Room class (turn enforcement + state mutation)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from conftest import drive_to_playing

from agent.contract import InterpretResult
from models.effects import AddPointsOp, DestroyCardOp, EffectProgram
from models.ws_messages import CreateCardMsg, DrawMsg, PassMsg, PlayMsg, Placement, StartMsg
from board.rooms.room import Room


def _room_with_two_players() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    return room


def test_room_constructs() -> None:
    room = Room("ABCDEF")
    assert room.code == "ABCDEF"
    assert room.state.room_code == "ABCDEF"
    assert room.get_player_ids() == []


def test_add_player_is_immutable_reassign() -> None:
    room = _room_with_two_players()
    assert room.get_player_ids() == ["p1", "p2"]


def test_pass_off_turn_sends_error() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)  # p2 is NOT active (turn_index 0 -> p1)
    asyncio.run(room.handle_action("p2", PassMsg()))
    # p2 got an error, turn/deck unchanged
    sent_types = [json.loads(c.args[0])["type"] for c in ws2.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.deck == ["c1", "c2"]
    assert room.state.turn_index == 0


def test_draw_action_draws_for_active_player() -> None:
    # The draw→play→end model has an explicit draw: the active player draws
    # draw_count card(s) off the top of the deck and has_drawn is set.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", DrawMsg()))
    assert room.state.get_player("p1").hand == ["c1"]  # draw_count == 1
    assert room.state.deck == ["c2"]
    assert room._has_drawn is True


def test_second_draw_in_same_turn_is_rejected() -> None:
    # Drawing is once per turn; a second draw errors and draws nothing more.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", DrawMsg()))
    asyncio.run(room.handle_action("p1", DrawMsg()))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    # Only the first draw took a card.
    assert room.state.get_player("p1").hand == ["c1"]
    assert room.state.deck == ["c2"]


def test_draw_off_turn_is_rejected() -> None:
    # Only the active player may draw; an off-turn draw errors and draws nothing.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)  # p2 is NOT active (turn_index 0 -> p1)
    asyncio.run(room.handle_action("p2", DrawMsg()))
    sent_types = [json.loads(c.args[0])["type"] for c in ws2.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.deck == ["c1", "c2"]
    assert room.state.get_player("p2").hand == []
    assert room._has_drawn is False


def test_effect_log_persists_in_state_for_refresh() -> None:
    # Every effect_applied broadcast must ALSO be appended to state.log so a
    # client that refreshes/reconnects can rehydrate its log from the snapshot
    # (the frontend seeds its log from msg.state.log). A pass is the simplest
    # log-producing action.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    assert room.state.log == []
    # Draw-first rule (deck non-empty): mark drawn so the pass is accepted, and
    # keep the log to just the pass line this test asserts on.
    room._has_drawn = True
    asyncio.run(room.handle_action("p1", PassMsg()))
    # The pass line is captured in the persistent snapshot log, and the snapshot
    # exposes it so a rejoining client rebuilds history.
    assert any("passed" in line for line in room.state.log)
    assert room.snapshot()["log"] == room.state.log


def test_start_sets_phase_setup() -> None:
    # A single StartMsg from the lobby now lands in the SETUP phase (card
    # authoring), not straight into "playing".
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())

    import agent.rag.store as store

    store._client = None  # force the offline seed-file fallback
    asyncio.run(room.handle_action("p1", StartMsg()))
    assert room.state.phase == "setup"


def test_start_sets_phase_playing() -> None:
    # Driving the full two-step flow (start -> author 5 each -> start) reaches
    # phase="playing".
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import agent.rag.store as store

    store._client = None  # force the offline seed-file fallback
    drive_to_playing(room, ["p1", "p2"])
    assert room.state.phase == "playing"


def test_start_builds_deck_of_at_least_30_and_deals_hands() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    # Force the offline path: no RAG store initialised -> seed-file fallback.
    import agent.rag.store as store

    store._client = None
    drive_to_playing(room, ["p1", "p2"])

    assert room.state.phase == "playing"
    # Deck was finalised: 30 premade + 10 authored + 10 blanks = 50, minus the
    # 10 cards dealt (5 each) = 40 left in the deck.
    total_hands = sum(len(p.hand) for p in room.state.players)
    assert len(room.state.deck) + total_hands == 50
    assert len(room.state.deck) == 40
    # Starting hands were dealt from the top of the deck. There is no auto-draw
    # at turn start, so both players hold exactly the dealt STARTING_HAND_SIZE;
    # the first player must send an explicit `draw` to take their turn's card.
    assert len(room.state.get_player("p1").hand) == 5
    assert len(room.state.get_player("p2").hand) == 5
    # Every dealt/deck card id resolves in the registry.
    for p in room.state.players:
        assert all(cid in room.state.cards for cid in p.hand)
    assert all(cid in room.state.cards for cid in room.state.deck)


def test_first_player_not_auto_drawn_then_explicit_draw_adds_cards() -> None:
    # No auto-draw at game start: the first player begins with exactly the dealt
    # STARTING_HAND_SIZE and has_drawn is False. An explicit `draw` then adds
    # draw_count card(s) on top of the starting hand.
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import agent.rag.store as store

    store._client = None
    drive_to_playing(room, ["p1", "p2"])

    # Both players hold only the dealt hand; the active player has not drawn yet.
    assert len(room.state.get_player("p1").hand) == 5
    assert len(room.state.get_player("p2").hand) == 5
    assert room.state.turn_index == 0
    assert room._has_drawn is False

    # p1 draws explicitly -> STARTING_HAND_SIZE + draw_count.
    asyncio.run(room.handle_action("p1", DrawMsg()))
    assert len(room.state.get_player("p1").hand) == 5 + room.state.draw_count
    assert room._has_drawn is True


def test_create_card_off_turn_allowed() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"phase": "playing"})
    room.connections.connect("p2", AsyncMock())
    fake_result = InterpretResult(program=None, snippet=None, verdict="invalid")
    with patch("agent.runtime.run_agent", return_value=fake_result):
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do something")))
    assert len(room.state.cards) == 1


def _chooser_room() -> Room:
    """Two-player room mid-game with a single 'chooser'-target card in p1's hand."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            # Non-empty deck so drawing the last card doesn't latch end-of-game;
            # the active player has already taken their draw step (_has_drawn) so
            # these play tests exercise the play path, not the draw-first gate.
            "deck": ["d1", "d2"],
            "cards": {"c1": {"id": "c1", "title": "Bless", "description": "give a chosen player points"}},
        }
    )
    room._has_drawn = True
    return room


def _chooser_result() -> InterpretResult:
    """Interpretation result with a chooser-target op requiring a choice."""
    program = EffectProgram(ops=[AddPointsOp(target="chooser", amount=5)], requires_choice=True)
    return InterpretResult(program=program, snippet=None, verdict="ok")


def test_play_chooser_card_with_valid_choice_applies() -> None:
    room = _chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="player", target_player_id="p2"), chosen_player_id="p2")
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    # The chosen player received the points and the turn advanced.
    assert room.state.get_player("p2").score == 5
    assert room.state.turn_index == 1


def test_play_chooser_card_without_choice_prompts() -> None:
    # Under the card-driven targeting design, a play that needs a player choice
    # but supplies none is HELD PENDING: the server sends a prompt_choice with
    # the candidate players and does NOT advance the turn (no error, no score
    # change, card not consumed).
    room = _chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", chosen_player_id=None)
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    sent_types = [m["type"] for m in sent]
    assert "prompt_choice" in sent_types
    assert "error" not in sent_types
    prompt = next(m for m in sent if m["type"] == "prompt_choice")
    assert prompt["card_id"] == "c1"
    assert {c["player_id"] for c in prompt["choices"]} == {"p1", "p2"}
    assert all("name" in c for c in prompt["choices"])
    # Turn NOT advanced, no score applied.
    assert room.state.get_player("p2").score == 0
    assert room.state.turn_index == 0


def test_play_chooser_followup_with_choice_applies_and_advances() -> None:
    # The follow-up play carrying the picked chosen_player_id applies the effect
    # and advances the turn (the second play re-interprets — no server pending
    # state is held).
    room = _chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))  # prompt
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1", chosen_player_id="p2")))  # answer
    assert room.state.get_player("p2").score == 5
    assert room.state.turn_index == 1


def test_play_chooser_card_with_invalid_choice_errors_cleanly() -> None:
    room = _chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_player_id="ghost")
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.get_player("p2").score == 0
    assert room.state.turn_index == 0


# ── chosen_card (CardTarget axis) plumbing ──
def _card_chooser_room() -> Room:
    """Two-player room mid-game; p1 has a 'destroy a chosen card' card 'c1',
    and target card 't1' sits in p1's in-play zone."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            # Non-empty deck so drawing the last card doesn't latch end-of-game;
            # _has_drawn is set below so these tests exercise the play path.
            "deck": ["d1", "d2"],
            "cards": {"c1": {"id": "c1", "title": "Zap", "description": "destroy a chosen card"}},
        }
    )
    new_players = [p.model_copy(update={"in_play": ["t1"]}) if p.id == "p1" else p for p in room.state.players]
    room.state = room.state.model_copy(update={"players": new_players})
    room._has_drawn = True
    return room


def _card_chooser_result() -> InterpretResult:
    program = EffectProgram(ops=[DestroyCardOp(card_target="chosen_card")], requires_choice=True)
    return InterpretResult(program=program, snippet=None, verdict="ok")


def test_play_card_choice_with_valid_card_applies() -> None:
    room = _card_chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_card_id="t1")
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    # The chosen card was destroyed and the turn advanced.
    assert "t1" not in room.state.get_player("p1").in_play
    assert "t1" in room.state.discard
    assert room.state.turn_index == 1


def test_play_card_choice_without_card_prompts() -> None:
    # The card-target axis also prompts (rather than errors) when no card was
    # chosen: the server sends a prompt_choice listing the selectable cards and
    # holds the play pending (no destruction, turn NOT advanced).
    room = _card_chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", chosen_card_id=None)
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    sent_types = [m["type"] for m in sent]
    assert "prompt_choice" in sent_types
    assert "error" not in sent_types
    prompt = next(m for m in sent if m["type"] == "prompt_choice")
    assert "t1" in {c["card_id"] for c in prompt["choices"]}
    assert "t1" in room.state.get_player("p1").in_play
    assert room.state.turn_index == 0


def test_play_card_choice_followup_with_card_applies_and_advances() -> None:
    room = _card_chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))  # prompt
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1", chosen_card_id="t1")))  # answer
    assert "t1" not in room.state.get_player("p1").in_play
    assert "t1" in room.state.discard
    assert room.state.turn_index == 1


def test_play_card_choice_with_invalid_card_errors_cleanly() -> None:
    room = _card_chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_card_id="ghost_card")
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert "t1" in room.state.get_player("p1").in_play
    assert room.state.turn_index == 0


# ── draw → play → pass turn model ──
def _playing_room(deck: list[str]) -> Room:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"phase": "playing", "deck": list(deck)})
    return room


def test_pass_advances_turn_and_next_player_must_draw() -> None:
    room = _playing_room(["d1", "d2", "d3"])
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    # p1 draws (draw→play→end), then ends their turn.
    asyncio.run(room.handle_action("p1", DrawMsg()))
    asyncio.run(room.handle_action("p1", PassMsg()))
    # Turn advanced to p2. No auto-draw at turn start: p2's hand is untouched and
    # has_drawn resets, so they must draw themselves.
    assert room.state.turn_index == 1
    assert room.state.get_player("p2").hand == []
    assert room.state.deck == ["d2", "d3"]  # only p1's draw removed a card
    assert room.state.get_player("p1").hand == ["d1"]
    assert room._has_drawn is False
    ws2.send_text.assert_called()
    # p2 then draws explicitly.
    asyncio.run(room.handle_action("p2", DrawMsg()))
    assert room.state.get_player("p2").hand == ["d2"]


def test_draw_takes_exactly_draw_count_cards() -> None:
    # An explicit draw takes exactly draw_count (default 1) card(s) — there is no
    # auto-draw and no pragmatic "extra card" rescue in the draw→play→end model.
    room = _playing_room(["d1", "d2", "d3", "d4"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", DrawMsg()))
    assert room.state.get_player("p1").hand == ["d1"]  # only draw_count=1 card
    assert room.state.deck == ["d2", "d3", "d4"]


def test_draw_with_zero_draw_count_takes_nothing() -> None:
    # With draw_count=0 an explicit draw takes no cards (no rescue draw): the
    # draw step is still consumed (has_drawn) so the player can then play/pass.
    room = _playing_room(["d1", "d2"])
    room.state = room.state.model_copy(update={"draw_count": 0})
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", DrawMsg()))
    assert room.state.get_player("p1").hand == []
    assert room.state.deck == ["d1", "d2"]
    assert room._has_drawn is True


def test_pass_on_empty_deck_ends_game_with_winner() -> None:
    room = _playing_room([])
    room.state = room.state.model_copy(
        update={
            "players": [
                room.state.players[0].model_copy(update={"score": 3}),
                room.state.players[1].model_copy(update={"score": 7}),
            ]
        }
    )
    # Model "the last card was already drawn earlier": exhaustion latched, deck
    # empty. p1's pass (allowed with no deck) ends their turn -> the game ends.
    room._deck_exhausted = True
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room.handle_action("p1", PassMsg()))
    # Turn ends on an exhausted deck -> end-of-game resolves, winners are
    # computed, and the epilogue vote opens (phase="epilogue"). p2 (highest
    # score) is the winner.
    assert room.state.phase == "epilogue"
    assert room.state.winner_ids == ["p2"]
    # Both players received the final state broadcast (nobody stuck).
    ws1.send_text.assert_called()
    ws2.send_text.assert_called()
    assert any("Winner" in line for line in room.state.log)


def test_last_card_drawer_finishes_turn_before_end() -> None:
    # A turn with exactly one card left: the active player draws it (deck now
    # empty, exhaustion latched) and the game does NOT end yet — they still get
    # to act. Ending is deferred until this turn ends (see _advance_turn).
    room = _playing_room(["last"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", DrawMsg()))
    assert room.state.phase == "playing"
    assert room.state.deck == []
    assert room.state.get_player("p1").hand == ["last"]
    assert room._deck_exhausted is True


def test_deck_exhaustion_end_to_end_via_pass() -> None:
    # p1 draws the last card (exhaustion latches), then passes -> the turn ends
    # and the game ends. p1's own turn completed first (its draw+pass did not
    # error).
    room = _playing_room(["last"])
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    room.state = room.state.model_copy(
        update={
            "players": [
                room.state.players[0].model_copy(update={"score": 5}),
                room.state.players[1],
            ],
        }
    )
    asyncio.run(room.handle_action("p1", DrawMsg()))  # draws 'last', latches exhaustion
    assert room.state.get_player("p1").hand == ["last"]
    asyncio.run(room.handle_action("p1", PassMsg()))  # ends turn -> end-of-game
    # End-of-game opens the epilogue with winners already computed.
    assert room.state.phase == "epilogue"
    assert room.state.winner_ids == ["p1"]  # p1 has 5, p2 has 0


# ── blank cards: authored on play ──
def _blank_room() -> Room:
    """Two-player room mid-game with a single blank card 'blank-0' in the deck."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            # Non-empty deck so drawing the last card doesn't latch end-of-game;
            # _has_drawn is set so these tests exercise the (author-on-)play path.
            "deck": ["d1", "d2"],
            "cards": {
                "blank-0": {"id": "blank-0", "title": "", "description": "", "blank": True, "creator_id": "blank"}
            },
        }
    )
    room._has_drawn = True
    return room


def _self_points_result() -> InterpretResult:
    """A simple 'give self points' interpretation (no target choice needed)."""
    return InterpretResult(program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]), snippet=None, verdict="ok")


def test_play_blank_authors_card_and_applies_and_advances() -> None:
    # Playing a blank with title+description fills in the card (title/description
    # persisted, blank flag cleared, creator set to the player), interprets it,
    # applies the effect, and advances the turn.
    room = _blank_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="blank-0", title="Gain 3", description="Gain 3 points.")
    with patch("agent.runtime.run_agent", return_value=_self_points_result()) as mock_interp:
        asyncio.run(room.handle_action("p1", msg))
    # Card was authored in the registry.
    card = room.state.cards["blank-0"]
    assert card["title"] == "Gain 3"
    assert card["description"] == "Gain 3 points."
    assert card["creator_id"] == "p1"
    assert "blank" not in card
    # Interpreter saw the AUTHORED text (persist-before-interpret ordering) plus
    # the live state, actor_id and creator_id (the player who authored the blank).
    from models.game_state import GameState

    mock_interp.assert_called_once()
    call = mock_interp.call_args
    assert call.args[0] == "Gain 3"
    assert call.args[1] == "Gain 3 points."
    assert isinstance(call.args[2], GameState)  # live GameState passed to the agent
    assert call.args[3] == "p1"  # actor_id
    assert call.kwargs["creator_id"] == "p1"  # authored-on-play: creator == actor
    # Effect applied and turn advanced.
    assert room.state.get_player("p1").score == 3
    assert room.state.turn_index == 1


def test_play_blank_persists_authoring_before_interpretation_for_followup() -> None:
    # Core ordering: the authored content is persisted BEFORE interpretation, so
    # a prompt_choice follow-up play (which omits title/description) re-interprets
    # the now-real card. Simulate: first play is a blank chooser card (prompts),
    # second play carries only the chosen_player_id.
    room = _blank_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_chooser_result()) as mock_interp:
        # First play: author the blank + interpret -> needs a target -> prompt.
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", title="Bless", description="give points")))
        # After the first play the blank is already a real, authored card.
        card = room.state.cards["blank-0"]
        assert card["title"] == "Bless"
        assert "blank" not in card
        assert room.state.turn_index == 0  # held pending, no advance yet
        # Follow-up play carries ONLY the choice (no title/description) — the
        # persisted card lets it re-interpret correctly.
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", chosen_player_id="p2")))
    # Both plays re-interpreted the same authored text (title/description are the
    # first two positional args; run_agent also receives state/actor/creator).
    assert mock_interp.call_args_list[0].args[:2] == ("Bless", "give points")
    assert mock_interp.call_args_list[1].args[:2] == ("Bless", "give points")
    assert room.state.get_player("p2").score == 5
    assert room.state.turn_index == 1


def test_play_blank_without_content_is_rejected() -> None:
    # A blank played with no title/description is guarded: an error is sent, the
    # card stays blank, and the turn does not advance (nothing interpreted).
    room = _blank_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_self_points_result()) as mock_interp:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0")))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    mock_interp.assert_not_called()
    assert room.state.cards["blank-0"]["blank"] is True
    assert room.state.turn_index == 0
