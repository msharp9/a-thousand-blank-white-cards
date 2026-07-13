# Canonical Card Annotation Spec — v2

Every card in every dataset (`data/seed_cards*.json`, `data/eval/eval_cards*.json`,
`data/eval/real_cards.json`) carries a canonical object describing how it behaves
in *1000 Blank White Cards*. Seed files use the key `canonical`; eval files keep
the key `human_canonical`. Annotate from the card's `title` + `description` +
`alt_text`. Encode the card's **intended** effect, not a literal word-for-word
reading.

## Top-level card shape

```json
{
  "id": "seed-gold-004",           // stable id, unique within the file
  "title": "Steal 3 Points",       // <= 60 chars
  "description": "Take 3 points from a player of your choice.",  // <= 500 chars, NO [alt text] prefix
  "alt_text": "a hand grabbing coins from a pile",  // str | null — describes the card art
  "image_url": "https://i.imgur.com/...",           // real_cards.json only
  "canonical": { ... }             // "human_canonical" in eval files
}
```

`alt_text` is first-class: user-drawn card art needs a description, and cards can
key off art content ("double points for every card with a monkey on it" matches
against other cards' `alt_text`). `null` when the card has no art or the art is
purely decorative with no describable content.

## Canonical object

```json
{
  "target":    "player",       // see TARGET
  "placement": "discard",      // see PLACEMENT
  "venue":     "all",          // see VENUE — required, no implicit default
  "trigger":   null,           // see TRIGGER
  "ops":       [ ... ] | null, // see OPS
  "sandbox":   "def apply(state, ctx): ...",  // see SANDBOX
  "magnitude_sign": "positive" // eval datasets only, optional
}
```

**Every card carries BOTH `ops` and `sandbox`.** `sandbox` is always executable
code (never prose). `ops` is `null` only when the effect cannot be expressed as
structured ops. Rationale: cards are RAG exemplars — when the agent meets a card
too complex for ops, it composes sandbox code by studying how simple cards do
each piece (a card that draws, a card that counts a hand, a card that adds
points → "Chess Master: draw 2, then gain points equal to your hand size").

### PLACEMENT — where the card physically goes after being played

- `discard` — the effect resolves once and the card goes to the discard pile.
  All one-shot/immediate cards, including physical dares and reactions.
- `center` — the card stays in the shared table center as a game-wide modifier
  (changes the draw amount, the win condition, a global ongoing rule).
- `player` — the card stays **in front of one player** as a modifier attached to
  that player (their points count double, they can't be targeted, they are
  poisoned). The affected player may be the actor or a chosen player — `target`
  says who.

To be fair, every card *could* just go to the discard; center/player placement
exists so persistent effects stay visible as reminders and are targetable
("steal a modifier", "destroy a modifier").

There is no `self` placement (v1 legacy — see mapping appendix) and no
`destroy` placement (a card that removes itself from the game encodes that with
a `destroy_card {card_target: "this"}` op and placement `discard`).

Invariant: placement `discard` ⇔ one-shot; `center`/`player` ⇔ persistent
modifier. (The old `timing` field is gone — it was fully redundant with this.)

### TARGET — who/what the effect primarily affects

Prefer **`player`** for anything that can be played on anyone. Only use `self`
when the card explicitly limits the effect to the person who plays it.

- `self` — only the actor ("you", "the player of this card").
- `player` — a chosen player (the actor picks; may be an opponent OR themselves).
  **The default** for point swings, effects, and challenges. When in doubt
  between `self` and `player`, choose `player`.
- `all` — every player, including the actor.
- `all_others` — every player except the actor.
- `card` — a single card ("destroy a card", "steal a card in play").
- `all_cards` — a set of cards ("all cards in front of a player").
- `none` — no player/card target (reverse turn order, change the draw count,
  a global rule with no per-player effect).

(`center` is NOT a target — that was v1 placement leakage.)

### VENUE — `"all" | "in_person" | "online"` (required)

Whether the card works when players are remote or must be physically together.
Used to filter decks for remote games.

- `all` — works both remotely and in person. Most cards.
- `in_person` — requires physical presence, contact, real objects, or shared
  space: touching another player, sharing/eating food, passing a physical
  object, moving around the room, physical dares.
- `online` — only makes sense digitally (references a chat feature, emoji,
  a screen). Use sparingly.

Purely verbal/social challenges that work over video (sing a song, do an
impression, answer trivia) are `all`, not `in_person` — a camera is enough.

### TRIGGER — when the card's effect fires

Values are the engine's `GameEvent` strings (`src/engine/events.py`) plus
`on_reaction`:

- `null` — one-shot: the effect fires when played, done. **All placement
  `discard` cards except reactions.**
- `on_play`, `on_validate_play`, `on_score_change`, `on_turn_start`,
  `on_turn_end`, `on_draw_step`, `on_win_check`, `on_game_end` — for persistent
  modifiers (`center`/`player` placement): the event that re-fires the card's
  hook while it stays in play.
- `on_reaction` — **reaction cards** (counterspell, steal-spell, "you also lose
  3 for trying"). NOT playable during the owner's normal play step; playable
  ONLY during the reaction window that opens when another player plays a card.
  Reactions are one-shot: placement `discard`.

Invariant: `trigger` is `null` ⇔ placement is `discard`, with the single
exception of reactions (`discard` + `on_reaction`).

### MAGNITUDE_SIGN — `"positive" | "negative" | "neutral"` (eval datasets only)

Net effect on the target's standing: `positive` (gains points/advantage),
`negative` (loses), `neutral` (no point change or a wash). Optional human label
consumed by eval scorers; not written into seed/game data.

### OPS — structured operations

A list of `{"op": <name>, "args": {...}}` in the authoring vocabulary
(compiled to runtime reducers by `src/engine/compile.py`):

- `add_points` — `{"target": <TARGET>, "amount": <int, +/->}` (negative for losses)
- `steal_points` — `{"from_target": <TARGET>, "to_target": <TARGET>, "amount": <int>}`
- `set_points` — `{"target": <TARGET>, "amount": <int>}`
- `skip_turn` / `extra_turn` — `{"target": <TARGET>}`
- `draw_cards` — `{"target": <TARGET>, "amount": <int>}`
- `reverse_order` / `scramble_order` — `{}`
- `change_draw_count` — `{"amount": <int>}` (new absolute draw count)
- `destroy_card` — `{"card_target": "this" | "chosen_card" | "all_in_play" | ...}`
- `set_win_condition` — `{"kind": "highest_points"|"lowest_points"|"first_to"|"empty_hand"|"last_standing"|"none", "threshold": <int|null>}`
- `set_rule` — `{"path": <str>, "value": ...}`
- `set_condition` — `{"target": <TARGET>, "key": <str>, "value": ...}`
- `set_card_attribute` — `{"card_target": ..., "key": <str>, "value": ...}`
- `create_card` — `{"title": ..., "description": ..., "ops": [...], "destination": ...}`
- `register_hook` — `{"event": <GameEvent>, "scope": "center"|"player", "code": <sandbox code>}`
- `custom_note` — `{"note": <str>}` — flavour / table-adjudicated actions with no engine effect.
- `counter_play` — `{"mode": "negate"|"steal_hand"|"redirect"}` — reaction cards
  only (see REACTIONS below).

When the effect is a real-world action the engine can't execute (a dare, a
performance) but it DOES change points, encode the point change with
`add_points` plus a `custom_note` describing the action.

### SANDBOX — executable effect code (required on every card)

A string containing exactly one function:

```python
def apply(state, ctx):
    ...
```

validated by `engine.sandbox.validate.validate_snippet` and executed in an
isolated subprocess against the `SandboxGame` facade. Even trivially
ops-expressible cards carry equivalent sandbox code — that redundancy is the
teaching corpus.

Available on `state` (see `src/engine/sandbox/api_surface.py`):
reads — `current_player_id`, `actor_id`, `draw_count`, `deck_size`,
`turn_order`, `players()`, `player(id)` (`.id/.name/.score/.hand_size/.connected`),
`rules()`, `my_hand()`, `hand_size(id)`, `conditions(id)`, `card(id)`
(returns `title`, `description`, `alt_text`, `attributes`, `origin`);
mutators — `add_points`, `subtract_points`, `set_points`, `skip_turn`,
`extra_turn`, `set_draw_count`, `note`, `reverse_order`, `scramble_order`,
`steal_points`, `draw_cards`, `destroy_card`, `set_win_condition`, `end_game`,
`set_rule`, `set_condition`, `set_card_attribute`, `create_card`,
`shuffle_into_deck`, `register_hook`, `unregister_hook`, plus context-gated
`reject_play` (on_validate_play hooks) and `counter_play` (reactions).

Style guide:
- Player targets are runtime Target strings: `"self"`, `"chooser"` (the chosen
  player), `"all"`, `"all_others"`, `"left_neighbor"`, `"right_neighbor"`,
  `"id:<player_id>"`, `"has:<condition_key>"`.
- Read `ctx` defensively: `ctx.get("actor_id", "")`.
- Use `state.note(...)` for the table-adjudicated part of a dare.
- Point losses use `subtract_points` (`add_points` rejects negative amounts).

### REACTIONS — the `on_reaction` contract

When a player plays a card, other players holding `on_reaction` cards get a
timed window to play one. Inside a reaction's sandbox:

- `state.actor_id` — the **reactor** (the player playing the reaction).
- `ctx["pending_card_id"]`, `ctx["pending_actor_id"]`,
  `ctx["pending_card_title"]`, `ctx["pending_ops"]` — the play being reacted
  to (`pending_ops` is the pending plan's op dicts; snippet code is not exposed).
- `state.counter_play(mode)` decides the pending play's fate:
  - `"negate"` — the pending card is countered: its effect never happens, it
    goes to the discard.
  - `"steal_hand"` — the pending card's effect never happens; the card goes to
    the reactor's hand instead.
  - `"redirect"` — the pending effect resolves as if the reactor had played it.
- Emitting no `counter_play` is legal — the reaction's own side effects apply
  and the original play still resolves ("you also lose 3 for trying").

A pending card with the `uncounterable` attribute never opens a window.
Reactions cannot be countered (no windows open during a reaction).

## Appendix: v1 → v2 mapping

Legacy annotations are normalized on input by
`models.card.normalise_canonical` (permanent shim — persisted room canonicals
and RAG payloads carry v1 dicts forever):

| v1 | v2 |
|---|---|
| `timing` | dropped; used only to disambiguate legacy placement |
| `placement: "self"` + `timing: "modifier"` | `placement: "player"` |
| `placement: "self"` + `timing: "immediate"` | `placement: "discard"` |
| `placement: "destroy"` | `placement: "discard"` (self-removal stays in ops) |
| `trigger_event` | renamed `trigger` |
| `trigger/trigger_event: "on_play"` on an immediate | `trigger: null` |
| `trigger_event: "on_draw"` | `trigger: "on_draw_step"` |
| `trigger_event: "on_score"` | `trigger: "on_score_change"` |
| `trigger: "on_play_card"` | `trigger: "on_play"` |
| `trigger: "on_empty_hand"` | `trigger: "on_win_check"` |
| `trigger: "on_physical_action"` | `trigger: null` (table-adjudicated; encode via note/set_rule) |
| `target: "center"` | re-annotate: `none` for global rules |
| `snippet` (prose) | effect moved to ops `custom_note` or rewritten as real `sandbox` code |
| `snippet` (starts with `def apply`) | renamed `sandbox` |
| description starting with `[...]` | bracket content moved to top-level `alt_text` |
| missing `venue` | `"all"` |
