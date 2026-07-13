"""engine.sandbox.api_surface — restricted façade passed to LLM snippet apply().

A snippet's apply(state, ctx) receives a SandboxGame; it CANNOT touch raw
GameState. Each mutating call records an op dict in self._ops. After apply()
returns, the parent collects self.ops() as a JSON list (the diff) and re-validates
it through the engine's own reducers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _PlayerView:
    """Read-only window into a single player's public state."""

    id: str
    name: str
    score: int
    hand_size: int
    connected: bool


class SandboxGame:
    """Restricted game façade injected into snippet execution.

    Exposes read-only player views and whitelisted mutators that record ops as
    dicts. Instantiated inside the sandboxed subprocess from JSON-decoded
    state/ctx; records ops which the child serialises to stdout for the parent.
    """

    def __init__(self, state_dict: dict[str, Any], ctx_dict: dict[str, Any]) -> None:
        self._state = state_dict
        self._ctx = ctx_dict
        self._ops: list[dict[str, Any]] = []

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
                return dict(p.get("conditions") or {})
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

    def destroy_card(self, card_id: str | None = None, card_target: str | None = None) -> None:
        """Destroy cards by CardTarget address ('this', 'all_in_play', 'id:…', 'attr:k=v')."""
        legacy_targets = {"all_in_hand", "all_in_play", "chosen_card", "this"}
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

    def set_condition(self, target: str, key: str, value: Any = True) -> None:
        """Set a free-form condition on targeted players (value=None removes it)."""
        self._ops.append({"op": "set_condition", "target": target, "key": str(key), "value": value})

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
        count: int = 1,
    ) -> None:
        """Mint `count` copies of a new card (authoring ops compile when it is later played)."""
        self._ops.append(
            {
                "op": "create_card",
                "title": str(title),
                "description": str(description),
                "ops": list(ops or []),
                "attributes": dict(attributes or {}),
                "destination": destination,
                "count": count,
            }
        )

    def shuffle_into_deck(
        self, title: str, description: str = "", ops: list[dict[str, Any]] | None = None, count: int = 1
    ) -> None:
        """Convenience alias: create_card with destination='deck_shuffle'."""
        self.create_card(title, description, ops, destination="deck_shuffle", count=count)

    def register_hook(self, event: str, scope: str = "center", code: str | None = None) -> None:
        """Install a persistent sandboxed hook (rejected inside hook-produced diffs)."""
        if code is None:
            if "def apply" not in scope:
                raise ValueError("register_hook requires sandbox code; pass code=... with scope='player' or 'center'")
            code, scope = scope, "center"
        self._ops.append({"op": "register_hook", "event": str(event), "scope": scope, "code": str(code)})

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
