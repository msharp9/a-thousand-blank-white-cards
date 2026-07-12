"""Bead 13v — EndGameOp + live win-condition evaluation during play.

Before this bead, the ONLY end-game path was deck exhaustion: `evaluate_win_condition`
was never checked during play, so an "End the Game" card had no live effect and
`set_win_condition(first_to, N)` never actually ended anything until the deck ran dry.
These tests drive a room through the deterministic compiled-ops path (`canonical.ops`)
so no LLM is involved, and assert the game ends immediately — deck untouched — rather
than waiting for `_advance_turn`'s deck-exhaustion check.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from models.ws_messages import PlayMsg
from board.rooms.room import Room


def _mid_deck_room(p1_hand: list[str], cards: dict, deck: list[str]) -> Room:
    """A two-player room mid-game: deck non-empty, both players connected, p1 active."""
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    players = [
        r.state.players[0].model_copy(update={"hand": p1_hand}),
        r.state.players[1],
    ]
    r.state = r.state.model_copy(update={"phase": "playing", "deck": deck, "cards": cards, "players": players})
    r._has_drawn = True  # skip the draw-first gate; unrelated to what's under test
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def test_end_game_card_ends_immediately_without_deck_exhaustion() -> None:
    card = {
        "id": "endit",
        "title": "End The Game",
        "description": "The game ends right now.",
        "canonical": {"ops": [{"op": "end_game", "args": {}}]},
    }
    room = _mid_deck_room(["endit"], {"endit": card}, deck=["d1", "d2", "d3"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="endit")))

    assert room.state.phase in ("results", "ended")
    assert room.state.deck == ["d1", "d2", "d3"]  # untouched — did not wait for exhaustion
    assert room._deck_exhausted is False


def test_end_game_authoring_synonym_win_the_game_compiles() -> None:
    card = {
        "id": "win1",
        "title": "You Win",
        "description": "You win the game.",
        "canonical": {"ops": [{"op": "win_the_game", "args": {}}]},
    }
    room = _mid_deck_room(["win1"], {"win1": card}, deck=["d1"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="win1")))

    assert room.state.phase in ("results", "ended")
    assert room.state.deck == ["d1"]


def test_set_win_condition_first_to_ends_the_moment_threshold_is_reached() -> None:
    # One card sets a first_to(10) win condition and, in the SAME play, scores
    # enough points to hit it — the game must end right away, mid-deck.
    card = {
        "id": "rush",
        "title": "Rush To Ten",
        "description": "First to 10 points wins; gain 10 points.",
        "canonical": {
            "ops": [
                {"op": "set_win_condition", "args": {"kind": "first_to", "threshold": 10}},
                {"op": "add_points", "args": {"target": "self", "amount": 10}},
            ]
        },
    }
    room = _mid_deck_room(["rush"], {"rush": card}, deck=["d1", "d2"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="rush")))

    assert room.state.get_player("p1").score == 10
    assert room.state.win_condition.kind == "first_to"
    assert room.state.phase in ("results", "ended")
    assert room.state.deck == ["d1", "d2"]
    assert room._deck_exhausted is False


def test_set_win_condition_first_to_not_yet_reached_keeps_playing() -> None:
    card = {
        "id": "setup_only",
        "title": "First To Ten",
        "description": "First to 10 points wins.",
        "canonical": {"ops": [{"op": "set_win_condition", "args": {"kind": "first_to", "threshold": 10}}]},
    }
    room = _mid_deck_room(["setup_only"], {"setup_only": card}, deck=["d1", "d2"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="setup_only")))

    assert room.state.win_condition.kind == "first_to"
    assert room.state.phase == "playing"
    assert room.state.turn_index == 1  # turn advanced normally, no early end


def test_win_the_game_played_by_the_losing_player_crowns_that_player() -> None:
    card = {
        "id": "yolo",
        "title": "YOLO",
        "description": "You win the game.",
        "canonical": {"ops": [{"op": "win_the_game", "args": {}}]},
    }
    room = _mid_deck_room(["yolo"], {"yolo": card}, deck=["d1"])
    players = [
        room.state.players[0].model_copy(update={"score": -9}),
        room.state.players[1].model_copy(update={"score": 50}),
    ]
    room.state = room.state.model_copy(update={"players": players})

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="yolo")))

    assert room.state.phase in ("results", "ended")
    assert room.state.winner_ids == ["p1"]
    assert room.state.winner_override == []


def test_plain_end_game_with_unequal_scores_crowns_the_leader() -> None:
    card = {
        "id": "endit",
        "title": "End The Game",
        "description": "The game ends right now.",
        "canonical": {"ops": [{"op": "end_game", "args": {}}]},
    }
    room = _mid_deck_room(["endit"], {"endit": card}, deck=["d1"])
    players = [
        room.state.players[0].model_copy(update={"score": -9}),
        room.state.players[1].model_copy(update={"score": 50}),
    ]
    room.state = room.state.model_copy(update={"players": players})

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="endit")))

    assert room.state.phase in ("results", "ended")
    assert room.state.winner_ids == ["p2"]


def test_post_end_actions_are_rejected_and_end_scoring_applies_once() -> None:
    keeper = {
        "id": "keep10",
        "title": "Worth 10 If Kept",
        "description": "Worth 10 points at game end.",
        "trigger": "on_game_end",
        "canonical": {"ops": [{"op": "add_points", "args": {"target": "self", "amount": 10}}]},
    }
    ender = {
        "id": "endit",
        "title": "End The Game",
        "description": "The game ends right now.",
        "canonical": {"ops": [{"op": "end_game", "args": {}}]},
    }
    room = _mid_deck_room(["endit"], {"endit": ender, "keep10": keeper}, deck=["d1", "d2"])
    players = [
        room.state.players[0].model_copy(update={"hand": ["endit"]}),
        room.state.players[1].model_copy(update={"hand": ["keep10"]}),
    ]
    room.state = room.state.model_copy(update={"players": players})

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="endit")))
    assert room.state.phase == "results"
    assert room.state.get_player("p2").score == 10
    assert room.state.rules.end_condition.type == "deck_empty"

    async def _post_end_actions() -> None:
        from models.ws_messages import DrawMsg

        await room.handle_action("p1", DrawMsg())
        await room.handle_action("p1", PlayMsg(card_id="endit"))

    asyncio.run(_post_end_actions())

    assert room.state.get_player("p2").score == 10
    assert room.state.deck == ["d1", "d2"]
    assert room.state.phase == "results"


def test_uno_v1_as_pure_rule_ops() -> None:
    # The Uno exemplar expressed entirely as data: win = empty hand, end = empty
    # hand, draw step 0. Playing the card flips the rules; the actor then plays
    # their remaining card, empties their hand, and wins immediately.
    uno = {
        "id": "uno",
        "title": "House Rules: Uno",
        "description": "Win by emptying your hand.",
        "canonical": {
            "ops": [
                {"op": "set_rule", "args": {"path": "win_condition", "value": {"kind": "empty_hand"}}},
                {"op": "set_rule", "args": {"path": "end_condition.type", "value": "empty_hand"}},
                {"op": "set_rule", "args": {"path": "draw", "value": 0}},
            ]
        },
    }
    filler = {
        "id": "f1",
        "title": "Gain 1",
        "description": "Gain 1 point.",
        "canonical": {"ops": [{"op": "add_points", "args": {"target": "self", "amount": 1}}]},
    }
    filler2 = {
        "id": "f2",
        "title": "Gain 2",
        "description": "Gain 2 points.",
        "canonical": {"ops": [{"op": "add_points", "args": {"target": "self", "amount": 2}}]},
    }
    room = _mid_deck_room(["uno", "f1"], {"uno": uno, "f1": filler, "f2": filler2}, deck=["d1", "d2"])
    players = [
        room.state.players[0].model_copy(update={"hand": ["uno", "f1"]}),
        room.state.players[1].model_copy(update={"hand": ["f2"]}),
    ]
    room.state = room.state.model_copy(update={"players": players})

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="uno")))
    assert room.state.rules.draw == 0
    assert room.state.rules.win_condition.kind == "empty_hand"
    assert room.state.phase == "playing"

    async def _next_turn_and_win() -> None:
        from models.ws_messages import DrawMsg

        await room.handle_action("p2", DrawMsg())
        await room.handle_action("p2", PlayMsg(card_id="f2"))

    asyncio.run(_next_turn_and_win())
    assert room.state.phase in ("results", "ended")
    assert "p2" in room.state.winner_ids


def test_spicy_uno_colors_and_created_cards_without_a_snippet() -> None:
    # Phase-B Uno: one card tags every in-hand card with a color AND mints
    # Draw 2 / Reverse cards into the deck — no snippet, pure ops.
    spicy = {
        "id": "spicy",
        "title": "House Rules: Spicy Uno",
        "description": "Cards get colors; the deck grows Draw 2s and a Reverse.",
        "canonical": {
            "ops": [
                {"op": "set_card_attribute", "args": {"card_target": "all_in_hand", "key": "color", "value": "red"}},
                {
                    "op": "create_card",
                    "args": {
                        "title": "Draw 2",
                        "description": "Draw two cards.",
                        "ops": [{"op": "draw_cards", "args": {"target": "self", "amount": 2}}],
                        "attributes": {"color": "blue"},
                        "destination": "deck_top",
                        "count": 2,
                    },
                },
                {
                    "op": "create_card",
                    "args": {
                        "title": "Reverse",
                        "description": "Reverse the turn order.",
                        "ops": [{"op": "reverse_order", "args": {}}],
                        "destination": "deck_top",
                    },
                },
            ]
        },
    }
    other = {"id": "o1", "title": "Other", "description": "x"}
    room = _mid_deck_room(["spicy", "o1"], {"spicy": spicy, "o1": other}, deck=["d1"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="spicy")))

    assert room.state.cards["o1"]["attributes"] == {"color": "red"}
    created = [cid for cid in room.state.deck if cid.startswith("created-")]
    assert len(created) == 3
    titles = {room.state.cards[cid]["title"] for cid in created}
    assert titles == {"Draw 2", "Reverse"}
    assert all(room.state.cards[cid]["origin"] == "authored" for cid in created)
