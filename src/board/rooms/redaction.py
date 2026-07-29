"""board.rooms.redaction — per-viewer snapshot redaction.

Hidden information (opponents' hand contents, the draw pile's contents and
order) must never leave the server: face-down rendering on the client is a
convention, not a secret. :func:`redact_snapshot` turns the full
``Room.snapshot()`` dict into the view that is safe to send to one viewer.
"""

from __future__ import annotations

from typing import Any

# Phases in which the deck's CONTENTS are deliberately public: during setup it
# holds the shared pre-made pool the authoring screen renders so players can
# write cards that synergize with it (see lobby→setup in Room.handle_start).
# From "playing" onward it is the shuffled draw pile, whose contents and order
# back scry / stacked-deck effects and must stay server-side.
PUBLIC_DECK_PHASES = frozenset({"lobby", "setup"})


def _hand_visible(
    player: dict[str, Any],
    viewer_id: str | None,
    *,
    reveal_all_cards: bool = False,
) -> bool:
    """True when ``viewer_id`` may see this player's hand CONTENT.

    Their own hand, a hand played face-up (``hand_public``, visible to every
    viewer including spectators), or a hand persistently revealed to this
    specific viewer (``hand_revealed_to`` — reveal_hand op, bead 7hd.2).
    """
    if reveal_all_cards or player.get("id") == viewer_id:
        return True
    if player.get("hand_public"):
        return True
    return viewer_id is not None and viewer_id in (player.get("hand_revealed_to") or [])


def _visible_card_ids(
    snap: dict[str, Any],
    viewer_id: str | None,
    *,
    reveal_all_cards: bool = False,
) -> set[str]:
    """Card ids whose CONTENT ``viewer_id`` is entitled to see.

    Public zones (in_play, discard, center, exiled), every hand visible to the viewer
    (their own, plus revealed hands — see :func:`_hand_visible`), and the deck
    while it is the shared setup pool. Beyond zones, content that has already
    been revealed to the table stays visible: the card suspended in an open
    reaction window (``pending_play``), every card a history event names
    (played cards remain readable even if they later move to a hidden zone),
    and the epilogue vote outcomes.
    """
    visible: set[str] = set()
    for player in snap.get("players", []):
        visible.update(player.get("in_play", []))
        if _hand_visible(player, viewer_id, reveal_all_cards=reveal_all_cards):
            visible.update(player.get("hand", []))
    visible.update(snap.get("discard", []))
    visible.update(snap.get("exiled", []))
    visible.update(snap.get("house_rules", []))
    if reveal_all_cards or snap.get("phase") in PUBLIC_DECK_PHASES:
        visible.update(snap.get("deck", []))
    if snap.get("phase") == "setup" and viewer_id is not None:
        for card_id, card in (snap.get("cards") or {}).items():
            if isinstance(card, dict) and card.get("origin") == "authored" and card.get("creator_id") == viewer_id:
                visible.add(card_id)
    interaction_visibility = snap.get("interaction_card_visibility")
    if interaction_visibility is not None and viewer_id in (interaction_visibility.get("viewer_ids") or []):
        visible.update(interaction_visibility.get("card_ids", []))
    pending_play = snap.get("pending_play")
    if pending_play is not None:
        visible.add(pending_play.get("card_id"))
    for event in snap.get("history_events", []):
        card_id = event.get("card_id")
        if card_id is not None:
            visible.add(card_id)
    epilogue = snap.get("epilogue_result")
    if epilogue is not None:
        for outcome in [*epilogue.get("kept", []), *epilogue.get("destroyed", [])]:
            visible.add(outcome.get("id"))
    return visible


def _redact_admin_preview(redacted: dict[str, Any], viewer_id: str | None) -> None:
    proposal = redacted.get("pending_admin_proposal")
    if not isinstance(proposal, dict):
        return
    proposal = dict(proposal)
    previews = []
    for raw_item in proposal.get("preview", []):
        item = dict(raw_item)
        if viewer_id is not None and viewer_id in (item.get("private_viewer_ids") or []):
            item["detail"] = item.get("private_detail") or item.get("detail", "")
        item.pop("private_detail", None)
        item.pop("private_viewer_ids", None)
        previews.append(item)
    proposal["preview"] = previews
    redacted["pending_admin_proposal"] = proposal


def redact_snapshot(
    snap: dict[str, Any],
    viewer_id: str | None,
    *,
    reveal_all_cards: bool = False,
) -> dict[str, Any]:
    """Return a copy of ``snap`` redacted for ``viewer_id``.

    - Every player entry gains ``hand_count``; any hand the viewer may not
      see (:func:`_hand_visible` — not their own, not face-up via
      ``hand_public``, not revealed to them via ``hand_revealed_to``) is
      emptied. A ``viewer_id`` of None — or a spectator id, which never
      matches a player — keeps only the face-up (``hand_public``) hands.
    - ``hand_revealed_to`` is itself secret: WHO a hand was quietly revealed
      to must not leak to the rest of the table. The owner keeps the full
      list (they know who is peeking); every other viewer keeps at most
      their own id — just enough for the client's "revealed to you" badge.
      ``reveal_bindings`` (engine bookkeeping) name the same audience, so
      they are stripped for every viewer.
    - ``deck_count`` is always added; ``deck`` is emptied outside
      :data:`PUBLIC_DECK_PHASES`.
    - The ``cards`` registry is filtered to :func:`_visible_card_ids` — the
      id lists above only say WHERE cards are; the registry is what carries
      their content, so hidden entries must be dropped, not just de-listed.
    - ``discard``, ``exiled``, ``in_play`` and the center zone are public and
      untouched.
    - ``interaction_card_visibility`` (set by ``Room.snapshot()`` while a
      deck-top interaction — scry / draw-N-keep-1 — is pending) keeps the
      offered cards' registry entries for exactly the interaction's audience,
      then is stripped for every viewer.

    The input dict is never mutated; only the copied containers this function
    rewrites are duplicated.
    """
    redacted = dict(snap)
    players = []
    for player in snap.get("players", []):
        entry = dict(player)
        entry["hand_count"] = len(entry.get("hand", []))
        if not _hand_visible(entry, viewer_id, reveal_all_cards=reveal_all_cards):
            entry["hand"] = []
        if entry.get("id") != viewer_id:
            revealed_to = entry.get("hand_revealed_to") or []
            entry["hand_revealed_to"] = [viewer_id] if viewer_id in revealed_to else []
        players.append(entry)
    redacted["players"] = players
    redacted.pop("reveal_bindings", None)
    redacted["deck_count"] = len(snap.get("deck", []))
    if not reveal_all_cards and snap.get("phase") not in PUBLIC_DECK_PHASES:
        redacted["deck"] = []
    cards = snap.get("cards")
    if isinstance(cards, dict):
        if reveal_all_cards:
            redacted["cards"] = dict(cards)
        else:
            visible = _visible_card_ids(snap, viewer_id)
            redacted["cards"] = {cid: card for cid, card in cards.items() if cid in visible}
    # Consumed above (it grants the pending deck-top interaction's audience
    # their offered cards); stripped for EVERY viewer because it names hidden
    # deck ids and who is being shown them.
    redacted.pop("interaction_card_visibility", None)
    _redact_admin_preview(redacted, viewer_id)
    return redacted
