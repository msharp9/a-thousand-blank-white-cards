# state-example.jsonc ↔ GameState reconciliation

`docs/state-example.jsonc` is the **authoritative, hand-authored design** for the game
state. `src/models/game_state.py` is an *implementation* of that design, and it has
**diverged** from it. This note reconciles the two.

**Authority direction: the doc wins.** Where the code disagrees with the doc, the
code is the regression, not the doc. This file records each divergence and the code
bead filed to bring the implementation back in line with the design. The doc is left
exactly as authored (only whitespace/comments untouched).

An earlier pass in this session wrongly did the opposite — it rewrote the doc to match
the code and declared "the engine is a superset." That was incorrect and has been
reverted; the reasoning below replaces it.

## Divergences (doc = design of record; code must change)

### 1. Turn order — explicit `turnOrder`, NOT a `direction` flag
- **Design:** `turnOrder: ["Player1", "Player2"]` — an explicit, ordered, *mutable*
  list of who plays when.
- **Code:** `turn_index: int` + `direction: Literal[1, -1]` (clockwise / CCW).
- **Why the code is wrong:** direction can only express a *reverse*. It cannot express
  a **scramble/randomize-order** card, an insert, a swap of two seats, or any other
  reordering — all of which are legal made-up cards in this game. Turn order must be a
  first-class mutable list, and "reverse" becomes just one operation on it.
- **Action:** code bead — replace the `direction` model with an explicit `turn_order`
  and make reverse/scramble/etc. operate on it.

### 2. Per-player `conditions` — an open-ended map, NOT hardcoded private sets
- **Design:** each player has `conditions` (e.g. `null`, or `{"skipNextTurn": 1}`) — an
  **arbitrary, open-ended** bag of statuses.
- **Code:** two hardcoded, non-serialized `PrivateAttr` sets: `_skip_next` and
  `_extra_turn`. Nothing else can be represented.
- **Why the code is wrong:** the entire premise of 1000 Blank White Cards is emergent,
  player-invented effects. A player can be *poisoned*, *confused*, or *pertwiddled* —
  conditions that don't exist until someone writes the card. Hardcoding two specific
  statuses as private sets both (a) can't represent arbitrary conditions and (b) hides
  them from the serialized snapshot the clients/agent see.
- **Action:** code bead — model `conditions` as an open-ended, **serialized** per-player
  map; fold the existing skip/extra-turn behavior into it as two well-known keys.

### 3. Spectators — a separate simple list, NOT entries in `players`
- **Design:** `spectators: ["Person1", "Person2"]` — a flat list, separate from players.
- **Code:** spectators live inside `players[]` as `Player(spectator=True)` and are
  filtered out everywhere (`turn_players()`, scoring, dealing, …).
- **Why the code is wrong:** players are **complex, dynamic** entities (hand, in_play,
  score, conditions, turn position); spectators are **simple and deterministic** (just a
  name/identity, watching). Modeling a simple deterministic thing as a complex dynamic
  one forces a `spectator` guard into every player code path and invites bugs (a
  spectator accidentally dealt to, scored, or given a turn). They should be their own
  simple collection.
- **Action:** code bead — split spectators back out into their own list; drop the
  `Player.spectator` flag and its guards.

### 4. Structure & naming (lower-stakes, but the doc is still the target)
- **`board {cardsInCenter, discardPile, deck}`** (nested) vs flat `deck` / `discard` /
  `house_rules` (center). The design groups the shared table zones under `board`; the
  code flattened them and renamed center→`house_rules`.
- **`rules {draw, play, cannotPlay, endCondition, winCondition}`** (declarative block)
  vs scattered `draw_count`, implicit play/cannot-play logic, and `win_condition`.
  Notably the design has an explicit **`cannotPlay: {draw: 1}`** rule and a **`play`**
  count; the code buries these in loop logic rather than exposing them as configurable
  rules.
- **`history`** vs `log` (rename).
- **Action:** roll these into the code beads above (or a small follow-up) so the
  implementation's shape and names track the design.

## Code beads filed

See the `investigate/reconcile` bead's children / links (filed in this session) for the
concrete code changes: explicit mutable turn order (#1), open-ended serialized
conditions (#2), and separate spectator collection (#3). Item #4 rides along with them.

## Note on tests

Several tests currently encode the *diverged* shape (e.g. `direction`, `spectator`
flag, private skip sets). Those tests must be updated alongside the code beads — they
are asserting the regression, not the design.
