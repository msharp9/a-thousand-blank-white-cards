"""engine.sandbox.api_surface — restricted façade passed to LLM snippet apply().

A snippet's apply(state, ctx) receives a SandboxGame; it CANNOT touch raw
GameState. Each mutating call records an op dict in self._ops. After apply()
returns, the parent collects self.ops() as a JSON list (the diff) and re-validates
it through the engine's own reducers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from models.effects import validate_move_cards_source
from models.game_state import normalize_condition_key


class _ConditionView(dict[str, Any]):
    """Detached condition mapping with case-insensitive string lookups."""

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(normalize_condition_key(key))

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            key = normalize_condition_key(key)
        return super().__contains__(key)

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(normalize_condition_key(key), default)


@dataclass
class _PlayerView:
    """Read-only window into a single player's public state."""

    id: str
    name: str
    score: int
    hand_size: int
    connected: bool
    eliminated: bool


class SandboxGame:
    """Restricted game façade injected into snippet execution.

    Exposes read-only player views and whitelisted mutators that record ops as
    dicts. Instantiated inside the sandboxed subprocess from JSON-decoded
    state/ctx; records ops which the child serialises to stdout for the parent.
    """

    def __init__(
        self,
        state_dict: dict[str, Any],
        ctx_dict: dict[str, Any],
        rng_seed: int | None = None,
    ) -> None:
        self._state = state_dict
        self._ctx = ctx_dict
        self._ops: list[dict[str, Any]] = []
        self._rng = random.Random(rng_seed)

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def current_player_id(self) -> str:
        """Id of the player whose turn it is."""
        players = self._state["players"]
        idx = self._state.get("turn_index", 0) % len(players)
        return players[idx]["id"]

    @property
    def actor_id(self) -> str:
        """Id of the player who triggered the event (from ctx)."""
        return self._ctx.get("actor_id", self.current_player_id)

    def players(self) -> list[_PlayerView]:
        """Read-only views of all players."""
        return [self._view(p) for p in self._state["players"]]

    def player(self, player_id: str) -> _PlayerView:
        """Read-only view of a specific player by id."""
        for p in self._state["players"]:
            if p["id"] == player_id:
                return self._view(p)
        raise KeyError(f"Player {player_id!r} not found")

    @staticmethod
    def _view(p: dict[str, Any]) -> _PlayerView:
        return _PlayerView(
            id=p["id"],
            name=p["name"],
            score=p["score"],
            hand_size=len(p.get("hand", [])),
            connected=p.get("connected", True),
            eliminated=p.get("eliminated", False),
        )

    @property
    def draw_count(self) -> int:
        return self.rules().get("draw", self._state.get("draw_count", 1))

    def rules(self) -> dict[str, Any]:
        """The current mutable rules (draw, play, end_condition, win_condition, extra…)."""
        return dict(self._state.get("rules") or {})

    @property
    def deck_size(self) -> int:
        return len(self._state.get("deck", []))

    def my_hand(self) -> list[str]:
        """Card ids in the ACTOR's hand (other hands expose only their size)."""
        for p in self._state["players"]:
            if p["id"] == self.actor_id:
                return list(p.get("hand", []))
        return []

    def hand_size(self, player_id: str) -> int:
        return self.player(player_id).hand_size

    def conditions(self, player_id: str) -> dict[str, Any]:
        """A player's open conditions bag (poisoned, skip_next, …)."""
        for p in self._state["players"]:
            if p["id"] == player_id:
                return _ConditionView(
                    (normalize_condition_key(key), value) for key, value in (p.get("conditions") or {}).items()
                )
        raise KeyError(f"Player {player_id!r} not found")

    def card(self, card_id: str) -> dict[str, Any] | None:
        """Public metadata for a card: title, description, alt_text, attributes, origin."""
        card = (self._state.get("cards") or {}).get(card_id)
        if not isinstance(card, dict):
            return None
        return {
            "id": card.get("id", card_id),
            "title": card.get("title"),
            "description": card.get("description"),
            # Art description — queryable, so cards can key off what other
            # cards depict ("double points for cards with monkeys").
            "alt_text": card.get("alt_text"),
            "attributes": dict(card.get("attributes") or {}),
            "origin": card.get("origin"),
        }

    def history(
        self,
        kind: str | None = None,
        player_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return bounded public mechanics history without private card contents."""
        bounded = max(1, min(limit, 200))
        events = list(self._state.get("history_events") or [])
        if kind is not None:
            events = [event for event in events if event.get("kind") == kind]
        if player_id is not None:
            events = [
                event
                for event in events
                if event.get("actor_id") == player_id or player_id in (event.get("target_player_ids") or [])
            ]
        return [dict(event) for event in events[-bounded:]]

    def draw_totals(self) -> dict[str, int]:
        """Return exact cards-drawn totals keyed by player id."""
        totals = {player["id"]: 0 for player in self._state["players"]}
        for event in self._state.get("history_events") or []:
            if event.get("kind") != "draw":
                continue
            amount = event.get("amount")
            if not isinstance(amount, int):
                continue
            for player_id in event.get("target_player_ids") or []:
                if player_id in totals:
                    totals[player_id] += amount
        return totals

    @property
    def turn_order(self) -> list[str]:
        """The turn rotation order (explicit ``turn_order``, or ``players``
        in list order when not yet established)."""
        order = self._state.get("turn_order")
        if order:
            return list(order)
        return [p["id"] for p in self._state["players"]]

    # ------------------------------------------------------------------
    # Mutators — each appends an op dict; never modifies _state/_ctx
    # ------------------------------------------------------------------

    def add_points(self, target: str, amount: int) -> None:
        """Award `amount` points to player `target`."""
        self._require_nonneg_int(amount)
        self._ops.append({"op": "add_points", "target": target, "amount": amount})

    def subtract_points(self, target: str, amount: int) -> None:
        """Deduct `amount` points from player `target`."""
        self._require_nonneg_int(amount)
        self._ops.append({"op": "subtract_points", "target": target, "amount": amount})

    def set_points(self, target: str, amount: int) -> None:
        """Set player `target`'s score to exactly `amount`."""
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValueError(f"amount must be an int, got {amount!r}")
        self._ops.append({"op": "set_points", "target": target, "amount": amount})

    def skip_turn(self, target: str) -> None:
        """Skip player `target`'s next turn."""
        self._ops.append({"op": "skip_turn", "target": target})

    def skip(self, target: str) -> None:
        self.skip_turn(target)

    def change_draw_count(self, amount: int) -> None:
        """Set the per-turn draw count."""
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError(f"draw count must be a non-negative int, got {amount!r}")
        self._ops.append({"op": "change_draw_count", "amount": amount})

    def set_draw_count(self, amount: int) -> None:
        self.change_draw_count(amount)

    def custom_note(self, note: str) -> None:
        """Log a flavour message (no mechanical effect)."""
        self._ops.append({"op": "custom_note", "note": str(note)[:500]})

    def note(self, message: str) -> None:
        self.custom_note(message)

    def extra_turn(self, target: str) -> None:
        """Grant player `target` an extra turn."""
        self._ops.append({"op": "extra_turn", "target": target})

    def reverse_order(self) -> None:
        self._ops.append({"op": "reverse_order"})

    def scramble_order(self) -> None:
        self._ops.append({"op": "scramble_order"})

    def steal_points(self, from_target: str, to_target: str, amount: int) -> None:
        self._require_nonneg_int(amount)
        self._ops.append({"op": "steal_points", "from_target": from_target, "to_target": to_target, "amount": amount})

    def draw_cards(self, target: str, amount: int) -> None:
        """Have player `target` draw `amount` cards from the deck."""
        self._require_nonneg_int(amount)
        self._ops.append({"op": "draw_cards", "target": target, "amount": amount})

    def roll_die(
        self,
        sides: int = 6,
        count: int = 1,
        target: str = "self",
        outcome: str = "none",
    ) -> int:
        """Roll `count` dice (1-10) of `sides` sides (2-1000); returns the TOTAL.

        The roll happens HERE, immediately, and the recorded op carries the
        rolled values in `result` — so revalidation replays this exact roll
        instead of re-rolling. Callers can never supply the values: the engine
        alone rolls. `outcome` feeds the total into "add_points",
        "subtract_points" or "draw_cards" for `target`; "none" is a bare roll
        whose returned total your code can branch on.
        """
        if not isinstance(sides, int) or isinstance(sides, bool) or not 2 <= sides <= 1000:
            raise ValueError(f"sides must be an int in 2..1000, got {sides!r}")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            raise ValueError(f"count must be an int in 1..10, got {count!r}")
        if outcome not in ("add_points", "subtract_points", "draw_cards", "none"):
            raise ValueError(f"outcome must be add_points/subtract_points/draw_cards/none, got {outcome!r}")
        values = [self._rng.randint(1, sides) for _ in range(count)]
        self._ops.append(
            {"op": "roll_die", "sides": sides, "count": count, "target": target, "outcome": outcome, "result": values}
        )
        return sum(values)

    def discard_random(self, target: str = "self", count: int = 1) -> None:
        """Discard `count` (1-10) random cards from each `target` player's hand.

        Unlike roll_die, the picks are NOT resolved here: snippets cannot read
        other players' hands, so the engine draws the cards at apply time (a
        player holding fewer than `count` discards their whole hand). There is
        no return value to branch on.
        """
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            raise ValueError(f"count must be an int in 1..10, got {count!r}")
        self._ops.append({"op": "discard_random", "target": target, "count": count})

    _ZONES = ("deck", "discard", "hand", "in_play", "center", "exile")

    def move_cards(
        self,
        card_target: str | None = None,
        from_zone: str | None = None,
        selector: str = "top",
        count: int = 1,
        from_player: str | None = None,
        to_zone: str = "discard",
        to_position: str = "top",
        to_player: str | None = None,
    ) -> None:
        """Move cards between zones (deck/discard/hand/in_play/center/exile) without playing them.

        Give an explicit `card_target`, a `from_zone` with `selector`
        ("top"/"bottom"/"all"/"random") and `count` (1-50), or BOTH — the
        addressed card moves only if it actually sits in the declared zone
        (`selector`/`count` are not applied there and must stay defaults).
        `from_player`/`to_player` are required exactly when the corresponding
        zone is "hand" or "in_play"; `to_player` also accepts "card_owner"
        (each moved card routes to its own owner). `to_position` applies only
        to a deck destination: "top", "bottom", or "shuffle" (random
        positions). Random picks happen in the ENGINE at apply time and
        nothing is returned — your code can never learn which hidden card
        moved.
        """
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 50:
            raise ValueError(f"count must be an int in 1..50, got {count!r}")
        validate_move_cards_source(card_target, from_zone, selector, count)
        if from_zone is not None and from_zone not in self._ZONES:
            raise ValueError(f"from_zone must be one of {self._ZONES}, got {from_zone!r}")
        if to_zone not in self._ZONES:
            raise ValueError(f"to_zone must be one of {self._ZONES}, got {to_zone!r}")
        if selector not in ("top", "bottom", "all", "random"):
            raise ValueError(f"selector must be top/bottom/all/random, got {selector!r}")
        if to_position not in ("top", "bottom", "shuffle"):
            raise ValueError(f"to_position must be top/bottom/shuffle, got {to_position!r}")
        if (from_zone in ("hand", "in_play")) != (from_player is not None):
            raise ValueError("from_player is required exactly when from_zone is 'hand' or 'in_play'")
        if (to_zone in ("hand", "in_play")) != (to_player is not None):
            raise ValueError("to_player is required exactly when to_zone is 'hand' or 'in_play'")
        self._ops.append(
            {
                "op": "move_cards",
                "card_target": card_target,
                "from_zone": from_zone,
                "selector": selector,
                "count": count,
                "from_player": from_player,
                "to_zone": to_zone,
                "to_position": to_position,
                "to_player": to_player,
            }
        )

    def shuffle_deck(self, include_discard: bool = False) -> None:
        """Shuffle the draw pile; include_discard=True reshuffles the discard pile into it."""
        self._ops.append({"op": "shuffle_deck", "include_discard": bool(include_discard)})

    def destroy_card(self, card_id: str | None = None, card_target: str | None = None) -> None:
        """Destroy cards by CardTarget address ('this', 'last_played', 'all_in_play', 'all_in_center', 'id:…', 'attr:k=v')."""
        legacy_targets = {"all_in_hand", "all_in_play", "all_in_center", "chosen_card", "this", "last_played"}
        if (
            card_target is None
            and card_id is not None
            and (card_id in legacy_targets or card_id.startswith(("id:", "attr:")))
        ):
            card_target, card_id = card_id, None
        op: dict[str, Any] = {"op": "destroy_card"}
        if card_target is not None:
            op["card_target"] = card_target
        if card_id is not None:
            op["card_id"] = card_id
        self._ops.append(op)

    def transfer_card(self, card_target: str = "this", to_target: str = "self") -> None:
        """Move selected cards from their current zone into a player's hand.

        `to_target` names one player, or "card_owner" to route each card to
        its own owner (current holder, else who played it, else its creator).
        "Return the last card played to its owner's hand" =
        transfer_card('last_played', 'card_owner').
        """
        self._ops.append({"op": "transfer_card", "card_target": card_target, "to_target": to_target})

    def reveal_hand(
        self, target: str = "self", to: str = "all", persistent: bool = False, mode: str = "reveal"
    ) -> None:
        """Reveal a hand (`target` = whose, `to` = who may see it) or conceal it again.

        persistent=False is a one-shot peek; persistent=True keeps the hand
        face-up (to="all") or visible to the resolved players until
        mode="conceal" hides it again.
        """
        if mode not in ("reveal", "conceal"):
            raise ValueError(f"reveal_hand mode must be reveal/conceal, got {mode!r}")
        self._ops.append(
            {"op": "reveal_hand", "target": target, "to": to, "persistent": bool(persistent), "mode": mode}
        )

    def eliminate_player(self, target: str) -> None:
        """Knock the targeted player(s) out of the game: their hand is discarded and
        they take no more turns, but their in-play cards stay in effect. The last
        player still standing can never be eliminated."""
        self._ops.append({"op": "eliminate_player", "target": target})

    def set_win_condition(self, kind: str, threshold: int | None = None) -> None:
        self._ops.append({"op": "set_win_condition", "kind": kind, "threshold": threshold})

    def end_game(self, winner: str | list[str] | None = None, winners: list[str] | None = None) -> None:
        """End now, optionally forcing one target or an explicit target list."""
        op: dict[str, Any] = {"op": "end_game"}
        if isinstance(winner, list):
            if winners is not None:
                raise ValueError("end_game accepts winner or winners, not both")
            winners = winner
        elif winner is not None:
            op["winner"] = winner
        if winners:
            op["winners"] = list(winners)
        self._ops.append(op)

    def set_rule(self, path: str, value: Any) -> None:
        """Write a rules path: draw, play, end_condition.type, win_condition.kind, extra.<key>…"""
        self._ops.append({"op": "set_rule", "path": str(path), "value": value})

    def set_condition(self, target: str, key: str, value: Any = True, duration_turns: int | None = None) -> None:
        """Set a free-form condition on targeted players (value=None removes it).

        ``duration_turns`` makes it expire: the TTL ticks down at each targeted
        player's turn start and the condition is removed when it reaches 0.
        """
        op: dict[str, Any] = {"op": "set_condition", "target": target, "key": str(key), "value": value}
        if duration_turns is not None:
            op["duration_turns"] = int(duration_turns)
        self._ops.append(op)

    def set_card_attribute(self, card_target: str, key: str, value: Any) -> None:
        """Tag targeted cards with open metadata (e.g. a color)."""
        self._ops.append({"op": "set_card_attribute", "card_target": card_target, "key": str(key), "value": value})

    def create_card(
        self,
        title: str,
        description: str = "",
        ops: list[dict[str, Any]] | None = None,
        attributes: dict[str, Any] | None = None,
        destination: str = "deck_shuffle",
        target: str | None = None,
        count: int = 1,
    ) -> None:
        """Mint `count` copies of a new card (authoring ops compile when it is later played).

        `destination` is "deck_shuffle" (default), "deck_top", "deck_bottom",
        "hand", "discard", or "center". With "hand" the copies go to the
        `target` player(s) — default "self" (the actor); pass a player Target
        (e.g. "id:<player_id>") to hand cards to a specific player. Passing a
        player Target as `destination` (e.g. `destination="id:X"`) is treated
        as `destination="hand", target="X"`.
        """
        _DESTINATIONS = {"deck_shuffle", "deck_top", "deck_bottom", "hand", "discard", "center"}
        if target is None and destination not in _DESTINATIONS:
            target, destination = destination, "hand"
        self._ops.append(
            {
                "op": "create_card",
                "title": str(title),
                "description": str(description),
                "ops": list(ops or []),
                "attributes": dict(attributes or {}),
                "destination": destination,
                "target": target if target is not None else "self",
                "count": count,
            }
        )

    def shuffle_into_deck(
        self, title: str, description: str = "", ops: list[dict[str, Any]] | None = None, count: int = 1
    ) -> None:
        """Convenience alias: create_card with destination='deck_shuffle'."""
        self.create_card(title, description, ops, destination="deck_shuffle", count=count)

    def register_hook(
        self,
        event: str,
        scope: str = "center",
        code: str | None = None,
        *,
        title: str = "",
        condition_keys: list[str] | None = None,
    ) -> None:
        """Install a persistent sandboxed hook (rejected inside hook-produced diffs)."""
        if code is None:
            if "def apply" not in scope:
                raise ValueError("register_hook requires sandbox code; pass code=... with scope='player' or 'center'")
            code, scope = scope, "center"
        normalized_keys: list[str] = []
        for value in condition_keys or []:
            key = normalize_condition_key(value)
            if key and key not in normalized_keys:
                normalized_keys.append(key)
        self._ops.append(
            {
                "op": "register_hook",
                "event": str(event),
                "scope": scope,
                "code": str(code),
                "title": str(title),
                "condition_keys": normalized_keys,
            }
        )

    def unregister_hook(self, source_card_id: str) -> None:
        """Remove hooks registered by `source_card_id`."""
        self._ops.append({"op": "unregister_hook", "source_card_id": source_card_id})

    def reject_play(self, reason: str) -> None:
        """ON_VALIDATE_PLAY hooks only: veto the play being validated."""
        self._ops.append({"op": "reject_play", "reason": str(reason)[:300]})

    def counter_play(self, mode: str = "negate") -> None:
        """Reaction cards only: decide the pending play's fate.

        mode "negate" = the pending card's effect never happens (discard);
        "steal_hand" = no effect, the pending card goes to your hand;
        "redirect" = the pending effect resolves as if you had played it.
        The pending play is described by ctx["pending_card_id"],
        ctx["pending_actor_id"], ctx["pending_card_title"], ctx["pending_ops"].
        """
        if mode not in ("negate", "steal_hand", "redirect"):
            raise ValueError(f"counter_play mode must be negate/steal_hand/redirect, got {mode!r}")
        self._ops.append({"op": "counter_play", "mode": mode})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _require_nonneg_int(amount: int) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError(f"amount must be a non-negative int, got {amount!r}")

    def ops(self) -> list[dict[str, Any]]:
        """Return a copy of recorded ops for serialisation."""
        return list(self._ops)
